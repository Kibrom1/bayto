"""PipelineFloorPolicy (Build mode, docs/build-mode-design.md B3).

Who speaks next is decided by what the last speaker reported, not by a rotation or a vote. Every
Build turn ends with one line, `VERDICT: <word>`; the mode's `route` table maps
`"<role>.report.<verdict>"` to the next role (or `done`). Pure like the other policies: everything
it needs is on the ConversationView.

Safety valves, all of which stop the pipeline with `AskHuman` (a pause that keeps the work, never
a failure): a verdict the route table does not know (for example `blocked`), a role that has used
up its turns (`max_fix_cycles` extra turns for any role), a next role that is not seated, and a
speaker who keeps omitting the verdict line.
"""
from __future__ import annotations

import re

from acp.floor import HandRaise

from .types import AskHuman, ConversationView, Done, FloorDecision, Grant

_VERDICT_RE = re.compile(r"^\s*\**VERDICT\**\s*:\s*\**\s*([A-Za-z][A-Za-z_-]*)\s*\**\s*$", re.IGNORECASE | re.MULTILINE)
MAX_MISSING_VERDICT_RETRIES = 2  # consecutive turns by the same role before we stop and ask


def parse_verdict(text: str | None) -> str | None:
    """The last `VERDICT: word` line in a turn, lowercased with `_` normalised to `-`; None if absent."""
    matches = _VERDICT_RE.findall(text or "")
    return matches[-1].lower().replace("_", "-") if matches else None


class PipelineFloorPolicy:
    def __init__(self, route: dict[str, str], *, max_fix_cycles: int = 3) -> None:
        if "start" not in route:
            raise ValueError("a pipeline route table needs a 'start' entry")
        self._route = route
        self._max_turns = max_fix_cycles + 1

    def next(self, view: ConversationView, raised: list[HandRaise]) -> FloorDecision | None:
        if view.last_speaker is None:
            return self._go(view, self._route["start"], "start")

        if view.last_verdict is None:
            if view.consecutive_same_speaker < MAX_MISSING_VERDICT_RETRIES:
                return Grant(participant=view.last_speaker, reason="report-verdict")
            return AskHuman(reason=f"{view.last_speaker} finished {view.consecutive_same_speaker} turns in a row "
                                   "without a VERDICT line")

        key = f"{view.last_speaker}.report.{view.last_verdict}"
        target = self._route.get(key)
        if target is None:
            return AskHuman(reason=f"{view.last_speaker} reported '{view.last_verdict}', which this pipeline "
                                   "does not route; a person needs to decide the next step")
        if target == "done":
            return Done(reason="build-complete")
        return self._go(view, target, key)

    def _go(self, view: ConversationView, target: str, via: str) -> FloorDecision:
        if target not in view.seat_order or target in view.muted:
            return AskHuman(reason=f"the pipeline needs '{target}' (after {via}) but that seat is missing or muted")
        used = view.turns_by_role.get(target, 0)
        if used >= self._max_turns:
            return AskHuman(reason=f"{target} has already used {used} turns; fix cycles are exhausted")
        return Grant(participant=target, reason=via)
