"""Tool surface the LLM is allowed to call.

Read-only tools are exposed directly. There is deliberately no "disable risk" tool:
order submission always goes through `OverlayAgent.run_once`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from options_agent.alpaca.client import PaperAlpaca
from options_agent.alpaca.options import fetch_chain_quotes, fetch_contracts

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_account",
        "description": "Paper account equity, cash and buying power.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_positions",
        "description": "Current positions, equity and options.",
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
]


def build_toolbox(client: PaperAlpaca) -> dict[str, Callable[..., Any]]:
    return {
        "get_account": lambda: client.account().model_dump(mode="json"),
        "get_positions": lambda: [p.model_dump(mode="json") for p in client.positions()],
        "get_option_contracts": lambda underlying: [
            c.model_dump(mode="json") for c in fetch_contracts(client, underlying, limit=50)
        ],
        "get_option_chain": lambda underlying: fetch_chain_quotes(client, underlying),
    }
