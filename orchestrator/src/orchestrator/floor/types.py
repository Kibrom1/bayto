"""FloorPolicy control-flow types (M2.6): orchestrator-only, never put on the wire as-is.

Split follows the M2.4 sandbox/ precedent: wire-representable protocol shapes (HandRaise)
live in agent-comms; these types don't, because a `Grant` is never itself an Envelope --
it "becomes an assignment message plus one wake-up" (the not-yet-built M2.7+ turn-runner
translates it via OrchestratorConversation.send()).

`FloorPolicy.next()` returns `FloorDecision | None`, where None means "quiet round, keep
going" and `Converged` means the K-quiet-rounds threshold actually fired -- these are
different things (the protocol doc's "or nobody" phrasing covers the None case). This is
the architect's reading of one doc comment, not schema-verified.

`Parallel` and `AskHuman` are included as real variant types now even though neither M2.6
policy (RoundRobin, RaiseHand) produces them -- the union needs all four members to be
stable for later policies.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from acp.floor import HandRaise
from pydantic import BaseModel, Field


class ConversationView(BaseModel):
    session_id: uuid.UUID
    round: int
    seat_order: list[str]  # participant ids, fixed order; empty if the mode doesn't use seats
    muted: set[str] = Field(default_factory=set)
    last_speaker: str | None = None
    consecutive_quiet_rounds: int = 0  # computed, not policy-mutable state
    grants_this_window: dict[str, int] = Field(default_factory=dict)  # for the speaking-time cap
    queue_jumped_this_round: bool = False
    # Not in the architect's original field list (session_id..queue_jumped_this_round) --
    # added because RaiseHandFloorPolicy's Haiku scorer needs "a bounded recent-transcript
    # window from ConversationView" (its score() signature takes only view + personas, no
    # separate transcript param). Pre-bounded by whoever builds the view (M2.7+'s
    # turn-runner); FloorPolicy.next() itself never reads this field, only the scorer does.
    recent_transcript: list[str] = Field(default_factory=list)


class Grant(BaseModel):
    participant: str
    reason: str | None = None


class Parallel(BaseModel):
    participants: list[str]


class Converged(BaseModel):
    rounds_quiet: int


class AskHuman(BaseModel):
    reason: str


FloorDecision = Grant | Parallel | Converged | AskHuman


class FloorPolicy(Protocol):
    def next(self, view: ConversationView, raised: list[HandRaise]) -> FloorDecision | None: ...
