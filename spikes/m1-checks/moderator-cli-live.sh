#!/usr/bin/env bash
# Live check: the moderator's summary + synthesis work through your Claude login, with no API key.
# Run from the repo root on the machine where you are signed in to `claude`.
set -euo pipefail
cd "$(dirname "$0")/../../orchestrator"
unset ANTHROPIC_API_KEY
PYTHONPATH=src:../agent-comms/src uv run --all-extras python - <<'PY'
import asyncio, uuid
from orchestrator.llm import ClaudeCliClient
from orchestrator.moderator.summarizer import AnthropicSummarizer
from orchestrator.moderator.turn_summary import TurnSummary

async def main():
    s = AnthropicSummarizer(model="claude-haiku-4-5", client=ClaudeCliClient())
    r = await s.summarize(uuid.uuid4(), None, [
        TurnSummary(round=1, speaker="PM", content="Ship the board first; setup can wait."),
        TurnSummary(round=1, speaker="Customer", content="Without setup I cannot start anything."),
    ])
    assert r.summary.strip(), "empty summary"
    print("PASS summary via subscription:", r.summary[:120].replace("\n", " "), "| new_argument =", r.has_new_argument)

asyncio.run(main())
PY
