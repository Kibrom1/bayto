#!/usr/bin/env bash
# Checks everything Bayto needs before the first start and says how to fix what is missing.
#
#   scripts/bayto-doctor.sh
#
# Read-only: it installs and changes nothing. Exit code 0 means ready, 1 means something needs fixing.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://bayto:bayto@localhost:5432/bayto}"
bad=0
ok()   { printf '  ok       %s\n' "$1"; }
fail() { printf '  MISSING  %s\n           fix: %s\n' "$1" "$2"; bad=1; }
warn() { printf '  note     %s\n' "$1"; }

echo "Tools"
command -v uv >/dev/null 2>&1     && ok "uv"     || fail "uv" "install from https://docs.astral.sh/uv/"
if command -v node >/dev/null 2>&1; then
  major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  if [ "$major" -ge 22 ]; then ok "node $(node -v)"; else fail "node $(node -v) is older than 22" "install Node 22 or newer"; fi
else fail "node" "install Node 22 or newer"; fi
command -v npm >/dev/null 2>&1    && ok "npm"    || fail "npm" "comes with Node"
command -v curl >/dev/null 2>&1   && ok "curl"   || fail "curl" "install curl"
command -v claude >/dev/null 2>&1 && ok "claude CLI" || fail "claude CLI" "install Claude Code, then run: claude (and sign in)"
command -v sbx >/dev/null 2>&1    && ok "sbx (Docker Sandboxes)" || fail "sbx" "install Docker Sandboxes and sign in"
command -v docker >/dev/null 2>&1 && ok "docker (optional: Postgres and Observability from the Services page)" \
                                  || warn "docker not found; fine if you run Postgres yourself and skip Observability"

echo "Database"
host="localhost"; port="5432"
rest="${DATABASE_URL#*://}"; rest="${rest#*@}"; hostport="${rest%%/*}"
host="${hostport%%:*}"; [ "$hostport" != "${hostport#*:}" ] && port="${hostport#*:}"
if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
  ok "Postgres is accepting connections at $host:$port"
else
  fail "Postgres at $host:$port" "start it (for example: docker run -d --name bayto-pg -p 5432:5432 -e POSTGRES_USER=bayto -e POSTGRES_PASSWORD=bayto -e POSTGRES_DB=bayto postgres:16) or start it from the Services page"
fi

echo "Ports"
for p in 8000 5173; do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$p/"; then warn "port $p is already in use (an old Bayto still running?)"; else ok "port $p is free"; fi
done

echo "Environment"
case "$REPO" in
  *.nosync*) ;;
  "$HOME"/Desktop/*|"$HOME"/Documents/*|*"Mobile Documents"*)
    if [ "$(uname)" = "Darwin" ]; then
      warn "this repository is in a folder iCloud syncs; it can swap files for unreadable placeholders (git, .venv, node_modules). Move it: mv '$REPO' ~/dev/bayto"
    fi ;;
esac
if [ "$(uname)" = "Darwin" ]; then
  n="$(find "$REPO" -path '*/node_modules' -prune -o -path '*/.git' -prune -o -flags +dataless -print 2>/dev/null | head -5 | wc -l | tr -d ' ')"
  [ "$n" != 0 ] && warn "$n+ files here are iCloud placeholders (not downloaded); open them or move the repo out of iCloud"
fi
[ -n "${ANTHROPIC_API_KEY:-}" ] && warn "ANTHROPIC_API_KEY is set; Bayto runs on your Claude subscription and ignores it"
[ -d "$REPO/orchestrator" ] && [ -d "$REPO/web" ] && ok "repository layout" || fail "repository layout" "run this from a full checkout of the repository"

echo
if [ "$bad" = 0 ]; then echo "Ready. Start Bayto with: scripts/bayto-up.sh"; else echo "Fix the MISSING items above, then run this again."; fi
exit "$bad"
