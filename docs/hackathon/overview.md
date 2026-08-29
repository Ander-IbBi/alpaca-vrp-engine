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

A system that reads the account and the market → proposes a collar (or seeds SPY) →
the risk layer vetoes or approves → the LLM explains → executes on the Alpaca
**paper** account → shows up on a dashboard.

## Required stack

| Piece | Role | In this repo |
| --- | --- | --- |
| **Trading API** | Mandatory | `src/options_agent/alpaca/` with `alpaca-py` |
| **MCP server** | Mandatory MCP **or** CLI | Documented in the README; useful in Cursor |
| **CLI** | Alternative to MCP | Documented; paper by default |
| **Paper trading** | Simulated money, real data | The only mode in the code |
| **Options** | The track | `strategy/overlay.py` (collar) + `alpaca/orders.py` |

Judges **do not see your IDE**. At kickoff the minimum was: description + video ≤5 min
+ GitHub with demo. The week's P&L counts as much as the repo.

## How you win

1. Meet the hard requirements (API + MCP/CLI, options, new paper account).
2. Make it obvious this is an **agent** with a risk layer, not a script that buys a call.
3. Do not blow up the account: a boring, explainable curve beats an all-in.
4. Presentation: README, demo URL, video ≤5 min, slides.

Criteria (kickoff): P&L first, then API/MCP/CLI, creativity, presentation.
Main prizes: **$2,500 / $1,500 / $1,000**. Extra social: **$500** × 2 teams.

## Our angle

**Aggressive collar**: the agent seeds 100 SPY and covers with a put (~delta −0.20)
financed by selling a call (~delta +0.20). Defined risk (floor at the put, ceiling at
the call). One playbook, no mid-week redesign: if it is already collared, hold.

If kickoff pushes toward volatility or pure alpha, you swap `strategy/` and the rest
of the system (Alpaca, risk, journal, UI) stays the same.

## Links

- Event: https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon
- Live: https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live
- Discord: https://discord.gg/lablabai · Twitch: https://www.twitch.tv/lablabai
- Alpaca paper: https://app.alpaca.markets/paper/dashboard/overview
- MCP: https://github.com/alpacahq/alpaca-mcp-server · CLI: https://github.com/alpacahq/cli
- Submission: https://lablab.ai/delivering-your-hackathon-solution
