"""M2.5: `is_visible_to` is the pure predicate behind Conversation.transcript() -- the
deferred M1.1b visibility enforcement (recipients/moderator messages must actually be
hidden from everyone but their intended reader; only `all` was enforced before this).
Exhaustive over all/recipients/moderator x {sender, in-to, moderator, stranger}."""
import pytest

from acp.core import is_visible_to

FROM = "advocate"
TO = ["skeptic", "moderator"]


@pytest.mark.parametrize("viewer", ["advocate", "skeptic", "moderator", "stranger"])
def test_all_visibility_is_visible_to_everyone(viewer):
    assert is_visible_to("all", FROM, TO, viewer, moderator_role="moderator") is True


def test_recipients_visible_to_sender_and_each_recipient():
    assert is_visible_to("recipients", FROM, TO, "advocate", moderator_role="moderator") is True
    assert is_visible_to("recipients", FROM, TO, "skeptic", moderator_role="moderator") is True
    assert is_visible_to("recipients", FROM, TO, "moderator", moderator_role="moderator") is True


def test_recipients_hidden_from_a_stranger():
    assert is_visible_to("recipients", FROM, TO, "stranger", moderator_role="moderator") is False


def test_moderator_visible_to_sender_and_the_moderator_role():
    assert is_visible_to("moderator", FROM, TO, "advocate", moderator_role="moderator") is True
    assert is_visible_to("moderator", FROM, TO, "moderator", moderator_role="moderator") is True


def test_moderator_hidden_from_a_recipient_that_isnt_the_moderator():
    assert is_visible_to("moderator", FROM, TO, "skeptic", moderator_role="moderator") is False


def test_moderator_hidden_from_a_stranger():
    assert is_visible_to("moderator", FROM, TO, "stranger", moderator_role="moderator") is False


def test_moderator_visibility_degrades_safely_when_no_moderator_role_is_set():
    """Unset moderator (mode declared none) matches nobody but the sender -- it must not
    crash and must not leak to everyone."""
    assert is_visible_to("moderator", FROM, TO, "advocate", moderator_role=None) is True
    assert is_visible_to("moderator", FROM, TO, "skeptic", moderator_role=None) is False
    assert is_visible_to("moderator", FROM, TO, "moderator", moderator_role=None) is False


def test_unknown_visibility_raises():
    with pytest.raises(ValueError):
        is_visible_to("room", FROM, TO, "advocate", moderator_role="moderator")
