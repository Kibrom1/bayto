"""HOST: M1.16 live smoke test of orchestrator's AnthropicHandRaiseScorer against the real API.

Usage (from the repo root, needs a real ANTHROPIC_API_KEY; spends 3 short Haiku calls):
    cd orchestrator && uv run --extra dev python ../spikes/m1-checks/m1-16-scorer-smoke.py
Env: BAYTO_HAND_RAISE_MODEL (default claude-haiku-4-5), M116_PRICE_IN / M116_PRICE_OUT in
USD per million tokens (default 1.00 / 5.00, Haiku 4.5 list price; check before trusting).

Three fixed scenarios, each one real scorer call, judged with loose expectations:
  addressed  -- the Architect asks QA a direct question: qa-tester raises with reason=addressed
  disagree   -- a factual claim the Architect is briefed to contest: someone raises disagree/new_point
  converged  -- everyone has agreed: no raise other than agree_pass
Writes results/m1-16-<timestamp>.md (git-ignored) with per-call latency, tokens and cost.
Exit status 0 only if every call parses and every expectation holds. It never prints the key.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import time
import uuid
from pathlib import Path

from anthropic import AsyncAnthropic

from acp.floor import HandRaise, Reason
from orchestrator.floor.scorer import AnthropicHandRaiseScorer, PersonaBrief, ScorerError
from orchestrator.floor.types import ConversationView

RESULTS = Path(__file__).resolve().parent / "results"
MODEL = os.environ.get("BAYTO_HAND_RAISE_MODEL", "claude-haiku-4-5")
PRICE_IN = float(os.environ.get("M116_PRICE_IN", "1.00"))
PRICE_OUT = float(os.environ.get("M116_PRICE_OUT", "5.00"))

PERSONAS = [
    PersonaBrief(participant="architect", role="Architect", stance="pro-Postgres",
                 brief="Owns the system design. Argues the orchestrator should keep Postgres, not SQLite."),
    PersonaBrief(participant="backend-engineer", role="Backend Engineer", stance=None,
                 brief="Implements the orchestrator service in Python/FastAPI."),
    PersonaBrief(participant="qa-tester", role="QA/Tester", stance=None,
                 brief="Owns the test plan and the conformance suite."),
]
IDS = {p.participant for p in PERSONAS}


def _addressed(raises: list[HandRaise]) -> str | None:
    if any(h.participant == "qa-tester" and h.reason == Reason.ADDRESSED for h in raises):
        return None
    return "expected qa-tester to raise with reason=addressed"


def _disagree(raises: list[HandRaise]) -> str | None:
    if any(h.reason in (Reason.DISAGREE, Reason.NEW_POINT) for h in raises):
        return None
    return "expected at least one disagree/new_point raise"


def _converged(raises: list[HandRaise]) -> str | None:
    loud = [h.participant for h in raises if h.reason != Reason.AGREE_PASS]
    return None if not loud else f"expected only agree_pass, got raises from {loud}"


SCENARIOS = [
    ("addressed", [
        "architect: The floor loop now grants one speaker per round.",
        "architect: @qa-tester, does the conformance suite already cover crash-before-consume redelivery?",
    ], _addressed),
    ("disagree", [
        "backend-engineer: I propose we drop Postgres and keep all orchestrator state in SQLite files.",
        "backend-engineer: Concurrency will not matter at MVP scale.",
    ], _disagree),
    ("converged", [
        "architect: Final proposal: keep Postgres, one sandbox per task.",
        "backend-engineer: Agreed, nothing to add.",
        "qa-tester: Agreed, the test plan already covers it. Nothing further from me.",
    ], _converged),
]


class _UsageTap:
    """Wraps the real AsyncAnthropic client just to read usage off each response."""

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client, self.last_usage = client, None
        self.messages = self

    async def create(self, **kw):
        resp = await self._client.messages.create(**kw)
        self.last_usage = getattr(resp, "usage", None)
        return resp


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set: M1.16 needs a real API key (not the sbx proxy stand-in).", file=sys.stderr)
        return 2
    tap = _UsageTap(AsyncAnthropic())
    scorer = AnthropicHandRaiseScorer(model=MODEL, client=tap)  # type: ignore[arg-type]
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"m1-16-{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    lines = [f"# m1-16", "", f"Run: {dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}, model: {MODEL}, "
             f"price: ${PRICE_IN}/${PRICE_OUT} per MTok in/out", "",
             "| scenario | verdict | latency ms | in tok | out tok | cost USD | raises |", "|---|---|---|---|---|---|---|"]
    fails = 0
    total_cost = 0.0
    for name, transcript, check in SCENARIOS:
        view = ConversationView(session_id=uuid.uuid4(), round=1, seat_order=[p.participant for p in PERSONAS],
                                recent_transcript=transcript)
        t0 = time.perf_counter()
        try:
            raises = await scorer.score(view, PERSONAS)
            problem = check(raises)
            unknown = sorted({h.participant for h in raises} - IDS)
            if unknown:
                problem = f"unknown participants {unknown}" + (f"; {problem}" if problem else "")
        except (ScorerError, ValueError) as e:  # pydantic ValidationError is a ValueError
            raises, problem = [], f"{type(e).__name__}: {e}"
        ms = round((time.perf_counter() - t0) * 1000)
        u = tap.last_usage
        tin, tout = (u.input_tokens, u.output_tokens) if u else (0, 0)
        cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
        total_cost += cost
        fails += problem is not None
        shown = "; ".join(f"{h.participant}:{h.reason.value}@{h.urgency:.2f}" for h in raises) or "(none)"
        lines.append(f"| {name} | {'PASS' if problem is None else 'FAIL: ' + problem} | {ms} | {tin} | {tout} | {cost:.5f} | {shown} |")
    lines += ["", f"Summary: {len(SCENARIOS) - fails} pass, {fails} fail, total cost ${total_cost:.5f}. "
              "Paste the findings into docs/work-plan.md under M1.16."]
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nWrote {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
