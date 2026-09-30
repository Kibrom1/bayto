"""M2.6: RaiseHandFloorPolicy -- pure, no I/O; `raised` is a plain fixture list, no scorer,
DB or network involved. Covers addressed-first, AGREE_PASS-never-wins, urgency ranking
with the multiplier, the queue-jump cap, the speaking-time cap (exactly at
max_consecutive_grants=2), and Converged-after-exactly-2-quiet-rounds -- the product-owner
acceptance criteria for this policy."""
import uuid

from acp.floor import HandRaise, Reason
from orchestrator.floor import ConversationView, Converged, Grant, RaiseHandFloorPolicy
from orchestrator.floor.raise_hand import RaiseHandTuning

SESSION = uuid.uuid4()


def mk_view(**kw) -> ConversationView:
    defaults = dict(session_id=SESSION, round=1, seat_order=[])
    defaults.update(kw)
    return ConversationView(**defaults)


def hr(participant, reason, urgency, in_reply_to=None) -> HandRaise:
    return HandRaise(participant=participant, reason=reason, urgency=urgency, in_reply_to=in_reply_to)


# ---------------------------------------------------------------- defaults

def test_tuning_defaults_are_the_product_owner_confirmed_values():
    tuning = RaiseHandTuning()
    assert tuning.urgency_boost_reasons == {Reason.NEW_POINT, Reason.DISAGREE}
    assert tuning.urgency_boost_multiplier == 1.3
    assert tuning.max_consecutive_grants == 2
    assert tuning.converge_after_quiet_rounds == 2


# ---------------------------------------------------------------- addressed-first

def test_addressed_wins_regardless_of_others_urgency():
    raised = [
        hr("loud", Reason.NEW_POINT, 0.95),
        hr("quiet", Reason.ADDRESSED, 0.05),
    ]
    decision = RaiseHandFloorPolicy().next(mk_view(), raised)
    assert decision == Grant(participant="quiet", reason="addressed")


def test_addressed_falls_through_to_the_next_addressed_entry_when_the_first_is_capped():
    raised = [hr("a", Reason.ADDRESSED, 0.5), hr("b", Reason.ADDRESSED, 0.4)]
    view = mk_view(grants_this_window={"a": 2})
    decision = RaiseHandFloorPolicy().next(view, raised)
    assert decision == Grant(participant="b", reason="addressed")


# ---------------------------------------------------------------- AGREE_PASS never wins

def test_agree_pass_alone_never_gets_the_floor():
    raised = [hr("a", Reason.AGREE_PASS, 0.9)]
    decision = RaiseHandFloorPolicy().next(mk_view(consecutive_quiet_rounds=0), raised)
    assert decision is None  # treated as a quiet round, not a grant


def test_agree_pass_is_ignored_even_when_mixed_with_a_real_candidate():
    raised = [hr("passer", Reason.AGREE_PASS, 0.99), hr("speaker", Reason.NEW_POINT, 0.1)]
    decision = RaiseHandFloorPolicy().next(mk_view(), raised)
    assert decision == Grant(participant="speaker", reason="new_point")


# ---------------------------------------------------------------- urgency ranking + multiplier

def test_boosted_urgency_can_outrank_a_higher_raw_urgency_within_the_multipliers_math():
    """0.5 * 1.3 = 0.65 > 0.6 (unboosted): the NEW_POINT outranks the DISAGREE only because
    the boosted math actually clears it, not arbitrarily."""
    tuning = RaiseHandTuning(urgency_boost_reasons={Reason.NEW_POINT})  # DISAGREE unboosted here
    raised = [hr("new", Reason.NEW_POINT, 0.5), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy(tuning).next(mk_view(), raised)
    assert decision == Grant(participant="new", reason="new_point")


def test_boost_does_not_flip_the_ranking_when_the_math_does_not_clear_it():
    """0.4 * 1.3 = 0.52, still below 0.6: the boost isn't enough here, so DISAGREE wins."""
    tuning = RaiseHandTuning(urgency_boost_reasons={Reason.NEW_POINT})
    raised = [hr("new", Reason.NEW_POINT, 0.4), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy(tuning).next(mk_view(), raised)
    assert decision == Grant(participant="dis", reason="disagree")


# ---------------------------------------------------------------- queue-jump cap

def test_a_queue_jump_is_allowed_once_per_round():
    tuning = RaiseHandTuning(urgency_boost_reasons={Reason.NEW_POINT})
    raised = [hr("new", Reason.NEW_POINT, 0.5), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy(tuning).next(mk_view(queue_jumped_this_round=False), raised)
    assert decision == Grant(participant="new", reason="new_point")


def test_a_second_queue_jump_this_round_falls_back_to_plain_urgency_ranking():
    tuning = RaiseHandTuning(urgency_boost_reasons={Reason.NEW_POINT})
    raised = [hr("new", Reason.NEW_POINT, 0.5), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy(tuning).next(mk_view(queue_jumped_this_round=True), raised)
    assert decision == Grant(participant="dis", reason="disagree")  # plain urgency leader


# ---------------------------------------------------------------- speaking-time cap

def test_leader_at_the_cap_is_skipped_for_the_next_ranked_candidate():
    raised = [hr("leader", Reason.NEW_POINT, 0.9), hr("second", Reason.DISAGREE, 0.1)]
    view = mk_view(grants_this_window={"leader": 2})  # already at max_consecutive_grants
    decision = RaiseHandFloorPolicy().next(view, raised)
    assert decision == Grant(participant="second", reason="disagree")


def test_one_grant_below_the_cap_is_still_eligible():
    raised = [hr("leader", Reason.NEW_POINT, 0.9)]
    view = mk_view(grants_this_window={"leader": 1})  # one below max_consecutive_grants=2
    decision = RaiseHandFloorPolicy().next(view, raised)
    assert decision == Grant(participant="leader", reason="new_point")


def test_everyone_eligible_capped_returns_none():
    raised = [hr("a", Reason.NEW_POINT, 0.9), hr("b", Reason.DISAGREE, 0.5)]
    view = mk_view(grants_this_window={"a": 2, "b": 2})
    decision = RaiseHandFloorPolicy().next(view, raised)
    assert decision is None


# ---------------------------------------------------------------- convergence

def test_converges_exactly_at_the_configured_quiet_round_count():
    view = mk_view(consecutive_quiet_rounds=1)  # +1 this round = 2 = converge_after_quiet_rounds
    decision = RaiseHandFloorPolicy().next(view, raised=[])
    assert decision == Converged(rounds_quiet=2)


def test_does_not_converge_one_round_early():
    view = mk_view(consecutive_quiet_rounds=0)  # +1 this round = 1, below the default 2
    decision = RaiseHandFloorPolicy().next(view, raised=[])
    assert decision is None


def test_still_converges_past_the_threshold():
    view = mk_view(consecutive_quiet_rounds=5)
    decision = RaiseHandFloorPolicy().next(view, raised=[])
    assert decision == Converged(rounds_quiet=6)
