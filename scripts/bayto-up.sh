#!/usr/bin/env bash
# Starts Bayto for daily use: migrates and seeds the database, then runs the API and the web app
# on your Claude subscription (no API key). Ctrl-C stops both.
#
#   scripts/bayto-up.sh
#   WEB_PORT=5174 scripts/bayto-up.sh      # the web proxy is fixed to the API on :8000
#
# Needs: Postgres running (DATABASE_URL, default postgresql+psycopg://bayto:bayto@localhost:5432/bayto),
# uv, node/npm, the `claude` CLI signed in, and `sbx`. Logs go to ./.bayto-run/.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT=8000; WEB_PORT="${WEB_PORT:-5173}"
LOGS="$REPO/.bayto-run"; mkdir -p "$LOGS"
export PYTHONPATH="$REPO/orchestrator/src:$REPO/agent-comms/src${PYTHONPATH:+:$PYTHONPATH}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://bayto:bayto@localhost:5432/bayto}"

missing=0
for c in uv node npm claude sbx curl; do command -v "$c" >/dev/null 2>&1 || { echo "missing: $c"; missing=1; }; done
[ "$missing" = 0 ] || exit 2

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY is set; Bayto runs on your subscription, so it is ignored here."
  unset ANTHROPIC_API_KEY
fi
for p in "$API_PORT" "$WEB_PORT"; do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$p/"; then
    echo "port $p is already in use (an old Bayto still running?). Stop it, or set WEB_PORT for the web app."; exit 2
  fi
done

echo "Database: migrate and seed"
(cd "$REPO/orchestrator" && uv run alembic upgrade head && uv run python -m orchestrator.seed) > "$LOGS/db.log" 2>&1 \
  || { echo "migrate/seed failed. Is Postgres running at $DATABASE_URL?"; tail -15 "$LOGS/db.log"; exit 2; }

PIDS=()
cleanup() { for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null; done; sleep 1
            for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null; done; true; }
trap cleanup EXIT INT TERM

export BAYTO_LLM_BACKEND=cli BAYTO_STREAMING_TURNS=1
export BAYTO_SUMMARY_MODEL="${BAYTO_SUMMARY_MODEL:-claude-sonnet-5-5}" BAYTO_SYNTHESIS_MODEL="${BAYTO_SYNTHESIS_MODEL:-claude-sonnet-5-5}" \
       BAYTO_HAND_RAISE_MODEL="${BAYTO_HAND_RAISE_MODEL:-claude-sonnet-5-5}"

(cd "$REPO/orchestrator" && exec uv run uvicorn orchestrator.app:app --port "$API_PORT") > "$LOGS/api.log" 2>&1 &
PIDS+=($!)
if [ ! -d "$REPO/web/node_modules" ] || [ "$REPO/web/package-lock.json" -nt "$REPO/web/node_modules" ]; then
  (cd "$REPO/web" && npm install) > "$LOGS/npm.log" 2>&1
fi
(cd "$REPO/web" && exec npx vite --port "$WEB_PORT" --strictPort --host 127.0.0.1) > "$LOGS/web.log" 2>&1 &
PIDS+=($!)

for i in $(seq 1 60); do
  curl -fs "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1 && curl -fs "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1 && break
  sleep 1
  [ "$i" = 60 ] && { echo "did not come up. API log:"; tail -20 "$LOGS/api.log"; exit 2; }
done
echo
echo "Bayto is up:  http://127.0.0.1:$WEB_PORT   (API on :$API_PORT, logs in $LOGS)"
echo "Ctrl-C to stop."
wait
