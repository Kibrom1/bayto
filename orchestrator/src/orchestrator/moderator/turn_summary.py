"""TurnSummary: a minimal, moderator-owned projection of a Turn row (M2.7) -- not the full
ORM object, so the summarizer/synthesizer never hold a session-attached row (same
reasoning as M2.4's SandboxInfo being a plain dataclass rather than the SQLAlchemy Sandbox
row)."""
from __future__ import annotations

from pydantic import BaseModel


class TurnSummary(BaseModel):
    speaker: str
    content: str
    round: int
