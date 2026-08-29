"""Daily price history for the underlyings, reduced to a shape the signals can chew on.

The engine compares what options *imply* against what the underlying actually
*delivered*, so it needs a clean series of OHLC bars. Everything that touches the
network lives in `fetch_daily_bars`; the rest is pure and unit tested with literals.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from pydantic import BaseModel, Field

from vrp_engine.alpaca.client import PaperAlpaca

# Enough history for a 60-day beta regression plus a 21-day vol window.
DEFAULT_LOOKBACK_CALENDAR_DAYS = 130


class Bar(BaseModel):
    """One daily OHLC bar."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class PriceHistory(BaseModel):
    """Ordered daily bars for one symbol, oldest first."""

    symbol: str
    bars: list[Bar] = Field(default_factory=list)

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    @property
    def last_close(self) -> float | None:
        return self.bars[-1].close if self.bars else None

    def log_returns(self) -> list[float]:
        """Close-to-close log returns. Non-positive prices are skipped, not guessed."""
        from math import log

        returns: list[float] = []
        for previous, current in zip(self.bars, self.bars[1:], strict=False):
            if previous.close <= 0 or current.close <= 0:
                continue
            returns.append(log(current.close / previous.close))
        return returns


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bar_day(raw: Any) -> date | None:
    stamp = getattr(raw, "timestamp", None)
    if stamp is None and isinstance(raw, dict):
        stamp = raw.get("timestamp") or raw.get("t")
    if isinstance(stamp, datetime):
        return stamp.date()
    if isinstance(stamp, date):
        return stamp
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _bar_field(raw: Any, name: str, short: str) -> float | None:
    value = getattr(raw, name, None)
    if value is None and isinstance(raw, dict):
        value = raw.get(name, raw.get(short))
    return _as_float(value)


def history_from_bars(raw_bars: Any, *, symbol: str) -> PriceHistory:
    """Normalise alpaca-py bars (or plain dicts) into a `PriceHistory`.

    Bars missing any OHLC component are dropped: a half-formed bar would silently
    bias the volatility estimate rather than fail loudly.
    """
    bars: list[Bar] = []
    for raw in raw_bars or []:
        day = _bar_day(raw)
        open_ = _bar_field(raw, "open", "o")
        high = _bar_field(raw, "high", "h")
        low = _bar_field(raw, "low", "l")
        close = _bar_field(raw, "close", "c")
        if day is None or None in (open_, high, low, close):
            continue
        bars.append(
            Bar(
                day=day,
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=_bar_field(raw, "volume", "v") or 0.0,
            )
        )
    bars.sort(key=lambda b: b.day)
    return PriceHistory(symbol=symbol.upper(), bars=bars)


def fetch_daily_bars(
    client: PaperAlpaca,
    symbols: list[str],
    *,
    today: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_CALENDAR_DAYS,
) -> dict[str, PriceHistory]:
    """Daily bars for the whole universe in one request, keyed by symbol.

    A single batched call keeps the cycle fast; a per-symbol loop would spend most
    of the interval waiting on HTTP.
    """
    tickers = [s.upper() for s in symbols if s.strip()]
    if not tickers:
        return {}

    as_of = today or date.today()
    request = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Day,
        start=as_of - timedelta(days=lookback_days),
        end=as_of,
    )
    response = client.stock_data.get_stock_bars(request)
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response
    histories: dict[str, PriceHistory] = {}
    for ticker in tickers:
        raw = (data or {}).get(ticker) or []
        histories[ticker] = history_from_bars(raw, symbol=ticker)
    return histories
