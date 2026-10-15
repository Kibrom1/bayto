"""Every mode file the product ships must let the orchestrator do its own job.

The orchestrator sends as "moderator" (the turn assignment that grants a seat the floor) and as
"human" (interjections and the stop record). The shipped modes declare `moderator: null` and a
"*" role that does not allow those kinds, so the very first turn grant of every real session
died with `'moderator' may not send kind 'assignment'` -- found only by running the real app,
because the test fixtures define an explicit moderator role the shipped modes lacked.
"""
import asyncio
from pathlib import Path

import pytest

from orchestrator.conversation import OrchestratorConversation
from orchestrator.modes_registry import resolve_mode

MODES_DIR = Path(__file__).resolve().parents[2] / "modes"
SHIPPED = sorted(p.stem for p in MODES_DIR.glob("*.yaml"))


def test_the_shipped_modes_are_found():
    assert {"open-chat", "debate", "brainstorm"} <= set(SHIPPED)


@pytest.mark.parametrize("name", SHIPPED)
async def test_the_orchestrator_can_send_what_it_needs_to_in_every_shipped_mode(name, tmp_path, monkeypatch):
    monkeypatch.delenv("BAYTO_MODES_DIR", raising=False)
    mode = resolve_mode(name)
    conv = OrchestratorConversation.create(conversation_id="c1", factory_dir=tmp_path, mode=mode)

    moderator = mode.moderator or "moderator"
    await conv.send(sender=moderator, to=["*"], kind="assignment", body="your turn")
    await conv.send(sender="human", to=["*"], kind="note", body="interjection")
    await conv.send(sender="human", to=["*"], kind="x-stop", body='{"synthesize": true}')
    await conv.send(sender="architect", to=[moderator], kind="answer", body="my view")


def test_build_mode_route_only_names_seeded_roles_and_every_role_can_be_reached():
    """The pipeline's route table is data; a typo in a role name would strand a live session (the
    policy pauses for a person, which is safe but useless). Check it against the real templates."""
    from orchestrator.seed import BUILD_TEMPLATES

    mode = resolve_mode("build")
    roles = {t["role"] for t in BUILD_TEMPLATES}
    assert mode.floor_policy == "pipeline" and mode.route["start"] in roles

    targets = {v for v in mode.route.values() if v != "done"}
    sources = {k.split(".")[0] for k in mode.route if k != "start"}
    assert targets <= roles and sources <= roles
    assert targets | {mode.route["start"]} == roles  # nobody is seated but never reached
    assert "done" in mode.route.values()  # there is a way to succeed


def test_every_build_role_states_the_verdicts_the_route_expects():
    from orchestrator.seed import BUILD_TEMPLATES

    mode = resolve_mode("build")
    for tpl in BUILD_TEMPLATES:
        expected = {k.split(".")[2] for k in mode.route if k.startswith(tpl["role"] + ".")}
        assert expected, tpl["role"]
        for verdict in expected:
            assert f"VERDICT: {verdict}" in tpl["system_prompt"], (tpl["role"], verdict)
        assert "VERDICT: <word>" in tpl["system_prompt"]
