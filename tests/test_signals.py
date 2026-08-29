"""Volatility estimators, the VRP, the trend classifier and beta."""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
from conftest import TODAY, build_chain, build_history, build_trending_history

from vrp_engine.alpaca.market_data import Bar, PriceHistory
from vrp_engine.strategy.signals import (
    STANCE_BUY_VOL,
    STANCE_SELL_VOL,
    STANCE_STAND_DOWN,
    TRADING_DAYS,
    TREND_DOWN,
    TREND_FLAT,
    TREND_UP,
    atm_implied_vol,
    beta_to_market,
    blended_realized_vol,
    build_signal,
    classify_stance,
    close_to_close_vol,
    ema,
    parkinson_vol,
    term_slope,
    trend_state,
)

# --- realized volatility -----------------------------------------------------


def test_close_to_close_annualises_a_known_deviation():
    # Alternating +/-1% returns have a sample stdev of about 1% per day.
    returns = [0.01 if i % 2 == 0 else -0.01 for i in range(21)]
    vol = close_to_close_vol(returns, 21)
    assert vol == pytest.approx(0.01 * math.sqrt(TRADING_DAYS), rel=0.05)


def test_close_to_close_needs_a_minimum_sample():
    assert close_to_close_vol([0.01, -0.01], 21) is None


def test_close_to_close_uses_only_the_window():
    calm = [0.001 if i % 2 == 0 else -0.001 for i in range(10)]
    wild = [0.05 if i % 2 == 0 else -0.05 for i in range(10)]
    vol = close_to_close_vol(calm + wild, 10)
    assert vol > 0.5


def test_parkinson_matches_close_to_close_order_of_magnitude():
    history = build_history(daily_vol=0.01, days=40)
    cc = close_to_close_vol(history.log_returns(), 21)
    park = parkinson_vol(history.bars, 21)
    assert cc is not None and park is not None
    assert park == pytest.approx(cc, rel=0.6)


def test_parkinson_needs_a_minimum_sample():
    bars = [Bar(day=TODAY, open=1, high=1.1, low=0.9, close=1)]
    assert parkinson_vol(bars, 21) is None


def test_parkinson_ignores_bars_with_no_range():
    bars = [Bar(day=TODAY - timedelta(days=i), open=1, high=0, low=0, close=1) for i in range(10)]
    assert parkinson_vol(bars, 10) is None


def test_blend_is_between_its_components():
    history = build_history(daily_vol=0.01, days=60)
    blended = blended_realized_vol(history)
    cc = close_to_close_vol(history.log_returns(), 21)
    park = parkinson_vol(history.bars, 21)
    assert blended is not None
    assert min(cc, park) <= blended <= max(cc, park)


def test_blend_is_none_without_history():
    assert blended_realized_vol(PriceHistory(symbol="SPY")) is None


def test_higher_vol_input_gives_higher_estimate():
    calm = blended_realized_vol(build_history(daily_vol=0.004, days=60))
    wild = blended_realized_vol(build_history(daily_vol=0.02, days=60))
    assert wild > calm


# --- moving averages and trend ----------------------------------------------


def test_ema_of_a_constant_series_is_that_constant():
    assert ema([5.0] * 30, 8) == pytest.approx(5.0)


def test_ema_tracks_a_rising_series_below_its_last_value():
    values = [float(i) for i in range(1, 31)]
    assert ema(values, 8) < values[-1]


def test_ema_of_an_empty_series_is_none():
    assert ema([], 8) is None


def test_choppy_tape_is_flat():
    history = build_history(daily_vol=0.01, days=60)
    assert trend_state(history, realized_vol=blended_realized_vol(history)) == TREND_FLAT


def test_strong_uptrend_is_detected():
    history = build_trending_history(daily_drift=0.006, daily_vol=0.003, days=60)
    assert trend_state(history, realized_vol=blended_realized_vol(history)) == TREND_UP


def test_strong_downtrend_is_detected():
    history = build_trending_history(daily_drift=-0.006, daily_vol=0.003, days=60)
    assert trend_state(history, realized_vol=blended_realized_vol(history)) == TREND_DOWN


def test_short_history_is_flat_rather_than_a_guess():
    history = build_history(days=5)
    assert trend_state(history, realized_vol=0.2) == TREND_FLAT


def test_trend_without_a_vol_estimate_is_flat():
    history = build_trending_history(days=60)
    assert trend_state(history, realized_vol=None) == TREND_FLAT


# --- beta -------------------------------------------------------------------


def test_beta_of_a_series_against_itself_is_one():
    returns = [0.01, -0.02, 0.005, 0.013, -0.004] * 6
    assert beta_to_market(returns, returns) == pytest.approx(1.0)


def test_beta_of_a_doubled_series_is_two():
    market = [0.01, -0.02, 0.005, 0.013, -0.004] * 6
    symbol = [r * 2 for r in market]
    assert beta_to_market(symbol, market) == pytest.approx(2.0)


def test_beta_of_an_inverse_series_is_negative():
    market = [0.01, -0.02, 0.005, 0.013, -0.004] * 6
    symbol = [-r for r in market]
    assert beta_to_market(symbol, market) == pytest.approx(-1.0)


def test_beta_defaults_to_one_without_enough_overlap():
    assert beta_to_market([0.01], [0.01]) == pytest.approx(1.0)


def test_beta_is_clamped_against_absurd_regressions():
    market = [0.0001, -0.0001] * 20
    symbol = [r * 500 for r in market]
    assert beta_to_market(symbol, market) == pytest.approx(3.0)


def test_beta_defaults_to_one_when_the_market_never_moves():
    assert beta_to_market([0.01] * 20, [0.0] * 20) == pytest.approx(1.0)


# --- implied volatility and term structure ----------------------------------


def test_atm_implied_vol_recovers_the_chain_input():
    chain = build_chain(spot=500.0, implied_vol=0.27)
    expiry = chain[0].expiration
    assert atm_implied_vol(chain, spot=500.0, expiration=expiry) == pytest.approx(0.27)


def test_atm_implied_vol_is_none_without_quotes():
    assert atm_implied_vol([], spot=500.0, expiration=TODAY) is None


def test_atm_implied_vol_needs_a_positive_spot():
    chain = build_chain()
    assert atm_implied_vol(chain, spot=0.0, expiration=chain[0].expiration) is None


def test_term_slope_is_positive_when_the_front_is_richer():
    front = TODAY + timedelta(days=3)
    back = TODAY + timedelta(days=10)
    chain = build_chain(spot=500.0, expiration=front, implied_vol=0.40) + build_chain(
        spot=500.0, expiration=back, implied_vol=0.25
    )
    slope = term_slope(chain, spot=500.0, expiries=[front, back])
    assert slope == pytest.approx(0.15, abs=0.01)


def test_term_slope_needs_two_expiries():
    chain = build_chain()
    assert term_slope(chain, spot=500.0, expiries=[chain[0].expiration]) is None


# --- stance -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("vrp_z", "expected"),
    [
        (0.5, STANCE_SELL_VOL),
        (0.15, STANCE_SELL_VOL),
        (0.05, STANCE_STAND_DOWN),
        (0.0, STANCE_STAND_DOWN),
        (-0.05, STANCE_STAND_DOWN),
        (-0.15, STANCE_BUY_VOL),
        (-0.9, STANCE_BUY_VOL),
        (None, STANCE_STAND_DOWN),
    ],
)
def test_stance_thresholds(vrp_z, expected):
    assert classify_stance(vrp_z, entry=0.15) == expected


# --- the assembled signal ---------------------------------------------------


def _signal(*, implied_vol: float, daily_vol: float = 0.008, expiries=None, chain=None):
    history = build_history(daily_vol=daily_vol, days=90, start=500.0)
    spot = history.last_close
    expiry = TODAY + timedelta(days=7)
    candidates = chain if chain is not None else build_chain(
        spot=spot, expiration=expiry, implied_vol=implied_vol
    )
    return build_signal(
        symbol="SPY",
        spot=spot,
        history=history,
        candidates=candidates,
        expiries=expiries if expiries is not None else [expiry],
        market_returns=history.log_returns(),
        today=TODAY,
        vrp_z_entry=0.15,
        term_slope_blackout=0.08,
    )


def test_rich_options_produce_a_sell_stance():
    signal = _signal(implied_vol=0.40, daily_vol=0.008)
    assert signal.stance == STANCE_SELL_VOL
    assert signal.vrp > 0
    assert signal.actionable


def test_cheap_options_produce_a_buy_stance():
    signal = _signal(implied_vol=0.05, daily_vol=0.02)
    assert signal.stance == STANCE_BUY_VOL
    assert signal.vrp < 0


def test_fairly_priced_options_stand_down_and_say_why():
    history = build_history(daily_vol=0.008, days=90, start=500.0)
    realized = blended_realized_vol(history)
    signal = _signal(implied_vol=realized)
    assert signal.stance == STANCE_STAND_DOWN
    assert not signal.actionable
    assert any("no-trade band" in note for note in signal.notes)


def test_event_blackout_overrides_a_rich_signal():
    front = TODAY + timedelta(days=3)
    back = TODAY + timedelta(days=10)
    history = build_history(daily_vol=0.008, days=90, start=500.0)
    spot = history.last_close
    chain = build_chain(spot=spot, expiration=front, implied_vol=0.60) + build_chain(
        spot=spot, expiration=back, implied_vol=0.30
    )
    signal = build_signal(
        symbol="SPY",
        spot=spot,
        history=history,
        candidates=chain,
        expiries=[front, back],
        market_returns=history.log_returns(),
        today=TODAY,
        vrp_z_entry=0.15,
        term_slope_blackout=0.08,
    )
    assert signal.event_blackout
    assert signal.stance == STANCE_STAND_DOWN
    assert not signal.actionable
    assert any("dated event" in note for note in signal.notes)


def test_no_expiry_in_window_is_reported():
    signal = _signal(implied_vol=0.4, expiries=[])
    assert signal.expiration is None
    assert not signal.actionable
    assert any("no expiry" in note for note in signal.notes)


def test_missing_implied_vol_is_reported():
    expiry = TODAY + timedelta(days=7)
    chain = build_chain(expiration=expiry)
    stripped = [c.model_copy(update={"implied_volatility": None}) for c in chain]
    signal = _signal(implied_vol=0.3, chain=stripped, expiries=[expiry])
    assert signal.implied_vol is None
    assert any("at-the-money implied vol" in note for note in signal.notes)


def test_signal_records_the_horizon_in_days():
    signal = _signal(implied_vol=0.4)
    assert signal.horizon_days == 7


def test_vrp_z_normalises_by_realized_vol():
    signal = _signal(implied_vol=0.40, daily_vol=0.008)
    assert signal.vrp_z == pytest.approx(signal.vrp / signal.realized_vol)
