# VRP Engine

An autonomous options agent that trades the **variance risk premium** — the gap between
the volatility the option market charges and the volatility the underlying actually
delivers — with defined-risk spreads across a multi-underlying universe. It runs
exclusively on **Alpaca paper trading**.

[![CI](https://github.com/Ander-IbBi/alpaca-vrp-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Ander-IbBi/alpaca-vrp-engine/actions/workflows/ci.yml)

Built for the
[Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(28 Aug – 4 Sep 2026, track *Options Alpha Agents*).

The previous design — a defined-risk collar overlay — is archived at
[alpaca-collar-overlay](https://github.com/Ander-IbBi/alpaca-collar-overlay).

## The thesis in one paragraph

Options are usually priced above what the underlying goes on to deliver; sometimes they
are priced below. The engine measures that gap per underlying, takes whichever side the
gap favours, and only opens a position when its own probability model says the market is
overpaying for the specific structure on the table. It never sells premium on faith: the
number that authorises a trade is the **probability wedge**, the difference between the
win probability under the engine's distribution and the win probability the market's own
distribution implies. A negative wedge is a rejection, no matter how attractive the
credit looks.

Everything is defined risk. Each short leg is paid for by a long leg of the same type
and expiry inside the same ticket, so the worst case is a number the dashboard prints
rather than an open-ended exposure.

## How a cycle works

```
observe -> guard -> signals -> propose -> risk -> research -> analyst
        -> CLI verify -> execute -> reconcile -> journal
```

The ordering is the design. The risk layer runs *after* the strategy and *before* the
LLM, so no model output can talk its way past a budget. The CLI reads the book right
before a ticket goes out and again right after, because acting on a stale view of the
book is how an unattended agent doubles a position it thought it had closed.

Exactly one ticket leaves per cycle, in this priority order:

1. **Unwind inherited positions.** Capital tied up in a book the engine cannot model is
   capital it cannot risk-manage either.
2. **Flatten** if an account breaker demands it.
3. **Manage what is open.** A live structure has real money on it; a hypothetical one
   does not.
4. **Pull portfolio delta back inside budget**, by *selling* a spread on the offsetting
   side, so the correction earns premium instead of costing it.
5. **Open the best new structure** — highest expected value per dollar-day of risk
   across the whole universe, that also survives sizing and the portfolio risk check.
6. **Otherwise hold**, and write down which checks ran.

## Selection matrix

The sign of the variance risk premium decides whether the engine sells or buys premium.
The trend decides the shape.

| VRP signal | Tape | Structure |
| --- | --- | --- |
| `VRP_z >= +0.15` | flat | iron condor, both short wings near \|delta\| 0.18 |
| `VRP_z >= +0.15` | up | put credit spread, short leg near delta −0.22 |
| `VRP_z >= +0.15` | down | call credit spread, short leg near delta +0.22 |
| `VRP_z <= −0.15` | up | call debit spread (long ≈0.45 delta, short ≈0.25) |
| `VRP_z <= −0.15` | down | put debit spread |
| inside the band | any | stand down, and the journal says why |

Spread width is not a constant: each shape is emitted at several widths and the
expected-value layer keeps whichever one actually pays best. A strongly positive IV term
slope (front expiry richer than the next) reads as a dated event and blacks the
underlying out entirely — cheaper and more honest than a hardcoded earnings calendar.

The full derivation, including the expected-value integral and the Kelly arithmetic,
is in [docs/strategy.md](docs/strategy.md).

## Sizing, and the budgets it answers to

A stake starts as a fractional-Kelly bet on the modelled edge and is then cut by every
budget that applies, in order — per trade, per underlying, per correlation bucket,
remaining aggregate budget, options buying power, per-order contract cap, and a
liquidity clip against open interest where it is known. The binding constraint is
recorded on every ticket, so no size in the journal is ever a hunch.

| Budget | Default | Scope |
| --- | --- | --- |
| Aggregate theoretical max loss | 45% of equity | whole book |
| Per trade max loss | 4.5% | one ticket |
| Per underlying | 12% | one symbol |
| Index bucket (SPY, QQQ, IWM, DIA) | 30% | combined |
| Modelled loss at a 2σ one-week shock | 18% | whole book |
| Beta-weighted net delta | ±25% of equity | SPY-equivalent notional |
| Daily loss breaker | 6% | stops opening, keeps managing exits |
| Hard equity floor | 82% of start | flatten and stand down |

These are aggressive on purpose, and they are visible on purpose. Because every
structure is defined risk, "using the capital" means collateral deployed against a known
worst case, not an open-ended bet.

## The standout: a real portfolio payoff engine

A per-order cap cannot see a book — ten individually sensible spreads on correlated
names are one large bet. So the risk layer rebuilds the whole portfolio as a payoff
curve: every option becomes a piecewise-linear function of its underlying's terminal
price, the curves are summed per underlying, and each underlying's shock is mapped
through its beta onto a common market shock. Because a piecewise-linear function attains
its minimum at a breakpoint, the worst case is computed exactly at the strikes rather
than sampled on a grid.

Crucially, each proposal is priced **as if already filled**, and it is approved only if
the resulting portfolio still satisfies every budget. That is a genuine pre-trade
portfolio check, and it is what lets the engine be aggressive without being reckless.

## Three planes: API, CLI, MCP

Each Alpaca surface does the job it is best at, and only one of them can move money.

- **Execution — `alpaca-py`**, always `paper=True`. The only path that reaches
  `submit_order`, and only after `review_proposal`.
- **Verification — the [Alpaca CLI](https://github.com/alpacahq/cli)**. A second client
  with a second auth path, read before every ticket and again after every submission. If
  the two views of the book disagree, the cycle refuses to trade; if they diverge after a
  fill, new entries freeze until they agree again. Exits stay allowed throughout, because
  a safety check must never be able to trap the book it fired over.
- **Research — the [Alpaca MCP server](https://github.com/alpacahq/alpaca-mcp-server)**.
  A real stdio client calling read-only tools for the daily regime briefing, and as a
  **second source for chain snapshots that the engine cross-checks against the SDK before
  sizing anything**. A stale or crossed quote is the most likely way a fake edge enters
  the model, and the cheapest way to catch it is to ask a second source the same
  question. MCP never places an order: the allow-list is enforced in code, not in a
  prompt.

Detail in [docs/mcp-and-cli.md](docs/mcp-and-cli.md); the full wiring is in
[docs/architecture.md](docs/architecture.md).

## What the LLM is and is not for

The engine runs without it. The strategy is deterministic, the risk layer is code, the
sizing is arithmetic. The one thing code genuinely cannot do is read the news, so the
analyst receives the research plane's headlines alongside the already-built,
already-approved ticket and may raise a **soft veto** on one of five fixed reasons
(`stale_quote`, `duplicate`, `wide_spread`, `event_risk`, `illiquid`). It cannot change a
strike, resize a position, approve something risk rejected, or invent a veto reason. A
hallucinated veto is discarded and any failure fails open, so the unattended loop keeps
running.

## Safety

This project has **no live-trading code path**. `TradingClient` is always built with
`paper=True`, and the process refuses to start if `ALPACA_LIVE_TRADE=true`. Orders are
dry-run until `DRY_RUN=false`. Naked short options are structurally impossible: every
short leg must be paired with a protective long leg in the same ticket, and
`review_proposal` proves it rather than trusting the label on the ticket.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # Windows: copy .env.example .env
```

Paste **paper** keys from the
[Alpaca paper dashboard](https://app.alpaca.markets/paper/dashboard/overview) into `.env`.

```bash
uv run smoke-paper                            # verify keys: clock, account, options BP
uv run scan                                   # signals + ranked opportunities, no trading
uv run python scripts/broker_report.py        # SDK vs CLI, signals, scanner, stress table
uv run run-agent                              # one cycle (dry run)
uv run run-agent --execute                    # send the ticket to paper
uv run run-agent --loop --execute --interval 180
uv run streamlit run app/streamlit_app.py     # dashboard (read-only; never sends orders)
.\scripts\run-forever.ps1 -Execute            # Windows: restart-on-crash wrapper
uv run pytest                                 # 680+ tests, no keys or network needed
uv run ruff check .
```

Windows note: `start-agent.cmd` double-clicks into the same loop (preflight, type
`EXECUTE` to send, second window with `agent-health`), and `stop-agent.cmd` ends it.
Optional; the commands above are enough.

Optional extras: `uv sync --extra llm` for the analyst, `uv sync --extra mcp` for the
research plane. Both fail open when absent.

The Streamlit app never submits an order. Without API keys it still replays
`app/fixtures/demo_journal.jsonl`. On Streamlit Community Cloud, put **paper** keys in
the secrets UI (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `DRY_RUN=true`,
`ALPACA_LIVE_TRADE=false`) — never in the repo.

`--loop` repeats the cycle during the session and polls less often overnight. Options are
day orders, so nothing is sent while the market is closed.

## Layout

| Path | Role |
| --- | --- |
| `src/vrp_engine/config.py` | Settings, every budget as a fraction of equity, paper-only guardrail |
| `src/vrp_engine/alpaca/` | Trading client, bars, quoted chains, order building, CLI and MCP bridges |
| `src/vrp_engine/strategy/` | `signals`, `structures`, `pricing`, `sizing`, `management`, `reset`, `engine` |
| `src/vrp_engine/risk/` | `limits` (defined-risk proof, portfolio budgets), `portfolio` (payoff and stress), `account` (breakers) |
| `src/vrp_engine/agent/` | Cycle loop, LLM analyst, read-only tool surface |
| `src/vrp_engine/journal.py` | Append-only JSONL audit trail |
| `src/vrp_engine/viz.py` | Pure chart data, shared by the live and replayed views |
| `app/streamlit_app.py` | Dashboard: overview, risk, opportunities, journal, how it works |
| `app/fixtures/` | Sample decision journal the hosted demo falls back to |
| `docs/` | Strategy maths, architecture, MCP and CLI |

Every module under `strategy/` except `engine.py` is a pure function of data, which is
why the test suite can replay the entire decision surface offline.

## Configuration

The full list lives in `.env.example`, with every value commented. The ones worth
knowing:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | – | Paper keys |
| `ALPACA_LIVE_TRADE` | `false` | Anything truthy aborts startup |
| `DRY_RUN` | `true` | Build and validate orders without sending them |
| `UNIVERSE` | 14 liquid names | Comma-separated; liquidity is re-checked at runtime |
| `MIN_DTE` / `MAX_DTE` | `1` / `9` | Expiry window; theta per day is highest here |
| `VRP_Z_ENTRY` | `0.15` | How rich or cheap volatility must be to act |
| `MIN_EDGE` / `MIN_WEDGE` | `0.03` / `0.02` | Expected-value gates |
| `KELLY_FRACTION` | `0.35` | Haircut on the full Kelly stake |
| `RISK_BUDGET_PCT` | `0.45` | Aggregate max loss as a fraction of equity |
| `MAX_TRADE_LOSS_PCT` | `0.045` | Per-ticket max loss |
| `ALLOW_LEGACY_UNWIND` | `true` | Lets the engine clear positions it did not open |
| `MCP_ENABLED` | `true` | Research plane; fails open when unavailable |

## Honest risk statement

At a 45% aggregate max-loss budget and 0.35 Kelly, a realistic good week is roughly +8
to +14% and a realistic bad week is −10 to −18%, with the hard floor at −18% forcing a
flatten. The dashboard shows that number at all times rather than hiding it.

## Further reading

- [Strategy](docs/strategy.md) — realised vs implied vol, the wedge, Kelly, the payoff engine
- [Architecture](docs/architecture.md) — cycle wiring and module map
- [MCP and CLI](docs/mcp-and-cli.md) — why three Alpaca surfaces, and why only one can trade

## License

MIT. Paper trading only; nothing here is investment advice.
