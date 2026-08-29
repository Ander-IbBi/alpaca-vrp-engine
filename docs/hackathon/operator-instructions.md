# What you need to do

Instructions for **running the hackathon**, not for programming. The code is
already in place: an aggressive collar on SPY, paper only, dry-run by default.

Kickoff already happened (28 Aug). Brief in [rules-and-criteria](rules-and-criteria.md).
Submission cutoff: **4 Sep 17:00 CET** (≈11:00 your time).

Judges **do not see Cursor**. They see: the paper account (week P&L) + repo + demo +
video ≤5 min.

---

## Status now (Friday 28, evening)

| Item | Status |
| --- | --- |
| Code / tests | Ready. 91 tests and ruff green, no network or keys. |
| Strategy | Financed collar + **active management** (roll ITM call, roll for expiry, harvest the put). |
| Submission account | `PA3GMY396XY9`, $100k, **options level 3**, empty book. Keys already in `.env`. |
| GitHub | Public: https://github.com/Ander-IbBi/alpaca-options-agent — CI green. |
| Alpaca CLI | Installed in `~/.alpaca-cli` with a paper session logged in. |
| Agent loop | **Not running**, and it cannot: the market opens **Monday 31 at 9:30**. |

The only thing left to generate P&L is to start on Monday.

---

## Schedule (write both down)

You are on UTC−4 (New York time in summer).

| What | UTC | Your clock |
| --- | --- | --- |
| Kickoff + registration closes | Fri **28 Aug 15:00** | Fri **28 Aug 11:00** |
| Paper trading week | 28 Aug – 4 Sep | same |
| Video / slides / demo day | Thu **3 Sep** | all day |
| Submission cutoff | Fri **4 Sep 17:00 CET** | Fri **4 Sep ~11:00** |

Submit on **the night of 3 Sep** or **the morning of the 4th**. The 4th is not for
inventing a strategy.

---

## Tabs you should keep open

Pin these (or keep them handy):

1. **Event** — https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon
2. **Live / milestones** — https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live
3. **Kickoff (Twitch)** — https://www.twitch.tv/lablabai
4. **lablab Discord** — https://discord.gg/lablabai
5. **Team dashboard** (submission) — your lablab team page, not Alpaca's
6. **Alpaca paper** — https://app.alpaca.markets/paper/dashboard/overview
7. **How to submit** — https://lablab.ai/delivering-your-hackathon-solution
8. **Team guide** — https://lablab.ai/getting-started-guide

Track to pick: **Options Alpha Agents**.

---

## This weekend (market closed)

No trading: the exchange opens on Monday. You can still close these:

### 1. lablab

- [ ] Team created in the dashboard **even if you are going solo** (1 to 6 people).
- [ ] lablab Discord, hackathon channel.
- [ ] Start filling in the submission form (it saves; do not wait until day 4).

### 2. Built in Public (optional, separate $500 prize)

Posts on **X and LinkedIn** tagging Alpaca and lablab. The form takes **5 links**.
Ideas you can already tell: the public repo, why a collar instead of a directional
bot, and the SDK/CLI cross-check.

### 3. Quick check (optional)

```powershell
uv run python scripts/broker_report.py
```

Shows the account as seen by the SDK and by the CLI, plus a dry-run cycle.

---

## Monday 31 — P&L starts

The market opens at **9:30** (your time). The agent already knows what to do.

### A. Seed and collar

```powershell
# Cycle 1: buy 100 SPY
uv run python scripts/run_agent.py --execute

# Wait for the fill (watch the dashboard). Cycle 2: open the collar
uv run python scripts/run_agent.py --execute
```

If the market is closed, `--execute` **does not send** options (they are day orders).
Wait for the open.

When you see 100 SPY + long put + short call, the playbook is on. From then on:

```powershell
uv run python scripts/run_agent.py --loop --execute --interval 900
```

That window **must stay open** (every 15 min: hold, journal, circuit breaker). If you
close the PC or the terminal, the agent stops. Nothing terrible happens (the collar
stays on Alpaca); when you come back, run the same command.

Optional, in another terminal, so you can see what the judges will see:

```powershell
uv run streamlit run app/streamlit_app.py
```

Leave the "Send order to paper" toggle **off** unless you want a cycle by hand. The
loop in the other window already executes.

### D. LLM (optional, recommended for the demo)

If you want explanations in the journal:

```powershell
uv sync --extra llm
```

Uncomment `OPENAI_API_KEY` in `.env`. Without a key, the rule-based advisor approves
and the cycle continues as usual.

---

## Monday 31 – Wednesday 2: what to watch

Do not redesign the strategy mid-week. The agent already manages on its own.

Look, do not touch:

| Signal | What it means | What you do |
| --- | --- | --- |
| «overlay already on … Hold: short 790c safe; …» | Correct: it looked and decided to wait | Nothing |
| «Roll the short call up» | SPY rose above the call; recovers upside | Nothing, that is what it should do |
| «Roll the collar out» | Expiry was getting close | Nothing |
| «Harvest the hedge» | The put doubled in a drop; takes the profit | Nothing |
| «Broker views disagree… stale book» | SDK and CLI do not match | Check the dashboard; usually a half fill. It resolves itself |
| «Open overlay orders… waiting» | There is an unfilled limit | Nothing; the limit is day, the next cycle retries |
| «Risk layer blocked» | The code saved you | Do not force the order by hand |
| Equity < ~80k or daily loss > 1500 | Circuit breaker | The agent stops sending. Do not "recover" by hand with more risk |
| Half position (only put or only call) | The agent is waiting on purpose | Do not sell another call on the same 100 shares |
| Smoke or cycle talks about **live** | Stop immediately | Wrong keys; go back to paper |
| `.env` in a commit | Stop | Never push it. Only the empty `.env.example` |

Once a day:

1. Alpaca dashboard: equity, positions, still **paper**.
2. Last lines of `data/journal/agent.jsonl` (or the Streamlit table).
3. Discord / lablab live in case rules change.

A week's P&L is noise. A boring, explainable curve beats an all-in.

---

## Thursday 3 — what you have to **submit**

Submit from the **lablab team dashboard**, not by email. Field details in
[submission](submission.md).

Prepare, in this order:

1. **Public GitHub repo.** Private = judges do not score code. README in English
   (the one already there). Zero secrets.
2. **Demo with a URL** that a judge can open without installing anything. Streamlit from
   the repo: account, positions, a cycle, journal. Even if kickoff accepts only video
   + repo, a URL still helps: less friction.
3. **MP4 video** ≤ 5 min, ≤ 300 MB, **uploaded** to the form (not just a YouTube link
   if lablab asks for the file).
4. **Slides PDF.**
5. **Cover** PNG/JPG 16:9.
6. Copy: short title, short description (≤255), long (≥100 words): problem, collar,
   options, Alpaca API + MCP/CLI, paper only.

### Video (NotebookLM)

Generate the pitch in NotebookLM — you do not record yourself. Sources and the
prompt to paste: [video-script](video-script.md). Problem → approach → how it
runs → reflection. No demo footage required. Export and upload the MP4/audio
the form asks for.

---

## Friday 4 — cutoff 11:00 your time

- [ ] Form submitted **with margin** (ideal: already sent on the 3rd).
- [ ] Public repo, live demo URL, video plays, PDF opens.
- [ ] Correct track.
- [ ] Submission paper account number as they ask (if they ask).

That day: no new strategies, no large refactors.

---

## Never do this

- **Live** trading or `ALPACA_LIVE_TRADE=true`.
- Push `.env` or paste keys in the README / issues / public Discord.
- A **naked** options short by hand "to improve P&L".
- Switch from the collar to another idea on Thursday 3 because "it is not working".
- Leave the only `--loop` on a PC that sleeps without checking that the collar is
  already on the account (positions live on Alpaca; the loop only decides).

---

## Header commands (copy and paste)

From `C:\Users\User\Projects\alpaca-options-agent`:

```powershell
uv run python scripts/smoke_paper.py
uv run python scripts/broker_report.py
uv run python scripts/run_agent.py
uv run python scripts/run_agent.py --execute
uv run python scripts/run_agent.py --loop --execute --interval 900
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
| Which folder do I touch? | [Repo guide](../repo-guide.md) |
| What is MCP vs the CLI? | [mcp-and-cli](../mcp-and-cli.md) |
