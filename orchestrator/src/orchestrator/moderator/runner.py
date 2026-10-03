"""ModeratorRunner (M2.7): the turn-runner loop. One instance per active Session, same
run_once()/run() split as MessageMirror's poll_once()/run_forever() (the third module to
use this house pattern, after mirror.py and sandbox/local.py's methods) -- run_once() is
one full loop iteration, fully unit-testable; run() is the thin, uncovered wrapper looping
run_once() until the session ends or a stop_event fires.

An explicit state machine, not polling: after granting the floor and waking a participant,
the runner awaits a pubsub subscription (assumed already scoped to this session -- see
`_await_response`'s docstring) rather than re-polling the DB -- push-based downstream of
MessageMirror, which already publishes newly-mirrored rows to pubsub after commit.

Genuinely unverifiable from this dev sandbox: a full end-to-end session needs BOTH the live
`sbx` CLI (SandboxProvider.wake_role) AND live Anthropic credentials (scorer, summarizer,
synthesizer) simultaneously. Every piece here is unit-tested in isolation against fakes/a
real tmp_path FileTransport/a real throwaway Postgres; see docs/decisions.md, 2026-09-30,
for the compound-gap disclosure and the follow-up end-to-end smoke test this flags.

Several judgment calls made in this module (documented at each site, indexed here since
they're easy to miss in a large file): the participant<->Agent.role identity assumption;
`round` semantics differ by floor_policy and are computed at grant time, not from `view`;
`consecutive_quiet_rounds` is in-memory-only runner state, lost on an orchestrator restart
(M2.9 territory); `queue_jumped_this_round` is always False here because this runner
defines "one grant cycle = one round" for raise-hand, so a round never spans multiple
`policy.next()` calls (see docs/decisions.md); a self-emitted hand-raise Envelope's `body`
is the HandRaise model's own `model_dump(mode="json")` (a new, disclosed wire convention,
since the protocol doc's HandRaise sketch never specified how "an agent emits it as a
message itself" actually encodes the structured fields into a message body); `Session.budget`
is assumed to be `{"max_tokens": int, "max_cost": float}` (no canonical schema exists for
this JSONB column yet); Session.status literally reuses the protocol doc's lifecycle
diagram's node names (`active`, `needs_human`, `finished`, `failed`) for the handful of
transitions this runner makes, without building the other states/transitions in that
diagram (queued/warming/ready/blocked_access) -- M2.6's "don't build the 9-state machine"
boundary still holds; this only reuses its vocabulary for the corner the runner touches.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from acp.floor import HandRaise, Reason
from acp.modes import ModeConfig
from opentelemetry import trace
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..conversation import OrchestratorConversation
from ..models import Agent, Artifact
from ..models import Message as MessageRow
from ..models import Session as SessionRow
from ..models import SessionAgent, Turn
from ..pubsub import PubSub
from ..sandbox.provider import SandboxInfo, SandboxProvider
from ..floor import (
    AskHuman,
    ConversationView,
    Converged,
    FloorDecision,
    FloorPolicy,
    Grant,
    HandRaiseScorer,
    Parallel,
    PersonaBrief,
    StopRulesConfig,
)
from .budget import session_usage
from .summarizer import Summarizer
from .synthesizer import Synthesizer
from .turn_summary import TurnSummary

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

STATUS_ACTIVE = "active"
STATUS_NEEDS_HUMAN = "needs_human"
STATUS_FINISHED = "finished"
# M2.8's POST /sessions/{id}/stop writes one of these two (never "needs_human", which
# means something different -- a pause, not a stop request). STATUS_STOPPING is the
# synthesize=true (default) case and falls through to the generic external-status-change
# handling below (which already runs synthesis via _finish); STATUS_CANCELLING is the
# synthesize=false case and needs its own branch to skip synthesis entirely.
STATUS_STOPPING = "stopping"
STATUS_CANCELLING = "cancelling"

# M2.10: Session.stop_reason values, set on every terminal path (product-owner: "this is a
# natural place to record it consistently"). A deliberately separate, underscore-separated
# naming convention from the internal hyphenated `reason` strings used elsewhere in this
# module (RunOnceResult.reason, _check_stop_conditions' return value, log messages) --
# "budget_exceeded" specifically is a literal product-owner acceptance-criterion string,
# and using one consistent style for every value in this NEW field (rather than mixing
# hyphens and underscores within it) was the more defensible call than reusing the
# pre-existing internal spelling verbatim.
_STOP_REASON_VALUES = {
    "max-rounds": "max_rounds",
    "no-new-arguments": "staleness",
    "converged": "converged",
    "external-status-change": "user_stop",
}

_STREAK_LOOKBACK = 50  # bounds the speaking-time-cap query; a real streak can't exceed this in practice


class ModeratorConfig(BaseModel):
    grant_timeout_seconds: float = 300.0
    quiet_sleep_seconds: float = 20.0
    transcript_window_turns: int = 10


@dataclass
class RunOnceResult:
    stopped: bool
    reason: str | None = None
    quiet: bool = False  # True when this iteration made no grant (run() should sleep)


class ModeratorRunner:
    def __init__(
        self,
        *,
        session_id: uuid.UUID,
        sessionmaker: async_sessionmaker,
        conversation: OrchestratorConversation,
        sandbox_provider: SandboxProvider,
        sandbox: SandboxInfo,
        mode: ModeConfig,
        policy: FloorPolicy,
        stop_rules: StopRulesConfig,
        pubsub: PubSub,
        summarizer: Summarizer,
        synthesizer: Synthesizer,
        scorer: HandRaiseScorer | None = None,  # required only when mode.floor_policy == "raise-hand"
        config: ModeratorConfig = ModeratorConfig(),
    ) -> None:
        if mode.floor_policy == "raise-hand" and scorer is None:
            raise ValueError("raise-hand mode needs a HandRaiseScorer")
        self._session_id = session_id
        self._sessionmaker = sessionmaker
        self._conversation = conversation
        self._sandbox_provider = sandbox_provider
        self._sandbox = sandbox
        self._mode = mode
        self._policy = policy
        self._stop_rules = stop_rules
        self._pubsub = pubsub
        self._summarizer = summarizer
        self._synthesizer = synthesizer
        self._scorer = scorer
        self._config = config
        self._opening_done = False
        self._consecutive_quiet_rounds = 0
        self._last_hand_raise_seq = 0
        self._last_interject_seq = 0
        # M2.12: phase machine state
        self._current_phase_index = 0
        self._turns_in_phase = 0

    # ---------------------------------------------------------------- the loop

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Not exercised by tests directly (no live sbx/Anthropic setup needed);
        run_once() is the tested unit, same as MessageMirror.run_forever()."""
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            result = await self.run_once()
            if result.stopped:
                return
            if result.quiet:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self._config.quiet_sleep_seconds)
                except asyncio.TimeoutError:
                    pass

    async def run_once(self) -> RunOnceResult:
        with tracer.start_as_current_span("moderator.run_once",
                                           attributes={"bayto.session_id": str(self._session_id)}) as span:
            view = await self._build_view()
            span.set_attribute("bayto.round", view.round)

            async with self._sessionmaker() as session:
                status = (await session.get(SessionRow, self._session_id)).status
            if status == STATUS_NEEDS_HUMAN:
                # Already paused (by an earlier AskHuman escalation, or a human directly via
                # M3.3's POST /sessions/{id}/pause) -- a pause is not a wrap-up: don't run
                # synthesis, just stop looping until something external moves it back to
                # active. M2.7 did not build a resume path for this; M3.3 adds one
                # (POST /sessions/{id}/resume, api/sessions.py) by relaunching through
                # launch_runner() -- the same relaunch M2.9's restart-reconcile already
                # uses -- rather than changing anything in this loop: a freshly launched
                # runner's first run_once() call already re-detects any sent-but-unanswered
                # assignment via _pending_grant() below before granting anything new, so
                # "resume" needs no special-casing here at all.
                return RunOnceResult(stopped=True, reason=STATUS_NEEDS_HUMAN)
            if status == STATUS_CANCELLING:
                # M2.8's POST /stop with synthesize=false: wind down WITHOUT running the
                # Synthesizer at all -- the one stop trigger that doesn't wrap up via _finish.
                await self._end_without_synthesis("user_stop")
                return RunOnceResult(stopped=True, reason=STATUS_CANCELLING)
            if status != STATUS_ACTIVE:
                # Genuinely external ("user-ends-it"): something already moved this session to
                # a stopping/terminal value this runner didn't itself just set (M2.8's
                # POST /stop with synthesize=true, the default, is the intended source; so is
                # any other future externally-set non-active value). Unlike needs_human, this
                # DOES wrap up.
                await self._finish(view, "external-status-change")
                return RunOnceResult(stopped=True, reason="external-status-change")

            pending_grant = await self._pending_grant()
            if pending_grant is not None:
                # M2.9: an assignment was durably sent (a Message row) but never got a
                # response -- the process may have died between send() succeeding and either
                # wake_role() being called or the response landing. Checked UNCONDITIONALLY
                # here (not only after a restart) so the same code path covers a real restart
                # and an ordinary transient hiccup mid-loop; the runner never needs to know
                # which case it's in.
                await self._resume_pending_grant(view, pending_grant)
                return RunOnceResult(stopped=False)

            stop_reason = await self._check_stop_conditions(view)
            if stop_reason is not None:
                if stop_reason == "budget-exceeded":
                    # Product-owner-confirmed: unlike every other stop trigger, budget-exceeded
                    # does NOT run final synthesis. /stop's synthesize=true default is a user
                    # opting in, in the moment, to spend a bit more for a wrap-up; budget-exceeded
                    # is the cap itself firing -- if it always overshot by one synthesis call,
                    # the cap wouldn't actually be a cap. The session still gets a minimal,
                    # non-LLM terminal marker (stop_reason) so a bare transcript isn't
                    # uninterpretable, without spending anything extra.
                    await self._end_without_synthesis("budget_exceeded")
                else:
                    await self._finish(view, stop_reason)
                return RunOnceResult(stopped=True, reason=stop_reason)

            if not self._opening_done:
                pending = await self._pending_opening_participant(view)
                if pending is not None:
                    await self._handle_grant(view, Grant(participant=pending, reason="opening"))
                    return RunOnceResult(stopped=False)
                self._opening_done = True
            elif self._mode.floor_policy == "raise-hand":
                # M3.2: a participant added mid-session (after the initial opening pass
                # already completed) still gets one opening-style grant before competing
                # normally -- the opening phase's own purpose (one uninterrupted turn to
                # establish a position) applies identically to someone joining mid-
                # conversation. Round-robin doesn't need this: being inserted into
                # seat_order already guarantees them a turn when the rotation reaches them,
                # so this is raise-hand-only -- see docs/decisions.md.
                late_joiner = await self._pending_opening_participant(view)
                if late_joiner is not None:
                    await self._handle_grant(view, Grant(participant=late_joiner, reason="opening"))
                    return RunOnceResult(stopped=False)

            raised = await self._collect_hand_raises(view) if self._mode.floor_policy == "raise-hand" else []
            decision = self._policy.next(view, raised)
            return await self._dispatch(view, decision)

    async def _dispatch(self, view: ConversationView, decision: FloorDecision | None) -> RunOnceResult:
        if decision is None:
            self._consecutive_quiet_rounds += 1
            return RunOnceResult(stopped=False, quiet=True)
        if isinstance(decision, Grant):
            self._consecutive_quiet_rounds = 0
            await self._handle_grant(view, decision)
            return RunOnceResult(stopped=False)
        if isinstance(decision, Converged):
            await self._finish(view, "converged")
            return RunOnceResult(stopped=True, reason="converged")
        if isinstance(decision, AskHuman):
            await self._escalate_to_human(view, decision.reason)
            return RunOnceResult(stopped=True, reason=STATUS_NEEDS_HUMAN)
        if isinstance(decision, Parallel):
            # No M2.6/M2.7 policy produces this. Kept as an explicit "not supported by any
            # built policy" stub for the FloorDecision union to type-check, per the
            # architect's instruction not to write speculative operational logic for it.
            raise NotImplementedError("Parallel floor decisions aren't produced by any built policy yet")
        raise AssertionError(f"unhandled FloorDecision: {decision!r}")

    # ---------------------------------------------------------------- opening pass

    async def _pending_opening_participant(self, view: ConversationView) -> str | None:
        """Opening is floor-policy-agnostic: a single fixed pass through seat_order, in
        order, one uninterrupted turn each, regardless of turn_policy -- raise-hand mode
        does not get a real scored opening; the scorer only starts once every seat has
        spoken. Computed from Turn history (not an in-memory flag) so it survives an
        orchestrator restart mid-opening -- and, since M3.2, so it also correctly finds a
        participant added mid-session (see run_once()'s late-joiner check, which calls this
        again after self._opening_done for raise-hand mode). Muted/removed participants
        (view.muted) never get an opening grant either -- mute is an absolute eligibility
        gate (M3.2, see docs/decisions.md)."""
        if not view.seat_order:
            return None
        async with self._sessionmaker() as session:
            spoken = set((await session.execute(
                select(Agent.role).join(Turn, Turn.speaker_id == Agent.id)
                .where(Turn.session_id == self._session_id)
            )).scalars())
        for participant in view.seat_order:
            if participant not in spoken and participant not in view.muted:
                return participant
        return None

    # ---------------------------------------------------------------- view building

    async def _build_view(self) -> ConversationView:
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(SessionAgent, Agent.role)
                .join(Agent, Agent.id == SessionAgent.agent_id)
                .where(SessionAgent.session_id == self._session_id)
                .order_by(SessionAgent.seat_order)
            )).all()
            # Data-integrity expectation on whoever populates SessionAgent (same class of
            # assumption as M2.6's seat_order note): seat_order is non-null and Agent.role
            # is the participant identifier used throughout this runner (matching HandRaise
            # .participant, Grant.participant, FloorPolicy's seat_order entries).
            seat_order = [role for _, role in rows]
            # M3.2: a removed participant folds into the same eligibility filter as a muted
            # one -- FloorPolicy code never needs to distinguish WHY someone's ineligible,
            # only display/data layers do (M3.9's problem, not this one's). Still counted
            # in seat_order (round-robin's next_seat()/RaiseHandFloorPolicy.next() both
            # already filter muted out of the eligible set).
            muted = {role for sa, role in rows if sa.muted or sa.removed_at is not None}

            last_turn = (await session.execute(
                select(Turn).where(Turn.session_id == self._session_id).order_by(Turn.seq.desc()).limit(1)
            )).scalar_one_or_none()
            current_round = last_turn.round if last_turn else 0
            last_speaker = None
            if last_turn is not None:
                agent = await session.get(Agent, last_turn.speaker_id)
                last_speaker = agent.role if agent else None

            grants_this_window = await self._grants_this_window(session)
            recent_transcript = await self._recent_transcript_lines(session)

        return ConversationView(
            session_id=self._session_id,
            round=current_round,
            seat_order=seat_order,
            muted=muted,
            last_speaker=last_speaker,
            consecutive_quiet_rounds=self._consecutive_quiet_rounds,
            grants_this_window=grants_this_window,
            # Always False: for raise-hand, this runner defines one grant cycle as one
            # round (see the module docstring), so a round never spans more than one
            # policy.next() call -- the one-jump-per-round cap can never be exhausted
            # within a single call. The mechanism stays correct in floor/raise_hand.py for
            # a future round definition spanning multiple grants.
            queue_jumped_this_round=False,
            recent_transcript=recent_transcript,
        )

    async def _grants_this_window(self, session) -> dict[str, int]:
        """The speaking-time cap only ever needs the trailing streak of consecutive grants
        to the same participant -- not a lifetime or windowed total."""
        speaker_ids = (await session.execute(
            select(Turn.speaker_id).where(Turn.session_id == self._session_id)
            .order_by(Turn.seq.desc()).limit(_STREAK_LOOKBACK)
        )).scalars().all()
        if not speaker_ids:
            return {}
        streak_id = speaker_ids[0]
        streak = 0
        for speaker_id in speaker_ids:
            if speaker_id != streak_id:
                break
            streak += 1
        agent = await session.get(Agent, streak_id)
        return {agent.role: streak} if agent else {}

    async def _recent_transcript_lines(self, session) -> list[str]:
        turns = (await session.execute(
            select(Turn).where(Turn.session_id == self._session_id)
            .order_by(Turn.seq.desc()).limit(self._config.transcript_window_turns)
        )).scalars().all()
        lines = []
        for t in reversed(turns):
            agent = await session.get(Agent, t.speaker_id)
            speaker = agent.role if agent else str(t.speaker_id)
            lines.append(f"{speaker}: {t.content}")
        return lines

    # ---------------------------------------------------------------- stop conditions

    async def _check_stop_conditions(self, view: ConversationView) -> str | None:
        """Convergence is NOT checked here -- FloorPolicy's Converged decision is the
        trigger for that, handled in _dispatch. The external-status-change ("user-ends-it")
        check happens earlier, in run_once(), since it needs to distinguish a genuine
        external stop from an already-paused needs_human session (see run_once()'s
        docstring comment). This only covers max_rounds, the no-new-arguments staleness
        counter, and the token/cost budget -- callable only once run_once() has confirmed
        the session is still STATUS_ACTIVE."""
        async with self._sessionmaker() as session:
            row = await session.get(SessionRow, self._session_id)

            # M2.12: check if all phases are complete
            if self._mode.phases and self._current_phase_index >= len(self._mode.phases):
                return "phases-complete"

            if self._stop_rules.max_rounds is not None and view.round >= self._stop_rules.max_rounds:
                return "max-rounds"
            if row.stale_argument_count >= self._stop_rules.stale_argument_turns:
                return "no-new-arguments"
            if await self._budget_exceeded(row):
                return None # M2.12: trigger fallback instead of stopping session
        return None

    async def _budget_exceeded(self, row: SessionRow) -> bool:
        """Session.budget is nullable (null = uncapped, skip the check); shape assumed to
        be {"max_tokens": int, "max_cost": float} -- no canonical schema exists for this
        JSONB column yet. Accuracy is bounded by the self-reported-meta convention (see
        docs/agent-communication-protocol.md's tokens_in/tokens_out/cost section). Usage
        aggregation itself lives in `budget.session_usage` (M3.1, shared with
        `GET /sessions/{id}` so the two never compute this differently)."""
        if not row.budget:
            return False
        usage = await session_usage(self._sessionmaker, self._session_id)
        max_tokens = row.budget.get("max_tokens")
        if max_tokens is not None and (usage.tokens_in + usage.tokens_out) >= max_tokens:
            return True
        max_cost = row.budget.get("max_cost")
        if max_cost is not None and usage.cost >= max_cost:
            return True
        return False

    # ---------------------------------------------------------------- hand-raise collection

    async def _collect_hand_raises(self, view: ConversationView) -> list[HandRaise]:
        personas = await self._current_personas(view.muted)
        scored = await self._scorer.score(view, personas)
        self_reported = await self._self_reported_hand_raises()
        interjected = await self._interjected_addressed_hand_raises()
        merged: dict[str, HandRaise] = {h.participant: h for h in scored}
        merged.update({h.participant: h for h in self_reported})  # explicit signal beats a cheap guess
        merged.update({h.participant: h for h in interjected})  # a human addressing someone wins outright
        return list(merged.values())

    async def _current_personas(self, muted: set[str]) -> list[PersonaBrief]:
        """M3.2: fetched fresh from the current SessionAgent roster every call (not a
        constructor-time snapshot) so a mid-session add is immediately visible to the
        scorer, and a muted/removed participant is never even offered to it -- no reason to
        spend a Haiku call scoring someone who can't be granted the floor regardless of the
        answer."""
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(Agent).join(SessionAgent, SessionAgent.agent_id == Agent.id)
                .where(SessionAgent.session_id == self._session_id).order_by(SessionAgent.seat_order)
            )).scalars().all()
        return [PersonaBrief(participant=a.role, role=a.role, stance=a.stance, brief=a.system_prompt or "")
                for a in rows if a.role not in muted]

    async def _interjected_addressed_hand_raises(self) -> list[HandRaise]:
        """M2.8's POST /sessions/{id}/interject: a directed interject (a single, specific
        `to` participant, not a broadcast) synthesizes a HandRaise(reason=ADDRESSED,
        urgency=1.0) into the merge, reusing RaiseHandFloorPolicy's existing
        "addressed wins" rule rather than adding new floor-policy code. A broadcast
        interject (to=["*"] or multiple recipients) addresses no one in particular and
        synthesizes nothing here."""
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(MessageRow).where(
                    MessageRow.session_id == self._session_id,
                    MessageRow.from_ == "human",
                    MessageRow.seq > self._last_interject_seq,
                ).order_by(MessageRow.seq)
            )).scalars().all()
        if rows:
            self._last_interject_seq = rows[-1].seq
        hand_raises = []
        for row in rows:
            if len(row.to) == 1 and row.to[0] != "*":
                hand_raises.append(HandRaise(participant=row.to[0], reason=Reason.ADDRESSED, urgency=1.0))
        return hand_raises

    async def _self_reported_hand_raises(self) -> list[HandRaise]:
        """A self-emitted hand-raise Envelope's `body` is HandRaise.model_dump(mode="json")
        -- a wire convention this task had to invent, since the protocol doc's "an agent
        may emit its own hand-raise message" never specified how the structured fields
        (reason, urgency) get encoded into a message body. See docs/decisions.md."""
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(MessageRow).where(
                    MessageRow.session_id == self._session_id,
                    MessageRow.kind == "hand-raise",
                    MessageRow.seq > self._last_hand_raise_seq,
                ).order_by(MessageRow.seq)
            )).scalars().all()
        if rows:
            self._last_hand_raise_seq = rows[-1].seq
        hand_raises = []
        for row in rows:
            try:
                hand_raises.append(HandRaise.model_validate(json.loads(row.body)))
            except Exception:
                log.warning("malformed self-reported hand-raise from %s (message_id=%s), ignoring",
                            row.from_, row.message_id)
        return hand_raises

    # ---------------------------------------------------------------- granting the floor

    async def _pending_grant(self) -> str | None:
        """M2.9: detects a Grant whose assignment was durably sent (a "assignment" Message
        row) but has no response yet -- the participant it was addressed to. Looks only at
        the MOST RECENT assignment: once a participant responds (any message with a higher
        seq), that assignment is resolved and the check naturally returns None again until
        the *next* assignment is sent. No new schema: a plain query over the already-mirrored
        Message rows."""
        async with self._sessionmaker() as session:
            last_assignment = (await session.execute(
                select(MessageRow).where(MessageRow.session_id == self._session_id,
                                          MessageRow.kind == "assignment")
                .order_by(MessageRow.seq.desc()).limit(1)
            )).scalar_one_or_none()
            if last_assignment is None:
                return None
            participant = last_assignment.to[0]
            answered = await session.scalar(
                select(MessageRow.message_id).where(
                    MessageRow.session_id == self._session_id,
                    MessageRow.from_ == participant,
                    MessageRow.seq > last_assignment.seq,
                ).limit(1)
            )
        return None if answered else participant

    async def _handle_grant(self, view: ConversationView, decision: Grant) -> None:
        participant = decision.participant
        context = await self._build_context(view)
        await self._conversation.send(sender=self._mode.moderator or "moderator", to=[participant],
                                       kind="assignment", body=context)
        await self._wake_await_and_record(view, participant)

    async def _resume_pending_grant(self, view: ConversationView, participant: str) -> None:
        """The assignment was already sent and durably recorded -- do NOT resend it, only
        re-wake. wake_role() is a notification poke, not a stateful "exactly one wake
        credit" mechanism: notifying an already-attentive participant a second time is
        harmless, so always re-waking here (at-least-once delivery against an idempotent
        receiver) is correct whether this is a real restart or an ordinary retry."""
        await self._wake_await_and_record(view, participant)

    async def _wake_await_and_record(self, view: ConversationView, participant: str) -> None:
        # Span scoped to wake+await+record only -- closes BEFORE _update_rolling_summary(),
        # so moderator.summarize ends up a sibling of this span (both under
        # moderator.run_once), not nested inside it.
        with tracer.start_as_current_span("moderator.grant_wait", attributes={
            "bayto.session_id": str(self._session_id), "bayto.participant": participant,
            "timeout_s": self._config.grant_timeout_seconds,
        }) as span:
            # Determine if we should use local fallback based on budget or previous error
            async with self._sessionmaker() as session:
                row = await session.get(SessionRow, self._session_id)
                use_fallback = row.fallback_active or await self._budget_exceeded(row)

            # Subscribe BEFORE waking: a fake/fast/same-process participant can publish its
            # response synchronously inside wake_role() itself (as the tests' FakeSandboxProvider
            # does), and asyncio.Queue holds items for a not-yet-.get()-ing subscriber, but only
            # for subscribers that already exist -- subscribing after wake_role() would race and
            # could miss it.
            queue = self._pubsub.subscribe()
            try:
                try:
                    await self._sandbox_provider.wake_role(self._sandbox, participant, fallback=use_fallback)
                except SandboxCommandError as e:
                    # Reactive Fallback: if we hit a platform limit (Weekly/Daily), switch to Ollama immediately
                    if "limit" in e.stderr.lower() or "rate limit" in e.stderr.lower():
                        log.warning("session %s: platform limit hit in sandbox; triggering reactive fallback", self._session_id)
                        async with self._sessionmaker() as session:
                            row = await session.get(SessionRow, self._session_id)
                            row.fallback_active = True
                            await session.commit()
                        # Retry immediately with fallback enabled
                        await self._sandbox_provider.wake_role(self._sandbox, participant, fallback=True)
                    else:
                        raise

                response = await self._await_response(queue, participant, self._config.grant_timeout_seconds)

                # Reactive Fallback (Content Level): if the agent's response body contains limit errors
                if response and "body" in response:
                    body_text = response["body"].lower()
                    if "weekly limit" in body_text or "usage limit" in body_text or "rate limit" in body_text:
                        log.warning("session %s: platform limit detected in response body; triggering reactive fallback", self._session_id)
                        async with self._sessionmaker() as session:
                            row = await session.get(SessionRow, self._session_id)
                            row.fallback_active = True
                            await session.commit()

                        # Retry the turn immediately using the fallback path
                        await self._sandbox_provider.wake_role(self._sandbox, participant, fallback=True)
                        response = await self._await_response(queue, participant, self._config.grant_timeout_seconds)

                if response is None:
                    # One retry: re-wake (not re-send -- the assignment is already sitting
                    # unread) and wait again, per the architect's "retry the same grant once
                    # more."
                    # Re-calculate fallback in case it was triggered by the first attempt
                    async with self._sessionmaker() as session:
                        row = await session.get(SessionRow, self._session_id)
                        use_fallback = row.fallback_active or await self._budget_exceeded(row)

                    await self._sandbox_provider.wake_role(self._sandbox, participant, fallback=use_fallback)
                    response = await self._await_response(queue, participant, self._config.grant_timeout_seconds)
            finally:
                self._pubsub.unsubscribe(queue)

            if response is None:
                span.set_attribute("outcome", "timeout")
                await self._escalate_to_human(view, f"{participant} did not respond after 2 grants")
                return

            span.set_attribute("outcome", "responded")
            await self._record_turn(view, participant, response)

        await self._update_rolling_summary()

    async def _build_context(self, view: ConversationView) -> str:
        async with self._sessionmaker() as session:
            row = await session.get(SessionRow, self._session_id)
            summary = row.rolling_summary

            # M5: include summaries of previous sessions for the same task
            prev_sessions = (await session.execute(
                select(SessionRow.id, SessionRow.rolling_summary)
                .where(SessionRow.task_id == row.task_id, SessionRow.id != self._session_id)
                .order_by(SessionRow.started_at.desc())
            )).all()
            prev_summaries = [f"Session {s.id}: {s.rolling_summary}" for s in prev_sessions if s.rolling_summary]

        lines = []
        if prev_summaries:
            lines.append("Previous Session Context:")
            lines.extend(prev_summaries)
            lines.append("")

        # M2.12: include current phase in context
        if self._mode.phases and self._current_phase_index < len(self._mode.phases):
            phase = self._mode.phases[self._current_phase_index]
            lines.append(f"Current Phase: {phase['name']} - {phase.get('description', '')}")
            lines.append("")

        lines.append(f"Rolling summary: {summary or '(none yet)'}")
        lines.append("")
        lines.append("Recent turns:")
        lines.extend(view.recent_transcript)
        return "\n".join(lines)

    async def _await_response(self, queue: asyncio.Queue, participant: str, timeout: float) -> dict | None:
        """Waits up to `timeout` seconds on an already-subscribed queue for a mirrored
        message from `participant`. Push-based via pubsub, not DB polling. Assumes
        `self._pubsub` is already scoped to this session -- MessageMirror is "one instance
        per session's factory directory" (its own docstring), so one MessageMirror+PubSub
        pair per session is the expected wiring; fanning multiple sessions' events onto
        session-scoped queues is orchestrator session-supervisor wiring that M2.8 owns, not
        built here."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if event.get("type") == "message" and event.get("data", {}).get("from") == participant:
                return event["data"]

    # ---------------------------------------------------------------- turn recording

    async def _record_turn(self, view: ConversationView, participant: str, envelope_data: dict) -> None:
        """M2.7: durably record the agent's response as a Turn row.
        Calculates the current round using the policy's projection and extracts budget meta."""
        async with self._sessionmaker() as session:
            # 1. Resolve participant role to agent_id
            agent = await session.execute(
                select(Agent).where(Agent.role == participant)
            ).scalar_one()

            # 2. Determine sequence and round
            last_turn = (await session.execute(
                select(Turn).where(Turn.session_id == self._session_id).order_by(Turn.seq.desc()).limit(1)
            )).scalar_one_or_none()

            seq = (last_turn.seq + 1) if last_turn else 1
            round_val = self._round_for_grant(last_turn.round if last_turn else 0, participant, view.seat_order)

            # 3. Extract content and budget meta (per la-haiku convention)
            content = envelope_data.get("body", "")
            kind = envelope_data.get("kind", "note")
            meta = envelope_data.get("meta", {})
            tokens_in = meta.get("tokens_in")
            tokens_out = meta.get("tokens_out")
            cost = meta.get("cost")

            # 4. Persist the turn
            turn = Turn(
                session_id=self._session_id,
                seq=seq,
                speaker_id=agent.id,
                round=round_val,
                content=content,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
            )
            session.add(turn)
            await session.flush() # get turn.id

            # M6: record usage in the ledger
            from ..models import UsageLedger
            ledger_entry = UsageLedger(
                session_id=self._session_id,
                tokens_in=tokens_in or 0,
                tokens_out=tokens_out or 0,
                cost=cost or 0.0,
                turn_id=turn.id,
            )
            session.add(ledger_entry)

            # M5: if this is a decision-request, pause the session for human input
            if kind == "decision-request":
                row = await session.get(SessionRow, self._session_id)
                row.status = STATUS_NEEDS_HUMAN
                log.info("session %s: decision request received from %s; pausing for human", self._session_id, participant)

            await session.commit()

            # M2.12: advance the phase machine after a recorded turn
            await self._advance_phase()


    async def _advance_phase(self) -> None:
        """M2.12: update phase machine state. If all phases complete, the session ends."""
        if not self._mode.phases:
            return

        self._turns_in_phase += 1
        current_phase = self._mode.phases[self._current_phase_index]
        if self._turns_in_phase >= current_phase.get("max_rounds", 1):
            self._current_phase_index += 1
            self._turns_in_phase = 0
            if self._current_phase_index < len(self._mode.phases):
                log.info("session %s: transitioning to phase %s", self._session_id,
                         self._mode.phases[self._current_phase_index]["name"])
            else:
                log.info("session %s: all phases complete", self._session_id)

    def _round_for_grant(self, last_round: int, participant: str, seat_order: list[str]) -> int:
        """Round semantics differ by policy (the architect's instruction: let each
        FloorPolicy's projection define its own round counter rather than force one
        universal definition). round-robin: a new round is one full seat-rotation lap,
        i.e. whenever the granted participant is seat_order[0] again (and it's not the
        very first grant). raise-hand: one grant cycle = one round."""
        if self._mode.floor_policy == "round-robin":
            if seat_order and participant == seat_order[0] and last_round > 0:
                return last_round + 1
            return max(last_round, 1)
        return last_round + 1

    # ---------------------------------------------------------------- rolling summary

    async def _update_rolling_summary(self) -> None:
        async with self._sessionmaker() as session:
            row = await session.get(SessionRow, self._session_id)
            new_turns = (await session.execute(
                select(Turn).where(Turn.session_id == self._session_id,
                                    Turn.seq > (row.rolling_summary_through_seq or 0))
                .order_by(Turn.seq)
            )).scalars().all()
            if not new_turns:
                return
            summaries = [await self._to_turn_summary(session, t) for t in new_turns]
            result = await self._summarizer.summarize(self._session_id, row.rolling_summary, summaries)
            row.rolling_summary = result.summary
            row.rolling_summary_through_seq = new_turns[-1].seq
            row.stale_argument_count = 0 if result.has_new_argument else row.stale_argument_count + 1
            await session.commit()
            
            self._pubsub.publish({
                "type": "rolling_summary",
                "data": {"summary": result.summary}
            })

    async def _to_turn_summary(self, session, turn: Turn) -> TurnSummary:
        agent = await session.get(Agent, turn.speaker_id)
        return TurnSummary(speaker=agent.role if agent else str(turn.speaker_id),
                            content=turn.content, round=turn.round or 0)

    # ---------------------------------------------------------------- terminal actions

    async def _escalate_to_human(self, view: ConversationView, reason: str) -> None:
        """Pauses the loop and marks the session needs_human. Persisting a structured
        AskHuman event and building the actual human-notification/response channel are out
        of scope (architect: "exact shape is a minor implementation detail" / "do NOT
        build the human-notification/response channel") -- Session.status is the durable
        signal for now."""
        async with self._sessionmaker() as session:
            row = await session.get(SessionRow, self._session_id)
            row.status = STATUS_NEEDS_HUMAN
            await session.commit()
        log.warning("session %s needs human: %s", self._session_id, reason)

    async def _finish(self, view: ConversationView, reason: str) -> None:
        log.info("session %s finishing: %s", self._session_id, reason)
        async with self._sessionmaker() as session:
            row = await session.get(SessionRow, self._session_id)
            all_turns_rows = (await session.execute(
                select(Turn).where(Turn.session_id == self._session_id).order_by(Turn.seq)
            )).scalars().all()
            all_turns = [await self._to_turn_summary(session, t) for t in all_turns_rows]
            result = await self._synthesizer.synthesize(
                view, row.rolling_summary or "", all_turns,
                synthesis_prompt_hint=self._mode.synthesis_prompt_hint,
            )

            session.add(Artifact(session_id=self._session_id, type="synthesis",
                                  content_json=result.content_json, source_turn_ids=result.source_turn_ids))
            if result.minority_report is not None:
                session.add(Artifact(session_id=self._session_id, type="minority_report",
                                      content_json=result.minority_report,
                                      source_turn_ids=result.source_turn_ids))

            row.status = STATUS_FINISHED
            row.stop_reason = _STOP_REASON_VALUES.get(reason, reason)
            row.ended_at = datetime.now(timezone.utc)
            await session.commit()

    async def _end_without_synthesis(self, stop_reason: str) -> None:
        """The stop triggers that must NOT run the Synthesizer: M2.8's /stop with
        synthesize=false, and M2.10's budget-exceeded (see run_once())."""
        log.info("session %s ending without synthesis: %s", self._session_id, stop_reason)
        async with self._sessionmaker() as session:
            row = await session.get(SessionRow, self._session_id)
            row.status = STATUS_FINISHED
            row.stop_reason = stop_reason
            row.ended_at = datetime.now(timezone.utc)
            await session.commit()
