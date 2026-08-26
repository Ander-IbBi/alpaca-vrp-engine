"""Environment settings plus the guardrail that keeps this project on paper trading."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LiveTradingForbiddenError(RuntimeError):
    """Raised when configuration would route orders to a live brokerage account."""


class MissingCredentialsError(RuntimeError):
    """Raised when paper API keys are absent."""


class Settings(BaseSettings):
    """Runtime configuration. Secrets come from the environment, never from source."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    # Any truthy value aborts startup. This project has no live-trading code path.
    alpaca_live_trade: bool = False

    # Comma-separated so a plain .env line like "UNDERLYINGS=SPY,QQQ" works.
    underlyings: str = "SPY"

    max_contracts_per_order: int = Field(default=5, ge=1)
    max_order_notional_usd: float = Field(default=2_500.0, gt=0)
    max_daily_loss_usd: float = Field(default=1_500.0, gt=0)
    min_equity_usd: float = Field(default=80_000.0, ge=0)

    # Orders are simulated until this is explicitly turned off.
    dry_run: bool = True

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    journal_path: Path = PROJECT_ROOT / "data" / "journal" / "agent.jsonl"

    def underlying_list(self) -> list[str]:
        return [s.strip().upper() for s in self.underlyings.split(",") if s.strip()]

    def has_alpaca_keys(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)


def load_settings() -> Settings:
    return Settings()


def assert_paper_only(settings: Settings | None = None) -> Settings:
    """Fail fast, before any Alpaca client exists, if live trading was requested."""
    loaded = settings or load_settings()
    if loaded.alpaca_live_trade:
        raise LiveTradingForbiddenError(
            "ALPACA_LIVE_TRADE is enabled. This project only trades in Alpaca paper."
        )
    return loaded


def require_credentials(settings: Settings) -> Settings:
    if not settings.has_alpaca_keys():
        raise MissingCredentialsError(
            "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY. Copy .env.example to .env "
            "and paste keys from https://app.alpaca.markets/paper/dashboard/overview"
        )
    return settings
