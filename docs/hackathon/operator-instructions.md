# What you need to do

Instructions for **running the hackathon**, not for programming. The code is already in
place: the **VRP Engine** — defined-risk option structures selected on the variance risk
premium, paper only, dry-run by default.

Kickoff already happened (28 Aug). Brief in [rules-and-criteria](rules-and-criteria.md).
Submission cutoff: **4 Sep 17:00 Central European Summer Time = 15:00 UTC = 11:00 your
clock**. Treat 10:00 your clock as the deadline so a one-hour ambiguity in "CET" cannot
cost the submission.

Judges **do not see Cursor**. They see: the paper account (week P&L) + repo + demo +
video ≤5 min.

---

## Status now (Saturday 29, early morning)

| Item | Status |
| --- | --- |
| Code / tests | Ready. 586 tests and ruff green, no network or keys. |
| Strategy | **VRP Engine**: credit spreads, debit spreads and iron condors chosen by the variance risk premium, sized by fractional Kelly, checked against a portfolio payoff engine. |
| Old strategy | Archived, intact, at https://github.com/Ander-IbBi/alpaca-collar-overlay (tag `restore-collar-playbook`). |
| GitHub | Public: https://github.com/Ander-IbBi/alpaca-vrp-engine — CI green. |
| Alpaca CLI | Installed in `~/.alpaca-cli` with a paper session logged in. |
| Alpaca MCP | Used **in code** by the agent's research plane, and in Cursor for development. |
| Submission account | `PA3GMY396XY9` — $100,000, **flat**, options level 3. Confirmed by the SDK *and* the CLI. |
| Inherited book | **None.** Nothing to unwind; the engine starts on a clean account. |
| Dry run | `broker_report.py` runs clean end to end: two agreeing broker views, 14 signals, a ranked scanner, an empty stress table. |
| Agent loop | **Not running**, and it cannot: the market opens **Monday 31 at 9:30**. |

The only thing left to generate P&L is to start on Monday.

### Account identity: settled

An earlier draft of this file worried that `.env` might point at a different account from
the one visible through MCP (`PA3YMS2WX13Z`, which held the old collar). It does not.
`uv run smoke-paper` and the CLI both report **`PA3GMY396XY9`**, flat at $100,000. That is
the number to put on the submission form. The account with the collar is a different,
unused one — leave it alone.

---

## Schedule (write both down)

You are on UTC−4 (New York time in summer).

| What | UTC | Your clock |
| --- | --- | --- |
| Kickoff + registration closed | Fri **28 Aug 15:00** | Fri **28 Aug 11:00** |
| Paper trading week | 28 Aug – 4 Sep | same |
| Video / slides / demo day | Thu **3 Sep** | all day |
| Submission cutoff | Fri **4 Sep 15:00** | Fri **4 Sep 11:00** |

Submit on **the night of 3 Sep** or **the morning of the 4th**. The 4th is not for
inventing a strategy.

---

## Tabs you should keep open

1. **Event** — https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon
2. **Live / milestones** — https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live
3. **lablab Discord** — https://discord.gg/lablabai
4. **Team dashboard** (submission) — your lablab team page, not Alpaca's
5. **Alpaca paper** — https://app.alpaca.markets/paper/dashboard/overview
6. **How to submit** — https://lablab.ai/delivering-your-hackathon-solution

Track: **Options Alpha Agents**.

---

## This weekend (market closed)

No trading: the exchange opens on Monday. You can still close these:

### 1. lablab

- [ ] Team created in the dashboard **even if you are going solo** (1 to 6 people).
- [ ] lablab Discord, hackathon channel.
- [ ] Start filling in the submission form (it saves; do not wait until day 4).

### 2. Built in Public (optional, separate $500 prize)

Posts on **X and LinkedIn** tagging Alpaca and lablab. The form takes **5 links**. Things
you can already tell without spoiling anything: the public repo, why the engine is
*two-sided* on volatility instead of a premium-selling bot, the probability wedge as the
one number that authorises a trade, the portfolio payoff engine, and the three Alpaca
planes (API executes, CLI verifies, MCP researches).

### 3. Dry-run proof (do this one)

```powershell
uv run smoke-paper                       # keys, clock, account, options buying power
uv run scan                              # signals + ranked opportunities, no trading
uv run python scripts/broker_report.py   # SDK vs CLI, signals, scanner, stress table
uv run run-agent                         # one full cycle, dry run
```

Nothing is sent without `--execute`. Read the `rationale` on the proposal: it should name
the structure, the realised and implied vol, the wedge, and which budget bound the size.
If it does not, something is wrong and you want to know now, not Monday at 09:31.

**What the Friday-night run actually showed**, so Monday holds no surprises: both broker
views agree on a flat $100k account; all 14 signals compute; realised volatility currently
sits *above* implied on most single names, so the engine's stance is `buy_vol` rather than
the usual premium selling. The catch is that a cheap option still needs a direction, so
`buy_vol` on a `flat` tape produces nothing by design, and the scanner found only one
candidate across the universe (an AMZN call debit spread).

That is the strategy working, not failing — but be ready for the engine to trade *rarely*
if that regime holds into Monday. Do not react by loosening thresholds. If by Tuesday the
scanner is still producing almost nothing, the useful question is whether the trend
classifier is calling too many names `flat`, and that is a diagnosis to make from the
journal, not a knob to turn in a panic.

---

## Monday 31 — P&L starts

The market opens at **9:30** (your time).

### A. One supervised cycle first

The account is flat, so there is nothing to unwind. Run a single cycle by hand at about
**09:45** — the engine deliberately skips the first 15 minutes, because opening quotes are
wide and unstable and they flatter every edge estimate:

```powershell
uv run run-agent --execute
```

Read the output before starting the loop. You want to see: signals for most of the
universe, a scanner with candidates, and either a ticket with a rationale naming the
structure and the wedge, or an explicit stand-down. Both are healthy. A crash or an empty
signals table is not.

### B. Let it run

```powershell
uv run run-agent --loop --execute --interval 180
```

Three minutes, not fifteen: the engine trades 1–9 DTE structures, where a profit target
or an assignment guard can become urgent inside one fifteen-minute nap.

That window **must stay open**. If you close the PC or the terminal, the agent stops.
Nothing terrible happens — the positions live on Alpaca, and every one of them is
defined-risk — but nothing gets managed either. Restart with the same command.

Better, use the shipped restart-on-crash wrapper so a transient fault does not end your
week:

```powershell
.\scripts\run-forever.ps1 -Execute          # add -Interval 180 to change the cadence
.\scripts\run-forever.ps1                   # same thing, dry run
```

The loop already survives API faults internally — a failed cycle is journalled and the
next one runs — so the wrapper only covers the rarer case where the process itself dies.
Ctrl+C twice to stop it for good: once for the child, once for the wrapper.

Optional, in another terminal, to see what the judges will see:

```powershell
uv run streamlit run app/streamlit_app.py
```

Leave the "Send order to paper" toggle **off** unless you want a cycle by hand. The loop
in the other window already executes.

### C. LLM (optional, recommended for the demo)

```powershell
uv sync --extra llm      # analyst: regime briefing + soft veto
uv sync --extra mcp      # research plane: news, movers, second quote source
```

Uncomment `OPENAI_API_KEY` in `.env`. Without a key the rule-based analyst approves and
the cycle continues; without `mcp` the research plane reports unavailable and the cycle
continues. Both are worth having on for the video: the briefing and the quote
cross-check are visible in the journal.

---

## Monday 31 – Wednesday 2: what to watch

Do not redesign the strategy mid-week. Do not touch the budget percentages because two
days went badly — that is exactly the decision the fixed budgets exist to prevent.

Look, do not touch:

| Signal in the journal | What it means | What you do |
| --- | --- | --- |
| "stand down: VRP_z inside the band" | No measurable edge today on that name | Nothing. Not trading is a decision |
| Many names showing `buy_vol` with a `flat` trend | Volatility is cheap but there is no direction to point a debit spread at, so those names produce nothing by design | Nothing. Expect quiet days; this is the case the Friday dry run showed |
| "event blackout: term slope …" | Front expiry is pricing a dated catalyst | Nothing. That is the earnings guard working |
| "wedge −0.01 below floor" | The model does not beat the market's own odds | Nothing |
| "best candidate … rejected" | The scanner ran and nothing cleared | Nothing |
| "no ranked candidate fitted inside the risk budgets" | The book is already as loaded as allowed | Nothing |
| "profit target: captured 58% of the credit" | An exit, as designed | Nothing |
| "assignment guard" | Short strike near the money into expiry | Nothing, that is the point |
| "Research plane disagrees with the SDK on this structure's quotes" | MCP and the SDK differ by >15% | Nothing; refusing a stale edge is correct |
| "Broker views disagree… stale book" | SDK and CLI do not match | Check the dashboard; usually a half fill, resolves itself |
| "Freezing new entries until the SDK and the CLI agree" | Post-fill mismatch | Exits still work. It thaws on the next agreeing cycle |
| "cancelled a stale … order" | A limit sat unfilled at a mid that moved | Nothing |
| "Risk layer blocked the trade" | The code saved you | Do **not** place the order by hand |
| "daily loss breaker" | Down 6% today: no new risk, exits still managed | Nothing. Do not "make it back" |
| "flatten" / "equity floor" | Down 18% from the high-water mark, or below 82% of start | Let it flatten. Do not restart with bigger size |
| Smoke or a cycle mentions **live** | Stop immediately | Wrong keys; go back to paper |
| `.env` in a commit | Stop | Never push it. Only the empty `.env.example` |

Once a day:

1. Alpaca dashboard: equity, positions, still **paper**.
2. Last lines of `data/journal/agent.jsonl` (or the Streamlit journal table).
3. Discord / lablab live in case rules change.

A week's P&L is noise. An explainable curve with a printed worst case beats an all-in.

---

## Thursday 3 — what you have to **submit**

Submit from the **lablab team dashboard**, not by email. Field details in
[submission](submission.md).

Prepare, in this order:

1. **Public GitHub repo.** Private = judges do not score code. README in English (the one
   already there). Zero secrets.
2. **Demo with a URL** a judge can open without installing anything: the Streamlit
   dashboard — equity curve, open structures, the ranked scanner, the payoff curve with
   stress markers, the journal.
3. **MP4 video** ≤ 5 min, ≤ 300 MB, **uploaded** to the form.
4. **Slides PDF.**
5. **Cover** PNG/JPG 16:9.
6. Copy: short title, short description (≤255), long (≥100 words): the variance risk
   premium, defined-risk structures, the wedge, the portfolio stress engine, Alpaca API +
   MCP + CLI, paper only.

### Video (NotebookLM)

Generate the pitch in NotebookLM — you do not record yourself. Sources and the prompt to
paste: [video-script](video-script.md). Problem → approach → how it runs → reflection.
Export and upload the MP4 the form asks for.

---

## Friday 4 — cutoff 11:00 your time

- [ ] Form submitted **with margin** (ideal: already sent on the 3rd).
- [ ] Public repo, live demo URL, video plays, PDF opens.
- [ ] Correct track.
- [ ] The paper account number from `uv run smoke-paper`, if they ask for it.

That day: no new strategies, no large refactors.

---

## Never do this

- **Live** trading or `ALPACA_LIVE_TRADE=true`.
- Push `.env` or paste keys in the README / issues / public Discord.
- A **naked** options short by hand "to improve P&L". The engine cannot build one; you
  can, and it would break the one claim the repo makes about itself.
- Raise `RISK_BUDGET_PCT` or `KELLY_FRACTION` mid-week to chase a loss.
- Switch strategy on Thursday 3 because "it is not working".
- Leave the only `--loop` on a PC that sleeps. Positions are defined-risk, so nothing
  explodes, but exits do not happen either.

---

## Header commands (copy and paste)

From `C:\Users\User\Projects\alpaca-options-agent`:

```powershell
uv run smoke-paper
uv run scan
uv run python scripts/broker_report.py
uv run run-agent
uv run run-agent --execute
uv run run-agent --loop --execute --interval 180
.\scripts\run-forever.ps1 -Execute
uv run streamlit run app/streamlit_app.py
uv run pytest
uv run ruff check .
```

Without `--execute`, **nothing is sent** to the broker.

---

## If you get lost

| Question | Where |
| --- | --- |
| What is the event? | [overview](overview.md) |
| What do they score? | [rules-and-criteria](rules-and-criteria.md) |
| Form fields? | [submission](submission.md) |
| How do I make the video? | [video-script](video-script.md) (NotebookLM) |
| Short calendar? | [week-plan](week-plan.md) |
| How does the strategy actually work? | [strategy](../strategy.md) |
| Which folder do I touch? | [architecture](../architecture.md) |
| What is MCP vs the CLI? | [mcp-and-cli](../mcp-and-cli.md) |
