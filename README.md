# Alpaca Options Overlay Agent

An AI agent that overlays a **defined-risk collar** on an equity book, running
exclusively on **Alpaca paper trading**. Built for the
[Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(28 Aug - 4 Sep 2026, track *Options Alpha Agents*).

Every cycle the agent:

1. reads the paper account, positions and market clock,
2. cross-checks that view against Alpaca's **CLI** — a second client, a second auth
   path — and refuses to trade on a book the two clients disagree about,
3. picks the next playbook step: seed the shares, open the collar, or **manage the
   collar that is already on**,
4. runs hard risk checks that the model cannot switch off,
5. asks an optional LLM to explain (soft veto only; unknown reasons fail open),
6. records the decision in an append-only journal,
7. submits a **limit** ticket at the net mid, or explains why it did not.

### The strategy

Long 100 SPY, floored by a put near delta −0.20, financed by a short call at roughly the
put's premium. Defined risk on both sides: the put is the floor, the shares cover the
call, and a naked short is rejected by code.

Then it is managed, every cycle, in order of urgency:

| Trigger | Action | Why |
| --- | --- | --- |
| Short call goes in the money | Roll it up and out | The collar stops earning above the strike, and the shares risk assignment |
| Any leg within 10 DTE | Roll the collar out | Never carry expiry or assignment risk through the week |
| Long put worth 2x its cost | Sell it, re-arm a lower floor | A hedge that paid off is profit sitting in a wasting asset |
| None of the above | Hold, and record which checks ran | A quiet cycle should still show its reasoning |

Each management ticket is capped: the agent will not pay more than its roll budget, and
it will not re-arm a floor that leaves more downside than the per-order limit allows.

## Safety

This project has **no live-trading code path**. `TradingClient` is always built with
`paper=True`, and the process refuses to start if `ALPACA_LIVE_TRADE=true`. Orders are
dry-run by default until `DRY_RUN=false`. Naked short options are always rejected;
a short call in the collar must be covered by long shares.

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
uv run python scripts/broker_report.py        # SDK vs CLI vs agent, side by side
uv run python scripts/run_agent.py            # one agent cycle (dry run)
uv run python scripts/run_agent.py --execute  # send the ticket to paper
uv run python scripts/run_agent.py --loop --execute --interval 900
uv run streamlit run app/streamlit_app.py     # judge-facing demo
uv run pytest                                 # tests, no keys or network needed
```

`--loop` repeats the cycle during the session. Cycle 1 buys SPY; cycle 2 opens the
collar; the rest is hold + journal. Options day orders are not sent when the market
is closed.

## Architecture

```
market data + account  ->  strategy  ->  risk  ->  LLM review  ->  orders  ->  Alpaca paper
                                 \                      /
                                  ->      journal     <-
```

| Path | Role |
| --- | --- |
| `src/options_agent/config.py` | Settings and the paper-only guardrail |
| `src/options_agent/alpaca/` | Trading client, quoted option chain, order building |
| `src/options_agent/strategy/` | Aggressive collar playbook; swap this module to change the edge |
| `src/options_agent/risk/` | Per-order limits, covered-call check, account circuit breaker |
| `src/options_agent/agent/` | Cycle loop, LLM tool surface, optional advisor |
| `src/options_agent/journal.py` | Append-only JSONL audit trail |
| `app/streamlit_app.py` | Demo UI |
| `docs/` | Competition notes and repo guide (Spanish) |

The risk layer is deliberately separate from the strategy: the LLM may comment or
soft-veto, but `review_proposal` decides what reaches the broker.

## Alpaca MCP and CLI

Full detail in [docs/mcp-and-cli.md](docs/mcp-and-cli.md). In short, each tool does the
job it is best at:

- **Trading API** (`alpaca-py`) is the only thing that places orders.
- **CLI** ([alpacahq/cli](https://github.com/alpacahq/cli)) is an *independent* read of
  the same account, run before every ticket. If the CLI and the SDK disagree about the
  account or the open positions, the cycle stops rather than trade a stale book. If the
  binary is not installed the check reports so and the agent continues.
- **MCP** ([alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server))
  gives an LLM client a window on the account for research and supervision — reading
  chains, greeks and fills. It never routes an order, because every order must pass
  `review_proposal` first.

```bash
# CLI, no Go toolchain needed on Windows: grab the release binary, then
alpaca profile login --api-key --paper
uv run python scripts/broker_report.py   # SDK and CLI, side by side

# MCP: copy mcp.example.json to .cursor/mcp.json and paste paper keys
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | - | Paper keys |
| `ALPACA_LIVE_TRADE` | `false` | Anything truthy aborts startup |
| `UNDERLYINGS` | `SPY` | Watchlist, comma-separated |
| `MAX_CONTRACTS_PER_ORDER` | `5` | Per-order size cap |
| `MAX_ORDER_NOTIONAL_USD` | `2500` | Options cost / max-loss cap |
| `MAX_EQUITY_NOTIONAL_USD` | `80000` | Stock-seed notional cap |
| `SEED_SHARES` | `100` | Shares bought when the book is empty |
| `MAX_DAILY_LOSS_USD` | `1500` | Daily circuit breaker |
| `MIN_EQUITY_USD` | `80000` | Equity floor |
| `DRY_RUN` | `true` | Build orders without sending them |

## License

MIT. Paper trading only; nothing here is investment advice.
