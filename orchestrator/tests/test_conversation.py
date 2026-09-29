"""M2.5: OrchestratorConversation wraps agent-comms' Conversation in asyncio.to_thread,
backed by a FileTransport against a real (tmp_path) factory dir -- no live Postgres or sbx
needed. Mode-file YAML parsing itself is covered in agent-comms/tests/test_modes.py; here
a ModeConfig is built directly to keep these tests focused on the async wrapper."""
import pytest
from acp import PermissionError_
from acp.modes import ModeConfig
from acp.transport import FileTransport

from orchestrator.conversation import OrchestratorConversation

PIPELINE_MODE = ModeConfig(
    name="code-factory",
    floor_policy="pipeline",
    roles={
        "coordinator": ["assignment", "question", "decision-request", "summary", "note"],
        "developer": ["report", "question", "answer", "note"],
        "qa": ["report", "critique", "question", "note"],
    },
    route={
        "start": "coordinator",
        "developer.report.implemented": "qa",
        "qa.report.review-pass": "coordinator",
    },
    moderator="coordinator",
)


async def test_send_persists_a_real_envelope_to_the_factory_dir(tmp_path):
    conv = OrchestratorConversation.create(conversation_id="c1", factory_dir=tmp_path, mode=PIPELINE_MODE)

    env = await conv.send("coordinator", ["developer"], "assignment", "implement M2.5")

    assert env.seq == 1
    assert list((tmp_path / "messages").glob("*.json"))


async def test_send_policy_from_the_mode_is_enforced(tmp_path):
    conv = OrchestratorConversation.create(conversation_id="c1", factory_dir=tmp_path, mode=PIPELINE_MODE)

    with pytest.raises(PermissionError_):
        await conv.send("developer", ["qa"], "assignment", "not allowed")


async def test_transcript_for_is_viewer_scoped_through_the_wrapper(tmp_path):
    conv = OrchestratorConversation.create(conversation_id="c1", factory_dir=tmp_path, mode=PIPELINE_MODE)
    await conv.send("developer", ["qa"], "note", "side-channel", visibility="recipients")

    assert [e.body for e in await conv.transcript_for("qa")] == ["side-channel"]
    assert await conv.transcript_for("coordinator") == []


async def test_stage_round_trips_through_the_wrapper(tmp_path):
    conv = OrchestratorConversation.create(conversation_id="c1", factory_dir=tmp_path, mode=PIPELINE_MODE)

    assert await conv.stage() == "open"
    assert await conv.stage("review") == "review"


async def test_claim_is_single_fire_through_the_wrapper(tmp_path):
    conv = OrchestratorConversation.create(conversation_id="c1", factory_dir=tmp_path, mode=PIPELINE_MODE)

    assert await conv.claim("notify:m1:developer") is True
    assert await conv.claim("notify:m1:developer") is False


async def test_orchestrator_can_author_messages_that_the_mirror_would_poll(tmp_path):
    """Answers the design question of where orchestrator-authored messages come from:
    the orchestrator sends through its own OrchestratorConversation, landing in the same
    files a MessageMirror already polls -- no separate write path."""
    conv = OrchestratorConversation.create(conversation_id="c1", factory_dir=tmp_path, mode=PIPELINE_MODE)
    await conv.send("coordinator", ["developer"], "assignment", "go")

    mirrored = FileTransport(tmp_path).all()
    assert [e.kind for e in mirrored] == ["assignment"]
