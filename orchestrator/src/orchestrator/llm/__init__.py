"""LLM backends for the moderator's side calls (summary, synthesis, raise-hand scoring)."""
from .backend import make_client
from .claude_cli import ClaudeCliClient, ClaudeCliError

__all__ = ["ClaudeCliClient", "ClaudeCliError", "make_client"]
