"""M2.6: RoundRobinFloorPolicy / next_seat -- pure, no I/O. Exhaustive over
seat_order/last_speaker/muted combinations."""
import uuid

import pytest

from orchestrator.floor import ConversationView, Grant, RoundRobinFloorPolicy
from orchestrator.floor.round_robin import next_seat

SEATS = ["coordinator", "developer", "qa"]


def test_no_last_speaker_returns_the_first_eligible_seat():
    assert next_seat(SEATS, None, set()) == "coordinator"


def test_advances_to_the_next_seat_in_order():
    assert next_seat(SEATS, "coordinator", set()) == "developer"
    assert next_seat(SEATS, "developer", set()) == "qa"


def test_wraps_around_from_the_last_seat_to_the_first():
    assert next_seat(SEATS, "qa", set()) == "coordinator"


def test_muted_seats_are_skipped():
    assert next_seat(SEATS, "coordinator", {"developer"}) == "qa"


def test_last_speaker_no_longer_eligible_falls_back_to_the_first_eligible_seat():
    """last_speaker was muted since its turn -- not an error, just restart the rotation."""
    assert next_seat(SEATS, "developer", {"developer"}) == "coordinator"


def test_all_seats_muted_raises():
    with pytest.raises(ValueError):
        next_seat(SEATS, None, {"coordinator", "developer", "qa"})


def test_single_eligible_seat_wraps_to_itself():
    assert next_seat(SEATS, "coordinator", {"developer", "qa"}) == "coordinator"


def test_policy_grants_the_next_seat_and_ignores_hand_raises():
    view = ConversationView(session_id=uuid.uuid4(), round=3, seat_order=SEATS, last_speaker="coordinator")
    decision = RoundRobinFloorPolicy().next(view, raised=[])
    assert decision == Grant(participant="developer")
