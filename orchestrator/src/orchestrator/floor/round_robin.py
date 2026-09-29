"""RoundRobinFloorPolicy (M2.6): pure, no persisted cursor -- computed fresh from
seat_order + last speaker every call. A stored cursor could drift from Turn history on
replay/fork; recomputing from the view can't.
"""
from __future__ import annotations

from acp.floor import HandRaise

from .types import ConversationView, FloorDecision, Grant


def next_seat(seat_order: list[str], last_speaker: str | None, muted: set[str]) -> str:
    eligible = [p for p in seat_order if p not in muted]
    if not eligible:
        raise ValueError("no eligible seats")
    if last_speaker not in eligible:
        return eligible[0]
    i = eligible.index(last_speaker)
    return eligible[(i + 1) % len(eligible)]


class RoundRobinFloorPolicy:
    """seat_order is expected pre-sorted/non-null for every participant in a round-robin
    session (from SessionAgent.seat_order) -- a data-integrity expectation on whoever
    builds ConversationView, not re-validated here. `raised` is accepted for FloorPolicy
    interface parity but unused: round-robin's order is authoritative regardless of
    hand-raises. Round-robin's own stop condition (e.g. a debate mode's rotation ending) is
    Mode.stop_rules_json evaluation, a separate not-yet-designed mechanism -- out of scope
    here; do not invent round-robin convergence logic in this policy."""

    def next(self, view: ConversationView, raised: list[HandRaise]) -> FloorDecision | None:
        return Grant(participant=next_seat(view.seat_order, view.last_speaker, view.muted))
