"""Expected value: payoff integration, the wedge, and the ranking metric."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TODAY, build_chain, make_candidate

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER
from vrp_engine.strategy.pricing import (
    DRIFT_SIGMA_SHARE,
    MIN_YEARS,
    TRADING_DAYS,
    black_scholes_delta,
    evaluate_structure,
    fill_missing_deltas,
    kelly_fraction,
    leg_intrinsic,
    norm_cdf,
    norm_pdf,
    rank_evaluations,
    score_under_measure,
    structure_payoff,
    years_to_expiry,
)
from vrp_engine.strategy.signals import (
    STANCE_BUY_VOL,
    STANCE_SELL_VOL,
    TREND_DOWN,
    TREND_FLAT,
    TREND_UP,
    UnderlyingSignal,
)
from vrp_engine.strategy.structures import (
    SelectionParams,
    credit_spread_variants,
    debit_spread_variants,
    iron_condor_variants,
)

EXPIRY = TODAY + timedelta(days=7)
PARAMS = SelectionParams(max_spread_fraction=0.10, min_open_interest=0)


def _signal(
    *,
    realized: float = 0.15,
    implied: float = 0.25,
    trend: str = TREND_FLAT,
    stance: str = STANCE_SELL_VOL,
    spot: float = 500.0,
) -> UnderlyingSignal:
    return UnderlyingSignal(
        symbol="SPY",
        spot=spot,
        expiration=EXPIRY,
        horizon_days=7,
        realized_vol=realized,
        implied_vol=implied,
        vrp=implied - realized,
        vrp_z=(implied - realized) / realized,
        trend=trend,
        stance=stance,
    )


def _put_credit_spread(*, implied_vol: float = 0.25):
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=implied_vol)
    return credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )[0]


# --- distribution helpers ---------------------------------------------------


def test_norm_cdf_is_a_half_at_zero():
    assert norm_cdf(0.0) == pytest.approx(0.5)


def test_norm_cdf_matches_the_one_sigma_textbook_value():
    assert norm_cdf(1.0) == pytest.approx(0.8413, abs=1e-4)


def test_norm_cdf_is_symmetric():
    assert norm_cdf(-1.3) == pytest.approx(1.0 - norm_cdf(1.3))


def test_norm_pdf_peaks_at_zero():
    assert norm_pdf(0.0) == pytest.approx(1.0 / (2 * 3.141592653589793) ** 0.5)
    assert norm_pdf(0.0) > norm_pdf(0.5) > norm_pdf(2.0)


def test_years_to_expiry_converts_calendar_days_to_trading_years():
    assert years_to_expiry(TODAY + timedelta(days=TRADING_DAYS), TODAY) == pytest.approx(1.0)


def test_years_to_expiry_floors_a_same_day_expiry():
    assert years_to_expiry(TODAY, TODAY) == pytest.approx(MIN_YEARS)


def test_years_to_expiry_floors_an_expired_contract():
    assert years_to_expiry(TODAY - timedelta(days=5), TODAY) == pytest.approx(MIN_YEARS)


# --- Black-Scholes delta backfill -------------------------------------------


def test_atm_call_delta_is_near_one_half():
    delta = black_scholes_delta(
        spot=500, strike=500, sigma=0.2, years=0.1, option_type="call"
    )
    assert delta == pytest.approx(0.5, abs=0.02)


def test_put_and_call_deltas_differ_by_one():
    call = black_scholes_delta(spot=500, strike=490, sigma=0.2, years=0.1, option_type="call")
    put = black_scholes_delta(spot=500, strike=490, sigma=0.2, years=0.1, option_type="put")
    assert call - put == pytest.approx(1.0)


def test_delta_is_none_when_volatility_is_zero():
    assert (
        black_scholes_delta(spot=500, strike=500, sigma=0.0, years=0.1, option_type="call")
        is None
    )


def test_delta_is_none_when_the_spot_is_nonsense():
    assert (
        black_scholes_delta(spot=0.0, strike=500, sigma=0.2, years=0.1, option_type="call")
        is None
    )


def test_missing_deltas_get_filled_from_implied_vol():
    chain = [make_candidate(strike=490, delta=None, implied_volatility=0.2)]
    filled = fill_missing_deltas(chain, spot=500.0, today=TODAY)
    assert filled[0].delta is not None
    assert filled[0].delta < 0  # a put


def test_existing_deltas_are_left_untouched():
    chain = [make_candidate(strike=490, delta=-0.31)]
    assert fill_missing_deltas(chain, spot=500.0, today=TODAY)[0].delta == -0.31


def test_a_contract_without_implied_vol_stays_without_a_delta():
    chain = [make_candidate(strike=490, delta=None, implied_volatility=None)]
    assert fill_missing_deltas(chain, spot=500.0, today=TODAY)[0].delta is None


# --- payoff -----------------------------------------------------------------


def test_call_intrinsic_is_zero_below_the_strike():
    assert leg_intrinsic(option_type="call", strike=500, terminal=480) == 0.0


def test_call_intrinsic_grows_above_the_strike():
    assert leg_intrinsic(option_type="call", strike=500, terminal=520) == 20.0


def test_put_intrinsic_grows_below_the_strike():
    assert leg_intrinsic(option_type="put", strike=500, terminal=480) == 20.0


def test_put_intrinsic_is_zero_above_the_strike():
    assert leg_intrinsic(option_type="put", strike=500, terminal=520) == 0.0


def test_credit_spread_keeps_the_whole_credit_far_above_the_strikes():
    structure = _put_credit_spread()
    assert structure_payoff(structure, 900.0) == pytest.approx(structure.max_profit_usd)


def test_credit_spread_loses_the_maximum_far_below_the_strikes():
    structure = _put_credit_spread()
    assert structure_payoff(structure, 1.0) == pytest.approx(-structure.max_loss_usd)


def test_payoff_is_monotone_across_the_ramp_of_a_put_credit_spread():
    structure = _put_credit_spread()
    prices = [400.0, 450.0, 470.0, 490.0, 520.0]
    payoffs = [structure_payoff(structure, p) for p in prices]
    assert payoffs == sorted(payoffs)


def test_payoff_crosses_zero_at_the_breakeven():
    structure = _put_credit_spread()
    breakeven = structure.breakevens()[0]
    assert structure_payoff(structure, breakeven) == pytest.approx(0.0, abs=1e-6)
    assert structure_payoff(structure, breakeven + 1.0) > 0
    assert structure_payoff(structure, breakeven - 1.0) < 0


def test_condor_pays_the_maximum_between_the_short_strikes():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    condor = iron_condor_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, params=PARAMS
    )[0]
    assert structure_payoff(condor, 500.0) == pytest.approx(condor.max_profit_usd)


def test_condor_loses_on_both_tails():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    condor = iron_condor_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, params=PARAMS
    )[0]
    assert structure_payoff(condor, 1.0) < 0
    assert structure_payoff(condor, 5_000.0) < 0


def test_debit_spread_loses_only_the_premium_at_the_wrong_end():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    debit = debit_spread_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, option_type="call", params=PARAMS
    )[0]
    assert structure_payoff(debit, 1.0) == pytest.approx(-debit.max_loss_usd)


# --- integration under a measure -------------------------------------------


def test_win_probability_of_a_put_credit_spread_is_high():
    structure = _put_credit_spread()
    result = score_under_measure(structure, spot=500.0, sigma=0.15, years=7 / TRADING_DAYS)
    assert result is not None
    assert 0.6 < result.win_probability < 1.0


def test_lower_volatility_makes_a_short_premium_trade_more_likely_to_win():
    structure = _put_credit_spread()
    calm = score_under_measure(structure, spot=500.0, sigma=0.10, years=7 / TRADING_DAYS)
    wild = score_under_measure(structure, spot=500.0, sigma=0.40, years=7 / TRADING_DAYS)
    assert calm.win_probability > wild.win_probability


def test_lower_volatility_raises_the_expected_pnl_of_short_premium():
    structure = _put_credit_spread()
    calm = score_under_measure(structure, spot=500.0, sigma=0.10, years=7 / TRADING_DAYS)
    wild = score_under_measure(structure, spot=500.0, sigma=0.40, years=7 / TRADING_DAYS)
    assert calm.expected_pnl_usd > wild.expected_pnl_usd


def test_expected_loss_is_smaller_than_the_maximum_loss():
    structure = _put_credit_spread()
    result = score_under_measure(structure, spot=500.0, sigma=0.20, years=7 / TRADING_DAYS)
    assert 0 < result.expected_loss_usd < structure.max_loss_usd


def test_expected_pnl_sits_between_the_two_extremes():
    structure = _put_credit_spread()
    result = score_under_measure(structure, spot=500.0, sigma=0.20, years=7 / TRADING_DAYS)
    assert -structure.max_loss_usd < result.expected_pnl_usd < structure.max_profit_usd


def test_an_upward_drift_helps_a_put_credit_spread():
    structure = _put_credit_spread()
    flat = score_under_measure(structure, spot=500.0, sigma=0.2, years=7 / TRADING_DAYS)
    up = score_under_measure(
        structure, spot=500.0, sigma=0.2, years=7 / TRADING_DAYS, drift_share=0.25
    )
    assert up.expected_pnl_usd > flat.expected_pnl_usd


def test_scoring_refuses_a_zero_volatility_measure():
    assert score_under_measure(_put_credit_spread(), spot=500.0, sigma=0.0, years=0.02) is None


def test_scoring_refuses_a_zero_spot():
    assert score_under_measure(_put_credit_spread(), spot=0.0, sigma=0.2, years=0.02) is None


def test_probabilities_stay_inside_zero_and_one():
    structure = _put_credit_spread()
    for sigma in (0.02, 0.15, 1.5):
        result = score_under_measure(structure, spot=500.0, sigma=sigma, years=7 / TRADING_DAYS)
        assert 0.0 <= result.win_probability <= 1.0


# --- Kelly ------------------------------------------------------------------


def test_kelly_is_zero_without_an_edge():
    # p = 1/(1+odds) is the break-even win rate, so the stake must be zero.
    assert kelly_fraction(win_probability=0.5, max_profit=100, max_loss=100) == pytest.approx(0.0)


def test_kelly_grows_with_the_win_probability():
    low = kelly_fraction(win_probability=0.80, max_profit=100, max_loss=300)
    high = kelly_fraction(win_probability=0.90, max_profit=100, max_loss=300)
    assert high > low > 0


def test_kelly_is_never_negative():
    assert kelly_fraction(win_probability=0.10, max_profit=100, max_loss=300) == 0.0


def test_kelly_needs_a_real_loss_and_a_real_profit():
    assert kelly_fraction(win_probability=0.9, max_profit=0.0, max_loss=300) == 0.0
    assert kelly_fraction(win_probability=0.9, max_profit=100, max_loss=0.0) == 0.0


# --- full evaluation --------------------------------------------------------


def test_rich_implied_vol_produces_a_positive_wedge_and_edge():
    structure = _put_credit_spread(implied_vol=0.30)
    evaluation = evaluate_structure(
        structure, _signal(realized=0.15, implied=0.30), today=TODAY, min_edge=0.0, min_wedge=0.0
    )
    assert evaluation is not None
    assert evaluation.wedge > 0
    assert evaluation.edge > 0
    assert evaluation.acceptable


def test_selling_cheap_vol_is_rejected_on_the_wedge():
    # Implied below realized: the market is not overpaying, so short premium must fail.
    structure = _put_credit_spread(implied_vol=0.12)
    evaluation = evaluate_structure(
        structure, _signal(realized=0.30, implied=0.12), today=TODAY, min_edge=0.0, min_wedge=0.0
    )
    assert evaluation is not None
    assert evaluation.wedge < 0
    assert not evaluation.acceptable
    assert any("wedge" in reason for reason in evaluation.rejects)


def test_a_high_edge_floor_rejects_a_thin_trade():
    structure = _put_credit_spread(implied_vol=0.26)
    evaluation = evaluate_structure(
        structure, _signal(realized=0.24, implied=0.26), today=TODAY, min_edge=0.90, min_wedge=0.0
    )
    assert not evaluation.acceptable
    assert any("edge" in reason for reason in evaluation.rejects)


def test_evaluation_records_the_dte():
    evaluation = evaluate_structure(
        _put_credit_spread(), _signal(), today=TODAY, min_edge=0.0, min_wedge=0.0
    )
    assert evaluation.dte == 7


def test_evaluation_needs_a_realized_vol():
    signal = _signal().model_copy(update={"realized_vol": None})
    assert (
        evaluate_structure(
            _put_credit_spread(), signal, today=TODAY, min_edge=0.0, min_wedge=0.0
        )
        is None
    )


def test_evaluation_needs_an_implied_vol():
    signal = _signal().model_copy(update={"implied_vol": None})
    assert (
        evaluate_structure(
            _put_credit_spread(), signal, today=TODAY, min_edge=0.0, min_wedge=0.0
        )
        is None
    )


def test_max_loss_matches_the_structure():
    structure = _put_credit_spread()
    evaluation = evaluate_structure(
        structure, _signal(), today=TODAY, min_edge=0.0, min_wedge=0.0
    )
    assert evaluation.max_loss_usd == pytest.approx(structure.max_loss_usd)
    assert evaluation.max_loss_usd <= structure.width * CONTRACT_MULTIPLIER


def test_the_score_is_the_edge_per_day_of_risk():
    evaluation = evaluate_structure(
        _put_credit_spread(), _signal(), today=TODAY, min_edge=0.0, min_wedge=0.0
    )
    assert evaluation.score == pytest.approx(evaluation.edge / evaluation.dte)


def test_a_shorter_dte_wins_at_equal_edge():
    # Same edge over fewer days is a better use of the same collateral.
    near = evaluate_structure(
        credit_spread_variants(
            build_chain(spot=500.0, expiration=TODAY + timedelta(days=2), implied_vol=0.30),
            underlying="SPY",
            spot=500.0,
            expiration=TODAY + timedelta(days=2),
            option_type="put",
            target_delta=0.22,
            params=PARAMS,
        )[0],
        _signal(realized=0.15, implied=0.30).model_copy(
            update={"expiration": TODAY + timedelta(days=2)}
        ),
        today=TODAY,
        min_edge=0.0,
        min_wedge=0.0,
    )
    far = evaluate_structure(
        _put_credit_spread(implied_vol=0.30),
        _signal(realized=0.15, implied=0.30),
        today=TODAY,
        min_edge=0.0,
        min_wedge=0.0,
    )
    assert near.dte < far.dte
    assert near.score > far.score


def test_an_uptrend_tilts_the_model_distribution_upward():
    structure = _put_credit_spread(implied_vol=0.30)
    signal_up = _signal(realized=0.15, implied=0.30, trend=TREND_UP)
    signal_flat = _signal(realized=0.15, implied=0.30, trend=TREND_FLAT)
    up = evaluate_structure(structure, signal_up, today=TODAY, min_edge=0.0, min_wedge=0.0)
    flat = evaluate_structure(structure, signal_flat, today=TODAY, min_edge=0.0, min_wedge=0.0)
    assert up.expected_value_usd > flat.expected_value_usd
    assert DRIFT_SIGMA_SHARE > 0


def test_a_downtrend_hurts_a_put_credit_spread():
    structure = _put_credit_spread(implied_vol=0.30)
    down = evaluate_structure(
        structure,
        _signal(realized=0.15, implied=0.30, trend=TREND_DOWN),
        today=TODAY,
        min_edge=0.0,
        min_wedge=0.0,
    )
    flat = evaluate_structure(
        structure,
        _signal(realized=0.15, implied=0.30, trend=TREND_FLAT),
        today=TODAY,
        min_edge=0.0,
        min_wedge=0.0,
    )
    assert down.expected_value_usd < flat.expected_value_usd


def test_buying_cheap_vol_shows_a_positive_edge():
    # A debit spread bought when realized far exceeds implied should score well.
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.12)
    debit = debit_spread_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, option_type="call", params=PARAMS
    )[0]
    evaluation = evaluate_structure(
        debit,
        _signal(realized=0.40, implied=0.12, trend=TREND_UP, stance=STANCE_BUY_VOL),
        today=TODAY,
        min_edge=0.0,
        min_wedge=0.0,
    )
    assert evaluation.edge > 0


def test_rationale_is_a_single_auditable_line():
    evaluation = evaluate_structure(
        _put_credit_spread(implied_vol=0.30),
        _signal(realized=0.15, implied=0.30),
        today=TODAY,
        min_edge=0.0,
        min_wedge=0.0,
    )
    text = evaluation.rationale()
    assert "SPY" in text
    assert "wedge" in text
    assert "\n" not in text


def test_ranking_puts_the_best_score_first():
    evaluations = []
    for implied in (0.26, 0.32, 0.29):
        evaluations.append(
            evaluate_structure(
                _put_credit_spread(implied_vol=implied),
                _signal(realized=0.15, implied=implied),
                today=TODAY,
                min_edge=0.0,
                min_wedge=0.0,
            )
        )
    ranked = rank_evaluations(evaluations)
    assert [e.score for e in ranked] == sorted((e.score for e in evaluations), reverse=True)


def test_ranking_an_empty_list_is_empty():
    assert rank_evaluations([]) == []
