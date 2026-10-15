#!/usr/bin/env bash
# Starts a live Bayto stack and runs live_e2e.py against it (real sbx seats, real `claude -p` moderator).
# Keep this folder OUTSIDE the Bayto repo; it only needs the repo's path.
#
#   BAYTO_REPO=~/Desktop/workspace/AI/bayto ./run_live_e2e.sh                 # debate, then open-chat
#   BAYTO_REPO=... ./run_live_e2e.sh debate                                   # one mode
#   BAYTO_REPO=... ./run_live_e2e.sh debate --headed --natural-finish         # extra flags go to live_e2e.py
#   BAYTO_REPO=... USE_RUNNING=1 ./run_live_e2e.sh                            # stack already up: just run the test
#
# Needs: Postgres running (DATABASE_URL, default postgresql+psycopg://bayto:bayto@localhost:5432/bayto),
# uv, node/npm, python3, the `claude` CLI signed in, and `sbx`.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${BAYTO_REPO:-}"
[ -n "$REPO" ] && [ -d "$REPO/orchestrator" ] || { echo "Set BAYTO_REPO to your Bayto checkout (the folder with orchestrator/ and web/)."; exit 2; }
REPO="$(cd "$REPO" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN="$HERE/runs/$STAMP"; mkdir -p "$RUN"
API_PORT="${API_PORT:-8000}"; WEB_PORT="${WEB_PORT:-5173}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://bayto:bayto@localhost:5432/bayto}"

MODES=()
EXTRA=()
for a in "$@"; do
  case "$a" in
    debate|open-chat|brainstorm) MODES+=("$a") ;;
    *) EXTRA+=("$a") ;;
  esac
done
[ ${#MODES[@]} -gt 0 ] || MODES=(debate open-chat)

say() { printf '\n== %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 ($2)"; MISSING=1; }; }

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null; done
  [ ${#PIDS[@]} -gt 0 ] && { sleep 1; for pid in "${PIDS[@]}"; do kill -9 "$pid" 2>/dev/null; done; } || true
}
trap cleanup EXIT INT TERM

say "Preflight"
MISSING=0
need uv "https://docs.astral.sh/uv/"; need node "node 20+"; need npm ""; need python3 ""
if [ -z "${USE_RUNNING:-}" ]; then
  need claude "install Claude Code and sign in"; need sbx "Docker Sandboxes CLI"
fi
[ "$MISSING" = 0 ] || exit 2
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY is set; Bayto is subscription-only, so it is being unset for this run."
  unset ANTHROPIC_API_KEY
fi

say "Python env for the browser driver"
VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" && "$VENV/bin/pip" -q install playwright && "$VENV/bin/playwright" install chromium || exit 2
fi
PY="$VENV/bin/python"

if [ -z "${USE_RUNNING:-}" ]; then
  # The orchestrator's venv may not have the project itself installed (alembic gets src/ from alembic.ini, the
# others do not). Put src/ and acp on the path, the same way spikes/m1-checks/moderator-cli-live.sh does.
export PYTHONPATH="$REPO/orchestrator/src:$REPO/agent-comms/src${PYTHONPATH:+:$PYTHONPATH}"

say "Database: migrate and seed"
  (cd "$REPO/orchestrator" && uv run alembic upgrade head && uv run python -m orchestrator.seed) > "$RUN/db.log" 2>&1 \
    || { echo "migrate/seed failed (DATABASE_URL=$DATABASE_URL). See $RUN/db.log"; tail -15 "$RUN/db.log"; exit 2; }

  say "Starting the API on :$API_PORT"
  export BAYTO_SUMMARY_MODEL="${BAYTO_SUMMARY_MODEL:-claude-sonnet-5-5}" BAYTO_SYNTHESIS_MODEL="${BAYTO_SYNTHESIS_MODEL:-claude-sonnet-5-5}" \
         BAYTO_HAND_RAISE_MODEL="${BAYTO_HAND_RAISE_MODEL:-claude-sonnet-5-5}" BAYTO_LLM_BACKEND=cli BAYTO_STREAMING_TURNS=1
  (cd "$REPO/orchestrator" && exec uv run uvicorn orchestrator.app:app --port "$API_PORT") > "$RUN/api.log" 2>&1 &
  PIDS+=($!)

  say "Starting the web app on :$WEB_PORT"
  [ -d "$REPO/web/node_modules" ] || (cd "$REPO/web" && npm install) > "$RUN/npm.log" 2>&1
  (cd "$REPO/web" && exec npx vite --port "$WEB_PORT" --host 127.0.0.1) > "$RUN/web.log" 2>&1 &
  PIDS+=($!)

  for i in $(seq 1 60); do
    curl -fs "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1 && curl -fs "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1 && break
    sleep 1
    [ "$i" = 60 ] && { echo "stack did not come up. API log tail:"; tail -20 "$RUN/api.log"; exit 2; }
  done
  echo "API and web are up. Logs: $RUN"
  grep -i "not configured\|warning" "$RUN/api.log" | head -3
fi

say "Moderator through your Claude login (no sandbox)"
if [ -z "${USE_RUNNING:-}" ]; then
  bash "$REPO/spikes/m1-checks/moderator-cli-live.sh" > "$RUN/moderator-cli.log" 2>&1 \
    && echo "PASS moderator-cli-live" || { echo "FAIL moderator-cli-live (see $RUN/moderator-cli.log)"; tail -8 "$RUN/moderator-cli.log"; }
fi

STATUS=0
for mode in "${MODES[@]}"; do
  say "Live E2E: $mode"
  "$PY" "$HERE/live_e2e.py" --mode "$mode" --web "http://127.0.0.1:$WEB_PORT" --api "http://127.0.0.1:$API_PORT" \
        --out "$RUN/$mode" "${EXTRA[@]+"${EXTRA[@]}"}" 2>&1 | tee "$RUN/$mode.log"
  [ "${PIPESTATUS[0]}" = 0 ] || STATUS=1
done

[ -f "$RUN/api.log" ] && tail -200 "$RUN/api.log" > "$RUN/api-tail.log"
say "Done"
echo "Results: $RUN   (report.json per mode, screenshots, api.log)"
[ "$STATUS" = 0 ] && echo "ALL PASSED" || echo "SOME CHECKS FAILED: send me $RUN (zip it) plus orchestrator/.sessions/<session id>/"
exit "$STATUS"
