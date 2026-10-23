"""The opt-in semantic stage of attachment retrieval: embedding client, vector cache, hybrid ranking, fallback.

Everything here runs against fakes (httpx.MockTransport, hand-made vectors). It has never been run against a real
embedding API."""
import json
import logging
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

import orchestrator.semantic as semantic
from orchestrator.attachment_prompt import PROMPT_BUDGET_CHARS, attachment_prompt
from orchestrator.models import AttachmentChunkVec, Session, Task, TaskAttachment
from orchestrator.providers import ProviderError
from orchestrator.providers.embeddings import (EmbeddingClient, embedding_client, embedding_provider, embedding_tag,
                                                embeddings_ready)
from orchestrator.retrieval import KEYWORD_WEIGHT, SEMANTIC_WEIGHT, rank, split_chunks
from orchestrator.semantic import cosine, semantic_scores


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("OPENAI_API_KEY", "OLLAMA_HOST", "OPENAI_BASE_URL", "BAYTO_EMBEDDING_MODEL",
                "BAYTO_OLLAMA_EMBEDDING_MODEL", "BAYTO_EMBEDDING_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(semantic, "_noted", False)


# ---------------------------------------------------------------- the client

async def test_openai_embeddings_request_and_response_order():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers.get("authorization"), json.loads(request.content)
        # out of order on purpose: the client must sort by index
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]})

    c = EmbeddingClient("openai", base_url="https://api.example/v1", api_key="sk-secret", model="emb-x",
                        transport=httpx.MockTransport(handler))
    assert await c.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert seen["url"] == "https://api.example/v1/embeddings" and seen["auth"] == "Bearer sk-secret"
    assert seen["body"] == {"model": "emb-x", "input": ["a", "b"]} and c.tag == "openai:emb-x"


async def test_ollama_embeddings_use_api_embed_without_a_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers.get("authorization"), json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.5, 0.5]]})

    c = EmbeddingClient("ollama", base_url="http://127.0.0.1:11434", api_key=None, model="nomic-embed-text",
                        transport=httpx.MockTransport(handler))
    assert await c.embed(["a"]) == [[0.5, 0.5]]
    assert seen["url"] == "http://127.0.0.1:11434/api/embed" and seen["auth"] is None
    assert seen["body"] == {"model": "nomic-embed-text", "input": ["a"]}


async def test_large_inputs_are_sent_in_batches():
    sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["input"])
        sizes.append(n)
        return httpx.Response(200, json={"embeddings": [[1.0]] * n})

    c = EmbeddingClient("ollama", base_url="http://h", api_key=None, model="m", transport=httpx.MockTransport(handler))
    assert len(await c.embed(["x"] * 150)) == 150 and sizes == [64, 64, 22]


async def test_failures_are_provider_errors_and_never_show_the_key():
    def rejected(request): return httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})
    def garbled(request): return httpx.Response(200, json={"nope": 1})
    def short(request): return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1]}]})
    def down(request): raise httpx.ConnectError("refused", request=request)

    for handler, text in ((rejected, "401"), (garbled, "unexpected shape"), (short, "wrong number"), (down, "could not reach")):
        c = EmbeddingClient("openai", base_url="https://api.example/v1", api_key="sk-secret", model="m",
                            transport=httpx.MockTransport(handler))
        with pytest.raises(ProviderError, match=text) as err:
            await c.embed(["a", "b"])
        assert "sk-secret" not in str(err.value)


def test_provider_choice_and_models_come_from_the_environment(monkeypatch):
    assert embedding_provider() is None and not embeddings_ready() and embedding_tag() is None
    with pytest.raises(ProviderError, match="OPENAI_API_KEY or OLLAMA_HOST"):
        embedding_client()
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434/v1")
    assert embedding_provider() == "ollama" and embedding_tag() == "ollama:nomic-embed-text"
    assert embedding_client()._base == "http://127.0.0.1:11434"   # /api/embed is beside /v1
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert embedding_provider() == "openai" and embedding_tag() == "openai:text-embedding-3-small"
    monkeypatch.setenv("BAYTO_EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("BAYTO_OLLAMA_EMBEDDING_MODEL", "mxbai-embed-large")
    assert embedding_tag() == "openai:text-embedding-3-large"
    monkeypatch.setenv("BAYTO_EMBEDDING_PROVIDER", "ollama")
    assert embedding_tag() == "ollama:mxbai-embed-large"
    monkeypatch.delenv("OLLAMA_HOST")
    assert embedding_provider() is None   # forced to a provider that is not set up


# ---------------------------------------------------------------- hybrid ranking

# A toy embedding: one dimension per idea, so "walk away" and "terminate" land together without sharing a word.
IDEAS = [{"walk", "away", "terminate", "cancel", "exit"}, {"price", "fee", "cost", "invoice"}]


def fake_vec(text: str) -> list[float]:
    words = set(text.lower().replace(".", " ").replace(",", " ").split())
    return [float(len(words & idea)) for idea in IDEAS] + [1.0]


class FakeClient(EmbeddingClient):
    def __init__(self, tag="openai:fake", fail: Exception | None = None):
        super().__init__("openai", base_url="http://x", api_key=None, model=tag.split(":")[1])
        self.tag, self.calls, self.fail = tag, [], fail

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise self.fail
        return [fake_vec(t) for t in texts]


TERMINATION = "Either side may terminate the contract upon ninety days written notice."
QUERY = "walk away from the deal"


def _long(needle_at: int, needle: str, parts: int = 60) -> str:
    return "\n\n".join(needle if i == needle_at else f"general background section {i}. " + "background " * 40 for i in range(parts))


def _att(name, content, id=None):
    return SimpleNamespace(id=id or uuid.uuid4(), kind="file", name=name, source=None, content=content, roles=None)


def test_hybrid_finds_the_passage_keyword_ranking_misses():
    text = _long(40, TERMINATION)
    assert all(c.score == 0 for c in rank([text], QUERY)[0])          # no shared word: BM25 sees nothing
    semantic_scores_ = [[cosine(fake_vec(QUERY), fake_vec(c)) for c in split_chunks(text)]]
    best = max(rank([text], QUERY, semantic_scores_)[0], key=lambda c: c.score)
    assert "ninety days" in best.text


def test_without_semantic_scores_the_prompt_is_unchanged_and_with_them_it_shows_the_passage():
    contract = _long(40, TERMINATION, parts=80)
    items = [_att("contract.txt", contract)]
    plain = attachment_prompt(items, budget=6_000, query=QUERY)
    assert "ninety days" not in plain and 'excerpt="' not in plain      # nothing matched: the old head cut
    scores = [[cosine(fake_vec(QUERY), fake_vec(c)) for c in split_chunks(contract)]]
    hybrid = attachment_prompt(items, budget=6_000, query=QUERY, semantic=scores)
    assert "ninety days" in hybrid and 'excerpt="parts' in hybrid


def test_the_weights_are_documented_constants_and_an_exact_term_still_counts():
    assert (KEYWORD_WEIGHT, SEMANTIC_WEIGHT) == (0.4, 0.6) and KEYWORD_WEIGHT + SEMANTIC_WEIGHT == 1
    texts = ["alpha clause fourteen " + "pad " * 30, "beta " + "pad " * 30]
    keyword_hit, other = rank(texts, "clause fourteen", [[0.2], [0.2]])
    assert keyword_hit[0].score == pytest.approx(KEYWORD_WEIGHT) and other[0].score == 0   # equal similarity: BM25 decides


def test_a_semantic_result_of_the_wrong_shape_is_ignored():
    text = _long(3, TERMINATION, parts=10)
    assert rank([text], "background", [[0.1, 0.2]]) == rank([text], "background")
    assert rank([text], "background", []) == rank([text], "background")


# ---------------------------------------------------------------- the vector cache

async def _task_with(sm, *contents, semantic_on=True):
    async with sm() as db:
        task = Task(title="T", brief="B", output_type="decision", semantic_retrieval=semantic_on)
        db.add(task); await db.flush()
        rows = [TaskAttachment(task_id=task.id, kind="file", name=f"a{i}.txt", content=c, truncated=False)
                for i, c in enumerate(contents)]
        db.add_all(rows); await db.commit()
        return task.id, [r.id for r in rows]


async def _count(sm, attachment_id=None):
    async with sm() as db:
        q = select(func.count()).select_from(AttachmentChunkVec)
        if attachment_id:
            q = q.where(AttachmentChunkVec.attachment_id == attachment_id)
        return await db.scalar(q)


async def _rows(sm, ids):
    async with sm() as db:
        return [await db.get(TaskAttachment, i) for i in ids]


async def test_vectors_are_cached_so_only_the_query_is_sent_the_second_time(live_sessionmaker):
    sm = live_sessionmaker
    text = _long(20, TERMINATION, parts=40)
    _, ids = await _task_with(sm, text)
    n = len(split_chunks(text))
    at = next(i for i, c in enumerate(split_chunks(text)) if "ninety days" in c)
    client = FakeClient()
    first = await semantic_scores(sm, await _rows(sm, ids), QUERY, client=client)
    assert len(client.calls[0]) == n + 1 and client.calls[0][-1] == QUERY      # chunks, then the query
    assert await _count(sm) == n
    second = await semantic_scores(sm, await _rows(sm, ids), QUERY, client=client)
    assert client.calls[1] == [QUERY] and second == first
    assert max(first[0]) == first[0][at]                                         # the termination chunk is the closest


async def test_editing_an_attachment_re_embeds_only_what_changed_and_drops_chunks_that_are_gone(live_sessionmaker):
    sm = live_sessionmaker
    text = _long(1, TERMINATION, parts=6)
    _, [aid] = await _task_with(sm, text)
    client = FakeClient()
    await semantic_scores(sm, await _rows(sm, [aid]), QUERY, client=client)
    parts = text.split("\n\n")
    parts[4] = "Price: the monthly fee is nine hundred dollars."
    async with sm() as db:
        (await db.get(TaskAttachment, aid)).content = "\n\n".join(parts)
        await db.commit()
    await semantic_scores(sm, await _rows(sm, [aid]), QUERY, client=client)
    changed = client.calls[1]
    assert changed[:-1] == [p for p in split_chunks("\n\n".join(parts)) if "monthly fee" in p] and changed[-1] == QUERY
    async with sm() as db:
        (await db.get(TaskAttachment, aid)).content = parts[0]   # shrinks to one chunk
        await db.commit()
    await semantic_scores(sm, await _rows(sm, [aid]), QUERY, client=client)
    assert await _count(sm, aid) == 1


async def test_a_different_embedding_model_invalidates_the_cache(live_sessionmaker):
    sm = live_sessionmaker
    text = _long(1, TERMINATION, parts=4)
    _, ids = await _task_with(sm, text)
    n = len(split_chunks(text))
    await semantic_scores(sm, await _rows(sm, ids), QUERY, client=FakeClient("openai:small"))
    other = FakeClient("openai:large")
    await semantic_scores(sm, await _rows(sm, ids), QUERY, client=other)
    assert len(other.calls[0]) == n + 1
    async with sm() as db:
        assert {r.model for r in (await db.execute(select(AttachmentChunkVec))).scalars()} == {"openai:large"}


async def test_vectors_go_with_the_attachment_and_with_the_task(live_sessionmaker):
    sm = live_sessionmaker
    task_id, ids = await _task_with(sm, _long(1, TERMINATION, parts=4), _long(2, TERMINATION, parts=4))
    await semantic_scores(sm, await _rows(sm, ids), QUERY, client=FakeClient())
    assert await _count(sm, ids[0]) > 0 and await _count(sm, ids[1]) > 0
    async with sm() as db:
        await db.delete(await db.get(TaskAttachment, ids[0]))
        await db.commit()
    assert await _count(sm, ids[0]) == 0 and await _count(sm, ids[1]) > 0
    async with sm() as db:   # what DELETE /tasks/{id} does
        from sqlalchemy import delete
        await db.execute(delete(TaskAttachment).where(TaskAttachment.task_id == task_id))
        await db.commit()
    assert await _count(sm) == 0


# ---------------------------------------------------------------- failure never fails a turn

async def test_any_failure_returns_none_and_is_logged_once(live_sessionmaker, caplog):
    sm = live_sessionmaker
    _, ids = await _task_with(sm, _long(1, TERMINATION, parts=4))
    caplog.set_level(logging.WARNING, logger="orchestrator.semantic")
    down = FakeClient(fail=ProviderError("could not reach openai for embeddings: ConnectError"))
    for _ in range(3):
        assert await semantic_scores(sm, await _rows(sm, ids), QUERY, client=down) is None
    odd = FakeClient(fail=RuntimeError("secret sk-abc in message"))
    assert await semantic_scores(sm, await _rows(sm, ids), QUERY, client=odd) is None
    notes = [r.getMessage() for r in caplog.records if "semantic retrieval unavailable" in r.getMessage()]
    assert len(notes) == 1 and "ConnectError" in notes[0]
    assert "sk-abc" not in caplog.text
    assert await semantic_scores(sm, await _rows(sm, ids), QUERY, client=FakeClient()) is not None   # recovers...
    assert await semantic_scores(sm, await _rows(sm, ids), QUERY, client=down) is None               # ...and notes again
    assert len([r for r in caplog.records if "semantic retrieval unavailable" in r.getMessage()]) == 2


async def test_no_key_means_no_scores_and_no_request(live_sessionmaker, caplog):
    sm = live_sessionmaker
    _, ids = await _task_with(sm, _long(1, TERMINATION, parts=4))
    caplog.set_level(logging.WARNING, logger="orchestrator.semantic")
    assert await semantic_scores(sm, await _rows(sm, ids), QUERY) is None
    assert "OPENAI_API_KEY or OLLAMA_HOST" in caplog.text and await _count(sm) == 0


# ---------------------------------------------------------------- in the room

from orchestrator.floor import RoundRobinFloorPolicy                       # noqa: E402
from orchestrator.models import Agent, Message as MessageRow              # noqa: E402
from test_moderator_runner import _make_session, _respond, _seat, mk_mode_config, mk_runner   # noqa: E402


async def _room(tmp_path, sm, monkeypatch, *, semantic_on, seat_role="lawyer"):
    session_id = await _make_session(sm)
    await _seat(sm, session_id, seat_role, 0)
    async with sm() as db:
        row = await db.get(Session, session_id)
        (await db.get(Task, row.task_id)).semantic_retrieval = semantic_on
        agent = (await db.execute(select(Agent).where(Agent.role == seat_role))).scalar_one()
        agent.system_prompt = QUERY
        db.add(TaskAttachment(task_id=row.task_id, kind="file", name="contract.txt", truncated=False,
                              content=_long(120, TERMINATION, parts=200)))
        db.add(TaskAttachment(task_id=row.task_id, kind="file", name="board-notes.txt", truncated=False, roles=["cfo"],
                              content="BOARD-ONLY " + _long(10, "Price: the fee is secret.", parts=200)))
        await db.commit()
    client = FakeClient()
    monkeypatch.setattr(semantic, "embedding_client", lambda: client)

    async def on_wake(role):
        await _respond(transport, mirror, role, "my view", meta={"tokens_in": 1, "tokens_out": 1, "cost": 0.0})

    runner, transport, mirror, _ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=sm, session_id=session_id,
        mode=mk_mode_config(roles={"moderator": None, seat_role: None}), policy=RoundRobinFloorPolicy(), on_wake=on_wake)
    await runner.run_once()
    async with sm() as db:
        assignment = (await db.execute(select(MessageRow).where(MessageRow.kind == "assignment"))).scalars().first()
    return client, assignment.body


async def test_a_semantic_task_shows_the_passage_and_never_embeds_what_the_seat_may_not_read(tmp_path, live_sessionmaker, monkeypatch):
    client, prompt = await _room(tmp_path, live_sessionmaker, monkeypatch, semantic_on=True)
    assert "ninety days" in prompt                                    # found by meaning, not by words
    sent = " ".join(t for call in client.calls for t in call)
    assert "BOARD-ONLY" not in sent and "secret" not in sent         # hidden from the lawyer: never sent out
    assert "BOARD-ONLY" not in prompt and "board-notes" not in prompt
    assert "ninety days" in sent and QUERY in sent                    # what was sent: the readable chunks and the query
    async with live_sessionmaker() as db:
        ids = {r.attachment_id for r in (await db.execute(select(AttachmentChunkVec))).scalars()}
        names = {(await db.get(TaskAttachment, i)).name for i in ids}
    assert names == {"contract.txt"}


async def test_a_task_without_the_switch_sends_nothing(tmp_path, live_sessionmaker, monkeypatch):
    client, prompt = await _room(tmp_path, live_sessionmaker, monkeypatch, semantic_on=False)
    assert client.calls == [] and "ninety days" not in prompt


async def test_a_failing_provider_does_not_fail_the_turn(tmp_path, live_sessionmaker, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sm = live_sessionmaker
    session_id = await _make_session(sm)
    await _seat(sm, session_id, "lawyer", 0)
    async with sm() as db:
        row = await db.get(Session, session_id)
        (await db.get(Task, row.task_id)).semantic_retrieval = True
        (await db.execute(select(Agent).where(Agent.role == "lawyer"))).scalar_one().system_prompt = QUERY
        db.add(TaskAttachment(task_id=row.task_id, kind="file", name="contract.txt", truncated=False,
                              content=_long(120, TERMINATION, parts=200)))
        await db.commit()
    monkeypatch.setattr(semantic, "embedding_client", lambda: FakeClient(fail=ProviderError("boom")))

    async def on_wake(role):
        await _respond(transport, mirror, role, "my view", meta={"tokens_in": 1, "tokens_out": 1, "cost": 0.0})

    runner, transport, mirror, _ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=sm, session_id=session_id,
        mode=mk_mode_config(roles={"moderator": None, "lawyer": None}), policy=RoundRobinFloorPolicy(), on_wake=on_wake)
    await runner.run_once()
    async with sm() as db:
        answer = (await db.execute(select(MessageRow).where(MessageRow.kind == "answer"))).scalars().one()
    assert answer.body == "my view" and answer.meta["attachments"][0]["mode"] == "start"   # plain head cut, as before
