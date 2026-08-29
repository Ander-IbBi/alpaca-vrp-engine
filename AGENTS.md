# alpaca-options-agent — context for AI agents

> Source of truth for the project. Cursor reads this automatically. Keep it short.

## What it is
A hedge-overlay agent with **options** (aggressive collar on SPY) for the
[Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(28 Aug – 4 Sep 2026, track **Options Alpha Agents**). It runs **only on Alpaca paper**.
The deliverable is public code + Streamlit demo + video, not the IDE chat.

This repo is independent of the Obsidian vault `Vault/deep-hedging` (theoretical study).

## Stack
- Python 3.11+, packaged with **uv** (`pyproject.toml`)
- `alpaca-py` (Trading API, paper-only), Streamlit for the demo
- Optional LLM (`uv sync --extra llm`) in `agent/llm.py`
- Alpaca MCP and CLI: required by the event, documented in the README; they do not replace `alpaca-py`

## Layout
- `src/options_agent/`
  - `config.py` — settings + **abort if `ALPACA_LIVE_TRADE=true`**
  - `alpaca/` — `client.py` (paper), `options.py` (chain with quotes/greeks), `orders.py` (tickets)
  - `strategy/` — `base.py` (vocabulary), `overlay.py` (aggressive collar; swappable)
  - `risk/` — `limits.py` (per order, covered call), `account.py` (account circuit breaker)
  - `agent/` — `loop.py` (cycle), `tools.py` (LLM tools), `llm.py` (explains + soft veto)
  - `journal.py` — JSONL decision trail
- `app/streamlit_app.py` — demo for judges
- `scripts/` — `smoke_paper.py`, `run_agent.py`
- `tests/` — risk, config, strategy and orders **without** touching the network
- `docs/` — competition notes and guides

## Hard rules
- **Never live.** `TradingClient(..., paper=True)`; do not add a live flag.
- The strategy proposes; the LLM explains (soft veto, fail-open); `risk/` decides. Do not create paths that skip risk.
- Naked short options: forbidden. A short call in the collar requires covering shares.
- `DRY_RUN=true` by default; executing requires explicit intent.

## Conventions
- Code, comments, docstrings, notebooks, README and `docs/`: **English**.
- Markdown in `kebab-case`. Commits: Conventional Commits.
- Before calling work done: `uv run pytest` and `uv run ruff check .`.
