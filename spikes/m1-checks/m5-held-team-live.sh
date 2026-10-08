#!/usr/bin/env bash
# Live check of start_team / restart_team / close (docs/plan-held-team.md). Usage: m5-held-team-live.sh bayto-dev
# The sandbox must be running with no team started. Takes about 3 x the team start time (a few minutes).
# Runs in the orchestrator project's own environment; PYTHONPATH is a second guard so `orchestrator` and
# `acp` import even if uv resolves a different environment (a shell with another venv active did).
set -euo pipefail
name="${1:?sandbox name, e.g. bayto-dev}"
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root/orchestrator"
unset VIRTUAL_ENV
export PYTHONPATH="$root/orchestrator/src:$root/agent-comms/src${PYTHONPATH:+:$PYTHONPATH}"
exec uv run --project "$root/orchestrator" --all-extras python "$here/m5-held-team-live.py" "$name"
