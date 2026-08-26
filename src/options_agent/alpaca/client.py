"""Alpaca access, hard-wired to the paper environment."""

from __future__ import annotations

from typing import Any

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from options_agent.config import Settings, assert_paper_only, load_settings, require_credentials


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
        """Positions the overlay can hedge (options are the hedge, not the book)."""
        return [p for p in self.positions() if str(getattr(p, "asset_class", "")) != "us_option"]

    def open_orders(self) -> list[Any]:
        return list(self.trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)))

    def last_price(self, symbol: str) -> float | None:
        request = StockLatestTradeRequest(symbol_or_symbols=symbol.upper())
        trades = self.stock_data.get_stock_latest_trade(request)
        trade = trades.get(symbol.upper())
        return float(trade.price) if trade else None
