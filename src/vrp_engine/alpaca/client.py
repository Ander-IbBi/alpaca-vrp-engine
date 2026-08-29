"""Alpaca access, hard-wired to the paper environment."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest

from vrp_engine.config import Settings, assert_paper_only, load_settings, require_credentials


class PaperAlpaca:
    """Thin wrapper over alpaca-py. `paper=True` is not configurable on purpose."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = require_credentials(assert_paper_only(settings or load_settings()))
        # paper=True routes trading calls to paper-api.alpaca.markets.
        self.trading = TradingClient(
            api_key=self.settings.alpaca_api_key,
            secret_key=self.settings.alpaca_secret_key,
            paper=True,
        )
        self.option_data = OptionHistoricalDataClient(
            api_key=self.settings.alpaca_api_key,
            secret_key=self.settings.alpaca_secret_key,
        )
        self.stock_data = StockHistoricalDataClient(
            api_key=self.settings.alpaca_api_key,
            secret_key=self.settings.alpaca_secret_key,
        )

    def clock(self) -> Any:
        return self.trading.get_clock()

    def account(self) -> Any:
        return self.trading.get_account()

    def positions(self) -> list[Any]:
        return list(self.trading.get_all_positions())

    def equity_positions(self) -> list[Any]:
        """Share positions only. Imported lazily to keep `options` free of a cycle."""
        from vrp_engine.alpaca.options import is_option_position

        return [p for p in self.positions() if not is_option_position(p)]

    def option_positions(self) -> list[Any]:
        from vrp_engine.alpaca.options import is_option_position

        return [p for p in self.positions() if is_option_position(p)]

    def options_buying_power(self) -> float:
        """Collateral actually available for option structures.

        Options are not marginable the way shares are, so `buying_power` overstates
        what the engine can deploy. Alpaca reports the usable figure separately.
        """
        account = self.account()
        for field in ("options_buying_power", "non_marginable_buying_power", "cash"):
            value = getattr(account, field, None)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def open_orders(self) -> list[Any]:
        return list(self.trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)))

    def cancel_order(self, order_id: str) -> None:
        """Pull a working order. Used on stale limits whose mid has moved on."""
        self.trading.cancel_order_by_id(order_id)

    def portfolio_history(
        self,
        *,
        period: str = "1W",
        timeframe: str = "1H",
    ) -> list[tuple[datetime, float]]:
        """Equity over time, as (timestamp, equity) points.

        This is the contest's own scoreboard, so the demo shows it rather than a
        single end-of-day number.
        """
        request = GetPortfolioHistoryRequest(
            period=period,
            timeframe=timeframe,
            intraday_reporting="market_hours",
        )
        history = self.trading.get_portfolio_history(request)
        stamps = list(getattr(history, "timestamp", None) or [])
        equities = list(getattr(history, "equity", None) or [])
        points: list[tuple[datetime, float]] = []
        for stamp, equity in zip(stamps, equities, strict=False):
            # Alpaca back-fills the window with zeros for the time before the
            # account existed; plotting those would fake a huge opening gain.
            if equity is None or float(equity) <= 0:
                continue
            moment = (
                stamp
                if isinstance(stamp, datetime)
                else datetime.fromtimestamp(int(stamp), tz=UTC)
            )
            points.append((moment, float(equity)))
        return points

    def last_price(self, symbol: str) -> float | None:
        request = StockLatestTradeRequest(symbol_or_symbols=symbol.upper())
        trades = self.stock_data.get_stock_latest_trade(request)
        trade = trades.get(symbol.upper())
        return float(trade.price) if trade else None
