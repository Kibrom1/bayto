"""RaiseHandFloorPolicy (M2.6): pure sync function, same as round-robin. Collecting
hand-raises (the Haiku call in scorer.py, merged with any self-emitted hand-raise
messages) happens BEFORE next() is called, by whoever runs the turn loop (M2.7+'s
turn-runner, not built here) -- so the scorer and the policy are two separate,
independently-testable components, and no async touches FloorPolicy at all.

RaiseHandTuning's three numeric defaults are product-owner-confirmed acceptance-criteria
values (2026-09-29) -- do not change them without going back through product-owner.
"""
from __future__ import annotations

from acp.floor import HandRaise, Reason
from pydantic import BaseModel

from .types import ConversationView, Converged, FloorDecision, Grant


class RaiseHandTuning(BaseModel):
    urgency_boost_reasons: set[Reason] = {Reason.NEW_POINT, Reason.DISAGREE}
    urgency_boost_multiplier: float = 1.3
    max_consecutive_grants: int = 2
    converge_after_quiet_rounds: int = 2


class RaiseHandFloorPolicy:
    """The "one queue-jumping objection per round" rule has no precise spec in
    docs/agent-communication-protocol.md; the interpretation here -- a jump is when the
    urgency boost changes who wins vs. plain urgency, capped at one per round via
    `view.queue_jumped_this_round` -- is the architect's best-effort reading, confirmed by
    product-owner as fine to ship and revisit post-usage. Do not over-invest in alternate
    interpretations."""

    def __init__(self, tuning: RaiseHandTuning = RaiseHandTuning()) -> None:
        self._tuning = tuning

    def next(self, view: ConversationView, raised: list[HandRaise]) -> FloorDecision | None:
        addressed = [h for h in raised if h.reason == Reason.ADDRESSED]
        if addressed:
            return self._grant_under_cap(view, addressed)

        candidates = [h for h in raised if h.reason != Reason.AGREE_PASS]  # never gets the floor
        if not candidates:
            quiet = view.consecutive_quiet_rounds + 1
            return Converged(rounds_quiet=quiet) if quiet >= self._tuning.converge_after_quiet_rounds else None

        boosted_ranked = sorted(candidates, key=self._effective_urgency, reverse=True)
        ordered = self._apply_queue_jump_cap(view, candidates, boosted_ranked)
        return self._grant_under_cap(view, ordered)

    def _effective_urgency(self, h: HandRaise) -> float:
        boost = self._tuning.urgency_boost_multiplier if h.reason in self._tuning.urgency_boost_reasons else 1.0
        return h.urgency * boost

    def _apply_queue_jump_cap(self, view: ConversationView, candidates: list[HandRaise],
                               boosted_ranked: list[HandRaise]) -> list[HandRaise]:
        """If the boost is what put the top pick ahead of the plain-urgency leader, that's
        a queue-jump; only one such jump allowed per round (view.queue_jumped_this_round),
        otherwise fall back to the unboosted ranking this round."""
        plain_ranked = sorted(candidates, key=lambda h: h.urgency, reverse=True)
        is_jump = boosted_ranked[0].participant != plain_ranked[0].participant
        if is_jump and view.queue_jumped_this_round:
            return plain_ranked
        return boosted_ranked

    def _grant_under_cap(self, view: ConversationView, ranked: list[HandRaise]) -> Grant | None:
        """Walks `ranked` in priority order, skipping any participant at/over
        max_consecutive_grants; returns None if everyone eligible is capped this round."""
        for h in ranked:
            if view.grants_this_window.get(h.participant, 0) < self._tuning.max_consecutive_grants:
                return Grant(participant=h.participant, reason=h.reason.value)
        return None
