# What you are walking into: Alpaca × lablab hackathon

Text to reread cold. The operational notes live in [`docs/hackathon/`](../hackathon/overview.md).

## The event

lablab runs one-week online AI hackathons, with delivery through their platform
(repo, video, demo, slides). Alpaca sponsors this one because it wants to see **agents
that send real orders** to its brokerage, in a paper environment: simulated money, real
market.

The track is **Options Alpha Agents**. That bounds the field: you do not win with a
bot that buys SPY. You have to use **options**. "Alpha" here means extracting or
protecting value systematically, not guessing the price.

## Three interfaces, one broker

With your Alpaca keys you can operate in three ways:

1. **Trading API** (`alpaca-py`) — what the product uses. Reproducible: a judge clones
   the repo and runs it.
2. **MCP server** — translates LLM tools into Alpaca HTTP calls. That is what lets
   Cursor or Claude query your account in conversation.
3. **CLI** — the same thing from the terminal, paper by default.

All three point at **the same account**. That is why the repo forces `paper=True` and
aborts if someone sets `ALPACA_LIVE_TRADE=true`.

## Paper trading

A parallel account with ~100k simulated and real quotes. Fills are not identical to
live (optimistic fills, illiquid options), so it is good for demonstrating the system,
not for estimating a credible Sharpe.

The event asks for a **new** paper account so the P&L judges see is the week's.

## What our agent does

An **aggressive collar** on a long SPY book: a protective put financed by a short
call, same expiry, 21–45 DTE.

- **Intuition:** the put sets a floor; the call collects premium and cuts the ceiling.
  More aggressive than a standalone put because the net premium is small.
- **Defined risk:** max loss ≈ `(entry − put_strike) × 100 + collar net`.
  The risk layer still forbids selling naked options; the short call requires 100
  covering shares per contract.
- **Size:** one contract covers 100 shares. The playbook seeds 100 SPY and opens
  **one** collar. Never more contracts than shares / 100.
- **Skip:** if the collar already covers the book, hold. No rolling or structure
  change mid-week (the chosen expiry lasts longer than the event).

## What an "agent" is for the judges

It is not a chat in the IDE. It is a program that:

1. **Observes** — account, market clock, option chain.
2. **Reasons** — rules and, optionally, an LLM.
3. **Acts** — sends orders, always through a risk layer it cannot switch off.
4. **Explains itself** — JSONL journal and dashboard.

That fourth point is the one others usually miss, and the one that shows in a 5-minute
video.

## Silly mistakes that cost the submission

- Not creating a team on lablab (even if you are going solo).
- Private repo.
- Video as a link when they ask for an MP4 file.
- Demo down on cutoff day.
- Paper account without **options enabled**: check before Friday.
- Leaving `.env` in the repo.

Continue with the [repo guide](../repo-guide.md) to see how this maps onto code.
