"""RaiseHandFloorPolicy (M2.6/M2.7): pure sync function, same as round-robin. Collecting
hand-raises (the Haiku call in scorer.py, merged with any self-emitted hand-raise
messages) happens BEFORE next() is called, by whoever runs the turn loop (M2.7's
ModeratorRunner) -- so the scorer and the policy are two separate, independently-testable
components, and no async touches FloorPolicy at all.

StopRulesConfig (M2.7) merges M2.6's original RaiseHandTuning with Mode.stop_rules_json:
they were the same configuration surface (a mode's stop/tuning knobs) described by two
would-be parsers for one JSON/YAML blob, so this is the single parsed type for both --
see docs/decisions.md, 2026-09-29. Its five numeric fields are product-owner-confirmed
acceptance-criteria values -- do not change the defaults without going back through
product-owner. `urgency_boost_reasons` (which Reason values get the urgency multiplier) is
deliberately NOT a config field: only NEW_POINT and DISAGREE are eligible reasons in the
current four-value Reason enum (ADDRESSED short-circuits before ranking, AGREE_PASS is
filtered out before ranking), so making this configurable would let a mode silently boost
ADDRESSED/AGREE_PASS in ways the "addressed-first"/"AGREE_PASS never wins" rules above them
don't account for. `_BOOST_REASONS` stays an internal constant.
"""
from __future__ import annotations

from acp.floor import HandRaise, Reason
from acp.modes import ModeConfig
from pydantic import BaseModel

from .types import ConversationView, Converged, FloorDecision, Grant

_BOOST_REASONS = frozenset({Reason.NEW_POINT, Reason.DISAGREE})


class StopRulesConfig(BaseModel):
    max_rounds: int | None = None
    converge_after_quiet_rounds: int = 2
    stale_argument_turns: int = 3
    urgency_boost_multiplier: float = 1.3
    max_consecutive_grants: int = 2

    @classmethod
    def from_mode(cls, mode: ModeConfig) -> "StopRulesConfig":
        """`stop_rules` is a raw dict on ModeConfig (agent-comms doesn't depend on
        orchestrator, so it can't produce this typed config itself); missing keys fall
        back to the product-owner-confirmed defaults above."""
        return cls(**(mode.stop_rules or {}))


class RaiseHandFloorPolicy:
    """The "one queue-jumping objection per round" rule has no precise spec in
    docs/agent-communication-protocol.md; the interpretation here -- a jump is when the
    urgency boost changes who wins vs. plain urgency, capped at one per round via
    `view.queue_jumped_this_round` -- is the architect's best-effort reading, confirmed by
    product-owner as fine to ship and revisit post-usage. Do not over-invest in alternate
    interpretations."""

    def __init__(self, tuning: StopRulesConfig = StopRulesConfig()) -> None:
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
        boost = self._tuning.urgency_boost_multiplier if h.reason in _BOOST_REASONS else 1.0
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
