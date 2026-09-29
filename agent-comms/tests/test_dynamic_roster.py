"""M1.8: a generated roster drives the handoff shim, and crew-style briefs use the sandbox commands."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from acp.roster import RosterError, build_team, load_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIM = REPO_ROOT / "agent-comms/src/acp/handoff_compat.py"
CATALOG = REPO_ROOT / "roles"


def handoff(factory: Path, *args):
    env = {**os.environ, "FACTORY_DIR": str(factory)}
    return subprocess.run([sys.executable, str(SHIM), *args], env=env, text=True, capture_output=True, timeout=30)


def test_shim_uses_roster_seats(tmp_path):
    factory = tmp_path / "factory"
    factory.mkdir()
    (factory / "roster.json").write_text(json.dumps({"roles": ["coordinator", "architect", "qa-tester"]}))
    assert handoff(factory, "init", "--run", "r", "--task", "t").returncode == 0
    assert (factory / "consumed" / "architect").is_dir() and not (factory / "consumed" / "developer").exists()
    ok = handoff(factory, "send", "--to", "architect", "--from", "coordinator", "--kind", "assignment", "--body", "design it")
    assert ok.returncode == 0, ok.stderr
    read = handoff(factory, "read", "architect", "--json")
    assert json.loads(read.stdout)[0]["body"] == "design it"
    assert handoff(factory, "send", "--to", "human", "--from", "qa-tester", "--kind", "note", "--body", "x").returncode == 0
    old_role = handoff(factory, "send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "x")
    assert old_role.returncode == 2 and "architect" in old_role.stderr


def test_shim_defaults_to_workshop_trio_without_roster(tmp_path):
    factory = tmp_path / "factory"
    assert handoff(factory, "init", "--run", "r", "--task", "t").returncode == 0
    assert handoff(factory, "send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "x").returncode == 0
    assert handoff(factory, "send", "--to", "architect", "--from", "coordinator", "--kind", "note", "--body", "x").returncode == 2


def test_crew_briefs_use_sandbox_commands_and_project_rules(tmp_path):
    build_team([{"role": "coordinator"}, {"role": "architect"}, {"role": "backend-engineer"}], load_catalog(CATALOG),
               tmp_path, comms="crew", extra="Never push.")
    arch = (tmp_path / "roles" / "architect.md").read_text()
    assert "handoff read architect --json" in arch and "crew send" in arch and "acp send" not in arch
    assert "Bash is off limits except for `handoff` and `crew`" in arch
    assert "## Project rules\nNever push." in arch
    eng = (tmp_path / "roles" / "backend-engineer.md").read_text()
    assert "off limits" not in eng


def test_unknown_comms_rejected(tmp_path):
    with pytest.raises(RosterError):
        build_team([{"role": "coordinator"}], load_catalog(CATALOG), tmp_path, comms="carrier-pigeon")
