"""Team-sandbox provisioning: the role catalog written from session agents must round-trip through
the repo's roster builder, and create() must use the provisioner when it is given seats."""
import asyncio
import subprocess
import uuid
from pathlib import Path

import pytest
from acp.roster import build_team, load_catalog

from orchestrator.sandbox.local import LocalSbxSandboxProvider
from orchestrator.sandbox.provider import SandboxProviderError, SeatSpec
from orchestrator.sandbox.provision import provision_team_sandbox, seat_id, write_catalog

SEATS = [
    SeatSpec(role="pm", name="PM", brief="You own scope.\n\nStance: scope-discipline", model="claude-sonnet-5"),
    SeatSpec(role="qa-tester", name="QA Tester", brief="Break it.", tools=("Bash",)),
    SeatSpec(role="pm", name="PM again", brief="duplicate role is collapsed"),
]


def test_seat_id_is_a_safe_slug():
    assert seat_id("E2E Tester") == "e2e-tester"
    assert seat_id("  ") == "seat"


def test_catalog_round_trips_through_the_roster_builder(tmp_path):
    roles, roster = tmp_path / "roles", tmp_path / "roster.txt"
    ids = write_catalog(SEATS, roles, roster)
    assert ids == ["pm", "qa-tester"]
    entries = [{"role": p[0], **({"model": p[1]} if len(p) > 1 else {})}
               for p in (line.split() for line in roster.read_text().splitlines())]
    res = build_team(entries, load_catalog(roles), tmp_path / "out", comms="crew")
    assert res["seats"] == ["pm", "qa-tester"]
    team = (tmp_path / "out" / "team.tsv").read_text().splitlines()
    assert team[0].split("\t")[3] == "claude-sonnet-5"
    assert "You own scope." in (tmp_path / "out" / "roles" / "pm.md").read_text()
    # read-only by default; Bash is only opened for the agent that asked for it
    assert "Write" in res["tools"]["pm"]["deny"] and "Bash(handoff:*)" in res["tools"]["pm"]["allow"]
    assert "Bash" in res["tools"]["qa-tester"]["allow"]


def test_missing_script_or_empty_roster_fail_with_a_clear_message(tmp_path):
    with pytest.raises(SandboxProviderError, match="not found"):
        asyncio.run(provision_team_sandbox("x", SEATS, repo_root=tmp_path))
    (tmp_path / "dev-team").mkdir()
    (tmp_path / "dev-team" / "run.sh").write_text("exit 0\n")
    with pytest.raises(SandboxProviderError, match="empty roster"):
        asyncio.run(provision_team_sandbox("x", [], repo_root=tmp_path))


def test_script_failure_surfaces_its_output(tmp_path):
    (tmp_path / "dev-team").mkdir()
    (tmp_path / "dev-team" / "run.sh").write_text('echo "boom: $1" >&2; exit 3\n')
    with pytest.raises(SandboxProviderError, match="exited 3.*boom: sbx-1"):
        asyncio.run(provision_team_sandbox("sbx-1", SEATS, repo_root=tmp_path))


def test_script_receives_roster_and_roles_dir(tmp_path):
    (tmp_path / "dev-team").mkdir()
    (tmp_path / "dev-team" / "run.sh").write_text(
        'cat "$ROSTER" > "$0.roster"; ls "$BAYTO_ROLES_DIR" > "$0.roles"; echo "$SESSION $NO_SWITCH" > "$0.env"\n')
    asyncio.run(provision_team_sandbox("sbx-2", SEATS, repo_root=tmp_path))
    run = tmp_path / "dev-team" / "run.sh"
    assert Path(f"{run}.roster").read_text().split() == ["pm", "claude-sonnet-5", "qa-tester"]
    assert "pm.yaml" in Path(f"{run}.roles").read_text()
    assert Path(f"{run}.env").read_text().strip() == "none 1"


async def test_create_uses_the_provisioner_instead_of_env_create(live_sessionmaker):
    from orchestrator.models import Task
    from test_local_sandbox import FakeSbxRunner

    calls = []

    async def provisioner(name, seats):
        calls.append((name, [s.role for s in seats]))

    runner = FakeSbxRunner()
    async with live_sessionmaker() as db:
        task = Task(title="t", brief="b", output_type="analysis")
        db.add(task)
        await db.commit()
        task_id = task.id
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner, provisioner=provisioner)
    info = await provider.create(task_id, name="sbx-p", seats=SEATS)
    assert calls == [("sbx-p", ["pm", "qa-tester", "pm"])]
    assert runner.calls == []  # no bare `sbx env create`
    assert info.name == "sbx-p" and info.status == "running"
