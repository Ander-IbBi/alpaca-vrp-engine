"""Research plane: a real stdio client for Alpaca's MCP server.

Three planes read the same account through three different clients, on purpose:

- `alpaca-py` executes. It is the only path that can reach `submit_order`.
- the CLI verifies. A second, independent implementation confirming the book.
- **MCP researches.** This module. Read-only tools that widen what the agent can see
  beyond what the trading SDK exposes: market movers, news, and a second opinion on
  option snapshots.

MCP never places an order. Only read-only tool names are allowed to leave this file,
and the allow-list is enforced here rather than trusted to a prompt.

Everything is fail-open and hard-timeout bounded. The engine's edge is computed from
the SDK's own data; MCP enriches the narrative and cross-checks the quotes. If the
server is missing, slow, or broken, the cycle carries on and says so in the journal.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field

from vrp_engine.alpaca.options import OptionCandidate
from vrp_engine.config import Settings

# Read-only by construction. A tool that is not on this list is never invoked, so no
# prompt or model output can turn the research plane into an execution plane.
READ_ONLY_TOOLS = frozenset(
    {
        "get_account_info",
        "get_all_positions",
        "get_clock",
        "get_market_movers",
        "get_most_active_stocks",
        "get_news",
        "get_option_chain",
        "get_option_latest_quote",
        "get_option_snapshot",
        "get_stock_bars",
        "get_stock_latest_quote",
        "get_stock_snapshot",
    }
)


class McpNotAvailableError(RuntimeError):
    """The MCP client library or server could not be used."""


class McpResult(BaseModel):
    """Outcome of one tool call. `ok=False` is a normal, non-fatal state."""

    tool: str
    ok: bool = False
    text: str = ""
    data: Any = None
    error: str | None = None


class McpResearch(BaseModel):
    """What the research plane returned this cycle."""

    available: bool = False
    server: str = ""
    tools_seen: int = 0
    results: dict[str, McpResult] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def text_of(self, tool: str, *, limit: int = 1200) -> str:
        result = self.results.get(tool)
        if result is None or not result.ok:
            return ""
        return result.text[:limit]

    def briefing(self, *, limit: int = 900) -> str:
        """Compact prose the LLM analyst can read as market context."""
        if not self.available:
            return f"Research plane unavailable: {'; '.join(self.notes) or 'not enabled'}"
        chunks: list[str] = []
        for tool in ("get_market_movers", "get_most_active_stocks", "get_news"):
            body = self.text_of(tool, limit=limit)
            if body:
                chunks.append(f"[{tool}]\n{body}")
        return "\n\n".join(chunks) if chunks else "Research plane returned no content."


class ToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def _parse_content(content: Any) -> tuple[str, Any]:
    """Flatten MCP content blocks into text, and parse JSON when the server sent it."""
    parts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    joined = "\n".join(parts).strip()
    if not joined:
        return "", None
    try:
        return joined, json.loads(joined)
    except json.JSONDecodeError:
        return joined, None


async def _gather(
    settings: Settings,
    calls: list[ToolCall],
) -> McpResearch:
    """Open one session, run every allowed call, close. Raises on transport failure."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise McpNotAvailableError(
            "the 'mcp' package is not installed; run `uv sync --extra mcp`"
        ) from exc

    command = settings.mcp_command
    args = settings.mcp_args_list()
    params = StdioServerParameters(
        command=command,
        args=args,
        # The server gets paper credentials and an explicit paper flag. There is no
        # configuration of this module that could point it at a live account.
        env={
            "ALPACA_API_KEY": settings.alpaca_api_key,
            "ALPACA_SECRET_KEY": settings.alpaca_secret_key,
            "ALPACA_PAPER_TRADE": "True",
        },
    )

    research = McpResearch(available=True, server=f"{command} {' '.join(args)}".strip())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            offered = {tool.name for tool in getattr(listing, "tools", [])}
            research.tools_seen = len(offered)

            for call in calls:
                if call.tool not in READ_ONLY_TOOLS:
                    research.results[call.tool] = McpResult(
                        tool=call.tool, ok=False, error="tool is not on the read-only allow-list"
                    )
                    continue
                if offered and call.tool not in offered:
                    research.results[call.tool] = McpResult(
                        tool=call.tool, ok=False, error="server does not offer this tool"
                    )
                    continue
                try:
                    response = await session.call_tool(call.tool, call.arguments)
                except Exception as exc:  # noqa: BLE001 — one bad tool must not sink the batch
                    research.results[call.tool] = McpResult(
                        tool=call.tool, ok=False, error=f"{type(exc).__name__}: {exc}"
                    )
                    continue
                text, data = _parse_content(getattr(response, "content", None))
                research.results[call.tool] = McpResult(
                    tool=call.tool, ok=True, text=text, data=data
                )
    return research


def gather_research(
    settings: Settings,
    *,
    calls: list[ToolCall] | None = None,
    runner: Any = None,
) -> McpResearch:
    """Run a batch of read-only MCP calls under a hard timeout, failing open.

    `runner` exists so tests can substitute the transport without a subprocess.
    """
    if not settings.mcp_enabled:
        return McpResearch(available=False, notes=["MCP_ENABLED is false"])
    if not settings.has_alpaca_keys():
        return McpResearch(available=False, notes=["no paper credentials for the MCP server"])

    batch = calls if calls is not None else default_calls(settings)
    invoke = runner or _gather

    async def _bounded() -> McpResearch:
        return await asyncio.wait_for(invoke(settings, batch), timeout=settings.mcp_timeout_seconds)

    try:
        return asyncio.run(_bounded())
    except TimeoutError:
        return McpResearch(
            available=False,
            notes=[f"MCP server did not answer within {settings.mcp_timeout_seconds}s"],
        )
    except McpNotAvailableError as exc:
        return McpResearch(available=False, notes=[str(exc)])
    except Exception as exc:  # noqa: BLE001 — research is optional, the cycle is not
        return McpResearch(available=False, notes=[f"{type(exc).__name__}: {exc}"])


def default_calls(settings: Settings) -> list[ToolCall]:
    """The daily briefing batch: breadth, activity and headlines on the universe."""
    universe = settings.universe_list()
    return [
        ToolCall(tool="get_market_movers", arguments={"top": 10}),
        ToolCall(tool="get_most_active_stocks", arguments={"top": 10}),
        ToolCall(
            tool="get_news",
            arguments={"symbols": ",".join(universe[:6]), "limit": 8},
        ),
    ]


def snapshot_calls(symbols: list[str]) -> list[ToolCall]:
    """A second read of specific option contracts, for cross-checking the SDK."""
    return [
        ToolCall(tool="get_option_snapshot", arguments={"symbols": ",".join(symbols)})
    ]


class QuoteCrossCheck(BaseModel):
    """Does the research plane agree with the SDK about what these options are worth?"""

    checked: bool = False
    agrees: bool = True
    max_divergence: float | None = None
    compared: int = 0
    notes: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        if not self.checked:
            return "quote cross-check skipped: " + ("; ".join(self.notes) or "no data")
        if self.agrees:
            return f"MCP agrees with the SDK on {self.compared} quote(s)"
        return "; ".join(self.notes)


def _mids_from_payload(payload: Any) -> dict[str, float]:
    """Pull symbol -> mid out of whatever shape the server used.

    Servers format snapshots differently across versions, so this walks the payload
    for anything that looks like a quote instead of assuming one schema.
    """
    mids: dict[str, float] = {}

    def visit(node: Any, key_hint: str | None = None) -> None:
        if isinstance(node, dict):
            bid = node.get("bid_price", node.get("bp"))
            ask = node.get("ask_price", node.get("ap"))
            quote = node.get("latest_quote") or node.get("latestQuote")
            if quote is not None:
                visit(quote, key_hint)
            symbol = str(node.get("symbol") or key_hint or "").upper()
            if symbol and bid is not None and ask is not None:
                try:
                    mid = (float(bid) + float(ask)) / 2
                except (TypeError, ValueError):
                    mid = 0.0
                if mid > 0:
                    mids[symbol] = mid
            for child_key, child in node.items():
                if isinstance(child, dict | list):
                    visit(child, child_key)
        elif isinstance(node, list):
            for child in node:
                visit(child, key_hint)

    visit(payload)
    return mids


def cross_check_option_quotes(
    research: McpResearch,
    candidates: list[OptionCandidate],
    *,
    tolerance: float = 0.15,
) -> QuoteCrossCheck:
    """Compare MCP mids against the SDK's before the engine sizes anything.

    A stale or crossed quote is the most likely way a fake edge enters the model, and
    the cheapest way to catch it is to ask a second source the same question.
    """
    result = research.results.get("get_option_snapshot")
    if result is None or not result.ok or result.data is None:
        return QuoteCrossCheck(
            checked=False, notes=["MCP returned no parseable option snapshot"]
        )

    mcp_mids = _mids_from_payload(result.data)
    if not mcp_mids:
        return QuoteCrossCheck(checked=False, notes=["no quotes found in the MCP payload"])

    notes: list[str] = []
    worst = 0.0
    compared = 0
    for candidate in candidates:
        sdk_mid = candidate.mid_price
        mcp_mid = mcp_mids.get(candidate.symbol.upper())
        if sdk_mid is None or sdk_mid <= 0 or mcp_mid is None:
            continue
        compared += 1
        divergence = abs(mcp_mid - sdk_mid) / sdk_mid
        worst = max(worst, divergence)
        if divergence > tolerance:
            notes.append(
                f"{candidate.symbol}: SDK mid {sdk_mid:.2f} vs MCP {mcp_mid:.2f} "
                f"({divergence:.0%} apart)"
            )

    if compared == 0:
        return QuoteCrossCheck(
            checked=False, notes=["no overlapping symbols between MCP and the SDK"]
        )
    return QuoteCrossCheck(
        checked=True,
        agrees=not notes,
        max_divergence=worst,
        compared=compared,
        notes=notes,
    )
