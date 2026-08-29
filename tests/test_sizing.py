"""Sizing: fractional Kelly first, then every budget in turn, with the binder recorded."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TODAY, build_chain

from vrp_engine.config import Settings
from vrp_engine.risk.portfolio import Exposure
from vrp_engine.strategy.pricing import evaluate_structure
from vrp_engine.strategy.signals import STANCE_SELL_VOL, TREND_FLAT, UnderlyingSignal
from vrp_engine.strategy.sizing import (
    BUYING_POWER_UTILISATION,
    OPEN_INTEREST_DIVISOR,
    RiskBudget,
    size_structure,
)
from vrp_engine.strategy.structures import SelectionParams, credit_spread_variants

EXPIRY = TODAY + timedelta(days=7)
PARAMS = SelectionParams(max_spread_fraction=0.10, min_open_interest=0)
GENEROUS = 10_000_000.0


def _evaluation(*, implied_vol: float = 0.32, open_interest: int | None = 100_000):
    chain = build_chain(
        spot=500.0, expiration=EXPIRY, implied_vol=implied_vol, open_interest=open_interest
    )
    structure = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )[-1]
    signal = UnderlyingSignal(
        symbol="SPY",
        spot=500.0,
        expiration=EXPIRY,
        horizon_days=7,
        realized_vol=0.15,
        implied_vol=implied_vol,
        vrp=implied_vol - 0.15,
        vrp_z=(implied_vol - 0.15) / 0.15,
        trend=TREND_FLAT,
        stance=STANCE_SELL_VOL,
    )
    return evaluate_structure(structure, signal, today=TODAY, min_edge=0.0, min_wedge=0.0)


def _budget(**overrides) -> RiskBudget:
    defaults = {
        "equity": 100_000.0,
        "kelly_haircut": 0.35,
        "risk_budget_pct": 0.45,
        "max_trade_loss_pct": 0.045,
        "max_underlying_loss_pct": 0.12,
        "max_bucket_loss_pct": 0.30,
        "max_contracts_per_order": 25,
    }
    defaults.update(overrides)
    return RiskBudget(**defaults)


def _size(budget: RiskBudget, exposure: Exposure | None = None, **kwargs):
    return size_structure(
        _evaluation(),
        budget=budget,
        exposure=exposure or Exposure(),
        bucket=kwargs.pop("bucket", "index"),
        options_buying_power=kwargs.pop("options_buying_power", GENEROUS),
    )


# --- budget resolution ------------------------------------------------------


def test_budget_resolves_percentages_into_dollars():
    budget = _budget()
    assert budget.aggregate_cap_usd == pytest.approx(45_000.0)
    assert budget.trade_cap_usd == pytest.approx(4_500.0)
    assert budget.underlying_cap_usd == pytest.approx(12_000.0)
    assert budget.bucket_cap_usd == pytest.approx(30_000.0)


def test_budget_from_settings_uses_the_configured_percentages():
    settings = Settings(
        alpaca_api_key="k",
        alpaca_secret_key="s",
        risk_budget_pct=0.30,
        max_trade_loss_pct=0.02,
        kelly_fraction=0.20,
    )
    budget = RiskBudget.from_settings(settings, equity=50_000.0)
    assert budget.aggregate_cap_usd == pytest.approx(15_000.0)
    assert budget.trade_cap_usd == pytest.approx(1_000.0)
    assert budget.kelly_haircut == pytest.approx(0.20)


def test_negative_equity_is_clamped_to_zero():
    budget = RiskBudget.from_settings(
        Settings(alpaca_api_key="k", alpaca_secret_key="s"), equity=-5_000.0
    )
    assert budget.equity == 0.0
    assert budget.aggregate_cap_usd == 0.0


# --- the clipping chain -----------------------------------------------------


def test_a_healthy_edge_produces_at_least_one_contract():
    result = _size(_budget())
    assert result.sizable
    assert result.contracts >= 1


def test_total_risk_is_contracts_times_the_per_contract_loss():
    result = _size(_budget())
    assert result.total_risk_usd == pytest.approx(
        result.contracts * result.per_contract_loss_usd
    )


def test_risk_never_exceeds_the_per_trade_cap():
    result = _size(_budget(kelly_haircut=1.0))
    assert result.total_risk_usd <= _budget().trade_cap_usd + result.per_contract_loss_usd


def test_a_tiny_kelly_haircut_shrinks_the_position():
    small = _size(_budget(kelly_haircut=0.01))
    large = _size(_budget(kelly_haircut=0.35))
    assert small.contracts < large.contracts


def test_kelly_is_the_binding_constraint_when_budgets_are_wide():
    result = _size(
        _budget(
            max_trade_loss_pct=1.0,
            risk_budget_pct=1.0,
            max_underlying_loss_pct=1.0,
            max_bucket_loss_pct=1.0,
        )
    )
    assert result.binding_constraint == "fractional Kelly"


def test_the_per_trade_cap_binds_when_kelly_wants_more():
    result = _size(_budget(kelly_haircut=1.0, max_trade_loss_pct=0.005))
    assert result.binding_constraint == "per_trade"


def test_an_exhausted_underlying_budget_binds():
    exposure = Exposure(
        total_usd=11_900.0, by_underlying={"SPY": 11_900.0}, by_bucket={"index": 11_900.0}
    )
    result = _size(_budget(kelly_haircut=1.0), exposure)
    assert result.binding_constraint == "per_underlying"


def test_an_exhausted_bucket_budget_binds():
    exposure = Exposure(
        total_usd=29_900.0, by_underlying={"QQQ": 29_900.0}, by_bucket={"index": 29_900.0}
    )
    result = _size(_budget(kelly_haircut=1.0), exposure)
    assert result.binding_constraint == "bucket"


def test_an_exhausted_aggregate_budget_binds():
    exposure = Exposure(
        total_usd=44_900.0, by_underlying={"AAPL": 44_900.0}, by_bucket={"AAPL": 44_900.0}
    )
    result = _size(_budget(kelly_haircut=1.0, max_underlying_loss_pct=1.0), exposure)
    assert result.binding_constraint == "aggregate"


def test_thin_options_buying_power_binds():
    result = _size(_budget(kelly_haircut=1.0), options_buying_power=600.0)
    assert result.binding_constraint == "options_buying_power"


def test_only_ninety_percent_of_buying_power_is_planned_for():
    result = _size(_budget(kelly_haircut=1.0), options_buying_power=10_000.0)
    assert result.headroom["options_buying_power"] == pytest.approx(
        10_000.0 * BUYING_POWER_UTILISATION
    )


def test_negative_buying_power_is_treated_as_zero():
    result = _size(_budget(), options_buying_power=-5_000.0)
    assert result.headroom["options_buying_power"] == 0.0
    assert result.contracts == 0


def test_the_per_order_contract_cap_binds():
    result = _size(_budget(kelly_haircut=1.0, max_trade_loss_pct=1.0, max_contracts_per_order=3))
    assert result.contracts == 3
    assert result.binding_constraint == "per-order contract cap"


def test_open_interest_caps_the_ticket():
    evaluation = _evaluation(open_interest=100)
    result = size_structure(
        evaluation,
        budget=_budget(kelly_haircut=1.0, max_trade_loss_pct=1.0),
        exposure=Exposure(),
        bucket="index",
        options_buying_power=GENEROUS,
    )
    assert result.contracts <= 100 // OPEN_INTEREST_DIVISOR
    assert result.binding_constraint == "open interest"


def test_missing_open_interest_does_not_cap_the_ticket():
    evaluation = _evaluation(open_interest=None)
    result = size_structure(
        evaluation,
        budget=_budget(),
        exposure=Exposure(),
        bucket="index",
        options_buying_power=GENEROUS,
    )
    assert result.binding_constraint != "open interest"


def test_zero_equity_leaves_no_room_at_all():
    result = _size(_budget(equity=0.0))
    assert result.contracts == 0
    assert not result.sizable


def test_an_unsizable_result_explains_itself():
    result = _size(_budget(equity=0.0))
    assert result.notes
    assert "no room left" in result.notes[0]


def test_headroom_reports_every_gate():
    result = _size(_budget())
    assert set(result.headroom) == {
        "per_trade",
        "per_underlying",
        "bucket",
        "aggregate",
        "options_buying_power",
    }


def test_the_rationale_names_the_binding_constraint():
    result = _size(_budget(kelly_haircut=1.0, max_trade_loss_pct=0.005))
    assert "per_trade" in result.rationale()
    assert "contract(s)" in result.rationale()


def test_kelly_target_is_the_full_stake_after_the_haircut():
    budget = _budget()
    result = _size(budget)
    assert result.kelly_target_usd == pytest.approx(
        result.kelly_full * budget.kelly_haircut * budget.equity
    )


def test_a_structure_without_edge_gets_no_size():
    # Selling volatility that is cheaper than realized: Kelly is zero, so size is zero.
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.10)
    structure = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )[-1]
    signal = UnderlyingSignal(
        symbol="SPY",
        spot=500.0,
        expiration=EXPIRY,
        horizon_days=7,
        realized_vol=0.60,
        implied_vol=0.10,
        vrp=-0.50,
        vrp_z=-0.83,
        trend=TREND_FLAT,
        stance=STANCE_SELL_VOL,
    )
    evaluation = evaluate_structure(
        structure, signal, today=TODAY, min_edge=0.0, min_wedge=0.0
    )
    result = size_structure(
        evaluation,
        budget=_budget(),
        exposure=Exposure(),
        bucket="index",
        options_buying_power=GENEROUS,
    )
    assert result.contracts == 0


def test_exposure_lookups_are_case_insensitive_and_default_to_zero():
    exposure = Exposure(total_usd=100.0, by_underlying={"SPY": 100.0})
    assert exposure.underlying("spy") == 100.0
    assert exposure.underlying("QQQ") == 0.0
    assert exposure.bucket("index") == 0.0
