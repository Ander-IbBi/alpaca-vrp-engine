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
| **Submissions close** | Fri **4 Sep, 17:00 CET** | Fri **4 Sep ~11:00** (if CET = CEST, UTC+2) |

When the countdown hits zero, **the form deactivates**. Start filling it days earlier.

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

**P&L:** the collar has to be **live on paper all week**, not just in a screenshot.
Unattended loop (`--loop --execute`) + journal. A week is noise; no all-in.

**Tech:** the product trades with `alpaca-py` (Trading API). MCP and/or CLI have to
appear in the README, the video, and, if you can, a real use (account/chain
inspection). Without that, criterion 2 falls even if the collar is correct.

**Creativity:** defined-risk hedge overlay (put + covered call), not another momentum
bot. Fits "portfolio income" / position management.

**Presentation:** ≤5 min video of *what you built*, GitHub with demo, form copy. Show
the cycle: opportunity → decision → risk → order or hold.

## What the stream did *not* close

- How they **link** the paper account number to P&L (ask on Discord `#…hackathon`
  with the `mentors` tag if it is not on the landing).
- Rubric with percentages.
- Whether the lablab form still asks for slides/cover besides the video (the generic
  guide does; Joanna cited description + video + GitHub/demo).
