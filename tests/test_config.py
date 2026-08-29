"""The paper-only guarantee, and that every budget resolves the way the docs claim."""

from __future__ import annotations

import pytest

from vrp_engine.config import (
    LiveTradingForbiddenError,
    MissingCredentialsError,
    Settings,
    assert_paper_only,
    require_credentials,
)


def test_live_trading_flag_aborts_startup():
    with pytest.raises(LiveTradingForbiddenError):
        assert_paper_only(Settings(alpaca_live_trade=True))


def test_paper_settings_pass_through():
    settings = Settings(alpaca_live_trade=False)
    assert assert_paper_only(settings) is settings


def test_missing_credentials_are_refused():
    with pytest.raises(MissingCredentialsError):
        require_credentials(Settings(alpaca_api_key="", alpaca_secret_key=""))


def test_credentials_present_pass_through():
    settings = Settings(alpaca_api_key="k", alpaca_secret_key="s")
    assert require_credentials(settings) is settings


def test_universe_is_parsed_and_upper_cased():
    settings = Settings(universe=" spy , qqq ,, nvda ")
    assert settings.universe_list() == ["SPY", "QQQ", "NVDA"]


def test_beta_bucket_groups_index_etfs_together():
    settings = Settings(beta_bucket="SPY,QQQ,IWM,DIA")
    assert settings.bucket_of("SPY") == "index"
    assert settings.bucket_of("qqq") == "index"
    assert settings.bucket_of("NVDA") == "NVDA"


def test_mcp_args_split_into_a_command_list():
    settings = Settings(mcp_args="alpaca-mcp-server --transport stdio")
    assert settings.mcp_args_list() == ["alpaca-mcp-server", "--transport", "stdio"]


def test_dry_run_defaults_to_true():
    assert Settings().dry_run is True


def test_legacy_unwind_defaults_to_enabled():
    assert Settings().allow_legacy_unwind is True


def test_default_budgets_match_the_documented_aggressiveness():
    settings = Settings()
    assert settings.risk_budget_pct == pytest.approx(0.45)
    assert settings.max_trade_loss_pct == pytest.approx(0.045)
    assert settings.max_underlying_loss_pct == pytest.approx(0.12)
    assert settings.max_bucket_loss_pct == pytest.approx(0.30)
    assert settings.max_stress_loss_pct == pytest.approx(0.18)
    assert settings.kelly_fraction == pytest.approx(0.35)


def test_per_trade_budget_is_smaller_than_every_aggregate_budget():
    settings = Settings()
    assert settings.max_trade_loss_pct < settings.max_underlying_loss_pct
    assert settings.max_underlying_loss_pct < settings.max_bucket_loss_pct
    assert settings.max_bucket_loss_pct < settings.risk_budget_pct


def test_expiry_window_is_short_dated():
    settings = Settings()
    assert settings.min_dte >= 0
    assert settings.max_dte <= 21


def test_negative_kelly_fraction_is_rejected():
    with pytest.raises(ValueError):
        Settings(kelly_fraction=-0.1)


def test_kelly_fraction_above_full_is_rejected():
    with pytest.raises(ValueError):
        Settings(kelly_fraction=1.5)


def test_equity_floor_must_be_a_fraction():
    with pytest.raises(ValueError):
        Settings(equity_floor_pct=1.2)
