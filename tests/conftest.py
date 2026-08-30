"""Shared builders for the test suite. Nothing here touches the network.

The most useful thing in this file is `build_chain`: it generates an option chain whose
prices, deltas and implied volatilities are internally consistent under Black-Scholes.
Tests can then set a real implied volatility and a real realised volatility and assert
that the engine reaches the conclusion the arithmetic demands, rather than asserting
against hand-tuned magic numbers.
"""

from __future__ import annotations

import math
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from vrp_engine.alpaca.market_data import Bar, PriceHistory
from vrp_engine.alpaca.options import OptionCandidate
from vrp_engine.config import Settings

_SETTINGS_ENV_NAMES = {name.upper() for name in Settings.model_fields}

TRADING_DAYS = 252
TODAY = date(2026, 8, 31)  # a Monday
NOW = datetime(2026, 8, 31, 14, 30, tzinfo=UTC)  # 10:30 New York


# --- Black-Scholes, used only to synthesise a coherent chain ------------------


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(*, spot: float, strike: float, sigma: float, years: float, kind: str) -> float:
    """Zero-rate Black-Scholes price."""
    if years <= 0 or sigma <= 0:
        intrinsic = spot - strike if kind == "call" else strike - spot
        return max(intrinsic, 0.0)
    sd = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * years) / sd
    d2 = d1 - sd
    if kind == "call":
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_delta(*, spot: float, strike: float, sigma: float, years: float, kind: str) -> float:
    if years <= 0 or sigma <= 0:
        return 0.0
    sd = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * years) / sd
    call = _norm_cdf(d1)
    return call if kind == "call" else call - 1.0


# --- builders ----------------------------------------------------------------


def occ_symbol(underlying: str, expiration: date, kind: str, strike: float) -> str:
    return (
        f"{underlying.upper()}{expiration:%y%m%d}"
        f"{'C' if kind == 'call' else 'P'}{int(round(strike * 1000)):08d}"
    )


def make_candidate(
    *,
    underlying: str = "SPY",
    kind: str = "put",
    strike: float = 500.0,
    expiration: date | None = None,
    bid: float | None = 1.0,
    ask: float | None = 1.1,
    delta: float | None = -0.20,
    gamma: float | None = 0.01,
    theta: float | None = -0.05,
    vega: float | None = 0.10,
    implied_volatility: float | None = 0.20,
    open_interest: int | None = None,
) -> OptionCandidate:
    """One contract with full control over every field."""
    expiry = expiration or (TODAY + timedelta(days=7))
    return OptionCandidate(
        symbol=occ_symbol(underlying, expiry, kind, strike),
        underlying=underlying.upper(),
        option_type=kind,
        strike=strike,
        expiration=expiry,
        bid=bid,
        ask=ask,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        implied_volatility=implied_volatility,
        open_interest=open_interest,
    )


def build_chain(
    *,
    underlying: str = "SPY",
    spot: float = 500.0,
    expiration: date | None = None,
    today: date = TODAY,
    implied_vol: float = 0.25,
    strike_step: float = 5.0,
    n_strikes: int = 12,
    spread_fraction: float = 0.04,
    open_interest: int | None = 5_000,
) -> list[OptionCandidate]:
    """A coherent chain: prices, deltas and IV all follow from `implied_vol`.

    The quoted spread is a fixed fraction of the mid, so the liquidity gate sees a
    realistic market instead of a zero-width one that would never be rejected.
    """
    expiry = expiration or (today + timedelta(days=7))
    years = max((expiry - today).days / TRADING_DAYS, 1 / (TRADING_DAYS * 4))
    base = round(spot / strike_step) * strike_step

    candidates: list[OptionCandidate] = []
    for step in range(-n_strikes, n_strikes + 1):
        strike = base + step * strike_step
        if strike <= 0:
            continue
        for kind in ("call", "put"):
            mid = bs_price(spot=spot, strike=strike, sigma=implied_vol, years=years, kind=kind)
            if mid < 0.02:
                # A market maker does not quote a two-cent option with a real spread.
                continue
            half = mid * spread_fraction / 2
            candidates.append(
                OptionCandidate(
                    symbol=occ_symbol(underlying, expiry, kind, strike),
                    underlying=underlying.upper(),
                    option_type=kind,
                    strike=strike,
                    expiration=expiry,
                    bid=round(mid - half, 4),
                    ask=round(mid + half, 4),
                    delta=bs_delta(
                        spot=spot, strike=strike, sigma=implied_vol, years=years, kind=kind
                    ),
                    gamma=0.01,
                    theta=-mid / max((expiry - today).days, 1),
                    vega=mid * 0.5,
                    implied_volatility=implied_vol,
                    open_interest=open_interest,
                )
            )
    return candidates


def build_history(
    *,
    symbol: str = "SPY",
    start: float = 500.0,
    days: int = 90,
    daily_vol: float = 0.008,
    drift: float = 0.0,
    end_day: date = TODAY,
) -> PriceHistory:
    """A deterministic price series with a known volatility and drift.

    Alternating returns give a stable, repeatable standard deviation, so a test can say
    "realised volatility is about X" and mean it.
    """
    bars: list[Bar] = []
    price = start
    for i in range(days):
        shock = daily_vol if i % 2 == 0 else -daily_vol
        price = price * math.exp(drift + shock)
        high = price * (1 + daily_vol / 2)
        low = price * (1 - daily_vol / 2)
        bars.append(
            Bar(
                day=end_day - timedelta(days=days - i),
                open=price,
                high=high,
                low=low,
                close=price,
                volume=1_000_000,
            )
        )
    return PriceHistory(symbol=symbol.upper(), bars=bars)


def build_trending_history(
    *,
    symbol: str = "SPY",
    start: float = 500.0,
    days: int = 90,
    daily_drift: float = 0.004,
    daily_vol: float = 0.004,
    end_day: date = TODAY,
) -> PriceHistory:
    """A series that trends hard enough to trip the trend classifier."""
    bars: list[Bar] = []
    price = start
    for i in range(days):
        shock = daily_vol if i % 2 == 0 else -daily_vol
        price = price * math.exp(daily_drift + shock)
        bars.append(
            Bar(
                day=end_day - timedelta(days=days - i),
                open=price,
                high=price * (1 + daily_vol),
                low=price * (1 - daily_vol),
                close=price,
                volume=1_000_000,
            )
        )
    return PriceHistory(symbol=symbol.upper(), bars=bars)


# --- broker fakes ------------------------------------------------------------


class FakePosition:
    """Duck-typed Alpaca position."""

    def __init__(
        self,
        symbol: str,
        qty: float,
        *,
        avg_entry_price: float = 1.0,
        current_price: float = 1.0,
        unrealized_pl: float = 0.0,
        asset_class: str = "us_option",
        market_value: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.current_price = current_price
        self.unrealized_pl = unrealized_pl
        self.asset_class = asset_class
        multiplier = 100 if asset_class == "us_option" else 1
        self.market_value = (
            market_value if market_value is not None else qty * multiplier * current_price
        )


class FakeAccount:
    def __init__(
        self,
        *,
        equity: float = 100_000.0,
        cash: float = 100_000.0,
        last_equity: float = 100_000.0,
        options_buying_power: float = 100_000.0,
        account_number: str = "PA0TEST0001",
    ) -> None:
        self.equity = equity
        self.cash = cash
        self.last_equity = last_equity
        self.options_buying_power = options_buying_power
        self.account_number = account_number


class FakeClock:
    def __init__(self, *, is_open: bool = True) -> None:
        self.is_open = is_open
        self.next_open = NOW


class FakeOrder:
    def __init__(self, order_id: str = "order-1", status: str = "accepted") -> None:
        self.id = order_id
        self.status = status


class RecordingTradingClient:
    """Captures submitted orders instead of sending them."""

    def __init__(self) -> None:
        self.submitted: list[Any] = []
        self.cancelled: list[str] = []

    def submit_order(self, request: Any) -> FakeOrder:
        self.submitted.append(request)
        return FakeOrder()

    def cancel_order_by_id(self, order_id: str) -> None:
        self.cancelled.append(order_id)


class FakeAlpaca:
    """Stands in for `PaperAlpaca` across the loop tests."""

    def __init__(
        self,
        *,
        settings: Settings,
        account: FakeAccount | None = None,
        positions: list[FakePosition] | None = None,
        histories: dict[str, PriceHistory] | None = None,
        chains: dict[str, list[OptionCandidate]] | None = None,
        market_open: bool = True,
        open_orders: list[Any] | None = None,
    ) -> None:
        self.settings = settings
        self.trading = RecordingTradingClient()
        self._account = account or FakeAccount()
        self._positions = positions or []
        self.histories = histories or {}
        self.chains = chains or {}
        self._market_open = market_open
        self._open_orders = open_orders or []
        self.snapshot_requests: list[list[str]] = []

    def clock(self) -> FakeClock:
        return FakeClock(is_open=self._market_open)

    def account(self) -> FakeAccount:
        return self._account

    def positions(self) -> list[FakePosition]:
        return list(self._positions)

    def open_orders(self) -> list[Any]:
        return list(self._open_orders)

    def cancel_order(self, order_id: str) -> None:
        self.trading.cancel_order_by_id(order_id)

    def options_buying_power(self) -> float:
        return float(self._account.options_buying_power)

    def last_price(self, symbol: str) -> float | None:
        history = self.histories.get(symbol.upper())
        return history.last_close if history else None

    def portfolio_history(self, **_: Any) -> list[tuple[datetime, float]]:
        return [(NOW, float(self._account.equity))]


# --- fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch) -> None:
    """Keep the developer's own `.env` and shell out of every test.

    `Settings` reads `.env` by design, so without this a local `DRY_RUN=true` or a
    custom universe would silently change what the suite asserts.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.upper() in _SETTINGS_ENV_NAMES:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Test settings with real credentials shape but a throwaway journal path."""
    return Settings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
        alpaca_live_trade=False,
        dry_run=True,
        universe="SPY,QQQ,NVDA",
        beta_bucket="SPY,QQQ",
        mcp_enabled=False,
        journal_path=tmp_path / "journal.jsonl",
    )


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def now() -> datetime:
    return NOW
