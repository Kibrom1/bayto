import json
from pathlib import Path

import pytest

from acp.roster import RosterError, build_team, load_catalog

CATALOG = Path(__file__).resolve().parents[2] / "roles"


def test_default_catalog_has_seven_roles():
    assert set(load_catalog(CATALOG)) == {"researcher", "product-owner", "coordinator", "architect",
                                          "backend-engineer", "frontend-engineer", "qa-tester"}


def test_build_team_files(tmp_path):
    cat = load_catalog(CATALOG)
    r = build_team([{"role": "coordinator"}, {"role": "backend-engineer", "model": "claude-sonnet-5"},
                    {"role": "qa-tester"}], cat, tmp_path, topic="Build X")
    rows = [l.split("\t") for l in (tmp_path / "team.tsv").read_text().splitlines()]
    assert rows[1] == ["backend-engineer", "claude", "anthropic", "claude-sonnet-5"]
    assert rows[0][3] == "claude-haiku"
    brief = (tmp_path / "roles" / "qa-tester.md").read_text()
    assert "Build X" in brief and "backend-engineer" in brief and "acp send" in brief
    tools = json.loads((tmp_path / "tools.json").read_text())
    assert "Write" in tools["coordinator"]["deny"] and "Write" in tools["backend-engineer"]["allow"]
    assert "pypi.org" in r["network"] and "registry.npmjs.org" not in r["network"]  # union of chosen roles only
    assert json.loads((tmp_path / "roster.json").read_text())["roles"] == ["coordinator", "backend-engineer", "qa-tester"]


def test_duplicate_roles_get_unique_seats(tmp_path):
    r = build_team([{"role": "researcher"}, {"role": "researcher"}], load_catalog(CATALOG), tmp_path)
    assert r["seats"] == ["researcher", "researcher-2"]


def test_unknown_or_empty_roster_rejected(tmp_path):
    cat = load_catalog(CATALOG)
    with pytest.raises(RosterError):
        build_team([{"role": "wizard"}], cat, tmp_path)
    with pytest.raises(RosterError):
        build_team([], cat, tmp_path)
