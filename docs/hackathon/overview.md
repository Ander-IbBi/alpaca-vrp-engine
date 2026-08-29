# Overview — Alpaca AI Trading Agents Hackathon

> Map of the competition. Rules in [rules-and-criteria](rules-and-criteria.md),
> submission in [submission](submission.md), calendar in [week-plan](week-plan.md).

## What it is

An **online** hackathon by [lablab.ai](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
with Alpaca. **28 August – 4 September 2026**. A single track: **Options Alpha Agents**.
~2600 registered.

This is not an academic paper: it is an **AI agent that trades options on paper**
plus a **demo** judges can open and understand in three minutes.

## What to build (one sentence)

A system that reads the account and the market → measures where options are mispriced →
proposes a defined-risk structure → the risk layer vetoes or approves → the LLM explains
→ executes on the Alpaca **paper** account → shows up on a dashboard.

## Required stack

| Piece | Role | In this repo |
| --- | --- | --- |
| **Trading API** | Mandatory | `src/vrp_engine/alpaca/` with `alpaca-py`, the only path to `submit_order` |
| **MCP server** | Mandatory MCP **or** CLI | `alpaca/mcp_bridge.py`: the agent is a real MCP client (research plane) |
| **CLI** | Alternative to MCP | `alpaca/cli_bridge.py`: pre-trade cross-check and post-fill reconciliation |
| **Paper trading** | Simulated money, real data | The only mode in the code |
| **Options** | The track | `strategy/structures.py` (verticals + condors) + `alpaca/orders.py` |

We use all three rather than the minimum one, and each has a distinct job — see
[mcp-and-cli](../mcp-and-cli.md).

Judges **do not see your IDE**. At kickoff the minimum was: description + video ≤5 min
+ GitHub with demo. The week's P&L counts as much as the repo.

## How you win

1. Meet the hard requirements (API + MCP/CLI, options, new paper account).
2. Make it obvious this is an **agent** with a risk layer, not a script that buys a call.
3. Be aggressive where it is measurable and bounded where it is not: every trade has a
   printed worst case, and the account has breakers the model cannot switch off.
4. Presentation: README, demo URL, video ≤5 min, slides.

Criteria (kickoff): P&L first, then API/MCP/CLI, creativity, presentation.
Main prizes: **$2,500 / $1,500 / $1,000**. Extra social: **$500** × 2 teams.

## Our angle

**VRP Engine.** Options are usually priced above what the underlying delivers, and
sometimes below. The agent measures that gap per underlying every cycle, takes whichever
side it favours — credit spreads and iron condors when volatility is rich, debit spreads
when it is cheap — and only opens a position when its own probability model beats the
odds the market's own price implies. Everything is defined risk, sized by fractional
Kelly, and cleared by a portfolio payoff engine that prices each proposal *as if already
filled*.

Judges asked for P&L first. A collar (our first design, now archived at
[alpaca-collar-overlay](https://github.com/Ander-IbBi/alpaca-collar-overlay)) caps upside
by construction, so its best case over 4.5 days is a flat line. This one is built to
accumulate.

The strategy sits behind a `Strategy` protocol, so the rest of the system (Alpaca planes,
risk, journal, UI) is independent of it.

## Links

- Event: https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon
- Live: https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live
- Discord: https://discord.gg/lablabai · Twitch: https://www.twitch.tv/lablabai
- Alpaca paper: https://app.alpaca.markets/paper/dashboard/overview
- MCP: https://github.com/alpacahq/alpaca-mcp-server · CLI: https://github.com/alpacahq/cli
- Submission: https://lablab.ai/delivering-your-hackathon-solution
