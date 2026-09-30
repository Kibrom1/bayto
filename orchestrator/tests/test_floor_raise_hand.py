"""M2.6/M2.7: RaiseHandFloorPolicy -- pure, no I/O; `raised` is a plain fixture list, no
scorer, DB or network involved. Covers addressed-first, AGREE_PASS-never-wins, urgency
ranking with the multiplier, the queue-jump cap, the speaking-time cap (exactly at
max_consecutive_grants=2), and Converged-after-exactly-2-quiet-rounds -- the product-owner
acceptance criteria for this policy. StopRulesConfig (M2.7) replaces M2.6's RaiseHandTuning
-- see raise_hand.py's module docstring for why `urgency_boost_reasons` is no longer a
config field (hardcoded to {NEW_POINT, DISAGREE} instead), which some tests below account
for by exercising `_effective_urgency`/`_apply_queue_jump_cap` directly rather than only
through the public `next()` path."""
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
    """NEW_POINT and DISAGREE are both boost-eligible by default, so the multiplier scales
    them equally here -- raw urgency still decides the winner."""
    raised = [hr("low", Reason.NEW_POINT, 0.3), hr("high", Reason.DISAGREE, 0.7)]
    decision = RaiseHandFloorPolicy().next(mk_view(), raised)
    assert decision == Grant(participant="high", reason="disagree")


def test_effective_urgency_applies_the_boost_multiplier_to_new_point_and_disagree():
    """Direct test of the multiplier's actual math (0.5 * 1.3 = 0.65): ADDRESSED/AGREE_PASS
    never reach this method in practice (short-circuited/filtered in next()), but the
    boost-eligibility itself is exercised here since no realistic `raised` list can
    isolate it through the public next() path."""
    policy = RaiseHandFloorPolicy(StopRulesConfig(urgency_boost_multiplier=1.3))
    assert policy._effective_urgency(hr("a", Reason.NEW_POINT, 0.5)) == pytest.approx(0.65)
    assert policy._effective_urgency(hr("a", Reason.DISAGREE, 0.5)) == pytest.approx(0.65)


def test_effective_urgency_does_not_boost_addressed_or_agree_pass():
    policy = RaiseHandFloorPolicy(StopRulesConfig(urgency_boost_multiplier=1.3))
    assert policy._effective_urgency(hr("a", Reason.ADDRESSED, 0.5)) == 0.5
    assert policy._effective_urgency(hr("a", Reason.AGREE_PASS, 0.5)) == 0.5


# ---------------------------------------------------------------- queue-jump cap

def test_apply_queue_jump_cap_allows_a_jump_when_none_used_yet_this_round():
    policy = RaiseHandFloorPolicy()
    candidates = [hr("dis", Reason.DISAGREE, 0.6), hr("new", Reason.NEW_POINT, 0.5)]
    boosted_ranked = [candidates[1], candidates[0]]  # "new" ranked ahead of plain-urgency order
    ordered = policy._apply_queue_jump_cap(mk_view(queue_jumped_this_round=False), candidates, boosted_ranked)
    assert ordered == boosted_ranked


def test_apply_queue_jump_cap_falls_back_to_plain_ranking_when_this_rounds_jump_is_used():
    policy = RaiseHandFloorPolicy()
    candidates = [hr("dis", Reason.DISAGREE, 0.6), hr("new", Reason.NEW_POINT, 0.5)]
    boosted_ranked = [candidates[1], candidates[0]]
    ordered = policy._apply_queue_jump_cap(mk_view(queue_jumped_this_round=True), candidates, boosted_ranked)
    assert ordered == [candidates[0], candidates[1]]  # plain urgency order: dis (0.6) first


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
