#!/usr/bin/env bash
# HOST: M1.13. Is message-level output enough for the UI, or do we need the headless turn runner (`claude -p --output-format stream-json`)?
# Usage: spikes/m1-checks/m1-13-streaming.sh <sandbox-name>     (sandbox must be running; costs one short Haiku call)
# Part 1 reads existing factory messages and reports the gaps between consecutive agent messages: the stretch where a UI built on
#        message files would show nothing. Run it after a real task for meaningful numbers.
# Part 2 prototypes the turn runner through `sbx exec` and measures time to first event, first text delta and completion.
# The verdict is still yours: this only supplies the numbers (work plan M1.13 calls it a judgement).
set -uo pipefail
source "$(dirname "$0")/lib.sh"
need_sbx
name="${1:?sandbox name, e.g. bayto-dev}"
begin m1-13

say "## Part 1. Message-level cadence in the existing factory dir"
g="$(sx "$name" 'cd "$HOME/work/factory/messages" 2>/dev/null && for f in *.json; do printf "%s %s %s\n" "$(stat -c %Y "$f")" "$(jq -r .from "$f")" "$(jq -r .kind "$f")"; done' | grep -E '^[0-9]+ ' )"
if [ -z "$g" ]; then info "no messages yet: run a real task with crew ask, then rerun part 1"; else
  echo "$g" | python3 -c '
import sys
rows=[l.split() for l in sys.stdin if l.strip()]
t=[int(r[0]) for r in rows]; gaps=[b-a for a,b in zip(t,t[1:])]
print("messages: %d, span: %d s" % (len(rows), t[-1]-t[0] if rows else 0))
if gaps:
    s=sorted(gaps); p=lambda q: s[min(len(s)-1,int(q*len(s)))]
    print("gap between consecutive messages (s): median %d, p90 %d, max %d; gaps over 60 s: %d of %d" % (p(.5), p(.9), s[-1], sum(g>60 for g in gaps), len(gaps)))
' | tee -a "$OUT"
fi
say

say "## Part 2. Turn-runner prototype: claude -p --output-format stream-json via sbx exec"
prompt='Count from 1 to 40, one number per line, with a short comment after each. No tools.'
start=$(now_ms)
raw="$RESULTS/m1-13-stream-$(date +%H%M%S).jsonl"
# stdin carries the prompt (variadic flags would swallow a positional prompt); --verbose is required for stream-json in -p mode.
printf '%s\n' "$prompt" | sbx exec -i "$name" bash -lc 'claude -p --model claude-haiku-4-5-20251001 --output-format stream-json --verbose --include-partial-messages' 2>"$RESULTS/.m1-13-err" |
  python3 -c '
import sys, time, json
t0=float(sys.argv[1]); first=None; delta=None; n=0; types={}
for line in sys.stdin:
    now=time.time()*1000-t0; n+=1
    sys.stdout.write(line)
    try: ev=json.loads(line)
    except Exception: types["unparsed"]=types.get("unparsed",0)+1; continue
    ty=ev.get("type","?"); sub=(ev.get("event") or {}).get("delta",{}).get("type")
    key=ty+("/"+sub if sub else ""); types[key]=types.get(key,0)+1
    if first is None: first=now
    if sub=="text_delta" and delta is None: delta=now
sys.stderr.write(json.dumps({"events":n,"first_event_ms":first,"first_text_delta_ms":delta,"total_ms":time.time()*1000-t0,"types":types})+"\n")
' "$start" > "$raw" 2> "$RESULTS/.m1-13-stats"
stats="$(tail -n1 "$RESULTS/.m1-13-stats")"; err="$(head -c 400 "$RESULTS/.m1-13-err")"; rm -f "$RESULTS/.m1-13-stats" "$RESULTS/.m1-13-err"
if printf '%s' "$stats" | grep -q '"events": *[1-9]'; then
  say '```'; say "$stats"; say '```'
  say "Raw stream saved to $raw"
  dl="$(printf '%s' "$stats" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("first_text_delta_ms") or "")')"
  if [ -n "$dl" ]; then pass "stream-json through sbx exec works and emits text deltas (first delta after ${dl%.*} ms)"; else info "stream works but no text_delta events appeared; check the types counts above"; fi
else
  fail "no stream events came back through sbx exec. stderr: ${err:-none}. Possibly claude is not logged in/proxied for non-seat runs, or sbx exec buffers output."
fi
say
say "## How to decide"
say "- Message-level is enough when part 1 shows gaps mostly under about 30 s and the UI only needs 'role X sent Y'."
say "- Build the turn runner (M2 scope, not this spike) when gaps routinely exceed about 60 s, or the UI must show live token text or tool activity, and part 2 shows text deltas arriving well before completion."
say "- Cost of the runner: each turn is a separate claude -p process, so seats lose the persistent herdr session (context carries only through --resume or what the runner re-sends)."
finish
