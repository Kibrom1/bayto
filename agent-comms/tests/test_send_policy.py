"""M2.5: SendPolicy is a different axis than Roster.can_send -- which Envelope `kind`s a
role may emit, not who it may address. Pure dict lookup, no I/O."""
from acp.models import Roster, SendPolicy


def test_role_restricted_to_its_listed_kinds():
    policy = SendPolicy(may_send={"qa": ["report", "critique", "question"]})
    assert policy.check("qa", "report") is True
    assert policy.check("qa", "critique") is True
    assert policy.check("qa", "assignment") is False


def test_role_mapped_to_none_is_unrestricted():
    policy = SendPolicy(may_send={"coordinator": None})
    assert policy.check("coordinator", "anything") is True
    assert policy.check("coordinator", "x-custom") is True


def test_role_absent_from_the_dict_is_unrestricted():
    policy = SendPolicy(may_send={"qa": ["report"]})
    assert policy.check("stranger", "assignment") is True


def test_from_mode_uses_the_modes_roles_dict():
    from acp.modes import ModeConfig

    mode = ModeConfig(name="m", floor_policy="pipeline",
                       roles={"qa": ["report"], "coordinator": None})
    policy = SendPolicy.from_mode(mode)
    assert policy.check("qa", "report") is True
    assert policy.check("qa", "assignment") is False
    assert policy.check("coordinator", "assignment") is True


# ---------------------------------------------------------------- M2.11 wildcard roles

def test_a_role_listed_alongside_a_star_entry_uses_its_own_list_not_the_star():
    policy = SendPolicy(may_send={"qa": ["report"], "*": ["note"]})
    assert policy.check("qa", "report") is True
    assert policy.check("qa", "note") is False  # qa's own list wins, star is a fallback only


def test_an_unlisted_role_falls_back_to_the_star_entry():
    policy = SendPolicy(may_send={"*": ["note", "question"]})
    assert policy.check("architect", "note") is True
    assert policy.check("architect", "assignment") is False


def test_roster_with_a_star_role_accepts_any_sender_and_recipient():
    roster = Roster(roles=["*"])
    assert roster.can_send("architect", ["security-reviewer"]) is True
    assert roster.can_send("anyone-at-all", ["also-anyone", "human"]) is True


def test_roster_with_a_star_role_still_honors_an_explicit_send_restriction():
    roster = Roster(roles=["*"], send={"architect": ["pm"]})
    assert roster.can_send("architect", ["pm"]) is True
    assert roster.can_send("architect", ["lawyer"]) is False
