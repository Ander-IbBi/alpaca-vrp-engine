# What you are walking into: Alpaca × lablab hackathon

Text to reread cold. The operational notes live in [`docs/hackathon/`](../hackathon/overview.md).

## The event

lablab runs one-week online AI hackathons, with delivery through their platform
(repo, video, demo, slides). Alpaca sponsors this one because it wants to see **agents
that send real orders** to its brokerage, in a paper environment: simulated money, real
market.

The track is **Options Alpha Agents**. That bounds the field: you do not win with a
bot that buys SPY. You have to use **options**. "Alpha" here means extracting value
systematically, not guessing the price — and since the judges score P&L first, extracting
beats protecting.

## Three interfaces, one broker

With your Alpaca keys you can operate in three ways:

1. **Trading API** (`alpaca-py`) — what places every order. Reproducible: a judge clones
   the repo and runs it.
2. **MCP server** — translates tool calls into Alpaca HTTP calls. It is what lets Cursor
   or Claude query your account in conversation — and, in this repo, what the agent
   itself calls as a client for research.
3. **CLI** — the same account from the terminal, paper by default.

All three point at **the same account**. That is the useful part and the dangerous part:
useful because two independent clients can check each other, dangerous because a stale
read from any of them is indistinguishable from the truth until you compare. It is also
why the repo forces `paper=True` and aborts if someone sets `ALPACA_LIVE_TRADE=true`.

## Paper trading

A parallel account with ~100k simulated and real quotes. Fills are not identical to
live (optimistic fills, illiquid options), so it is good for demonstrating the system,
not for estimating a credible Sharpe.

The event asks for a **new** paper account so the P&L judges see is the week's.

## What our agent does

The **VRP Engine**: it trades the gap between the volatility options are priced at and
the volatility the underlying actually delivers, with defined-risk structures across a
multi-underlying universe, 1–9 DTE.

- **Intuition:** insurance is usually sold above its fair price, so selling it has an
  edge — but not always, and not on every name at once. The engine measures the gap per
  underlying each cycle and takes whichever side it favours: credit spreads and iron
  condors when volatility is rich, debit spreads when it is cheap.
- **The authorisation:** a trade needs a positive **wedge** — the win probability under
  the engine's own distribution minus the win probability the market's price implies.
  Rich volatility alone is not enough; the specific structure has to be mispriced.
- **Defined risk:** every short leg is paid for by a long leg of the same type and expiry
  inside the same ticket, so max loss is a number, not an exposure. The risk layer proves
  this from the legs rather than trusting the label — naked shorts are unrepresentable.
- **Size:** fractional Kelly on the modelled edge, then cut by per-trade, per-underlying,
  per-bucket and aggregate budgets, buying power and liquidity. The binding constraint is
  written on every ticket.
- **Managed:** open positions are walked every cycle against a fixed ladder — stop loss,
  assignment guard, profit target, expiry-day forced exit — and exits are their own
  all-to-close ticket, because Alpaca only accepts a multi-leg order whose legs cover each
  other inside that same ticket.

Why not the collar we designed first? Judges scored **P&L first**, and a collar caps
upside by construction. It is archived intact at
[alpaca-collar-overlay](https://github.com/Ander-IbBi/alpaca-collar-overlay).

## What an "agent" is for the judges

It is not a chat in the IDE. It is a program that:

1. **Observes** — account, market clock, bars, option chains, and its own open book.
2. **Reasons** — deterministic rules and arithmetic; an LLM only narrates and may
   soft-veto.
3. **Acts** — sends orders, always through a risk layer it cannot switch off.
4. **Explains itself** — JSONL journal and dashboard.

That fourth point is the one others usually miss, and the one that shows in a 5-minute
video. Ours goes further than logging the decision: it logs the numbers the decision was
made from — realised and implied vol, both win probabilities, the wedge, the expected
value, and which budget bound the size — so a judge can re-derive the trade rather than
take our word for it.

## Silly mistakes that cost the submission

- Not creating a team on lablab (even if you are going solo).
- Private repo.
- Video as a link when they ask for an MP4 file.
- Demo down on cutoff day.
- Paper account without **options enabled**: check before Friday.
- Leaving `.env` in the repo.

Continue with [architecture](../architecture.md) to see how this maps onto code, or
[strategy](../strategy.md) for the maths behind the edge.
