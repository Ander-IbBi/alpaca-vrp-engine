"""Portfolio payoff engine: exact worst case, stress shocks and beta mapping."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TODAY, FakePosition, make_candidate, occ_symbol

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER
from vrp_engine.risk.portfolio import (
    CURVE_POINTS,
    STRESS_HORIZON_DAYS,
    Exposure,
    OptionHolding,
    ShareHolding,
    beta_mapped_curve,
    breakpoints,
    build_portfolio_risk,
    build_underlying_exposure,
    expiry_pnl,
    holdings_from_positions,
    prospective_holdings,
)

EXPIRY = TODAY + timedelta(days=7)


def _holding(
    *,
    kind: str = "put",
    strike: float = 490.0,
    contracts: float = -1.0,
    price: float = 2.0,
    underlying: str = "SPY",
    delta: float | None = None,
    vega: float | None = None,
    theta: float | None = None,
) -> OptionHolding:
    return OptionHolding(
        symbol=occ_symbol(underlying, EXPIRY, kind, strike),
        underlying=underlying,
        option_type=kind,
        strike=strike,
        expiration=EXPIRY,
        contracts=contracts,
        market_value=contracts * CONTRACT_MULTIPLIER * price,
        avg_entry_price=price,
        current_price=price,
        delta=delta,
        vega=vega,
        theta=theta,
    )


def _put_credit_spread() -> list[OptionHolding]:
    """Short the 490 put, long the 480: five dollars wide, two dollars collected."""
    return [
        _holding(kind="put", strike=490.0, contracts=-2.0, price=3.0),
        _holding(kind="put", strike=485.0, contracts=2.0, price=1.0),
    ]


# --- holdings from broker positions -----------------------------------------


def test_option_positions_become_option_holdings():
    positions = [FakePosition(occ_symbol("SPY", EXPIRY, "put", 490), -2.0)]
    options, shares = holdings_from_positions(positions)
    assert len(options) == 1
    assert shares == []
    assert options[0].strike == 490.0
    assert options[0].contracts == -2.0


def test_share_positions_become_share_holdings():
    positions = [FakePosition("SPY", 100.0, asset_class="us_equity", current_price=500.0)]
    options, shares = holdings_from_positions(positions)
    assert options == []
    assert shares[0].shares == 100.0


def test_greeks_are_joined_in_from_the_chain_snapshot():
    symbol = occ_symbol("SPY", EXPIRY, "put", 490)
    quote = make_candidate(strike=490.0, expiration=EXPIRY, delta=-0.25, vega=0.4, theta=-0.09)
    options, _ = holdings_from_positions(
        [FakePosition(symbol, -2.0)], greeks={symbol: quote}
    )
    assert options[0].delta == -0.25
    assert options[0].vega == 0.4
    assert options[0].theta == -0.09


def test_a_position_without_a_quote_still_lands_in_the_book():
    options, _ = holdings_from_positions(
        [FakePosition(occ_symbol("SPY", EXPIRY, "put", 490), -2.0)], greeks={}
    )
    assert options[0].delta is None


def test_positions_without_a_symbol_are_skipped():
    options, shares = holdings_from_positions([FakePosition("", 1.0)])
    assert options == []
    assert shares == []


def test_short_holdings_report_themselves_as_short():
    assert _holding(contracts=-1.0).is_short
    assert not _holding(contracts=1.0).is_short


def test_call_and_put_signs_point_opposite_ways():
    assert _holding(kind="call").sign == 1.0
    assert _holding(kind="put").sign == -1.0


def test_dte_counts_calendar_days():
    assert _holding().dte(TODAY) == 7


def test_a_short_option_records_premium_received():
    assert _holding(contracts=-1.0, price=2.0).premium_paid_or_received() == pytest.approx(200.0)


def test_a_long_option_records_premium_paid():
    assert _holding(contracts=1.0, price=2.0).premium_paid_or_received() == pytest.approx(-200.0)


# --- payoff at expiry -------------------------------------------------------


def test_a_short_put_is_worthless_above_the_strike():
    assert _holding(kind="put", strike=490.0, contracts=-1.0).intrinsic_value(520.0) == 0.0


def test_a_short_put_owes_money_below_the_strike():
    value = _holding(kind="put", strike=490.0, contracts=-1.0).intrinsic_value(480.0)
    assert value == pytest.approx(-1_000.0)


def test_shares_are_worth_their_terminal_price():
    assert ShareHolding(symbol="SPY", shares=100.0).intrinsic_value(510.0) == 51_000.0


def test_expiry_pnl_nets_off_todays_mark():
    # Short one put for 2.00: expiring worthless returns the 200 already marked against us.
    options = [_holding(kind="put", strike=490.0, contracts=-1.0, price=2.0)]
    assert expiry_pnl(520.0, options=options, shares=[]) == pytest.approx(200.0)


def test_expiry_pnl_of_a_credit_spread_is_capped_on_both_sides():
    spread = _put_credit_spread()
    best = expiry_pnl(600.0, options=spread, shares=[])
    worst = expiry_pnl(400.0, options=spread, shares=[])
    assert best == pytest.approx(400.0)  # two contracts, net 2.00 collected
    assert worst == pytest.approx(-600.0)  # 5 wide minus 2 collected, twice


def test_breakpoints_include_zero_every_strike_and_a_far_price():
    points = breakpoints(_put_credit_spread(), spot=500.0)
    assert points[0] == 0.0
    assert 485.0 in points
    assert 490.0 in points
    assert 500.0 in points
    assert points[-1] > 1_000.0


def test_breakpoints_are_sorted_and_unique():
    points = breakpoints(_put_credit_spread(), spot=490.0)
    assert points == sorted(set(points))


# --- one underlying ---------------------------------------------------------


def test_worst_case_of_a_credit_spread_is_the_width_minus_the_credit():
    exposure = build_underlying_exposure(
        "SPY", options=_put_credit_spread(), shares=[], spot=500.0
    )
    assert exposure.worst_case_loss_usd == pytest.approx(600.0)


def test_the_worst_case_price_is_a_breakpoint_not_the_spot():
    exposure = build_underlying_exposure(
        "SPY", options=_put_credit_spread(), shares=[], spot=500.0
    )
    assert exposure.worst_case_price <= 485.0


def test_an_empty_book_has_no_worst_case():
    exposure = build_underlying_exposure("SPY", options=[], shares=[], spot=500.0)
    assert exposure.worst_case_loss_usd == 0.0
    assert exposure.curve == []


def test_dollar_delta_uses_the_contract_multiplier_and_the_spot():
    exposure = build_underlying_exposure(
        "SPY",
        options=[_holding(contracts=-2.0, delta=-0.25)],
        shares=[],
        spot=500.0,
    )
    assert exposure.net_delta_usd == pytest.approx(-0.25 * -2 * 100 * 500)


def test_shares_contribute_their_full_notional_to_delta():
    exposure = build_underlying_exposure(
        "SPY",
        options=[],
        shares=[ShareHolding(symbol="SPY", shares=100.0, current_price=500.0)],
        spot=500.0,
    )
    assert exposure.net_delta_usd == pytest.approx(50_000.0)


def test_holdings_without_greeks_are_excluded_from_delta():
    exposure = build_underlying_exposure(
        "SPY", options=[_holding(contracts=-2.0, delta=None)], shares=[], spot=500.0
    )
    assert exposure.net_delta_usd == 0.0


def test_a_short_option_gives_positive_theta_and_negative_vega():
    exposure = build_underlying_exposure(
        "SPY",
        options=[_holding(contracts=-2.0, theta=-0.10, vega=0.5)],
        shares=[],
        spot=500.0,
    )
    assert exposure.net_theta > 0
    assert exposure.net_vega < 0


def test_the_stress_grid_covers_both_directions():
    exposure = build_underlying_exposure(
        "SPY", options=_put_credit_spread(), shares=[], spot=500.0, realized_vol=0.20
    )
    assert set(exposure.stress) == {"-2sigma", "-1sigma", "+1sigma", "+2sigma"}


def test_a_put_credit_spread_loses_more_at_minus_two_sigma_than_minus_one():
    # At 10% vol a one-week one-sigma move stays above the short strike, while two
    # sigma cuts into the spread: the two scenarios must not report the same number.
    exposure = build_underlying_exposure(
        "SPY", options=_put_credit_spread(), shares=[], spot=500.0, realized_vol=0.10
    )
    assert exposure.stress["-2sigma"] < exposure.stress["-1sigma"]


def test_stress_is_empty_without_a_volatility_estimate():
    exposure = build_underlying_exposure(
        "SPY", options=_put_credit_spread(), shares=[], spot=500.0, realized_vol=None
    )
    assert exposure.stress == {}


def test_the_stress_horizon_is_one_week():
    assert STRESS_HORIZON_DAYS == 5


def test_the_curve_has_the_configured_number_of_points():
    exposure = build_underlying_exposure(
        "SPY", options=_put_credit_spread(), shares=[], spot=500.0
    )
    assert len(exposure.curve) == CURVE_POINTS


# --- the whole book ---------------------------------------------------------


def _two_underlying_book() -> list[FakePosition]:
    return [
        FakePosition(occ_symbol("SPY", EXPIRY, "put", 490), -2.0, current_price=3.0),
        FakePosition(occ_symbol("SPY", EXPIRY, "put", 485), 2.0, current_price=1.0),
        FakePosition(occ_symbol("QQQ", EXPIRY, "call", 520), -1.0, current_price=2.0),
        FakePosition(occ_symbol("QQQ", EXPIRY, "call", 525), 1.0, current_price=1.0),
    ]


def test_the_book_is_split_by_underlying():
    risk = build_portfolio_risk(
        equity=100_000.0,
        positions=_two_underlying_book(),
        spots={"SPY": 500.0, "QQQ": 510.0},
    )
    assert [e.symbol for e in risk.underlyings] == ["QQQ", "SPY"]


def test_the_total_worst_case_sums_the_underlyings():
    risk = build_portfolio_risk(
        equity=100_000.0,
        positions=_two_underlying_book(),
        spots={"SPY": 500.0, "QQQ": 510.0},
    )
    assert risk.total_worst_case_loss_usd == pytest.approx(
        sum(e.worst_case_loss_usd for e in risk.underlyings)
    )


def test_worst_case_percent_is_measured_against_equity():
    risk = build_portfolio_risk(
        equity=10_000.0,
        positions=_put_credit_spread_positions(),
        spots={"SPY": 500.0},
    )
    assert risk.worst_case_pct == pytest.approx(risk.total_worst_case_loss_usd / 10_000.0)


def _put_credit_spread_positions() -> list[FakePosition]:
    return [
        FakePosition(occ_symbol("SPY", EXPIRY, "put", 490), -2.0, current_price=3.0),
        FakePosition(occ_symbol("SPY", EXPIRY, "put", 485), 2.0, current_price=1.0),
    ]


def test_percentages_are_zero_when_equity_is_zero():
    risk = build_portfolio_risk(
        equity=0.0, positions=_put_credit_spread_positions(), spots={"SPY": 500.0}
    )
    assert risk.worst_case_pct == 0.0
    assert risk.stress_loss_pct == 0.0
    assert risk.net_delta_pct == 0.0


def test_buckets_group_correlated_underlyings():
    risk = build_portfolio_risk(
        equity=100_000.0,
        positions=_two_underlying_book(),
        spots={"SPY": 500.0, "QQQ": 510.0},
        bucket_of=lambda symbol: "index" if symbol in {"SPY", "QQQ"} else symbol,
    )
    assert set(risk.exposure.by_bucket) == {"index"}
    assert risk.exposure.bucket("index") == pytest.approx(risk.total_worst_case_loss_usd)


def test_without_a_bucket_map_each_symbol_is_its_own_bucket():
    risk = build_portfolio_risk(
        equity=100_000.0,
        positions=_two_underlying_book(),
        spots={"SPY": 500.0, "QQQ": 510.0},
    )
    assert set(risk.exposure.by_bucket) == {"SPY", "QQQ"}


def test_beta_scales_the_delta_contribution():
    positions = [FakePosition(occ_symbol("NVDA", EXPIRY, "call", 100), 1.0, current_price=2.0)]
    quote = make_candidate(
        underlying="NVDA", kind="call", strike=100.0, expiration=EXPIRY, delta=0.5
    )
    low = build_portfolio_risk(
        equity=100_000.0,
        positions=positions,
        spots={"NVDA": 100.0},
        betas={"NVDA": 1.0},
        greeks={positions[0].symbol: quote},
    )
    high = build_portfolio_risk(
        equity=100_000.0,
        positions=positions,
        spots={"NVDA": 100.0},
        betas={"NVDA": 2.0},
        greeks={positions[0].symbol: quote},
    )
    assert high.beta_weighted_delta_usd == pytest.approx(2 * low.beta_weighted_delta_usd)


def test_stress_scenarios_add_across_underlyings():
    risk = build_portfolio_risk(
        equity=100_000.0,
        positions=_two_underlying_book(),
        spots={"SPY": 500.0, "QQQ": 510.0},
        vols={"SPY": 0.20, "QQQ": 0.25},
    )
    assert set(risk.stress) == {"-2sigma", "-1sigma", "+1sigma", "+2sigma"}


def test_the_worst_stress_loss_is_a_positive_number():
    risk = build_portfolio_risk(
        equity=100_000.0,
        positions=_put_credit_spread_positions(),
        spots={"SPY": 500.0},
        vols={"SPY": 0.40},
    )
    assert risk.worst_stress_loss_usd > 0


def test_the_worst_stress_loss_is_zero_when_every_scenario_gains():
    # A long call only gains at expiry relative to its mark on the upside; use an
    # empty book to make the "no loss anywhere" case unambiguous.
    risk = build_portfolio_risk(equity=100_000.0, positions=[], spots={})
    assert risk.worst_stress_loss_usd == 0.0


def test_the_summary_line_mentions_the_key_numbers():
    risk = build_portfolio_risk(
        equity=100_000.0,
        positions=_put_credit_spread_positions(),
        spots={"SPY": 500.0},
        vols={"SPY": 0.20},
    )
    text = risk.summary()
    assert "worst case" in text
    assert "stress" in text
    assert "beta delta" in text


def test_the_digest_is_scalars_only():
    risk = build_portfolio_risk(
        equity=100_000.0,
        positions=_put_credit_spread_positions(),
        spots={"SPY": 500.0},
        vols={"SPY": 0.20},
    )
    digest = risk.digest()
    assert "underlyings" not in digest
    assert digest["equity"] == 100_000.0
    assert isinstance(digest["stress"], dict)
    assert all(isinstance(v, int | float) for v in digest["stress"].values())


# --- beta-mapped aggregate curve -------------------------------------------


def test_the_beta_mapped_curve_is_centred_on_no_shock():
    risk = build_portfolio_risk(
        equity=100_000.0, positions=_put_credit_spread_positions(), spots={"SPY": 500.0}
    )
    curve = beta_mapped_curve(risk, span=0.10, points=21)
    shocks = [shock for shock, _ in curve]
    assert shocks[0] == pytest.approx(-0.10)
    assert shocks[-1] == pytest.approx(0.10)
    assert any(abs(s) < 1e-9 for s in shocks)


def test_a_put_credit_spread_book_loses_on_the_downside_shock():
    risk = build_portfolio_risk(
        equity=100_000.0, positions=_put_credit_spread_positions(), spots={"SPY": 500.0}
    )
    curve = dict(beta_mapped_curve(risk, span=0.10, points=21))
    down = min(curve.items(), key=lambda pair: pair[0])[1]
    up = max(curve.items(), key=lambda pair: pair[0])[1]
    assert down < up


def test_an_empty_book_maps_to_a_flat_zero_curve():
    risk = build_portfolio_risk(equity=100_000.0, positions=[], spots={})
    assert all(value == 0.0 for _, value in beta_mapped_curve(risk, points=11))


# --- prospective holdings ---------------------------------------------------


def test_prospective_holdings_sign_the_contracts_by_side():
    short = make_candidate(strike=490.0, expiration=EXPIRY)
    long = make_candidate(strike=485.0, expiration=EXPIRY)
    holdings = prospective_holdings(
        symbols_sides=[(short, "sell"), (long, "buy")], contracts=3
    )
    assert holdings[0].contracts == -3.0
    assert holdings[1].contracts == 3.0


def test_prospective_holdings_are_marked_at_the_mid():
    candidate = make_candidate(strike=490.0, expiration=EXPIRY, bid=2.0, ask=2.2)
    holding = prospective_holdings(symbols_sides=[(candidate, "sell")], contracts=1)[0]
    assert holding.current_price == pytest.approx(2.1)
    assert holding.market_value == pytest.approx(-210.0)


def test_prospective_holdings_carry_the_greeks_over():
    candidate = make_candidate(strike=490.0, expiration=EXPIRY, delta=-0.3, vega=0.4)
    holding = prospective_holdings(symbols_sides=[(candidate, "sell")], contracts=1)[0]
    assert holding.delta == -0.3
    assert holding.vega == 0.4


def test_adding_a_prospective_spread_raises_the_worst_case():
    before = build_underlying_exposure("SPY", options=[], shares=[], spot=500.0)
    proposed = prospective_holdings(
        symbols_sides=[
            (make_candidate(strike=490.0, expiration=EXPIRY, bid=3.0, ask=3.1), "sell"),
            (make_candidate(strike=485.0, expiration=EXPIRY, bid=1.0, ask=1.1), "buy"),
        ],
        contracts=2,
    )
    after = build_underlying_exposure("SPY", options=proposed, shares=[], spot=500.0)
    assert after.worst_case_loss_usd > before.worst_case_loss_usd


def test_exposure_defaults_to_an_empty_book():
    exposure = Exposure()
    assert exposure.total_usd == 0.0
    assert exposure.underlying("SPY") == 0.0
