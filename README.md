# Alpaca Options Overlay Agent

An AI agent that hedges an equity book with **options**, running exclusively on
**Alpaca paper trading**. Built for the
[Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(28 Aug - 4 Sep 2026, track *Options Alpha Agents*).

Every cycle the agent:

1. reads the paper account, positions and market clock,
2. proposes a defined-risk options overlay (baseline: a protective put),
3. runs hard risk checks that the model cannot switch off,
4. records the decision in an append-only journal,
5. submits the ticket to the paper account, or explains why it did not.

## Safety

This project has **no live-trading code path**. `TradingClient` is always built with
`paper=True`, and the process refuses to start if `ALPACA_LIVE_TRADE=true`. Orders are
dry-run by default until `DRY_RUN=false`.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # Windows: copy .env.example .env
```

Paste **paper** keys from the
[Alpaca paper dashboard](https://app.alpaca.markets/paper/dashboard/overview) into `.env`.

```bash
uv run python scripts/smoke_paper.py          # verify keys: clock + account
uv run python scripts/run_agent.py            # one agent cycle (dry run)
uv run python scripts/run_agent.py --execute  # send the ticket to paper
uv run streamlit run app/streamlit_app.py     # judge-facing demo
uv run pytest                                 # tests, no keys needed
```

## Architecture

```
market data + account  ->  strategy  ->  risk  ->  orders  ->  Alpaca paper
                                 \                    /
                                  ->   journal   <---
```

| Path | Role |
| --- | --- |
| `src/options_agent/config.py` | Settings and the paper-only guardrail |
| `src/options_agent/alpaca/` | Trading client, option contracts, order building |
| `src/options_agent/strategy/` | Overlay proposals; swap this module to change the edge |
| `src/options_agent/risk/` | Per-order limits and the account circuit breaker |
| `src/options_agent/agent/` | Cycle loop, LLM tool surface, optional advisor |
| `src/options_agent/journal.py` | Append-only JSONL audit trail |
| `app/streamlit_app.py` | Demo UI |
| `docs/` | Competition notes and repo guide (Spanish) |

The risk layer is deliberately separate from the strategy: the LLM may propose
anything, but `review_proposal` decides what reaches the broker, and naked short
options are always rejected.

## Alpaca MCP and CLI

The event requires the Trading API plus the **MCP server and/or CLI**. The product uses
`alpaca-py`; MCP and CLI are used for development and inspection.

**MCP** ([alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server)):

```json
{
  "mcpServers": {
    "alpaca": {
      "command": "uvx",
      "args": ["alpaca-mcp-server"],
      "env": {
        "ALPACA_API_KEY": "your_paper_key",
        "ALPACA_SECRET_KEY": "your_paper_secret"
      }
    }
  }
}
```

**CLI** ([alpacahq/cli](https://github.com/alpacahq/cli)), paper by default:

```bash
go install github.com/alpacahq/cli/cmd/alpaca@latest
alpaca profile login
alpaca data option chain --underlying-symbol SPY
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | - | Paper keys |
| `ALPACA_LIVE_TRADE` | `false` | Anything truthy aborts startup |
| `UNDERLYINGS` | `SPY` | Watchlist, comma-separated |
| `MAX_CONTRACTS_PER_ORDER` | `5` | Per-order size cap |
| `MAX_ORDER_NOTIONAL_USD` | `2500` | Per-order cost cap |
| `MAX_DAILY_LOSS_USD` | `1500` | Daily circuit breaker |
| `MIN_EQUITY_USD` | `80000` | Equity floor |
| `DRY_RUN` | `true` | Build orders without sending them |

## License

MIT. Paper trading only; nothing here is investment advice.
