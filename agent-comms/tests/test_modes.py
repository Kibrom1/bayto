"""M2.5: mode files -> ModeConfig (Roster/SendPolicy/route table), per the
docs/agent-communication-protocol.md 'Reuse' example. Fixture files on local disk
(tests/fixtures/modes/), no I/O beyond reading them."""
from pathlib import Path

from acp.modes import load_mode

FIXTURES = Path(__file__).parent / "fixtures" / "modes"


def test_load_mode_parses_name_and_floor_policy():
    mode = load_mode(FIXTURES / "code-factory.yaml")
    assert mode.name == "code-factory"
    assert mode.floor_policy == "pipeline"


def test_load_mode_parses_roles_into_a_flat_role_to_kinds_map():
    mode = load_mode(FIXTURES / "code-factory.yaml")
    assert mode.roles == {
        "coordinator": ["assignment", "question", "decision-request", "summary", "note"],
        "developer": ["report", "question", "answer", "note", "x-code.review-request"],
        "qa": ["report", "critique", "question", "note"],
        "human": ["decision", "question", "note"],
    }


def test_load_mode_parses_the_route_table():
    mode = load_mode(FIXTURES / "code-factory.yaml")
    assert mode.route == {
        "start": "coordinator",
        "developer.report.implemented": "qa",
        "qa.report.review-fail": "developer",
        "qa.report.review-pass": "coordinator",
    }


def test_route_table_lookup_is_a_plain_dict_lookup():
    mode = load_mode(FIXTURES / "code-factory.yaml")
    assert mode.next_role("start") == "coordinator"
    assert mode.next_role("developer.report.implemented") == "qa"
    assert mode.next_role("qa.report.review-fail") == "developer"
    assert mode.next_role("no-such-key") is None


def test_load_mode_parses_moderator():
    mode = load_mode(FIXTURES / "code-factory.yaml")
    assert mode.moderator == "coordinator"


def test_moderator_defaults_to_none_when_absent():
    mode = load_mode(FIXTURES / "open-chat.yaml")
    assert mode.moderator is None


def test_pipeline_mode_with_no_explicit_send_gets_a_permissive_roster():
    mode = load_mode(FIXTURES / "code-factory.yaml")
    roster = mode.send_roster()
    assert roster.roles == ["coordinator", "developer", "qa", "human"]
    assert roster.send == {"*": ["*"]}
    assert roster.can_send("developer", ["qa"]) is True  # recipients unrestricted
    assert roster.can_send("developer", ["human"]) is True


def test_mode_with_explicit_send_block_uses_it_verbatim():
    mode = load_mode(FIXTURES / "open-chat.yaml")
    roster = mode.send_roster()
    assert roster.send == {"moderator": ["*"], "advocate": ["*"], "skeptic": ["*"]}


def test_role_with_no_may_send_list_is_unrestricted_none_not_missing():
    mode = load_mode(FIXTURES / "open-chat.yaml")
    assert mode.roles["skeptic"] is None


def test_send_policy_from_mode_matches_the_parsed_roles():
    from acp.models import SendPolicy

    mode = load_mode(FIXTURES / "code-factory.yaml")
    policy = SendPolicy.from_mode(mode)
    assert policy.check("qa", "report") is True
    assert policy.check("qa", "assignment") is False
    assert policy.check("human", "decision") is True
    assert policy.check("human", "assignment") is False
