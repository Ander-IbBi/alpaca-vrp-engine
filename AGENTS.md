# alpaca-vrp-engine — context for AI agents

> Source of truth for the project. Cursor reads this automatically. Keep it short.

## What it is
**VRP Engine**: an autonomous agent that trades the **variance risk premium** with
defined-risk option structures (credit spreads, debit spreads, iron condors) across a
multi-underlying universe, for the
[Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(28 Aug – 4 Sep 2026, track **Options Alpha Agents**). It runs **only on Alpaca paper**.

The previous design (a collar overlay) is archived at
`Ander-IbBi/alpaca-collar-overlay`.

## Stack
- Python 3.11+, packaged with **uv** (`pyproject.toml`)
- `alpaca-py` (Trading API, paper-only), Streamlit for the demo
- Optional LLM analyst (`uv sync --extra llm`) in `agent/analyst.py`
- Optional MCP client (`uv sync --extra mcp`) in `alpaca/mcp_bridge.py`
- Three planes, each with a distinct job: **API executes, CLI verifies, MCP researches**

## Layout
- `src/vrp_engine/`
  - `config.py` — settings, budgets as fractions of equity, **abort if `ALPACA_LIVE_TRADE=true`**
  - `alpaca/` — `client.py` (paper), `market_data.py` (bars), `options.py` (multi-expiry
    chain with greeks/IV), `orders.py` (open/close MLeg tickets), `cli_bridge.py`
    (verification), `mcp_bridge.py` (research)
  - `strategy/` — `signals.py`, `structures.py`, `pricing.py`, `sizing.py`,
    `management.py`, `reset.py`, `engine.py` (`VrpEngine`), `base.py` (vocabulary)
  - `risk/` — `limits.py` (defined-risk proof + portfolio budgets), `portfolio.py` (payoff
    and stress engine), `account.py` (breakers, trading window)
  - `agent/` — `loop.py` (cycle), `analyst.py` (briefing + soft veto), `tools.py` (read-only)
  - `journal.py` — JSONL decision trail, and the drawdown breaker's memory
- `app/streamlit_app.py` — Streamlit dashboard
- `scripts/` — `smoke_paper.py`, `broker_report.py`, `run_agent.py`; console scripts
  `smoke-paper`, `scan`, `run-agent`
- `tests/` — 580+ tests, shared fakes in `conftest.py`, **no network, no keys**
- `docs/` — `strategy.md` (maths), `architecture.md`, `mcp-and-cli.md`

## Hard rules
- **Never live.** `TradingClient(..., paper=True)`; do not add a live flag.
- The strategy proposes; the LLM explains (soft veto, fail-open); `risk/` decides. Never
  create a path that reaches `submit_order` without `review_proposal`.
- **Defined risk only.** Every short leg must be covered by a long leg of the same type
  and expiry in the same ticket. Naked shorts are unrepresentable, not merely disallowed.
- An MLeg order is accepted only if every leg is covered inside that ticket, so exits are
  their own all-`*_to_close` ticket. No single-ticket rolls, no equity leg in an MLeg.
- MCP is read-only, enforced by the `READ_ONLY_TOOLS` allow-list in code.
- `DRY_RUN=true` by default; executing requires explicit intent.
- Everything under `strategy/` except `engine.py` stays a pure function of data.

## Conventions
- Code, comments, docstrings, notebooks, README and `docs/`: **English**.
- Markdown in `kebab-case`. Commits: Conventional Commits.
- Before calling work done: `uv run pytest` and `uv run ruff check .`.
