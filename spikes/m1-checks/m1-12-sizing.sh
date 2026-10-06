#!/usr/bin/env bash
# HOST: M1.12. Memory, CPU, creation time and assignment-to-first-message latency with 3, 5 and 7 agents.
# Usage: spikes/m1-checks/m1-12-sizing.sh [counts="3 5 7"]      (creates then removes sandboxes bayto-m112-<N>, one at a time)
# Cost: each seat reads its brief and acks, plus one tiny coordinator task per size. Roughly 15 short model calls in total.
# Needs Beans installed (see dev-team/README.md) and the host's `sbx`. Sandbox size comes from dev-team/run.sh (4 CPU, 8g).
set -uo pipefail
source "$(dirname "$0")/lib.sh"
need_sbx
counts="${1:-3 5 7}"
[ "$(printf '%s\n' $counts | sort -n | tail -n1)" -le 8 ] || { echo "roster_for has 8 roles: use counts of 8 or fewer" >&2; exit 2; }
roster_for() {  # first N of this fixed order; coordinator always first (first contact)
  local all=(coordinator backend-engineer qa-tester architect code-reviewer researcher frontend-engineer product-owner) i
  for ((i = 0; i < $1; i++)); do echo "${all[i]}"; done
}
begin m1-12
say "Sizes tested: $counts. Sandbox spec from dev-team/run.sh: $(grep -E '^\s+(cpus|memory):' "$REPO/dev-team/run.sh" | tr -s ' ' | tr '\n' ' ')"; say
say "| agents | create+prepare (s) | start-team (s) | warm exec (ms) | RAM idle base (MB) | RAM after team (MB) | RAM peak under task (MB) | MB/agent | CPU avg during task (%) | assignment -> first message (s) |"
say "|---|---|---|---|---|---|---|---|---|---|"

# Runs inside the sandbox. The probe asks the coordinator for one handoff message, because a reply typed in its terminal never becomes a factory message (the first run timed out for that reason). First message = first one NOT from the human; the submission itself is a message): samples memory every 2s and CPU over the window while a task is submitted.
read -r -d '' PROBE <<'EOS'
used() { free -m | awk '/^Mem:/{print $3}'; }
cpu() { awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5+$6}' /proc/stat; }
export PATH="$HOME/work/bin:$HOME/.local/bin:$PATH" FACTORY_DIR="$HOME/work/factory"
msgs() { cat "$FACTORY_DIR"/messages/*.json 2>/dev/null | jq -s '[.[] | select(.from != "human")] | length' 2>/dev/null || echo 0; }
before=$(msgs); read -r t0 i0 <<<"$(cpu)"; start=$(date +%s%N)
( peak=0; while :; do u=$(used); [ "$u" -gt "$peak" ] && peak=$u; echo "$peak" > /tmp/m112-peak; sleep 2; done ) & sampler=$!
crew ask "Sizing probe, not real work. Your only action: run  handoff send --to backend-engineer --from coordinator --kind assignment --body ack  and then stop. Do not read or change any files." </dev/null >/dev/null 2>&1
first=""
for _ in $(seq 1 1500); do                       # up to 300s at 0.2s
  if [ "$(msgs)" -gt "$before" ]; then first=$(( ($(date +%s%N) - start) / 1000000 )); break; fi
  sleep 0.2
done
sleep 20                                         # let the team settle so the peak includes follow-on turns
read -r t1 i1 <<<"$(cpu)"; kill "$sampler" 2>/dev/null
echo "first_ms=${first:-NONE} peak_mb=$(cat /tmp/m112-peak) cpu_pct=$(awk -v t0=$t0 -v t1=$t1 -v i0=$i0 -v i1=$i1 'BEGIN{d=t1-t0; printf "%.0f", d>0 ? 100*(d-(i1-i0))/d : 0}')"
EOS

for n in $counts; do
  sb="bayto-m112-$n"; rf="$RESULTS/roster-$n.txt"; roster_for "$n" > "$rf"
  envfile="$REPO/dev-team/.build/factory/sbxenv.yaml"
  say ""; echo "== $n agents: creating $sb" >&2
  t0=$(now_ms)
  if ! ( cd "$REPO" && NO_SWITCH=1 SESSION=none ROSTER="$rf" "$REPO/dev-team/run.sh" "$sb" ) >&2; then
    say "| $n | CREATE FAILED | | | | | | | | |"; continue
  fi
  create_s=$(( ($(now_ms) - t0) / 1000 ))
  t0=$(now_ms); sx "$sb" true >/dev/null; warm_ms=$(( $(now_ms) - t0 ))
  mem3() { sx "$sb" 'for i in 1 2 3; do free -m | awk "/^Mem:/{print \$3}"; sleep 3; done' | grep -E '^[0-9]+$' | sort -n | sed -n 2p; }
  base=$(mem3)
  t0=$(now_ms); hold_team "$sb" >&2; team_s=$(( ($(now_ms) - t0) / 1000 ))
  sleep 30   # settle before the idle reading
  after=$(mem3)
  out="$(sbx exec -i -w "$REPO" "$sb" bash -lc 'bash -s' <<<"$PROBE" 2>&1 | tail -n1)"
  first_ms=$(printf '%s' "$out" | sed -n 's/.*first_ms=\([0-9A-Z]*\).*/\1/p'); peak=$(printf '%s' "$out" | sed -n 's/.*peak_mb=\([0-9]*\).*/\1/p'); cpu=$(printf '%s' "$out" | sed -n 's/.*cpu_pct=\([0-9]*\).*/\1/p')
  per=$(( (${after:-0} - ${base:-0}) / n ))
  if [ "${first_ms:-NONE}" = NONE ]; then   # dump what the seats are doing before this sandbox is removed
    say ""; say "Diagnostics for $n agents (no first message): ready markers, herdr agents, herdr log tail (the team was started under a held-open exec)"; say '```'
    sbx exec -w "$REPO" "$sb" bash -lc 'ls "$HOME/work/factory/ready" 2>&1; echo ---; herdr agent list 2>&1 | head -c 2500; echo; echo ---; tail -n 15 "$HOME/work/factory/evidence/herdr.log" 2>&1; echo --- herdr-server.log; tail -n 40 "$HOME/.config/herdr/herdr-server.log" 2>&1; echo --- processes; ps -eo pid,etime,args 2>&1 | grep -E "herdr|claude" | grep -v grep | cut -c1-160 | head -20; echo --- team-started; ls -l "$HOME/work/factory/team-started" 2>&1; echo --- memory; free -m | head -2; dmesg 2>/dev/null | grep -i -E "oom|killed process" | tail -5' 2>&1 | tee -a "$OUT"
    say '```'
  fi
  first_s="NONE (300s timeout)"; [ "${first_ms:-NONE}" != NONE ] && first_s="$(python3 -c "print(round($first_ms/1000,1))")"
  say "| $n | $create_s | $team_s | $warm_ms | ${base:-?} | ${after:-?} | ${peak:-?} | $per | ${cpu:-?} | $first_s |"
  last_peak="${peak:-0}"; last_n="$n"
  release_team "$sb"
  echo "== removing $sb" >&2
  sbx env rm "$envfile" --env-arg "name=$sb" >&2 || say "(!) removing $sb failed: run: sbx env rm $envfile --env-arg name=$sb"
done

say ""
if [ -n "${last_n:-}" ] && [ "${last_peak:-0}" -gt 0 ]; then
  rec=$(python3 -c "import math; print(max(4, 2*math.ceil(${last_peak}*1.3/1024/2)))")
  say "Heuristic recommendation (peak RAM at $last_n agents x 1.3 headroom, rounded up to an even GB): memory ${rec}g. Decide the agent cap from the MB/agent column and the latency column; the work plan wants the recommended size and cap recorded."
fi
say "Not measured here: disk, token cost per size (read it from the Anthropic console around the run), and behaviour with several tasks at once."
info "CPU % is sandbox-wide busy time during the probe window (task + 20s settle); MB/agent = (RAM after team - RAM idle base) / agents."
finish
