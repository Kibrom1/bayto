"""Choose how the moderator talks to a model: the Anthropic API or the Claude subscription CLI."""
from __future__ import annotations

import os

from .claude_cli import ClaudeCliClient

BACKEND_ENV_VAR = "BAYTO_LLM_BACKEND"  # "api" | "cli" | unset (auto)


def backend_name() -> str:
    """Explicit setting wins; otherwise use the API when a key is present, else the subscription CLI."""
    v = (os.environ.get(BACKEND_ENV_VAR) or "").strip().lower()
    if v in ("api", "cli"):
        return v
    return "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"


def make_client():
    """A client for `messages.create`, or None to let the class build its own AsyncAnthropic."""
    return ClaudeCliClient() if backend_name() == "cli" else None
