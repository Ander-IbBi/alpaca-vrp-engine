"""Environment settings plus the guardrail that keeps this project on paper trading.

Every risk number is expressed as a **fraction of account equity**, not as a dollar
constant. That way the same configuration behaves identically on a $10k and a $100k
paper account, and the sizing layer never has a stale absolute number to trip over.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Streamlit Community Cloud injects these via st.secrets, not via a .env file.
# Only credential and safety flags are copied; strategy knobs stay at their defaults.
STREAMLIT_SECRET_ENV_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "DRY_RUN",
    "ALPACA_LIVE_TRADE",
)


def hydrate_env_from_mapping(
    secrets: Mapping[str, Any],
    *,
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Copy Streamlit-style secrets into the process environment.

    Existing env vars win, so a local `.env` is not overwritten by Cloud secrets
    during development. Empty values are ignored. Returns the names that were written.
    """
    env = os.environ if environ is None else environ
    written: list[str] = []
    for key in STREAMLIT_SECRET_ENV_KEYS:
        if key not in secrets:
            continue
        existing = env.get(key)
        if existing is not None and str(existing) != "":
            continue
        value = secrets[key]
        if value is None or str(value).strip() == "":
            continue
        env[key] = str(value)
        written.append(key)
    return written


class LiveTradingForbiddenError(RuntimeError):
    """Raised when configuration would route orders to a live brokerage account."""


class MissingCredentialsError(RuntimeError):
    """Raised when paper API keys are absent."""


def _symbol_list(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


class Settings(BaseSettings):
    """Runtime configuration. Secrets come from the environment, never from source."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Credentials and the paper-only guarantee -------------------------------
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    # Any truthy value aborts startup. This project has no live-trading code path.
    alpaca_live_trade: bool = False
    # The agent is autonomous: starting it *is* the decision to trade, so approved
    # tickets go out without anyone confirming them. This flag is a development escape
    # hatch for rehearsing a cycle offline, not an operating mode the loop asks about.
    dry_run: bool = False

    # --- Universe --------------------------------------------------------------
    # Comma-separated so a plain .env line works. Liquidity is re-checked at runtime,
    # so an illiquid name in this list simply never produces a candidate.
    universe: str = "SPY,QQQ,IWM,DIA,GLD,TLT,NVDA,TSLA,AMD,AAPL,MSFT,META,AMZN,GOOGL"
    # Index ETFs move together, so they share one concentration budget instead of
    # each getting its own. Treating them as independent would quietly triple the bet.
    beta_bucket: str = "SPY,QQQ,IWM,DIA"
    # Where portfolio delta hedges are placed: the most liquid chain available.
    hedge_symbol: str = "SPY"

    # --- Expiry window ---------------------------------------------------------
    # Short-dated on purpose: theta per day is highest here, and a contest week is
    # too short to wait on 45-day decay.
    min_dte: int = Field(default=1, ge=0)
    max_dte: int = Field(default=9, ge=1)

    # --- Signal thresholds -----------------------------------------------------
    # |VRP / RV| must clear this before the engine takes either side.
    vrp_z_entry: float = Field(default=0.15, gt=0)
    # Front-expiry IV richer than the next by this many vol points reads as a scheduled
    # event (earnings, catalyst). Cheaper and more honest than a hardcoded calendar.
    term_slope_blackout: float = Field(default=0.08, gt=0)
    target_short_delta: float = Field(default=0.22, gt=0, lt=0.5)
    target_condor_delta: float = Field(default=0.18, gt=0, lt=0.5)
    debit_long_delta: float = Field(default=0.45, gt=0, lt=1.0)
    debit_short_delta: float = Field(default=0.25, gt=0, lt=1.0)

    # --- Liquidity gates -------------------------------------------------------
    # Entry gates only. Exits are deliberately never liquidity-gated: getting out
    # matters more than getting a good price, so `build_exit` quotes whatever the
    # market shows rather than refusing to close a position that has gone wide.
    min_open_interest: int = Field(default=200, ge=0)
    max_spread_fraction: float = Field(default=0.08, gt=0)

    # --- Expected-value gates --------------------------------------------------
    # Expected value per dollar of risk. Below this the trade is noise.
    min_edge: float = Field(default=0.03, gt=0)
    # p_model - p_implied. The trade only exists if our distribution says the market
    # is overpaying; this is the whole thesis reduced to one number.
    min_wedge: float = Field(default=0.02, ge=0)

    # --- Sizing and risk budgets (fractions of equity) -------------------------
    kelly_fraction: float = Field(default=0.35, gt=0, le=1.0)
    risk_budget_pct: float = Field(default=0.45, gt=0, le=1.0)
    max_trade_loss_pct: float = Field(default=0.045, gt=0, le=1.0)
    max_underlying_loss_pct: float = Field(default=0.12, gt=0, le=1.0)
    max_bucket_loss_pct: float = Field(default=0.30, gt=0, le=1.0)
    # Modelled loss at a two-sigma one-week shock, which is a far more useful ceiling
    # than the theoretical worst case every spread would only reach simultaneously.
    max_stress_loss_pct: float = Field(default=0.18, gt=0, le=1.0)
    # Beta-weighted net delta, as SPY-equivalent notional over equity. The book may
    # lean, but it may not become a naked directional bet.
    max_net_delta_pct: float = Field(default=0.25, gt=0)
    max_contracts_per_order: int = Field(default=40, ge=1)

    # --- Account circuit breakers ---------------------------------------------
    # The contest reference: the account's opening equity.
    start_equity_usd: float = Field(default=100_000.0, gt=0)
    equity_floor_pct: float = Field(default=0.82, gt=0, lt=1.0)
    max_daily_loss_pct: float = Field(default=0.06, gt=0, lt=1.0)
    max_drawdown_pct: float = Field(default=0.18, gt=0, lt=1.0)

    # --- Trading window (US/Eastern minutes) -----------------------------------
    # The opening auction prints wide, unstable quotes; the close is where an unfilled
    # day order turns into unwanted overnight exposure.
    open_delay_minutes: int = Field(default=15, ge=0)
    no_new_risk_before_close_minutes: int = Field(default=20, ge=0)
    forced_exit_hour_et: int = Field(default=15, ge=0, le=23)

    # --- Management thresholds -------------------------------------------------
    profit_take_credit_pct: float = Field(default=0.55, gt=0, le=1.0)
    profit_take_condor_pct: float = Field(default=0.60, gt=0, le=1.0)
    profit_take_debit_pct: float = Field(default=1.00, gt=0)
    stop_loss_credit_multiple: float = Field(default=2.0, gt=0)
    assignment_delta: float = Field(default=0.60, gt=0, lt=1.0)
    assignment_proximity_pct: float = Field(default=0.005, gt=0)

    # --- Legacy book ----------------------------------------------------------
    # Lets the engine flatten positions it did not open (for example an inherited
    # collar) so their collateral is available for structures it can actually model.
    allow_legacy_unwind: bool = True

    # --- Research plane (Alpaca MCP server) ------------------------------------
    mcp_enabled: bool = True
    mcp_command: str = "uvx"
    mcp_args: str = "alpaca-mcp-server"
    mcp_timeout_seconds: int = Field(default=45, gt=0)

    # --- Optional LLM analyst -------------------------------------------------
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    journal_path: Path = PROJECT_ROOT / "data" / "journal" / "agent.jsonl"

    def universe_list(self) -> list[str]:
        return _symbol_list(self.universe)

    def beta_bucket_list(self) -> list[str]:
        return _symbol_list(self.beta_bucket)

    def mcp_args_list(self) -> list[str]:
        return [a for a in self.mcp_args.split() if a]

    def has_alpaca_keys(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    def bucket_of(self, symbol: str) -> str:
        """Concentration bucket for a symbol: the correlated index sleeve, or itself."""
        return "index" if symbol.upper() in set(self.beta_bucket_list()) else symbol.upper()


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
