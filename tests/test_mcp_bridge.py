"""The research plane: read-only, timeout-bounded, fail-open.

The transport is injected via `runner`, so no MCP server is ever launched.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import make_candidate

from vrp_engine.alpaca.mcp_bridge import (
    READ_ONLY_TOOLS,
    McpNotAvailableError,
    McpResearch,
    McpResult,
    ToolCall,
    cross_check_option_quotes,
    default_calls,
    gather_research,
    snapshot_calls,
)
from vrp_engine.config import Settings


def _settings(**overrides) -> Settings:
    defaults = {
        "alpaca_api_key": "k",
        "alpaca_secret_key": "s",
        "mcp_enabled": True,
        "mcp_timeout_seconds": 5,
        "universe": "SPY,QQQ,NVDA",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _runner(result: McpResearch):
    async def run(settings, calls):
        run.calls = calls
        return result

    return run


# --- the allow-list ---------------------------------------------------------


def test_no_order_placing_tool_is_on_the_allow_list():
    forbidden = {"place_option_order", "place_stock_order", "close_position", "cancel_order"}
    assert not (READ_ONLY_TOOLS & forbidden)


def test_the_allow_list_covers_the_tools_the_engine_actually_uses():
    assert {"get_market_movers", "get_news", "get_option_snapshot", "get_stock_bars"} <= (
        READ_ONLY_TOOLS
    )


def test_every_allowed_tool_name_reads_as_a_getter():
    assert all(name.startswith("get_") for name in READ_ONLY_TOOLS)


# --- the default batches ----------------------------------------------------


def test_the_daily_briefing_asks_for_breadth_activity_and_news():
    tools = {call.tool for call in default_calls(_settings())}
    assert tools == {"get_market_movers", "get_most_active_stocks", "get_news"}


def test_the_movers_call_names_the_market():
    """`market_type` is a path parameter; without it Alpaca answers 400, not an empty list."""
    movers = next(c for c in default_calls(_settings()) if c.tool == "get_market_movers")
    assert movers.arguments["market_type"] == "stocks"


def test_the_news_call_carries_the_universe():
    news = next(c for c in default_calls(_settings()) if c.tool == "get_news")
    assert "SPY" in news.arguments["symbols"]


def test_the_news_call_is_capped_to_a_handful_of_symbols():
    settings = _settings(universe="SPY,QQQ,IWM,DIA,NVDA,AAPL,MSFT,TSLA,AMD,META")
    news = next(c for c in default_calls(settings) if c.tool == "get_news")
    assert len(news.arguments["symbols"].split(",")) <= 6


def test_the_snapshot_batch_asks_only_for_snapshots():
    calls = snapshot_calls(["SPY_A", "SPY_B"])
    assert [c.tool for c in calls] == ["get_option_snapshot"]
    assert calls[0].arguments["symbols"] == "SPY_A,SPY_B"


# --- gathering, and failing open -------------------------------------------


def test_a_disabled_research_plane_returns_unavailable():
    research = gather_research(_settings(mcp_enabled=False))
    assert not research.available
    assert "MCP_ENABLED is false" in research.notes


def test_missing_credentials_disable_the_research_plane():
    settings = Settings(mcp_enabled=True, alpaca_api_key="", alpaca_secret_key="")
    research = gather_research(settings)
    assert not research.available
    assert "credentials" in research.notes[0]


def test_a_successful_batch_is_returned_as_is():
    expected = McpResearch(
        available=True,
        server="uvx alpaca-mcp-server",
        results={"get_news": McpResult(tool="get_news", ok=True, text="headline")},
    )
    research = gather_research(_settings(), runner=_runner(expected))
    assert research.available
    assert research.text_of("get_news") == "headline"


def test_the_requested_calls_reach_the_runner():
    runner = _runner(McpResearch(available=True))
    calls = [ToolCall(tool="get_news", arguments={"limit": 1})]
    gather_research(_settings(), calls=calls, runner=runner)
    assert runner.calls == calls


def test_a_slow_server_times_out_without_raising():
    async def slow(settings, calls):
        await asyncio.sleep(5)
        return McpResearch(available=True)

    research = gather_research(_settings(mcp_timeout_seconds=1), runner=slow)
    assert not research.available
    assert "did not answer" in research.notes[0]


def test_a_missing_mcp_package_is_reported_plainly():
    async def missing(settings, calls):
        raise McpNotAvailableError("the 'mcp' package is not installed")

    research = gather_research(_settings(), runner=missing)
    assert not research.available
    assert "not installed" in research.notes[0]


def test_any_unexpected_transport_failure_fails_open():
    async def broken(settings, calls):
        raise RuntimeError("stdio pipe closed")

    research = gather_research(_settings(), runner=broken)
    assert not research.available
    assert "RuntimeError" in research.notes[0]


# --- the briefing -----------------------------------------------------------


def test_the_briefing_stitches_the_research_tools_together():
    research = McpResearch(
        available=True,
        results={
            "get_market_movers": McpResult(tool="get_market_movers", ok=True, text="up: NVDA"),
            "get_news": McpResult(tool="get_news", ok=True, text="CPI print tomorrow"),
        },
    )
    briefing = research.briefing()
    assert "up: NVDA" in briefing
    assert "CPI print tomorrow" in briefing


def test_the_briefing_says_so_when_the_plane_is_unavailable():
    research = McpResearch(available=False, notes=["MCP_ENABLED is false"])
    assert "unavailable" in research.briefing()


def test_the_briefing_reports_an_empty_but_available_plane():
    assert "no content" in McpResearch(available=True).briefing()


def test_the_briefing_is_length_bounded():
    research = McpResearch(
        available=True,
        results={"get_news": McpResult(tool="get_news", ok=True, text="x" * 5_000)},
    )
    assert len(research.briefing(limit=100)) < 200


def test_a_failed_tool_contributes_no_text():
    research = McpResearch(
        available=True,
        results={"get_news": McpResult(tool="get_news", ok=False, error="boom")},
    )
    assert research.text_of("get_news") == ""


# --- the quote cross-check -------------------------------------------------


def _snapshot(payload) -> McpResearch:
    return McpResearch(
        available=True,
        results={
            "get_option_snapshot": McpResult(
                tool="get_option_snapshot", ok=True, text="{}", data=payload
            )
        },
    )


def test_agreeing_quotes_pass_the_cross_check():
    candidate = make_candidate(bid=2.0, ask=2.2)
    research = _snapshot({candidate.symbol: {"bid_price": 2.0, "ask_price": 2.2}})
    check = cross_check_option_quotes(research, [candidate])
    assert check.checked
    assert check.agrees
    assert check.compared == 1


def test_a_diverging_quote_is_flagged():
    candidate = make_candidate(bid=2.0, ask=2.2)
    research = _snapshot({candidate.symbol: {"bid_price": 5.0, "ask_price": 5.2}})
    check = cross_check_option_quotes(research, [candidate])
    assert not check.agrees
    assert "apart" in check.summary()


def test_the_worst_divergence_is_reported():
    candidate = make_candidate(bid=2.0, ask=2.0)
    research = _snapshot({candidate.symbol: {"bid_price": 2.2, "ask_price": 2.2}})
    check = cross_check_option_quotes(research, [candidate], tolerance=0.5)
    assert check.agrees
    assert check.max_divergence == pytest.approx(0.10)


def test_a_nested_latest_quote_shape_is_understood():
    candidate = make_candidate(bid=2.0, ask=2.2)
    research = _snapshot(
        {"snapshots": {candidate.symbol: {"latest_quote": {"bp": 2.0, "ap": 2.2}}}}
    )
    check = cross_check_option_quotes(research, [candidate])
    assert check.checked
    assert check.compared == 1


def test_a_list_of_snapshots_is_understood():
    candidate = make_candidate(bid=2.0, ask=2.2)
    research = _snapshot(
        [{"symbol": candidate.symbol, "bid_price": 2.0, "ask_price": 2.2}]
    )
    assert cross_check_option_quotes(research, [candidate]).compared == 1


def test_no_snapshot_means_the_check_is_skipped():
    check = cross_check_option_quotes(McpResearch(available=True), [make_candidate()])
    assert not check.checked
    assert "no parseable option snapshot" in check.notes[0]


def test_an_unparseable_payload_skips_the_check():
    check = cross_check_option_quotes(_snapshot({"nothing": "useful"}), [make_candidate()])
    assert not check.checked
    assert "no quotes found" in check.notes[0]


def test_non_overlapping_symbols_skip_the_check():
    research = _snapshot({"SOME_OTHER_SYMBOL": {"bid_price": 1.0, "ask_price": 1.1}})
    check = cross_check_option_quotes(research, [make_candidate()])
    assert not check.checked
    assert "no overlapping symbols" in check.notes[0]


def test_a_candidate_without_a_mid_is_not_compared():
    candidate = make_candidate(bid=None, ask=None)
    research = _snapshot({candidate.symbol: {"bid_price": 2.0, "ask_price": 2.2}})
    assert not cross_check_option_quotes(research, [candidate]).checked


def test_a_skipped_cross_check_says_so_in_its_summary():
    assert "skipped" in cross_check_option_quotes(McpResearch(), []).summary()
