"""Read-only tool surface exposed to the LLM analyst.

There is deliberately no tool that places, cancels or resizes an order, and no tool
that relaxes a limit. Order submission only ever happens inside `VrpAgent.run_once`,
after `review_proposal` has approved the ticket. The analyst can look at anything and
change nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vrp_engine.alpaca.client import PaperAlpaca
from vrp_engine.alpaca.market_data import PriceHistory, fetch_daily_bars
from vrp_engine.alpaca.options import fetch_chain_quotes, fetch_contracts

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_account",
        "description": "Paper account equity, cash and options buying power.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_positions",
        "description": "Current positions, options and shares.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_option_contracts",
        "description": "Active option contracts for an underlying symbol.",
        "parameters": {
            "type": "object",
            "properties": {"underlying": {"type": "string"}},
            "required": ["underlying"],
        },
    },
    {
        "name": "get_option_chain",
        "description": "Chain snapshot with quotes, greeks and implied volatility.",
        "parameters": {
            "type": "object",
            "properties": {"underlying": {"type": "string"}},
            "required": ["underlying"],
        },
    },
    {
        "name": "get_daily_bars",
        "description": "Recent daily OHLC bars, the input to realised volatility.",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]


def _daily_bars(client: PaperAlpaca, symbol: str) -> list[dict[str, Any]]:
    history = fetch_daily_bars(client, [symbol]).get(
        symbol.upper(), PriceHistory(symbol=symbol.upper())
    )
    return [bar.model_dump(mode="json") for bar in history.bars]


def build_toolbox(client: PaperAlpaca) -> dict[str, Callable[..., Any]]:
    return {
        "get_account": lambda: client.account().model_dump(mode="json"),
        "get_positions": lambda: [p.model_dump(mode="json") for p in client.positions()],
        "get_option_contracts": lambda underlying: [
            c.model_dump(mode="json") for c in fetch_contracts(client, underlying, limit=50)
        ],
        "get_option_chain": lambda underlying: fetch_chain_quotes(client, underlying),
        "get_daily_bars": lambda symbol: _daily_bars(client, symbol),
    }
