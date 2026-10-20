"""PipelineFloorPolicy (Build mode, docs/build-mode-design.md B3).

Who speaks next is decided by what the last speaker reported, not by a rotation or a vote. Every
Build turn ends with one line, `VERDICT: <word>`; the mode's `route` table maps
`"<role>.report.<verdict>"` to the next role (or `done`). Pure like the other policies: everything
it needs is on the ConversationView.

Safety valves, all of which stop the pipeline with `AskHuman` (a pause that keeps the work, never
a failure): a verdict the route table does not know (for example `blocked`) that no person has answered yet (once they do, the same role goes again), a role that has used
up its turns (`max_fix_cycles` extra turns for any role), a next role that is not seated, and a
speaker who keeps omitting the verdict line.
"""
from __future__ import annotations

import re

from acp.floor import HandRaise

from .types import AskHuman, ConversationView, Done, FloorDecision, Grant

_VERDICT_RE = re.compile(r"^\s*\**VERDICT\**\s*:\s*\**\s*([A-Za-z][A-Za-z_-]*)\s*\**\s*$", re.IGNORECASE | re.MULTILINE)
_EXIT_RE = re.compile(r"\bexit(?:ed)?(?:\s+(?:code|status))?\s*(?:with\s+)?(?:code\s+)?[:=]?\s*(-?\d+)", re.IGNORECASE)
_COMMAND_RE = re.compile(r"```|`[^`\n]+`|^\s*\$ \S", re.MULTILINE)
MAX_MISSING_VERDICT_RETRIES = 2  # consecutive turns by the same role before we stop and ask


def parse_verdict(text: str | None) -> str | None:
    """The last `VERDICT: word` line in a turn, lowercased with `_` normalised to `-`; None if absent."""
    matches = _VERDICT_RE.findall(text or "")
    return matches[-1].lower().replace("_", "-") if matches else None


def evidence_ok(text: str | None) -> bool:
    """A passing report shows its work: at least one command (code fence, backticks or a `$ ` line) and at
    least one exit code, all of them 0. A deterministic check, not a judgement; a miss costs one retry."""
    text = text or ""
    codes = [int(c) for c in _EXIT_RE.findall(text)]
    return bool(codes) and all(c == 0 for c in codes) and bool(_COMMAND_RE.search(text))


class PipelineFloorPolicy:
    def __init__(self, route: dict[str, str], *, max_fix_cycles: int = 3,
                 require_evidence: set[str] | frozenset[str] = frozenset()) -> None:
        if "start" not in route:
            raise ValueError("a pipeline route table needs a 'start' entry")
        self._route = route
        self._max_turns = max_fix_cycles + 1
        self._require_evidence = set(require_evidence)  # route keys whose report must show command + exit code 0

    def next(self, view: ConversationView, raised: list[HandRaise]) -> FloorDecision | None:
        if view.last_speaker is None:
            return self._go(view, self._route["start"], "start")

        if view.last_verdict is None:
            if view.consecutive_same_speaker < MAX_MISSING_VERDICT_RETRIES:
                return Grant(participant=view.last_speaker, reason="report-verdict")
            return AskHuman(reason=f"{view.last_speaker} finished {view.consecutive_same_speaker} turns in a row "
                                   "without a VERDICT line")

        key = f"{view.last_speaker}.report.{view.last_verdict}"
        if key in self._require_evidence and not view.last_evidence_ok:
            if view.consecutive_same_speaker < MAX_MISSING_VERDICT_RETRIES:
                return Grant(participant=view.last_speaker, reason="report-evidence")
            return AskHuman(reason=f"{view.last_speaker} reported '{view.last_verdict}' twice without showing the "
                                   "command it ran and an exit code of 0")
        target = self._route.get(key)
        if target is None and view.human_replied_since_last_turn:
            # The speaker is stuck (for example 'blocked') and a person has answered: give the same role another
            # turn, which carries the person's note. Still bounded by the per-role turn cap.
            return self._go(view, view.last_speaker, f"{key}+human")
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
