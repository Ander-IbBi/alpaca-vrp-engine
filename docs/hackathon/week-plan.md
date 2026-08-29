# Week plan

The skeleton is already there. The winning strategy is tuned **after** kickoff.

Operator checklist (what to open, when to execute, what to submit):
[operator-instructions](operator-instructions.md).

## Restore point

Git tag **`restore-collar-playbook`**: the aggressive collar already built, **before**
trying orders on paper. Return to this code state:

```bash
git switch --detach restore-collar-playbook
# or, if you want to move main here (destructive for later commits):
# git reset --hard restore-collar-playbook
```

That does not undo positions on Alpaca. If a paper trial leaves SPY/options, close
them in the dashboard or with `close_all_positions`. The **submission** account is
still a different one, on Friday.

## Wed 26 Aug — setup

- [x] Registered on lablab
- [x] Independent repo with code, tests and demo
- [ ] Team created on the lablab dashboard (even if you are going solo)
- [ ] lablab Discord
- [ ] Alpaca **paper** development account + `.env`
- [ ] `uv run python scripts/smoke_paper.py` green

## Thu 27 Aug

- Alpaca webinar (10:00 PDT): https://luma.com/qoym39ry
- Alpaca MCP working in Cursor; optionally, try the CLI
- Check that the paper account has **options enabled** (trading level)

## Fri 28 Aug — kickoff 15:00 UTC

- Twitch: https://www.twitch.tv/lablabai
- Update [rules-and-criteria](rules-and-criteria.md) with the real brief
- Open the **submission paper account** and put its keys in `.env`
- Buy the base book (the agent seeds 100 SPY) and run the first cycle
- First small orders: the P&L clock starts here

## Sat 29 – Mon 31 Aug

- [x] Real strategy in `strategy/` (collar by delta, SPY seed, skip if already covered)
- [x] LLM in the loop (`agent/llm.py`) to explain; soft veto, fail-open
- [x] Automate the cycle: `run_agent.py --loop --interval 900`
- Streamlit: equity curve, greeks and journal

## Tue 1 – Wed 2 Sep

- Polish the demo until a full cycle is visible live
- Final README and screenshots
- Leave the agent running with rules, not by hand

## Thu 3 Sep — creative day

- MP4 video, slides PDF, deploy Streamlit, public repo

## Fri 4 Sep — cutoff 15:00 UTC

- Submit with margin. No new strategies this day.
