"""M2.11: team-roles/*.yaml -- reference/documentation content for humans authoring new
Bayto modes, not consumed by any runtime loader today (verified against the actual repo: no
Role ORM table, and modes/*.yaml's roles.<name>.may_send is fully self-contained -- see
team-roles/README.md and docs/decisions.md). A light YAML-syntax-and-expected-keys check is
enough here; a full loader/parser test suite would test a consumer that doesn't exist."""
from pathlib import Path

import yaml

TEAM_ROLES_DIR = Path(__file__).resolve().parents[2] / "team-roles"
EXPECTED_ROLES = {
    "researcher", "product-owner", "coordinator", "architect",
    "backend-engineer", "frontend-engineer", "qa-tester",
}
REQUIRED_KEYS = {"role", "version", "responsibility", "may_send", "hands_work_to", "tool_profile"}


def _load_all() -> dict[str, dict]:
    return {p.stem: yaml.safe_load(p.read_text()) for p in TEAM_ROLES_DIR.glob("*.yaml")}


def test_exactly_the_seven_documented_roles_exist():
    assert set(_load_all()) == EXPECTED_ROLES


def test_every_role_file_has_the_expected_keys_and_matching_role_id():
    for stem, doc in _load_all().items():
        assert REQUIRED_KEYS <= doc.keys(), f"{stem}.yaml missing keys: {REQUIRED_KEYS - doc.keys()}"
        assert doc["role"] == stem
        assert isinstance(doc["may_send"], list) and doc["may_send"]
        assert isinstance(doc["hands_work_to"], list) and doc["hands_work_to"]
        assert isinstance(doc["responsibility"], str) and doc["responsibility"].strip()
        assert isinstance(doc["tool_profile"], str) and doc["tool_profile"].strip()


def test_coordinator_hands_work_to_uses_the_open_wildcard():
    doc = _load_all()["coordinator"]
    assert doc["hands_work_to"] == ["*"]
