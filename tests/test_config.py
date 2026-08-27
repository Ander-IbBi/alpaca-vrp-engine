import pytest

from options_agent.config import (
    LiveTradingForbiddenError,
    MissingCredentialsError,
    Settings,
    assert_paper_only,
    require_credentials,
)


def test_paper_is_the_default() -> None:
    settings = Settings()
    assert settings.alpaca_live_trade is False
    assert assert_paper_only(settings) is settings


def test_live_trading_is_refused() -> None:
    with pytest.raises(LiveTradingForbiddenError):
        assert_paper_only(Settings(alpaca_live_trade=True))


def test_missing_keys_raise() -> None:
    with pytest.raises(MissingCredentialsError):
        require_credentials(Settings(alpaca_api_key="", alpaca_secret_key=""))


def test_underlyings_parse_from_comma_list() -> None:
    settings = Settings(underlyings="spy, qqq ,iwm")
    assert settings.underlying_list() == ["SPY", "QQQ", "IWM"]


def test_dry_run_defaults_on() -> None:
    assert Settings().dry_run is True


def test_equity_seed_defaults() -> None:
    settings = Settings()
    assert settings.seed_shares == 100
    assert settings.max_equity_notional_usd == 80_000.0
