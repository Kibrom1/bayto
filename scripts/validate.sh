#!/usr/bin/env bash
# Pre-deploy validation: boots the real app instead of only running unit tests, so a change that passes pytest but cannot
# start (a bad migration, an import error, a framework upgrade that rejects the telemetry settings) is caught before it ships.
#
#   scripts/validate.sh            # everything
#   scripts/validate.sh static boot web   # only these stages (static, migrations, boot, blackbox, web)
#
# Needs Postgres at DATABASE_URL (default: the Bayto dev database). CI runs it in the "validate" job. Set PYTHON to use a
# different interpreter, for example PYTHON="uv run --all-extras python" from orchestrator/.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://bayto:bayto@localhost:5432/bayto}"
export BAYTO_SUMMARY_MODEL="${BAYTO_SUMMARY_MODEL:-validate}" BAYTO_SYNTHESIS_MODEL="${BAYTO_SYNTHESIS_MODEL:-validate}"
PORT="${VALIDATE_PORT:-8765}"
WORK="$(mktemp -d)"
bad=0
SERVER_PID=""
trap 'stop_server; rm -rf "$WORK"' EXIT

pass() { printf '  ok    %s\n' "$1"; }
fail() {
  printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/          /' | tail -15
  # In GitHub Actions also raise an annotation, which shows on the pull request and in the checks API.
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    local msg="$1"; [ -n "${2:-}" ] && msg="$1"$'\n'"$(printf '%s\n' "$2" | tail -15)"
    msg="${msg//'%'/%25}"; msg="${msg//$'\r'/%0D}"; msg="${msg//$'\n'/%0A}"
    echo "::error title=validate::$msg"
  fi
  bad=1
}
stage() { local s; for s in $WANT; do [ "$s" = "$1" ] && return 0; done; return 1; }
WANT="${*:-static migrations boot blackbox web}"

stop_server() {
  if [ -n "$SERVER_PID" ]; then kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; SERVER_PID=""; fi
}

# start_server <log> [VAR=value ...]: the API on $PORT with the given extra environment; returns 0 once /healthz answers.
start_server() {
  local log="$1"; shift
  stop_server
  ( cd "$REPO/orchestrator" && env "$@" $PYTHON -m uvicorn orchestrator.app:app --port "$PORT" >"$log" 2>&1 ) &
  SERVER_PID=$!
  local i
  for i in $(seq 1 60); do
    curl -fsS -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && return 0
    kill -0 "$SERVER_PID" 2>/dev/null || return 1
    sleep 1
  done
  return 1
}

code() { curl -s -o /dev/null -m 10 -w '%{http_code}' "$@"; }

if stage static; then
  echo "Static checks"
  if python3 -m py_compile "$REPO"/scripts/*.py 2>"$WORK/err"; then pass "scripts/*.py compile"; else fail "scripts/*.py compile" "$(cat "$WORK/err")"; fi
  shbad=""; for f in "$REPO"/scripts/*.sh; do bash -n "$f" 2>>"$WORK/sherr" || shbad="$shbad $f"; done
  if [ -z "$shbad" ]; then pass "scripts/*.sh parse"; else fail "scripts/*.sh parse:$shbad" "$(cat "$WORK/sherr")"; fi
fi

if stage migrations; then
  echo "Database migrations"
  heads="$(cd "$REPO/orchestrator" && $PYTHON -m alembic heads 2>"$WORK/err" | grep -c '(head)')"
  if [ "$heads" = 1 ]; then pass "exactly one Alembic head"; else fail "expected one Alembic head, found $heads (two branches both added a migration: merge them)" "$(cat "$WORK/err")"; fi
  if out="$(cd "$REPO/orchestrator" && $PYTHON -m alembic upgrade head 2>&1)"; then pass "alembic upgrade head"; else fail "alembic upgrade head" "$out"; fi
  if out="$(cd "$REPO/orchestrator" && $PYTHON -m orchestrator.seed 2>&1)"; then pass "seed"; else fail "seed" "$out"; fi
fi

if stage boot; then
  echo "App boots (the failures unit tests cannot see)"
  # Each row is an environment the app must start in. The OTEL rows are what broke startup with FastAPI 0.14x.
  i=0
  while IFS='|' read -r label envs; do
    i=$((i+1)); log="$WORK/boot$i.log"
    # shellcheck disable=SC2086
    if start_server "$log" $envs; then
      s_modes="$(code "http://127.0.0.1:$PORT/modes")"; s_tasks="$(code "http://127.0.0.1:$PORT/tasks")"
      if [ "$s_modes" = 200 ] && [ "$s_tasks" = 200 ]; then pass "$label"; else fail "$label: /modes=$s_modes /tasks=$s_tasks" "$(tail -15 "$log")"; fi
      sleep 2
      if grep -q "BadStatusLine" "$log"; then fail "$label: HTTP sent to the gRPC port (BadStatusLine in the log)" "$(grep BadStatusLine "$log" | head -3)"; fi
    else
      fail "$label: the app did not start" "$(tail -15 "$log")"
    fi
    stop_server
  done <<'ROWS'
default environment|BAYTO_X=1
tracing on, collector unreachable|OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317
OTEL variables left in a shell|OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317 OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp
ROWS

  log="$WORK/auth.log"
  if start_server "$log" BAYTO_API_TOKEN=validate-token; then
    a="$(code "http://127.0.0.1:$PORT/tasks")"; b="$(code -H 'Authorization: Bearer validate-token' "http://127.0.0.1:$PORT/tasks")"
    h="$(code "http://127.0.0.1:$PORT/healthz")"
    if [ "$a" = 401 ] && [ "$b" = 200 ] && [ "$h" = 200 ]; then pass "API token: 401 without, 200 with, /healthz open"
    else fail "API token: without=$a with=$b healthz=$h (want 401 200 200)" "$(tail -10 "$log")"; fi
  else fail "API token: the app did not start" "$(tail -15 "$log")"; fi
  stop_server

  # Models must be required: starting a session without a summary model has to say so, not crash later.
  log="$WORK/nomodel.log"
  if start_server "$log" BAYTO_SUMMARY_MODEL= BAYTO_SYNTHESIS_MODEL=; then pass "boots without helper models (start is refused later, with a message)"
  else fail "boots without helper models" "$(tail -10 "$log")"; fi
  stop_server
fi

if stage blackbox; then
  echo "API black-box checks (docs/e2e/smoke/api_blackbox.py)"
  if start_server "$WORK/bb.log"; then
    if API="http://127.0.0.1:$PORT" OUT="$WORK/bb.json" python3 "$REPO/docs/e2e/smoke/api_blackbox.py" >"$WORK/bb.out" 2>&1; then :; fi
    nfail="$(grep -c '^FAIL ' "$WORK/bb.out")"; summary="$(grep -E '[0-9]+/[0-9]+ checks passed' "$WORK/bb.out" | tail -1)"
    if [ -n "$summary" ] && [ "$nfail" = 0 ]; then pass "$summary"; else fail "black-box: ${summary:-no summary} ($nfail failing)" "$(grep '^FAIL ' "$WORK/bb.out" | head -12)"; fi
  else fail "black-box: the app did not start" "$(tail -15 "$WORK/bb.log")"; fi
  stop_server
fi

if stage web; then
  echo "Web app"
  cd "$REPO/web" || exit 1
  if out="$(npx tsc -b 2>&1)"; then pass "type check"; else fail "type check" "$out"; fi
  if out="$(npm run build 2>&1)"; then pass "production build"; else fail "production build" "$out"; fi
  ( npx vite --port 5199 --strictPort >"$WORK/vite.log" 2>&1 ) & VITE=$!
  ok=""; for i in $(seq 1 30); do [ "$(code http://127.0.0.1:5199/services)" = 200 ] && { ok=1; break; }; sleep 1; done
  if [ -n "$ok" ]; then pass "dev server serves /services"; else fail "dev server did not serve /services" "$(tail -10 "$WORK/vite.log")"; fi
  kill "$VITE" 2>/dev/null; pkill -P "$VITE" 2>/dev/null; wait "$VITE" 2>/dev/null
fi

echo
if [ "$bad" = 0 ]; then echo "Validation passed."; else echo "Validation FAILED. Do not deploy."; fi
exit "$bad"
