# NotebookLM video — sources and prompt

Generate the submission presentation with [NotebookLM](https://notebooklm.google.com)
(Audio or Video Overview). You do not record yourself.

Repo: https://github.com/Ander-IbBi/alpaca-vrp-engine

---

## How to use NotebookLM

1. Create a new notebook.
2. Upload these sources, in this order of importance:
   - this file (`docs/hackathon/video-script.md`) — the brief the narration should follow
   - `docs/strategy.md` — the maths, so nothing gets hand-waved
   - `README.md`
   - `docs/mcp-and-cli.md` (optional; only so the three planes are described accurately —
     do not let the model dwell on them)
3. Do not upload journals, equity exports, screenshots, `.env`, or anything with keys.
4. Paste the prompt below. Single narrator, briefing style, not a two-host debate.
5. Export and upload the file to the lablab form.

---

## Source brief (for NotebookLM to ground on)

### Opening tone

Direct and natural. The feel of: "Hi everyone — this video walks through my submission.
Let's start with the idea behind it." No labels like "pre-submission pitch", no agenda,
no "first, then, finally" table of contents.

### The idea (spend most of the time here)

Most agents in this track will try to forecast the next move. Over one contest week that
is a weak job: returns are noise, and a directional story is impossible to defend
afterwards — a good week and a lucky week look identical.

So this project answers a different question. If you do not know where the market goes,
is there anything about options that is *reliably* mispriced?

There is, and it is well documented: the **variance risk premium**. An option's price
embeds a forecast of how much the underlying will move, and on average that forecast is
too high. Sellers are paid for carrying gap risk, buyers pay for convexity they mostly do
not need, and the difference accrues to whoever is on the selling side.

But "on average" is not a trading edge, and this is the part worth dwelling on. Two things
turn it into one:

1. **Measure it, per underlying, every cycle** — do not assume it. Sometimes implied
   volatility sits *below* realised, and then the correct trade is to *buy* premium. The
   engine is genuinely two-sided, which is unusual: almost every premium strategy is a
   one-way bet that volatility is always overpriced.
2. **Require the specific structure to be mispriced**, not just the underlying. Rich
   implied volatility on a name does not mean a particular five-wide put credit spread has
   positive expected value.

### The number that authorises a trade: the wedge

This is the centrepiece. Take the exact structure being considered and score its payoff
twice, under two different probability distributions of where the underlying ends up:

- once using **realised** volatility — the engine's own view, from the price history
- once using **implied** volatility — the market's own view, which is what the price means

Each gives a probability of winning. The **wedge** is the difference. A positive wedge
says: under my distribution this structure wins more often than the price implies, so the
market is overpaying me to take it. A negative wedge is a hard rejection, no matter how
attractive the credit looks.

That is the whole difference between this and selling premium on faith. And it is written
into every journal entry, so any claim the agent makes can be re-derived from the numbers
it recorded.

Two details worth mentioning because they are where most implementations quietly cheat:

- Expected loss is **integrated over the loss region**, not assumed to be the maximum
  loss. Assuming max loss is the standard way a premium strategy understates its own edge.
- The premium used in every calculation is the mid pulled toward the *unfavourable* side
  of the quote. Slippage is assumed, not hoped away.

### What it actually trades

The sign of the premium picks the side; the trend picks the shape. Everything is
defined-risk, 1 to 9 days to expiry:

- volatility rich, tape flat → **iron condor**
- volatility rich, tape trending → a **credit spread** on the opposite side of the trend
- volatility cheap → a **debit spread** in the direction of the trend
- premium inside a neutral band → **stand down**, and the journal records why

Each shape is built at several widths, and the expected-value layer keeps whichever one
actually pays best rather than a hardcoded width.

One nice detail instead of an earnings calendar: when the front expiry's implied
volatility is much richer than the next expiry's, the market is pricing a dated event, so
the engine blacks that underlying out entirely. Same data it already fetched, and it
catches unscheduled catalysts a calendar would miss.

Candidates are ranked on **expected value per dollar-day of risk**. A two-day and a
nine-day candidate are not comparable on raw edge — the slower one wins simply by having
more time, while tying up collateral four times longer. Dividing by days held makes them
comparable and naturally favours fast theta decay, which is what a one-week window
rewards.

### Sizing: aggressive, but every contract traceable to a formula

The stake starts as a **fractional Kelly** bet on the modelled edge, at 0.35 of full
Kelly, and is then cut by every budget that applies: per trade, per underlying, per
correlation bucket, remaining aggregate budget, options buying power, an order cap, and a
liquidity clip against open interest.

**Which of those constraints bound the size is recorded on the ticket.** That single field
is what makes the aggression auditable rather than arbitrary: nobody has to trust the
engine, they can reconstruct the arithmetic.

### The standout: a real portfolio payoff engine

Per-order caps cannot see a book. Ten individually sensible spreads on correlated names
are one large bet.

So the risk layer rebuilds the whole portfolio as a payoff curve: every option becomes a
piecewise-linear function of its underlying's price, the curves are summed, and each
underlying is shocked through its beta onto one common market move. Because a
piecewise-linear function attains its minimum at a breakpoint, the worst case is computed
*exactly* at the strikes rather than sampled on a grid. It is the same model Alpaca
documents for its own spread margin rule, so the engine's worst case and the broker's
collateral requirement agree.

And the part that matters most: every proposal is priced **as if it had already filled**,
and it is approved only if the resulting portfolio still satisfies every budget. That is a
genuine pre-trade portfolio check. It is what lets the budgets be aggressive — 45 percent
of equity as aggregate theoretical max loss — without being reckless, because the worst
case is always a number, and it is on the dashboard.

### Who decides what

- The **strategy** proposes exactly one ticket per cycle.
- A **risk layer in code** approves or blocks: defined-risk proof, per-ticket caps,
  post-fill portfolio budgets, plus account breakers — a daily loss limit that stops new
  risk while still allowing exits, a drawdown limit measured from a high-water mark, and a
  hard equity floor.
- The **LLM** only narrates and may soft-veto on a fixed list of five reasons. It cannot
  change a strike, resize anything, or approve what risk rejected. A hallucinated reason is
  discarded, and if the model is down the cycle continues.

Risk runs *before* the LLM, deliberately, so no model output can talk its way past a
budget.

Positions are also managed rather than left alone: every cycle walks a fixed ladder over
the open book — stop loss, assignment guard near expiry, profit target at a set fraction
of the premium captured, and a forced exit on expiry day — worst position first, one
action at a time.

### Stack use (brief — do not lecture)

Three Alpaca surfaces, each with a job. The Trading API places every order, paper only,
and it is the only path that can. The CLI reads the same account through a second,
independent client both before a ticket and again after it — if the two disagree the cycle
refuses to trade, and a mismatch after a fill freezes new entries while still allowing
exits. And the agent is itself an **MCP client**: read-only tools for a daily market
briefing, and a second source for the option quotes, cross-checked against the SDK before
anything is sized, because a stale quote is the most likely way a fake edge enters the
model.

Mention these as *how they are used*. Judges know what they are and that the event
required them. No tutorials, and do not frame it as a clever scoring move.

### Closing

Short reflection. The claim is not that this predicts the market — it explicitly does not
try. The claim is a fully autonomous loop that measures a documented mispricing, refuses
to act when its own model says the edge is not there, sizes by arithmetic anyone can check,
and knows the worst case of its entire book at all times.

Be honest about the trade-off: at these budgets a good week is meaningfully positive and a
bad week is meaningfully negative, and the hard floor decides when to stop. That number is
printed on the dashboard rather than hidden.

With more time: a term-structure trade across expiries, and a learned rather than
parametric terminal distribution. Same loop, same risk layer. End cleanly. No "how we plan
to win", no scoreboard meta, no long disclaimer speech.

---

## Prompt to paste into NotebookLM

```text
Using ONLY the sources in this notebook, generate a single-narrator Audio Overview
(or Video Overview if available).

Open naturally, like a person explaining their submission — for example the feel of:
"Hi everyone, this video walks through my submission. Let's start with the idea
behind it." Do NOT call this a pre-submission video, a pitch, a briefing, or an
overview of what you are about to cover. Do NOT list an agenda or say
"first... then... finally..." as a table of contents. Just start.

Audience: technical judges (Alpaca / lablab, Options Alpha Agents). They know
options, credit spreads, iron condors, deltas, implied volatility, Kelly sizing,
paper trading, MCP and CLI. Do not teach those basics. Do not remind them that the
account is paper or that MCP/CLI were mandatory. Do not talk about winning or
scoring points.

WEIGHT THE CONTENT:
- Most of the time: the STRATEGY. Why not directional prediction. The variance risk
  premium and why it has to be measured per underlying rather than assumed. That the
  engine is two-sided: it buys premium when volatility is cheap. The PROBABILITY
  WEDGE as the single number that authorises a trade — scoring the same structure
  under realised volatility and under implied volatility, and requiring our win
  probability to beat the one the price implies. Then: which structures it trades and
  why, ranking by expected value per dollar-day of risk, fractional Kelly sizing with
  the binding constraint recorded on every ticket, and the portfolio payoff engine
  that prices each proposal as if already filled.
- Also cover, more briefly: strategy proposes, a risk layer in code decides, the LLM
  only narrates and may soft-veto; the management ladder on open positions.
- Little time: how the Trading API, CLI and MCP are used in this project — usage
  only, one or two sentences each at most. No tutorials.
- Short close: what the design claims, what it explicitly does not claim, the honest
  risk trade-off, one optional next step. No long disclaimers.

Style:
- One narrator only. No two-host conversation. No "welcome to the show".
- Clear spoken English, short sentences, technical and direct. No hype.
- Explain the maths in words a listener can follow without seeing a formula. Do not
  read equations aloud.
- Flexible length, under five minutes. Do not pad.
- No screen demos, no invented P&L numbers, no file paths, no function-name spam.

Generate now.
```

---

## If the first take is wrong

```text
Regenerate. Open with a natural greeting about explaining the submission — no
"pre-submission" label and no agenda. Spend most of the time on the variance risk
premium and the probability wedge: the same structure scored under realised
volatility and under implied volatility, and a trade only allowed when our win
probability beats the one the price implies. Also make clear the engine buys premium
when volatility is cheap, not only sells it. Keep MCP and CLI to brief usage only.
Cut any talk about winning, scoring, or reminding listeners that trading is paper.
Do not read formulas aloud.
```

---

## Talking points if you present live instead

Ranked by how much they differentiate the submission:

1. The wedge — two measures, one payoff, and a rejection when the model does not beat the
   price.
2. Two-sided on volatility. Almost nobody will be willing to buy premium.
3. The portfolio payoff engine, and the pre-trade check that prices a proposal as if
   already filled.
4. Expected value per dollar-day of risk, which makes candidates of different maturities
   comparable.
5. The binding constraint recorded on every ticket, so aggressive sizing is auditable.
6. Three broker planes with three distinct jobs, and MCP kept read-only by an allow-list
   in code rather than by a prompt.
