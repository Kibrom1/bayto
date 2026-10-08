#!/usr/bin/env bash
# Live check of start_team / restart_team / close (docs/plan-held-team.md). Usage: m5-held-team-live.sh bayto-dev
# The sandbox must be running with no team started. Takes about 3 x the team start time (a few minutes).
set -euo pipefail
name="${1:?sandbox name, e.g. bayto-dev}"
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../../orchestrator"
exec uv run python "$here/m5-held-team-live.py" "$name"
