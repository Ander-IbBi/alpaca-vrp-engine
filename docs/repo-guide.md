# Repo guide — what each piece is and why

If you do not know where a change belongs, start here.

## Quick map

```
alpaca-options-agent/
  README.md            ← what judges and GitHub see
  AGENTS.md            ← context for Cursor and other AIs
  pyproject.toml       ← dependencies and scripts (uv)
  .env.example         ← variable template; copy to .env
  src/options_agent/   ← the product
  app/                 ← Streamlit demo
  scripts/             ← smoke test and one-cycle runner
  tests/               ← tests with no network and no keys
  notebooks/           ← research, not the deliverable
  docs/                ← competition notes and guides
  .github/workflows/   ← CI: tests + lint on every push
```

## The flow, in one sentence

`agent/loop.py` builds a **context** → `strategy/` proposes a **ProposedTrade** →
`risk/` approves or vetoes it → the LLM explains (soft veto) → `alpaca/orders.py`
turns it into a ticket → `journal.py` records it → Streamlit shows it.

## `src/options_agent/`

| Module | What it is for | What it is not |
| --- | --- | --- |
| `config.py` | Load `.env` and **abort if live** | A place to store keys |
| `journal.py` | JSONL trail of each decision | A debug logger |
| `alpaca/client.py` | Paper client, account, positions, prices | A client you can point at live |
| `alpaca/options.py` | Chain with bid/ask/delta/IV, normalized to `OptionCandidate` | The strategy |
| `alpaca/orders.py` | Build and send the ticket (shares, single or multi-leg) | Who decides whether to trade |
| `risk/limits.py` | Per-order limits, collar max loss, covered call | Negotiable by the LLM |
| `risk/account.py` | Circuit breaker (equity floor, daily loss) | A per-position stop loss |
| `strategy/base.py` | Shared vocabulary (`ProposedTrade`, `StrategyContext`) | Market logic |
| `strategy/overlay.py` | Aggressive collar: SPY seed + put/call by delta | A meta-agent that switches products |
| `agent/loop.py` | The full cycle, closed market, `--loop` | The UI |
| `agent/tools.py` | Tools the LLM can call (read-only) | A back door to the broker |
| `agent/llm.py` | Explains the cycle; soft veto, fail-open | Who decides the trade |

### Why risk and strategy are separate

An LLM can hallucinate a nonsense trade. The strategy proposes; `review_proposal`
decides. The model only explains and, at most, applies a whitelist veto
(`stale_quote`, `duplicate`, `wide_spread`). If the LLM is down, the cycle continues.

### The actually testable part

`select_collar()` is a pure function: you pass contracts with delta and mid, spot and
date, and it returns put and call. That is why `tests/test_strategy.py` checks the
collar without an open market.

## `app/`

`streamlit_app.py` is what you deploy and what judges open: account metrics, positions,
a button to run a cycle, and the journal. If it is not visible here, for the jury it
does not exist.

## `scripts/`

- `smoke_paper.py` — do the keys work? Clock + account. Run this first.
- `run_agent.py` — one cycle; `--execute` to send the order; `--loop` to leave it running.

## `tests/`

Config, risk, strategy (collar + seed), orders + journal, options (OCC/quotes),
LLM (parsing and fail-open). None of them use the network, so they run in CI without
secrets.

## `docs/`

Competition notes and this technical map. The Obsidian vault `Vault/deep-hedging`
stays separate, for theory.

## Commands you will repeat

```bash
uv run pytest                                 # tests
uv run ruff check .                           # lint
uv run python scripts/run_agent.py            # dry-run cycle
uv run python scripts/run_agent.py --loop --interval 900
uv run streamlit run app/streamlit_app.py     # demo
```
