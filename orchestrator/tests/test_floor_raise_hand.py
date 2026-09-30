"""M2.6/M2.7: RaiseHandFloorPolicy -- pure, no I/O; `raised` is a plain fixture list, no
scorer, DB or network involved. Covers addressed-first, AGREE_PASS-never-wins, urgency
ranking with the multiplier, the queue-jump cap, the speaking-time cap (exactly at
max_consecutive_grants=2), and Converged-after-exactly-2-quiet-rounds -- the product-owner
acceptance criteria for this policy. StopRulesConfig (M2.7) replaces M2.6's RaiseHandTuning.

`urgency_boost_reasons` is a real config field (default {NEW_POINT, DISAGREE}), same as
M2.6. A 2026-09-30 regression briefly hardcoded it to exactly {NEW_POINT, DISAGREE} -- the
only two reasons that ever reach ranking -- which made every ranking-eligible candidate get
boosted equally, and multiplying a whole list by the same constant never changes its sort
order: the queue-jump cap became permanently unreachable dead code. Fixed by restoring
configurability (see raise_hand.py's module docstring and docs/decisions.md, 2026-09-30).
The jump tests below configure a strict subset (`urgency_boost_reasons={NEW_POINT}`) to
demonstrate a REAL jump through the public `next()` path -- not a hand-constructed
`boosted_ranked` list bypassing ranking -- which is exactly the regression test for this
bug: it only passes if boosting can actually change who wins.
"""
import uuid

import pytest
from acp.floor import HandRaise, Reason
from orchestrator.floor import ConversationView, Converged, Grant, RaiseHandFloorPolicy
from orchestrator.floor.raise_hand import StopRulesConfig

SESSION = uuid.uuid4()


def mk_view(**kw) -> ConversationView:
    defaults = dict(session_id=SESSION, round=1, seat_order=[])
    defaults.update(kw)
    return ConversationView(**defaults)


def hr(participant, reason, urgency, in_reply_to=None) -> HandRaise:
    return HandRaise(participant=participant, reason=reason, urgency=urgency, in_reply_to=in_reply_to)


# ---------------------------------------------------------------- defaults

def test_tuning_defaults_are_the_product_owner_confirmed_values():
    tuning = StopRulesConfig()
    assert tuning.max_rounds is None
    assert tuning.converge_after_quiet_rounds == 2
    assert tuning.stale_argument_turns == 3
    assert tuning.urgency_boost_multiplier == 1.3
    assert tuning.urgency_boost_reasons == {Reason.NEW_POINT, Reason.DISAGREE}
    assert tuning.max_consecutive_grants == 2


def test_from_mode_reads_the_modes_raw_stop_rules_dict():
    from acp.modes import ModeConfig

    mode = ModeConfig(name="m", floor_policy="raise-hand", roles={},
                       stop_rules={"max_rounds": 12, "stale_argument_turns": 5})
    tuning = StopRulesConfig.from_mode(mode)
    assert tuning.max_rounds == 12
    assert tuning.stale_argument_turns == 5
    assert tuning.converge_after_quiet_rounds == 2  # unset key falls back to the default


def test_from_mode_with_no_stop_rules_block_uses_all_defaults():
    from acp.modes import ModeConfig

    mode = ModeConfig(name="m", floor_policy="raise-hand", roles={})
    assert StopRulesConfig.from_mode(mode) == StopRulesConfig()


def test_from_mode_can_configure_a_strict_subset_of_boost_reasons():
    from acp.modes import ModeConfig

    mode = ModeConfig(name="m", floor_policy="raise-hand", roles={},
                       stop_rules={"urgency_boost_reasons": ["new_point"]})
    tuning = StopRulesConfig.from_mode(mode)
    assert tuning.urgency_boost_reasons == {Reason.NEW_POINT}


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

def test_higher_urgency_wins_among_boost_eligible_candidates():
    """With the default config, NEW_POINT and DISAGREE are both boost-eligible, so the
    multiplier scales them equally here -- raw urgency still decides the winner."""
    raised = [hr("low", Reason.NEW_POINT, 0.3), hr("high", Reason.DISAGREE, 0.7)]
    decision = RaiseHandFloorPolicy().next(mk_view(), raised)
    assert decision == Grant(participant="high", reason="disagree")


def test_boosted_urgency_can_outrank_a_higher_raw_urgency_within_the_multipliers_math():
    """0.5 * 1.3 = 0.65 > 0.6 (unboosted): with a mode that boosts only NEW_POINT, the
    NEW_POINT outranks the DISAGREE only because the boosted math actually clears it, not
    arbitrarily. This is the real regression test for the 2026-09-30 bug: it only passes if
    RaiseHandFloorPolicy can still produce a genuine boost-driven ranking change."""
    tuning = StopRulesConfig(urgency_boost_reasons={Reason.NEW_POINT})
    raised = [hr("new", Reason.NEW_POINT, 0.5), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy(tuning).next(mk_view(), raised)
    assert decision == Grant(participant="new", reason="new_point")


def test_boost_does_not_flip_the_ranking_when_the_math_does_not_clear_it():
    """0.4 * 1.3 = 0.52, still below 0.6: the boost isn't enough here, so DISAGREE wins."""
    tuning = StopRulesConfig(urgency_boost_reasons={Reason.NEW_POINT})
    raised = [hr("new", Reason.NEW_POINT, 0.4), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy(tuning).next(mk_view(), raised)
    assert decision == Grant(participant="dis", reason="disagree")


def test_effective_urgency_applies_the_boost_multiplier():
    policy = RaiseHandFloorPolicy(StopRulesConfig(urgency_boost_multiplier=1.3))
    assert policy._effective_urgency(hr("a", Reason.NEW_POINT, 0.5)) == pytest.approx(0.65)
    assert policy._effective_urgency(hr("a", Reason.DISAGREE, 0.5)) == pytest.approx(0.65)


def test_effective_urgency_does_not_boost_addressed_or_agree_pass():
    policy = RaiseHandFloorPolicy(StopRulesConfig(urgency_boost_multiplier=1.3))
    assert policy._effective_urgency(hr("a", Reason.ADDRESSED, 0.5)) == 0.5
    assert policy._effective_urgency(hr("a", Reason.AGREE_PASS, 0.5)) == 0.5


def test_with_default_config_boosting_never_changes_relative_order_between_new_point_and_disagree():
    """Documents the (correct, expected) inert-by-default behavior: since both
    ranking-eligible reasons are boosted equally by default, the boosted ranking always
    equals the plain-urgency ranking. This is NOT the bug -- the bug was making this the
    *only* possible outcome, unconfigurably. A mode can still opt into a strict subset
    (see test_boosted_urgency_can_outrank_a_higher_raw_urgency_within_the_multipliers_math)."""
    raised = [hr("new", Reason.NEW_POINT, 0.5), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy().next(mk_view(), raised)
    assert decision == Grant(participant="dis", reason="disagree")  # plain-urgency leader wins


# ---------------------------------------------------------------- queue-jump cap

def test_a_real_jump_is_allowed_once_per_round():
    tuning = StopRulesConfig(urgency_boost_reasons={Reason.NEW_POINT})
    raised = [hr("new", Reason.NEW_POINT, 0.5), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy(tuning).next(mk_view(queue_jumped_this_round=False), raised)
    assert decision == Grant(participant="new", reason="new_point")  # the jump succeeds


def test_a_second_jump_this_round_falls_back_to_plain_urgency_ranking():
    tuning = StopRulesConfig(urgency_boost_reasons={Reason.NEW_POINT})
    raised = [hr("new", Reason.NEW_POINT, 0.5), hr("dis", Reason.DISAGREE, 0.6)]
    decision = RaiseHandFloorPolicy(tuning).next(mk_view(queue_jumped_this_round=True), raised)
    assert decision == Grant(participant="dis", reason="disagree")  # plain-urgency leader, no second jump


def test_apply_queue_jump_cap_is_a_noop_when_the_leader_did_not_change():
    policy = RaiseHandFloorPolicy()
    candidates = [hr("dis", Reason.DISAGREE, 0.6), hr("new", Reason.NEW_POINT, 0.5)]
    boosted_ranked = [candidates[0], candidates[1]]  # same leader as plain ranking -- not a jump
    ordered = policy._apply_queue_jump_cap(mk_view(queue_jumped_this_round=True), candidates, boosted_ranked)
    assert ordered == boosted_ranked  # cap only applies to an actual jump


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
