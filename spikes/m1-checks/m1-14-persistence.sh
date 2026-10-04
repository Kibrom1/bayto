#!/usr/bin/env bash
# HOST: M1.14. Does agent state survive a sandbox stop and start?
# Usage: spikes/m1-checks/m1-14-persistence.sh <sandbox-name>     (team should be started; no model calls are made)
# Snapshots files, processes and Claude transcripts, stops the sandbox, lets the next `sbx exec` start it, snapshots again, compares.
# The stop command is unverified: it tries `sbx stop NAME` (override with SBX_STOP_CMD="...") and otherwise asks you to stop it by hand.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
need_sbx
name="${1:?sandbox name, e.g. bayto-dev}"
begin m1-14

snap() {
  sbx exec "$name" bash -lc 'bash -s' <<'EOS' 2>&1
export PATH="$HOME/work/bin:$HOME/.local/bin:$PATH"
f="$HOME/work/factory"
echo "boot: $(uptime -s 2>/dev/null)"
echo "marker: $(cat "$HOME/work/.m1-14-marker" 2>/dev/null || echo missing)"
echo "tmp-marker: $(cat /tmp/.m1-14-marker 2>/dev/null || echo missing)"
echo "factory-files: $(find "$f" -type f 2>/dev/null | wc -l)"
echo "messages: $(ls "$f/messages" 2>/dev/null | wc -l)"
echo "ready: $(ls "$f/ready" 2>/dev/null | sort | tr '\n' ' ')"
echo "team-started-file: $([ -e "$f/team-started" ] && echo yes || echo no)"
echo "state-hash: $(cat "$f/state.json" "$f/roster.json" 2>/dev/null | sha256sum | cut -c1-16)"
echo "msg-hash: $(cat "$f"/messages/*.json 2>/dev/null | sha256sum | cut -c1-16)"
echo "claude-transcripts: $(find "$HOME/.claude/projects" -name '*.jsonl' 2>/dev/null | wc -l)"
echo "transcript-bytes: $(find "$HOME/.claude/projects" -name '*.jsonl' -exec cat {} + 2>/dev/null | wc -c)"
echo "herdr-server-procs: $(pgrep -fc 'herdr server')"
echo "claude-procs: $(pgrep -fc 'claude')"
echo "herdr-agents: $(herdr agent list 2>&1 | head -n 40 | tr '\n' ' ' | cut -c1-400)"
EOS
}
val() { printf '%s\n' "$1" | sed -n "s/^$2: //p" | head -n1; }

sx "$name" 'echo m1-14-$(date +%s) | tee "$HOME/work/.m1-14-marker" > /tmp/.m1-14-marker' >/dev/null
say "Before stop:"; before="$(snap)"; say '```'; say "$before"; say '```'
[ "$(val "$before" team-started-file)" = yes ] || info "team is not started in this sandbox: process checks below will only show the empty state. Run start-team first for a meaningful M1.14."

stop_sandbox "$name"
t0=$(now_ms)
after="$(snap)"; restart_ms=$(( $(now_ms) - t0 ))
say; say "After stop + next sbx exec (restart took ${restart_ms} ms including the snapshot):"; say '```'; say "$after"; say '```'
say

b="$(val "$before" boot)"; a="$(val "$after" boot)"
[ "$b" != "$a" ] && pass "the sandbox really restarted (boot time $b -> $a)" || fail "boot time unchanged ($b): the sandbox was not stopped, so the rest of this run proves nothing"
[ "$(val "$after" marker)" = "$(val "$before" marker)" ] && pass "files in ~/work survive the stop (marker intact)" || fail "marker in ~/work did not survive"
[ "$(val "$after" tmp-marker)" = "$(val "$before" tmp-marker)" ] && info "/tmp survived the stop (not guaranteed by design, do not rely on it)" || info "/tmp did NOT survive: keep nothing important there"
for k in factory-files messages ready state-hash msg-hash team-started-file; do
  [ "$(val "$after" "$k")" = "$(val "$before" "$k")" ] && pass "factory $k unchanged" || fail "factory $k changed: '$(val "$before" "$k")' -> '$(val "$after" "$k")'"
done
[ "$(val "$after" claude-transcripts)" = "$(val "$before" claude-transcripts)" ] && [ "$(val "$after" transcript-bytes)" = "$(val "$before" transcript-bytes)" ] && pass "Claude transcripts survive (conversations can be resumed with --resume)" || fail "Claude transcripts differ after restart"

hp="$(val "$after" herdr-server-procs)"; cp_="$(val "$after" claude-procs)"
if [ "${hp:-0}" -gt 0 ] && [ "${cp_:-0}" -gt 0 ]; then pass "herdr server and agent processes are still running after restart (stop did not kill them)"
else
  info "processes did NOT survive: herdr servers=${hp:-0}, claude procs=${cp_:-0}. Expected for a VM stop."
  if [ "$(val "$after" team-started-file)" = yes ]; then
    info "BUT team-started still exists, so start-team will say 'Team already started' and do nothing. The orchestrator must remove \$HOME/work/factory/team-started (or ship a restart step) before re-running start-team after a stop"
  fi
fi
say
say "Recovery to try by hand if processes are gone (not run automatically):"
say "  sbx exec $name bash -lc 'rm -f \$HOME/work/factory/team-started; start-team'"
say "then check that the seats can pick up their earlier work (their briefs re-ack; conversation memory is only what --resume restores)."
finish
