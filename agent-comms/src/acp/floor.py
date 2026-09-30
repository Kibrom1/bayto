"""HandRaise: the FloorPolicy signal a participant sends to request the floor (M2.6).

Implements agent-comms/schema/hand-raise.v1.json -- the schema already committed to this
being the v1 wire shape (docs/agent-communication-protocol.md, 'Floor control'). Lives here,
not in orchestrator/floor/, because the doc is explicit a self-driving agent can emit a
hand-raise as a message itself ("both paths produce the same record"), so HandRaise has to
be encodable as an Envelope body -- same category as Envelope/Roster/SendPolicy.

Sent as an ordinary Envelope with kind="hand-raise": `models.Kind` already has a `hand_raise
= "hand-raise"` member (closed-vocabulary core kind, not an `x-*` extension), so no change
to `Kind` was needed to encode this.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "hand-raise.v1.json"


class Reason(str, Enum):
    ADDRESSED = "addressed"
    NEW_POINT = "new_point"
    DISAGREE = "disagree"
    AGREE_PASS = "agree_pass"


class HandRaise(BaseModel):
    participant: str
    reason: Reason
    urgency: float = Field(ge=0.0, le=1.0)
    in_reply_to: str | None = None


def hand_raise_json_schema() -> dict:
    """The single source of truth for both the wire shape and (via
    orchestrator's AnthropicHandRaiseScorer) the LLM's structured-output contract --
    nothing duplicated by hand. Reads schema/hand-raise.v1.json relative to this package's
    own location, which works for the editable dev install used everywhere in this project
    today (schema/ sits next to src/ in the same repo checkout); revisit if a built-wheel
    deployment that doesn't ship schema/ is ever used instead."""
    return json.loads(_SCHEMA_PATH.read_text())
