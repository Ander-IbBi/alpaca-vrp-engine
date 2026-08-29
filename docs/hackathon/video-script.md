# NotebookLM video — sources and prompt

Generate the submission presentation with [NotebookLM](https://notebooklm.google.com)
(Audio or Video Overview). You do not record yourself.

Repo: https://github.com/Ander-IbBi/alpaca-options-agent

---

## How to use NotebookLM

1. Create a new notebook.
2. Upload these sources:
   - `README.md`
   - this file (`docs/hackathon/video-script.md`)
   - `docs/mcp-and-cli.md` (optional; only so CLI/MCP use is accurate — do not let
     the model dwell on them)
3. Do not upload journals, equity exports, screenshots, or secrets.
4. Paste the prompt below. Prefer a single-narrator / briefing style, not a debate.
5. Export and upload the file to the lablab form.

---

## Source brief (for NotebookLM to ground on)

### Opening tone
Direct and natural. Example feel (do not copy word-for-word): “Hi everyone — this
video is meant to walk through my submission. Let’s start with the idea behind it.”
No labels like “pre-submission pitch”, “overview of this presentation”, or an
agenda of sections.

### The idea (spend most of the time here)
Most agents in this track will try to forecast the next move. That is a weak job
for a short contest week: returns are noisy, and a directional story is hard to
defend. The question this project answers instead: if you do not know where SPY
goes, what can an agent still do that is useful and testable?

Answer: **shape the risk** of a long equity book with options, then **keep
managing that shape** without a human in the loop. Prediction is not the product.
The collar and its management are.

### Strategy — aggressive collar on SPY
- Seed **100 SPY** shares.
- Buy a put near **delta −0.20** — the floor.
- Sell a call whose premium roughly pays for that put — the financing.
- Same expiry. Shares cover the call; naked shorts are rejected in code.
- Defined risk on both sides by construction, not by a prompt.

Opening the collar once is not enough. A collar left alone makes one decision for
the whole week. The agent **manages** every cycle, in this order:

1. Short call in the money → roll it up and out (recover upside, reduce assignment
   risk on the shares).
2. Either leg near expiry → roll the whole collar out (do not carry expiry risk).
3. Long put worth about **2×** its cost → sell it and re-arm a lower floor (a hedge
   that paid off should not sit and waste).
4. Otherwise **hold** — and still write down which checks ran. A quiet cycle must
   still show reasoning.

### Who decides what
- The **strategy** proposes the next playbook step (seed / open / manage / hold).
- A **risk layer in code** approves or blocks (size caps, covered-call check,
  equity floor, daily-loss breaker). Hard limits; the model cannot switch them off.
- An optional **LLM** only explains and may soft-veto on a short whitelist. It
  cannot approve what risk already rejected. If it is down, the cycle continues.

### Stack use (brief — do not lecture)
Orders go through the Trading API (`alpaca-py`). Before a ticket, the same account
is read again through the Alpaca CLI; if the two views disagree, the cycle refuses
to trade. MCP is used as a research/supervision window on the account (chains,
greeks, fills), not to place orders. Mention these only as how they are used —
judges already know what they are and that the event requires them. Do not explain
how MCP or the CLI work, and do not frame them as a clever “scoring” move.

### Closing
Short reflection: the collar is meant to be boring (upside cut, downside floored);
the claim is an autonomous loop that identifies, decides, manages, and can stop
itself, with options as the instrument that makes risk defined. With more time:
more underlyings and portfolio-delta hedging; same loop and risk layer. End cleanly.
No “how we plan to win”, no scoreboard meta, no long disclaimer speech.

---

## Prompt to paste into NotebookLM

```text
Using ONLY the sources in this notebook, generate a single-narrator Audio Overview
(or Video Overview if available).

Open naturally, like a person explaining their submission — for example the feel of:
“Hi everyone, this video is meant to explain my submission. Let’s start by saying…”
Do NOT call this a pre-submission video, a pitch, a briefing, or an overview of
what you are about to cover. Do NOT list an agenda or say “first… then… finally…”
as a table of contents. Just start.

Audience: technical judges (Alpaca / lablab, Options Alpha Agents). They know
collars, deltas, rolls, paper trading, MCP and CLI. Do not teach those basics.
Do not remind them that the account is paper or that MCP/CLI were mandatory.
Do not talk about how this is meant to win the competition or score points.

WEIGHT THE CONTENT:
- Most of the time: the STRATEGY — why not directional prediction; the SPY
  collar (shares, put floor, short call financing); why management matters;
  the four management steps; strategy proposes / risk decides / LLM only explains.
- Little time: how the Trading API, CLI, and MCP are used in this project
  (usage only, one or two sentences each at most). No tutorials.
- Short close: what the design claims, what it does not claim, optional next step.
  No long disclaimers.

Style:
- One narrator only. No two-host conversation. No “welcome to the show”.
- Clear spoken English, short sentences, technical and direct. No hype.
- Flexible length; prefer a few solid minutes. Do not pad.
- No screen demos, no invented P&L, no file paths, no function-name spam.

Generate now.
```

---

## If the first take is wrong

```text
Regenerate. Open with a natural greeting about explaining the submission — no
“pre-submission” label and no agenda. Spend most of the time on the collar
strategy and its management ladder. Keep MCP and CLI to brief usage only. Cut
any talk about winning, scoring, or reminding listeners that trading is paper.
```
