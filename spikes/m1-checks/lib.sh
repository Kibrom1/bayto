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

# ensure_team NAME : start-team is idempotent (exits 0 if team-started exists).
ensure_team() { sx "$1" 'start-team' | tail -n 3; }
