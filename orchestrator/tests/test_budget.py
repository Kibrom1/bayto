"""M3.1: budget.session_usage -- the shared aggregate extracted from ModeratorRunner's
budget check (M2.7) so GET /sessions/{id} never risks computing usage differently. Pure
insert/select round-trip against real Postgres, no sbx or Anthropic needed."""
import uuid

from orchestrator.models import Agent, Turn
from orchestrator.moderator.budget import session_usage

from test_moderator_runner import _make_session


async def _seat_agent(sessionmaker, session_id: uuid.UUID, role: str) -> uuid.UUID:
    async with sessionmaker() as db:
        agent = Agent(kind="native", name=role, role=role)
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return agent.id


async def test_session_usage_sums_tokens_and_cost_across_turns(live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    agent_id = await _seat_agent(live_sessionmaker, session_id, "a")
    async with live_sessionmaker() as db:
        db.add_all([
            Turn(session_id=session_id, seq=1, speaker_id=agent_id, content="x",
                 tokens_in=10, tokens_out=20, cost=0.01),
            Turn(session_id=session_id, seq=2, speaker_id=agent_id, content="y",
                 tokens_in=5, tokens_out=7, cost=0.002),
        ])
        await db.commit()

    usage = await session_usage(live_sessionmaker, session_id)
    assert usage.tokens_in == 15
    assert usage.tokens_out == 27
    assert abs(usage.cost - 0.012) < 1e-9


async def test_session_usage_is_zero_for_a_session_with_no_turns(live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    usage = await session_usage(live_sessionmaker, session_id)
    assert usage.tokens_in == 0
    assert usage.tokens_out == 0
    assert usage.cost == 0.0


async def test_session_usage_treats_a_turn_with_no_meta_as_zero(live_sessionmaker):
    """A turn that never self-reported tokens_in/tokens_out/cost (M2.7's warn-and-continue
    path) contributes 0, not None/NULL propagating through the sum."""
    session_id = await _make_session(live_sessionmaker)
    agent_id = await _seat_agent(live_sessionmaker, session_id, "a")
    async with live_sessionmaker() as db:
        db.add(Turn(session_id=session_id, seq=1, speaker_id=agent_id, content="no meta"))
        await db.commit()

    usage = await session_usage(live_sessionmaker, session_id)
    assert usage.tokens_in == 0
    assert usage.tokens_out == 0
    assert usage.cost == 0.0
