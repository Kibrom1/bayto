"""M2.5: SendPolicy is a different axis than Roster.can_send -- which Envelope `kind`s a
role may emit, not who it may address. Pure dict lookup, no I/O."""
from acp.models import SendPolicy


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
