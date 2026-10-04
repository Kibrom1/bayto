"""Floor-loop driver (M1.10, spike): sbx env create -> start the team -> loop { collect
raise-hand signals, grant the floor, send an assignment + crew-notify, tail messages }
until convergence -> moderator summary -> sbx env rm.

Deliberately NOT orchestrator/floor's RaiseHandFloorPolicy: that policy's extra rules
(queue-jump cap, max_consecutive_grants, ADDRESSED short-circuit, ModeConfig/
StopRulesConfig) are real M2 machinery this pre-M2 spike doesn't need -- and, more to the
point, importing anything from orchestrator/ would drag its full
FastAPI+SQLAlchemy+Alembic+Postgres dependency chain into a spike meant to stay
lightweight (see docs/decisions.md, 2026-10-04). The convergence rule here is exactly what
the architect's scope calls for: grant the floor each iteration to the single
highest-urgency non-AGREE_PASS hand-raise; converge once K consecutive iterations raise
nothing but AGREE_PASS (or nothing at all).

agent-comms' real Conversation/FileTransport is used directly here, not faked: it's
already lightweight local file I/O (no credential, no network), so there is nothing
meaningful to fake it against -- same reasoning as M2.5's own tests. Only the sbx CLI and
the Anthropic-backed scorer/summarizer get a seam + Fake, since those are the two pieces
this sandbox genuinely cannot exercise live (see FINDINGS.md for the compound gap).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from acp.core import Conversation
from acp.floor import HandRaise, Reason

from .scorer import ConversationView, HandRaiseScorer, PersonaBrief
from .sbx import SbxLifecycle
from .summarizer import ModeratorSummary, Summarizer, format_summary


@dataclass(frozen=True, slots=True)
class RunResult:
    iterations: int
    converged: bool
    grants: list[str] = field(default_factory=list)  # participant granted the floor, in order
    summary: ModeratorSummary | None = None


def next_grant(raised: list[HandRaise]) -> HandRaise | None:
    """Pure: the single highest-urgency candidate that isn't AGREE_PASS, or None if every
    raise this iteration is AGREE_PASS (or there were none). No queue-jump boost, no
    per-participant grant cap -- see module docstring for why this is simpler than M2's
    RaiseHandFloorPolicy."""
    candidates = [h for h in raised if h.reason != Reason.AGREE_PASS]
    if not candidates:
        return None
    return max(candidates, key=lambda h: h.urgency)


class FloorLoopDriver:
    def __init__(self, *, sbx: SbxLifecycle, scorer: HandRaiseScorer, summarizer: Summarizer,
                 conversation: Conversation, moderator: str = "moderator",
                 converge_after_quiet_iterations: int = 2, max_iterations: int = 20) -> None:
        self._sbx = sbx
        self._scorer = scorer
        self._summarizer = summarizer
        self._conversation = conversation
        self._moderator = moderator
        self._converge_after = converge_after_quiet_iterations
        self._max_iterations = max_iterations

    def _transcript_lines(self) -> list[str]:
        return [f"{e.from_}: {e.body}" for e in self._conversation.transcript(self._moderator)]

    async def run(self, *, sandbox_name: str, personas: list[PersonaBrief]) -> RunResult:
        await asyncio.to_thread(self._sbx.create, sandbox_name)
        await asyncio.to_thread(self._sbx.start_team, sandbox_name)

        grants: list[str] = []
        quiet_streak = 0
        round_n = 0
        converged = False

        try:
            while round_n < self._max_iterations:
                round_n += 1
                view = ConversationView(round=round_n, recent_transcript=self._transcript_lines())
                raised = await self._scorer.score(view, personas)

                grant = next_grant(raised)
                if grant is None:
                    quiet_streak += 1
                    if quiet_streak >= self._converge_after:
                        converged = True
                        break
                    continue

                quiet_streak = 0
                self._conversation.send(
                    self._moderator, [grant.participant], "assignment",
                    body=f"You have the floor (reason: {grant.reason.value}, urgency: {grant.urgency:.2f}).",
                )
                await asyncio.to_thread(self._sbx.crew_notify, sandbox_name, grant.participant)
                grants.append(grant.participant)

            summary = await self._summarizer.summarize(self._transcript_lines())
            self._conversation.send(self._moderator, ["human"], "summary", body=format_summary(summary))
            return RunResult(iterations=round_n, converged=converged, grants=grants, summary=summary)
        finally:
            await asyncio.to_thread(self._sbx.remove, sandbox_name)
