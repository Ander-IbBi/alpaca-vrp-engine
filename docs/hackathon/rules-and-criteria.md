# Rules and criteria

Updated with the **kickoff** (28 Aug 2026). If Discord or the landing contradicts this,
what is written there wins.

## Hard requirements (main challenge — mandatory)

1. Teams of **1 to 6**. Register on lablab and **create or join a team**.
2. **Autonomous** agents (and trading apps) on Alpaca.
3. Use the **Alpaca Trading API** (mandatory).
4. Use Alpaca's **MCP server or CLI** (one of the two is enough; on the stream MCP was
   presented as the core of the event — it should *show* in the demo/video).
5. Every strategy **incorporates options trading**.
6. Develop and test on **paper trading**. Zero real capital.

The main challenge is called **Options Alpha Agents**: AI agents designed to
**generate P&L** on Alpaca's platform. The solution has to show a **clear, testable**
strategy, and how the agent:

- identifies opportunities,
- decides in the market,
- **manages the position**,
- and performs **across the whole competition** (not a single demo trade).

Approaches they cited: options, trading agents, *portfolio income*, or others Alpaca
supports.

## Extra challenge (optional): Built in Public

Share progress on **X and LinkedIn**, tagging Alpaca and lablab (handles on the event
landing). The submission form takes **up to 5 post links**.

It does not replace the main challenge. Separate prizes: **$500** for each of the two
social-winning teams, plus **one month of Algo Trader Plus** per member of the winning
team.

## Dates

| Moment | Official time | Your clock (UTC−4) |
| --- | --- | --- |
| Kickoff | Fri 28 Aug (stream) | already happened |
| Discord Q&A | 18:00 CET on kickoff day | already happened |
| Build | 28 Aug – 4 Sep | the whole week |
| **Submissions close** | Fri **4 Sep, 17:00 CET** | Fri **4 Sep 11:00** (CEST, UTC+2) or **10:00** (CET literal, UTC+1) |

The hour is ambiguous, so [week-plan](week-plan.md) resolves it the safe way: work to
**10:00 your clock**. When the countdown hits zero **the form deactivates**, so start
filling it days earlier.

## Judge criteria (order on the stream)

Tony (Alpaca): creative ideas, **risk management**, technical execution, **P&L**.

Joanna, in this order:

1. **P&L performance** — "first and foremost".
2. **Technology implementation** — API, MCP and CLI.
3. **Creativity & originality**.
4. **Presentation and execution**.

They did not give numeric weights.

## Prizes (main)

Prize pool **$6,000**. Top 3 overall: **$2,500 / $1,500 / $1,000**.

## How to play them with *this* repo

**P&L first — and this is why the collar was scrapped.** A collar caps upside by
construction: its best case over a 4.5-day window is a flat line, which cannot place in a
contest scored on P&L. The VRP Engine is built to accumulate: it takes the side the
variance risk premium favours, sizes by fractional Kelly on modelled edge, and runs
**live on paper all week** under `--loop --execute` with a journal behind it. Aggressive,
but every position defined-risk and every budget printed.

**Tech:** all three Alpaca surfaces, each with a distinct job rather than a name-drop —
`alpaca-py` executes, the CLI verifies the book before and after every ticket, and the
agent is a real **MCP client** for the regime briefing and a second quote source. That is
the difference between meeting criterion 2 and scoring on it.

**Creativity:** two-sided volatility trading with a **probability wedge** as the entry
authorisation, plus a portfolio payoff engine that prices each proposal *as if already
filled*. Most entries in a hackathon like this will be a directional bot or a
premium-selling bot; being willing to *buy* premium when it is cheap, and being able to
prove the whole book's worst case, is the differentiator.

**Presentation:** ≤5 min video of *what you built*, GitHub with demo, form copy. Show the
cycle: signal → structure → expected value → sizing → risk → order or stand down. The
stand-down is worth showing — it is what makes the rest credible.

## What the stream did *not* close

- How they **link** the paper account number to P&L (ask on Discord `#…hackathon`
  with the `mentors` tag if it is not on the landing).
- Rubric with percentages.
- Whether the lablab form still asks for slides/cover besides the video (the generic
  guide does; Joanna cited description + video + GitHub/demo).
