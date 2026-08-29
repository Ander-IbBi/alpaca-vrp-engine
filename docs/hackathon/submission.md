# Submission — what to send

Source: kickoff 28 Aug 2026 + [lablab's generic guide](https://lablab.ai/delivering-your-hackathon-solution).
Submit from the **team dashboard** → **Submit your project** (3 pages).
The button appears next to the countdown. At **17:00 CET on 4 Sep** the form closes —
which is 11:00 your clock at the latest, and 10:00 if "CET" is meant literally. Work to
10:00; see [week-plan](week-plan.md).

## What they said at kickoff (minimum)

Three pieces. Without these there is no submission:

1. **Product description** — title, short description, long description in the form.
2. **Video (presentation)** — you showing what you built, **maximum 5 minutes**.
3. **GitHub with the demo** — repo (public) and the demo judges can see.

Extra fields for the extra challenge: **up to 5 URLs** of posts on X / LinkedIn.

## What the lablab form often asks (fill it if it appears)

The standard template often adds a 16:9 cover, a slides PDF and an app URL.
If the form has them, do not leave them empty. If they are not there, do not invent them.

| Field | What to put |
| --- | --- |
| Title | Short (~50 characters) |
| Short description | ≤255 characters |
| Long description | ≥100 words: the variance risk premium, defined-risk structures, the probability wedge, the portfolio stress engine, autonomous agent, paper, stack |
| Video | MP4 ≤5 min (typical upload cap ≤300 MB if they ask for a file) |
| GitHub | **Public** repo |
| Application URL / demo | Deployed Streamlit, or a README so clear that cloning is enough |
| Social posts | Up to 5 links (optional, Built in Public) |
| Technologies | Alpaca Trading API, MCP, CLI, Python, Streamlit, options, LLM |

## Before you hit send

- [ ] Team created on lablab (1–6) even if you are going solo
- [ ] Discord; hackathon channel; mentors with the tag they indicate
- [ ] Main challenge: autonomous agent + API + (MCP **or** CLI) + **options** + **paper**
- [ ] The agent has traded **during the week** (P&L / journal), not only a dry-run
- [ ] Public repo; `.env` out; `.env.example` in
- [ ] The video shows: identify → decide → size → risk-check → manage position → paper P&L
- [ ] MCP **and** CLI are visible, and shown doing real work in the cycle, not just named
- [ ] `uv run pytest` and `uv run ruff check .` green

## Video (NotebookLM)

Generate with the prompt in [video-script](video-script.md): problem → approach →
how it runs → reflection. No live demo or P&L footage required. Export from
NotebookLM and upload what the form asks for (usually MP4). Aim to have the file
ready by **3 Sep**; submit that night or early on the 4th.

## Prizes (so you do not mix them up on the form)

Main (top 3): $2,500 / $1,500 / $1,000. Social (two teams): $500 + Algo Trader Plus
one month per member.
