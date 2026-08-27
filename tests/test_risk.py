from options_agent.config import Settings
from options_agent.risk.account import check_account_guardrails
from options_agent.risk.limits import RiskLimits, review_proposal
from options_agent.strategy.base import ProposedLeg, ProposedTrade

LIMITS = RiskLimits(
    max_contracts_per_order=5,
    max_order_notional_usd=2_500,
    max_equity_notional_usd=80_000,
)


def _put(symbol: str = "SPY260918P00600000", side: str = "buy") -> ProposedLeg:
    return ProposedLeg(symbol=symbol, side=side)


def test_defined_risk_long_put_is_approved() -> None:
    proposal = ProposedTrade(
        qty=2,
        legs=[_put()],
        estimated_cost_usd=900.0,
        max_loss_usd=900.0,
    )
    decision = review_proposal(proposal, LIMITS)
    assert decision.allowed
    assert decision.reasons == []


def test_oversized_qty_is_blocked() -> None:
    proposal = ProposedTrade(qty=50, legs=[_put()], estimated_cost_usd=100.0)
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("exceeds max" in reason for reason in decision.reasons)


def test_naked_short_is_blocked() -> None:
    proposal = ProposedTrade(qty=1, legs=[_put(side="sell")], estimated_cost_usd=100.0)
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("naked short" in reason for reason in decision.reasons)


def test_missing_cost_estimate_is_blocked() -> None:
    proposal = ProposedTrade(qty=1, legs=[_put()])
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("cost estimate" in reason for reason in decision.reasons)


def test_too_expensive_is_blocked() -> None:
    proposal = ProposedTrade(qty=1, legs=[_put()], estimated_cost_usd=9_999.0)
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed


def test_skip_proposal_never_reaches_the_broker() -> None:
    decision = review_proposal(ProposedTrade(skip=True, rationale="nothing to do"), LIMITS)
    assert not decision.allowed


def test_collar_with_covering_shares_is_approved() -> None:
    proposal = ProposedTrade(
        qty=1,
        covering_shares=100,
        legs=[
            ProposedLeg(symbol="SPY260918P00750000", side="buy"),
            ProposedLeg(symbol="SPY260918C00790000", side="sell"),
        ],
        estimated_cost_usd=140.0,
        max_loss_usd=2_140.0,
    )
    assert review_proposal(proposal, LIMITS).allowed


def test_collar_without_covering_shares_is_blocked() -> None:
    proposal = ProposedTrade(
        qty=1,
        covering_shares=0,
        legs=[
            ProposedLeg(symbol="SPY260918P00750000", side="buy"),
            ProposedLeg(symbol="SPY260918C00790000", side="sell"),
        ],
        estimated_cost_usd=140.0,
        max_loss_usd=2_140.0,
    )
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("covering shares" in reason for reason in decision.reasons)


def test_collar_max_loss_above_cap_is_blocked() -> None:
    proposal = ProposedTrade(
        qty=1,
        covering_shares=100,
        legs=[
            ProposedLeg(symbol="SPY260918P00750000", side="buy"),
            ProposedLeg(symbol="SPY260918C00790000", side="sell"),
        ],
        estimated_cost_usd=140.0,
        max_loss_usd=4_200.0,
    )
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("max loss" in reason for reason in decision.reasons)


def test_credit_collar_cost_is_not_treated_as_missing() -> None:
    proposal = ProposedTrade(
        qty=1,
        covering_shares=100,
        legs=[
            ProposedLeg(symbol="SPY260918P00740000", side="buy"),
            ProposedLeg(symbol="SPY260918C00785000", side="sell"),
        ],
        estimated_cost_usd=-25.0,
        max_loss_usd=2_000.0,
    )
    assert review_proposal(proposal, LIMITS).allowed


def test_equity_seed_uses_the_equity_notional_cap() -> None:
    proposal = ProposedTrade(
        qty=100,
        kind="equity",
        legs=[ProposedLeg(symbol="SPY", side="buy")],
        estimated_cost_usd=77_000.0,
        max_loss_usd=77_000.0,
    )
    assert review_proposal(proposal, LIMITS).allowed


def test_equity_seed_above_cap_is_blocked() -> None:
    proposal = ProposedTrade(
        qty=100,
        kind="equity",
        legs=[ProposedLeg(symbol="SPY", side="buy")],
        estimated_cost_usd=90_000.0,
        max_loss_usd=90_000.0,
    )
    decision = review_proposal(proposal, LIMITS)
    assert not decision.allowed
    assert any("equity notional" in reason for reason in decision.reasons)


def test_account_guard_stops_on_daily_loss() -> None:
    settings = Settings(max_daily_loss_usd=1_000, min_equity_usd=50_000)
    result = check_account_guardrails(equity=97_000, last_equity=100_000, settings=settings)
    assert not result.trading_allowed
    assert result.day_pl == -3_000


def test_account_guard_allows_normal_day() -> None:
    settings = Settings(max_daily_loss_usd=1_000, min_equity_usd=50_000)
    result = check_account_guardrails(equity=100_200, last_equity=100_000, settings=settings)
    assert result.trading_allowed
