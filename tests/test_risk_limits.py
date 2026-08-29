"""The gate: defined-risk proof, exit validation and post-fill portfolio budgets."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TODAY, FakePosition, occ_symbol

from vrp_engine.config import Settings
from vrp_engine.risk.limits import (
    MAX_LEGS_PER_TICKET,
    RiskLimits,
    limits_from_settings,
    review_proposal,
    uncovered_short_legs,
)
from vrp_engine.risk.portfolio import build_portfolio_risk
from vrp_engine.strategy.base import (
    ACTION_CLOSE,
    ACTION_OPEN,
    ACTION_UNWIND,
    ProposedLeg,
    ProposedTrade,
    TradeAnalytics,
)

EXPIRY = TODAY + timedelta(days=7)
LIMITS = RiskLimits(
    equity=100_000.0,
    max_contracts_per_order=25,
    max_trade_loss_usd=4_500.0,
    max_aggregate_loss_usd=45_000.0,
    max_underlying_loss_usd=12_000.0,
    max_bucket_loss_usd=30_000.0,
    max_stress_loss_usd=18_000.0,
    max_net_delta_usd=25_000.0,
)


def _leg(kind: str, strike: float, side: str, intent: str, *, underlying: str = "SPY"):
    return ProposedLeg(
        symbol=occ_symbol(underlying, EXPIRY, kind, strike),
        side=side,
        position_intent=intent,
    )


def _put_credit_spread(*, qty: int = 1, cost: float = 800.0) -> ProposedTrade:
    return ProposedTrade(
        qty=qty,
        legs=[
            _leg("put", 490, "sell", "sell_to_open"),
            _leg("put", 485, "buy", "buy_to_open"),
        ],
        rationale="short vol",
        action=ACTION_OPEN,
        limit_price=2.0,
        estimated_cost_usd=cost,
        max_loss_usd=cost,
        analytics=TradeAnalytics(underlying="SPY", structure_kind="put_credit_spread"),
    )


def _exit_ticket(*, qty: int = 1) -> ProposedTrade:
    return ProposedTrade(
        qty=qty,
        legs=[
            _leg("put", 490, "buy", "buy_to_close"),
            _leg("put", 485, "sell", "sell_to_close"),
        ],
        rationale="profit target",
        action=ACTION_CLOSE,
        limit_price=0.5,
    )


HELD = {
    occ_symbol("SPY", EXPIRY, "put", 490): -1.0,
    occ_symbol("SPY", EXPIRY, "put", 485): 1.0,
}


# --- limits from settings ---------------------------------------------------


def test_limits_resolve_percentages_against_equity():
    settings = Settings(
        alpaca_api_key="k",
        alpaca_secret_key="s",
        start_equity_usd=100_000.0,
        max_trade_loss_pct=0.045,
        risk_budget_pct=0.45,
    )
    limits = limits_from_settings(settings, equity=50_000.0)
    assert limits.max_trade_loss_usd == pytest.approx(2_250.0)
    assert limits.max_aggregate_loss_usd == pytest.approx(22_500.0)


def test_limits_fall_back_to_the_starting_equity():
    settings = Settings(alpaca_api_key="k", alpaca_secret_key="s", start_equity_usd=80_000.0)
    assert limits_from_settings(settings, equity=None).equity == pytest.approx(80_000.0)


def test_naked_shorts_are_never_allowed_by_default():
    assert RiskLimits().allow_naked_short is False


# --- defined-risk proof -----------------------------------------------------


def test_a_covered_put_spread_has_no_uncovered_legs():
    assert uncovered_short_legs(_put_credit_spread()) == []


def test_a_lone_short_put_is_uncovered():
    proposal = ProposedTrade(legs=[_leg("put", 490, "sell", "sell_to_open")], qty=1)
    assert len(uncovered_short_legs(proposal)) == 1


def test_a_lone_short_call_is_uncovered():
    proposal = ProposedTrade(legs=[_leg("call", 510, "sell", "sell_to_open")], qty=1)
    assert len(uncovered_short_legs(proposal)) == 1


def test_a_long_put_above_the_short_does_not_cover_it():
    # A protective put must sit *below* the one we sold.
    proposal = ProposedTrade(
        qty=1,
        legs=[
            _leg("put", 490, "sell", "sell_to_open"),
            _leg("put", 495, "buy", "buy_to_open"),
        ],
    )
    assert uncovered_short_legs(proposal)


def test_a_long_call_below_the_short_does_not_cover_it():
    proposal = ProposedTrade(
        qty=1,
        legs=[
            _leg("call", 510, "sell", "sell_to_open"),
            _leg("call", 505, "buy", "buy_to_open"),
        ],
    )
    assert uncovered_short_legs(proposal)


def test_a_long_put_does_not_cover_a_short_call():
    proposal = ProposedTrade(
        qty=1,
        legs=[
            _leg("call", 510, "sell", "sell_to_open"),
            _leg("put", 490, "buy", "buy_to_open"),
        ],
    )
    assert uncovered_short_legs(proposal)


def test_a_full_iron_condor_is_fully_covered():
    proposal = ProposedTrade(
        qty=2,
        legs=[
            _leg("put", 490, "sell", "sell_to_open"),
            _leg("put", 485, "buy", "buy_to_open"),
            _leg("call", 510, "sell", "sell_to_open"),
            _leg("call", 515, "buy", "buy_to_open"),
        ],
    )
    assert uncovered_short_legs(proposal) == []


def test_one_long_leg_cannot_cover_two_short_ones():
    proposal = ProposedTrade(
        qty=1,
        legs=[
            ProposedLeg(
                symbol=occ_symbol("SPY", EXPIRY, "put", 490),
                side="sell",
                ratio_qty=2,
                position_intent="sell_to_open",
            ),
            _leg("put", 485, "buy", "buy_to_open"),
        ],
    )
    assert uncovered_short_legs(proposal)


def test_coverage_is_checked_per_expiry():
    later = TODAY + timedelta(days=21)
    proposal = ProposedTrade(
        qty=1,
        legs=[
            _leg("put", 490, "sell", "sell_to_open"),
            ProposedLeg(
                symbol=occ_symbol("SPY", later, "put", 485),
                side="buy",
                position_intent="buy_to_open",
            ),
        ],
    )
    assert uncovered_short_legs(proposal)


def test_an_unparseable_symbol_is_ignored_rather_than_crashing():
    proposal = ProposedTrade(
        qty=1, legs=[ProposedLeg(symbol="NOT-AN-OPTION", side="sell")]
    )
    assert uncovered_short_legs(proposal) == []


# --- opening tickets --------------------------------------------------------


def test_a_well_formed_credit_spread_is_approved():
    decision = review_proposal(_put_credit_spread(), LIMITS)
    assert decision.allowed
    assert decision.summary() == "approved"


def test_the_approval_records_the_coverage_check():
    decision = review_proposal(_put_credit_spread(), LIMITS)
    assert any("covered by a long leg" in check for check in decision.checks)


def test_a_skip_flag_is_rejected():
    proposal = _put_credit_spread()
    proposal.skip = True
    assert not review_proposal(proposal, LIMITS).allowed


def test_a_proposal_without_legs_is_rejected():
    assert not review_proposal(ProposedTrade(qty=1), LIMITS).allowed


def test_more_than_four_legs_is_rejected():
    legs = [
        _leg("put", 490, "sell", "sell_to_open"),
        _leg("put", 485, "buy", "buy_to_open"),
        _leg("call", 510, "sell", "sell_to_open"),
        _leg("call", 515, "buy", "buy_to_open"),
        _leg("call", 520, "buy", "buy_to_open"),
    ]
    proposal = ProposedTrade(qty=1, legs=legs, estimated_cost_usd=100.0, max_loss_usd=100.0)
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any(str(MAX_LEGS_PER_TICKET) in reason for reason in decision.reasons)


def test_zero_quantity_is_rejected():
    proposal = _put_credit_spread(qty=0)
    assert not review_proposal(proposal, LIMITS).allowed


def test_quantity_above_the_per_order_cap_is_rejected():
    proposal = _put_credit_spread(qty=100)
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("per order" in reason for reason in decision.reasons)


def test_a_missing_cost_estimate_is_rejected():
    proposal = _put_credit_spread()
    proposal.estimated_cost_usd = None
    assert not review_proposal(proposal, LIMITS).allowed


def test_collateral_above_the_per_trade_cap_is_rejected():
    proposal = _put_credit_spread(cost=9_000.0)
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("per-trade cap" in reason for reason in decision.reasons)


def test_a_missing_max_loss_is_rejected():
    proposal = _put_credit_spread()
    proposal.max_loss_usd = None
    assert not review_proposal(proposal, LIMITS).allowed


def test_a_stated_max_loss_above_the_cap_is_rejected():
    proposal = _put_credit_spread()
    proposal.max_loss_usd = 20_000.0
    assert not review_proposal(proposal, LIMITS).allowed


def test_a_naked_short_ticket_is_blocked():
    proposal = ProposedTrade(
        qty=1,
        legs=[_leg("put", 490, "sell", "sell_to_open")],
        estimated_cost_usd=100.0,
        max_loss_usd=100.0,
        action=ACTION_OPEN,
    )
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("naked short blocked" in reason for reason in decision.reasons)


def test_a_naked_short_stays_blocked_even_within_every_budget():
    tiny = RiskLimits(max_trade_loss_usd=1_000_000.0, max_aggregate_loss_usd=1_000_000.0)
    proposal = ProposedTrade(
        qty=1,
        legs=[_leg("call", 510, "sell", "sell_to_open")],
        estimated_cost_usd=1.0,
        max_loss_usd=1.0,
    )
    assert not review_proposal(proposal, tiny).allowed


# --- closing tickets --------------------------------------------------------


def test_a_matching_exit_is_approved():
    decision = review_proposal(_exit_ticket(), LIMITS, open_contracts=HELD)
    assert decision.allowed
    assert any("exit matches" in check for check in decision.checks)


def test_an_exit_cannot_be_reviewed_without_the_position_list():
    decision = review_proposal(_exit_ticket(), LIMITS, open_contracts=None)
    assert not decision.allowed
    assert any("without the current position list" in reason for reason in decision.reasons)


def test_closing_something_we_do_not_hold_is_rejected():
    decision = review_proposal(_exit_ticket(), LIMITS, open_contracts={})
    assert not decision.allowed


def test_buying_to_close_a_long_position_is_rejected():
    held = {
        occ_symbol("SPY", EXPIRY, "put", 490): 1.0,
        occ_symbol("SPY", EXPIRY, "put", 485): 1.0,
    }
    decision = review_proposal(_exit_ticket(), LIMITS, open_contracts=held)
    assert not decision.allowed
    assert any("not short" in reason for reason in decision.reasons)


def test_selling_to_close_a_short_position_is_rejected():
    held = {
        occ_symbol("SPY", EXPIRY, "put", 490): -1.0,
        occ_symbol("SPY", EXPIRY, "put", 485): -1.0,
    }
    decision = review_proposal(_exit_ticket(), LIMITS, open_contracts=held)
    assert not decision.allowed
    assert any("not long" in reason for reason in decision.reasons)


def test_closing_more_than_we_hold_is_rejected():
    decision = review_proposal(_exit_ticket(qty=5), LIMITS, open_contracts=HELD)
    assert not decision.allowed
    assert any("only 1 are open" in reason for reason in decision.reasons)


def test_an_exit_is_not_subject_to_the_cost_cap():
    # Closing reduces risk, so the collateral test that guards entries must not apply.
    proposal = _exit_ticket()
    proposal.estimated_cost_usd = 99_999.0
    assert review_proposal(proposal, LIMITS, open_contracts=HELD).allowed


# --- equity tickets ---------------------------------------------------------


def test_selling_inherited_shares_is_approved():
    proposal = ProposedTrade(
        qty=100,
        kind="equity",
        legs=[ProposedLeg(symbol="SPY", side="sell")],
        action=ACTION_UNWIND,
    )
    decision = review_proposal(proposal, LIMITS)
    assert decision.allowed
    assert any("reducing inherited exposure" in check for check in decision.checks)


def test_buying_shares_is_rejected():
    proposal = ProposedTrade(
        qty=100, kind="equity", legs=[ProposedLeg(symbol="SPY", side="buy")]
    )
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("buying shares" in reason for reason in decision.reasons)


def test_an_equity_ticket_without_a_symbol_is_rejected():
    assert not review_proposal(ProposedTrade(qty=100, kind="equity"), LIMITS).allowed


def test_an_equity_ticket_with_zero_shares_is_rejected():
    proposal = ProposedTrade(
        qty=0, kind="equity", legs=[ProposedLeg(symbol="SPY", side="sell")]
    )
    assert not review_proposal(proposal, LIMITS).allowed


# --- post-fill portfolio budgets -------------------------------------------


def _book(n_spreads: int, *, underlying: str = "SPY", vol: float | None = None):
    """A book of `n_spreads` five-wide put credit spreads, marked at a 2.00 credit."""
    positions = []
    for i in range(n_spreads):
        short_strike = 490.0 - i
        positions.append(
            FakePosition(
                occ_symbol(underlying, EXPIRY, "put", short_strike), -10.0, current_price=3.0
            )
        )
        positions.append(
            FakePosition(
                occ_symbol(underlying, EXPIRY, "put", short_strike - 5), 10.0, current_price=1.0
            )
        )
    return build_portfolio_risk(
        equity=100_000.0,
        positions=positions,
        spots={underlying: 500.0},
        vols={underlying: vol} if vol else None,
    )


def test_a_small_post_trade_book_passes_every_budget():
    decision = review_proposal(_put_credit_spread(), LIMITS, post_trade=_book(1), bucket="index")
    assert decision.allowed
    assert any("worst case" in check for check in decision.checks)


def test_an_oversized_aggregate_worst_case_is_rejected():
    decision = review_proposal(_put_credit_spread(), LIMITS, post_trade=_book(20))
    assert not decision.allowed
    assert any("aggregate budget" in reason for reason in decision.reasons)


def test_the_per_underlying_cap_can_reject_a_ticket():
    limits = LIMITS.model_copy(update={"max_underlying_loss_usd": 1_000.0})
    decision = review_proposal(_put_credit_spread(), limits, post_trade=_book(1))
    assert not decision.allowed
    assert any("per-underlying" in reason for reason in decision.reasons)


def test_the_bucket_cap_can_reject_a_ticket():
    limits = LIMITS.model_copy(update={"max_bucket_loss_usd": 500.0})
    decision = review_proposal(
        _put_credit_spread(), limits, post_trade=_book(1), bucket="SPY"
    )
    assert not decision.allowed
    assert any("bucket" in reason for reason in decision.reasons)


def test_the_stress_ceiling_can_reject_a_ticket():
    limits = LIMITS.model_copy(update={"max_stress_loss_usd": 100.0})
    decision = review_proposal(
        _put_credit_spread(), limits, post_trade=_book(2, vol=0.35)
    )
    assert not decision.allowed
    assert any("stress" in reason for reason in decision.reasons)


def test_the_delta_budget_can_reject_a_ticket():
    positions = [FakePosition("SPY", 1_000.0, asset_class="us_equity", current_price=500.0)]
    post = build_portfolio_risk(
        equity=100_000.0, positions=positions, spots={"SPY": 500.0}
    )
    decision = review_proposal(_put_credit_spread(), LIMITS, post_trade=post)
    assert not decision.allowed
    assert any("beta-weighted delta" in reason for reason in decision.reasons)


def test_without_a_post_trade_book_only_the_ticket_is_checked():
    decision = review_proposal(_put_credit_spread(), LIMITS, post_trade=None)
    assert decision.allowed
    assert not any("worst case" in check for check in decision.checks)


def test_a_rejection_summary_lists_the_reasons():
    decision = review_proposal(_put_credit_spread(cost=99_000.0), LIMITS)
    assert not decision.allowed
    assert "per-trade cap" in decision.summary()
