#!/usr/bin/env bash
# HOST helpers shared by the M1.11-M1.15 scripts. Source it; do not run it.
# Only these sbx forms are used (all appear in the vendored launcher or the repo docs):
#   sbx exec [-i] [-w DIR] NAME bash -lc '...'   sbx env rm FILE --env-arg name=NAME   sbx ls
# `sbx stop` is NOT verified anywhere in the repo: see stop_sandbox below.

M1_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$M1_DIR/../.." && pwd)"
RESULTS="${RESULTS:-$M1_DIR/results}"
mkdir -p "$RESULTS"

PASS=0; FAIL=0; INFO=0
OUT=""

begin() {  # begin <check-id> : opens results/<id>-<timestamp>.md
  OUT="$RESULTS/$1-$(date +%Y%m%d-%H%M%S).md"
  { echo "# $1"; echo; echo "Run: $(date -u +%FT%TZ) on $(uname -sm), sbx: $(sbx version 2>/dev/null | head -n1 || echo unknown)"; echo; } > "$OUT"
  echo "Writing $OUT"
}
say()  { printf '%s\n' "$*" | tee -a "$OUT"; }
pass() { PASS=$((PASS+1)); say "- PASS: $*"; }
fail() { FAIL=$((FAIL+1)); say "- FAIL: $*"; }
info() { INFO=$((INFO+1)); say "- INFO: $*"; }
finish() {
  say ""; say "Summary: $PASS pass, $FAIL fail, $INFO info. Paste the findings into docs/work-plan.md."
  [ "$FAIL" -eq 0 ]
}

need_sbx() { command -v sbx >/dev/null || { echo "sbx not found. Run this on the Mac, not inside a sandbox." >&2; exit 2; }; }

# sx NAME 'script' : run a bash -lc script inside the sandbox (stdout+stderr returned).
sx() { sbx exec "$1" bash -lc "$2" 2>&1; }

# sx_script NAME [ARGS...] < script : run the script on stdin inside the sandbox WITHOUT piping it through `sbx exec -i`.
# The script travels base64-encoded in the command line and runs from a temp file, so nothing in it can consume the script's
# own stdin, and its exit status is returned. Used by m1-15 after sections C and D came back empty with `bash -s` over `-i`.
sx_script() {
  local n="$1" b; shift
  b="$(base64 | tr -d '\n')"
  sbx exec "$n" bash -lc "echo $b | base64 -d > /tmp/.m1-probe.sh; bash /tmp/.m1-probe.sh $*; rc=\$?; rm -f /tmp/.m1-probe.sh; exit \$rc"
}

now_ms() { python3 -c 'import time; print(int(time.time()*1000))'; }

# stop_sandbox NAME : tries SBX_STOP_CMD (default "sbx stop NAME"); if that fails, asks the human to stop it.
stop_sandbox() {
  local name="$1" cmd="${SBX_STOP_CMD:-sbx stop $1}"
  if ! $cmd; then
    echo "'$cmd' failed (the stop command is unverified). Stop sandbox '$name' another way"
    echo "(Docker Desktop > Sandboxes, or 'sbx ls' / 'sbx --help' to find the command), then press Enter."
    read -r _
  fi
}

# hold_team / release_team: start the team and keep it alive (start-team itself is idempotent: exits 0 if team-started exists).
# Run from the app directory: start-team opens every seat in the CURRENT directory, and only $REPO (the mounted repo, same path inside
# the sandbox) is trusted by `prepare`. From the default home directory the seats stop at Claude's trust / outside-directory prompt and
# never acknowledge (the 3- and 5-agent M1.12 runs: start-team took its full timeouts, RAM barely moved, no message in 300 s).
# M1.12 finding (2026-10-06): herdr and every claude seat die as soon as the `sbx exec` that ran start-team returns (the herdr log just
# stops, no shutdown line; setsid did not help). The interactive dev-team shell works because it stays open. So the team is started
# inside an exec that is HELD OPEN in the background on the host for as long as the checks need it. release_team ends it.
hold_team() {  # hold_team NAME : start the team under a long-lived exec and wait (up to 400 s) for the team-started marker
  local n="$1" i
  sbx exec -w "$REPO" "$n" bash -lc 'start-team; sleep 3000' >"$RESULTS/.hold-$n.log" 2>&1 &
  echo $! > "$RESULTS/.hold-$n.pid"
  for i in $(seq 1 80); do
    sx "$n" 'test -f "$HOME/work/factory/team-started" && echo up' | grep -q '^up$' && return 0
    sleep 5
  done
  echo "team-started not seen after 400 s; see $RESULTS/.hold-$n.log" >&2; return 1
}
release_team() {  # release_team NAME : end the held exec (this also stops the team)
  local f="$RESULTS/.hold-$1.pid"
  [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; rm -f "$f"; return 0
}
