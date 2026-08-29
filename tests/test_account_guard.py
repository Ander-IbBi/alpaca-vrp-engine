"""Account circuit breakers and the session window."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from vrp_engine.config import Settings
from vrp_engine.risk.account import (
    SESSION_CLOSE,
    SESSION_OPEN,
    US_EASTERN,
    check_account_guardrails,
    trading_window,
)

MIDDAY = datetime(2026, 8, 31, 12, 0, tzinfo=US_EASTERN)


def _settings(**overrides) -> Settings:
    defaults = {
        "alpaca_api_key": "k",
        "alpaca_secret_key": "s",
        "start_equity_usd": 100_000.0,
        "equity_floor_pct": 0.82,
        "max_daily_loss_pct": 0.06,
        "max_drawdown_pct": 0.18,
        "open_delay_minutes": 5,
        "no_new_risk_before_close_minutes": 20,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# --- the session window -----------------------------------------------------


def test_midday_is_open_for_new_risk():
    window = trading_window(MIDDAY, open_delay_minutes=5, no_new_risk_before_close_minutes=20)
    assert window.open_for_new_risk


def test_the_opening_minutes_are_closed_for_new_risk():
    moment = MIDDAY.replace(hour=9, minute=32)
    window = trading_window(moment, open_delay_minutes=5, no_new_risk_before_close_minutes=20)
    assert not window.open_for_new_risk
    assert "first 5 minutes" in window.reason


def test_new_risk_opens_once_the_delay_has_passed():
    moment = MIDDAY.replace(hour=9, minute=36)
    window = trading_window(moment, open_delay_minutes=5, no_new_risk_before_close_minutes=20)
    assert window.open_for_new_risk


def test_the_last_minutes_are_closed_for_new_risk():
    moment = MIDDAY.replace(hour=15, minute=50)
    window = trading_window(moment, open_delay_minutes=5, no_new_risk_before_close_minutes=20)
    assert not window.open_for_new_risk
    assert "20 minutes of the close" in window.reason


def test_premarket_is_closed_for_new_risk():
    moment = MIDDAY.replace(hour=7, minute=0)
    window = trading_window(moment, open_delay_minutes=5, no_new_risk_before_close_minutes=20)
    assert not window.open_for_new_risk


def test_after_hours_is_closed_for_new_risk():
    moment = MIDDAY.replace(hour=18, minute=0)
    window = trading_window(moment, open_delay_minutes=5, no_new_risk_before_close_minutes=20)
    assert not window.open_for_new_risk


def test_minutes_to_close_counts_down():
    early = trading_window(
        MIDDAY.replace(hour=10, minute=0),
        open_delay_minutes=5,
        no_new_risk_before_close_minutes=20,
    )
    late = trading_window(
        MIDDAY.replace(hour=15, minute=0),
        open_delay_minutes=5,
        no_new_risk_before_close_minutes=20,
    )
    assert early.minutes_to_close > late.minutes_to_close


def test_minutes_to_close_never_goes_negative():
    window = trading_window(
        MIDDAY.replace(hour=20, minute=0),
        open_delay_minutes=5,
        no_new_risk_before_close_minutes=20,
    )
    assert window.minutes_to_close == 0


def test_a_utc_timestamp_is_converted_to_eastern():
    # 20:00 UTC is 16:00 in New York during daylight saving: past the close.
    moment = datetime(2026, 8, 31, 20, 0, tzinfo=ZoneInfo("UTC"))
    window = trading_window(moment, open_delay_minutes=5, no_new_risk_before_close_minutes=20)
    assert not window.open_for_new_risk


def test_a_naive_timestamp_is_read_as_eastern():
    window = trading_window(
        datetime(2026, 8, 31, 12, 0), open_delay_minutes=5, no_new_risk_before_close_minutes=20
    )
    assert window.open_for_new_risk


def test_the_session_bounds_are_the_regular_us_session():
    assert (SESSION_OPEN.hour, SESSION_OPEN.minute) == (9, 30)
    assert (SESSION_CLOSE.hour, SESSION_CLOSE.minute) == (16, 0)


# --- account guardrails -----------------------------------------------------


def test_a_healthy_account_is_clear_for_new_risk():
    result = check_account_guardrails(
        equity=101_000.0, last_equity=100_000.0, now=MIDDAY, settings=_settings()
    )
    assert result.new_risk_allowed
    assert not result.flatten_required
    assert result.summary() == "account clear for new risk"


def test_the_day_pnl_is_equity_minus_the_previous_close():
    result = check_account_guardrails(
        equity=98_000.0, last_equity=100_000.0, now=MIDDAY, settings=_settings()
    )
    assert result.day_pl == pytest.approx(-2_000.0)


def test_a_daily_loss_beyond_budget_stops_new_risk_but_not_exits():
    result = check_account_guardrails(
        equity=93_000.0, last_equity=100_000.0, now=MIDDAY, settings=_settings()
    )
    assert not result.new_risk_allowed
    assert not result.flatten_required
    assert "daily budget" in result.summary()


def test_a_daily_loss_inside_budget_keeps_trading():
    result = check_account_guardrails(
        equity=95_000.0, last_equity=100_000.0, now=MIDDAY, settings=_settings()
    )
    assert result.new_risk_allowed


def test_equity_below_the_hard_floor_forces_a_flatten():
    result = check_account_guardrails(
        equity=80_000.0, last_equity=81_000.0, now=MIDDAY, settings=_settings()
    )
    assert result.flatten_required
    assert not result.new_risk_allowed
    assert "hard floor" in result.summary()


def test_drawdown_is_measured_from_the_high_water_mark():
    result = check_account_guardrails(
        equity=90_000.0,
        last_equity=90_000.0,
        high_water_mark=120_000.0,
        now=MIDDAY,
        settings=_settings(),
    )
    assert result.drawdown_pct == pytest.approx(0.25)
    assert result.flatten_required


def test_a_drawdown_inside_the_limit_does_not_flatten():
    result = check_account_guardrails(
        equity=110_000.0,
        last_equity=110_000.0,
        high_water_mark=120_000.0,
        now=MIDDAY,
        settings=_settings(),
    )
    assert not result.flatten_required
    assert result.new_risk_allowed


def test_a_new_peak_becomes_the_high_water_mark():
    result = check_account_guardrails(
        equity=130_000.0,
        last_equity=125_000.0,
        high_water_mark=120_000.0,
        now=MIDDAY,
        settings=_settings(),
    )
    assert result.high_water_mark == pytest.approx(130_000.0)
    assert result.drawdown_pct == pytest.approx(0.0)


def test_without_a_high_water_mark_the_starting_equity_is_used():
    result = check_account_guardrails(
        equity=95_000.0, last_equity=95_000.0, now=MIDDAY, settings=_settings()
    )
    assert result.high_water_mark == pytest.approx(100_000.0)


def test_the_clock_alone_can_close_new_risk():
    result = check_account_guardrails(
        equity=100_000.0,
        last_equity=100_000.0,
        now=MIDDAY.replace(hour=15, minute=55),
        settings=_settings(),
    )
    assert not result.new_risk_allowed
    assert not result.flatten_required


def test_a_flatten_summary_takes_priority_over_the_no_new_risk_message():
    result = check_account_guardrails(
        equity=50_000.0,
        last_equity=100_000.0,
        now=MIDDAY.replace(hour=15, minute=55),
        settings=_settings(),
    )
    assert result.summary().startswith("flatten required")


def test_a_tighter_floor_fires_earlier():
    strict = check_account_guardrails(
        equity=95_000.0,
        last_equity=95_000.0,
        now=MIDDAY,
        settings=_settings(equity_floor_pct=0.98),
    )
    assert strict.flatten_required


def test_the_result_echoes_the_equity_it_judged():
    result = check_account_guardrails(
        equity=99_000.0, last_equity=100_000.0, now=MIDDAY, settings=_settings()
    )
    assert result.equity == pytest.approx(99_000.0)
    assert result.minutes_to_close is not None
