# Week plan

Short calendar. The operator checklist — what to open, when to execute, what to submit —
is in [operator-instructions](operator-instructions.md).

## Cutoff, once and for all

The rules say **17:00 CET on Fri 4 Sep**. In early September Central Europe is on summer
time (CEST, UTC+2), so that is **15:00 UTC = 11:00 your clock (UTC−4)**. If lablab meant
CET literally (UTC+1) it would be an hour earlier: 14:00 UTC = 10:00 your clock.

**Work to 10:00 your clock.** The hour is free; the submission is not.

## Restore points

| Tag / repo | What it holds |
| --- | --- |
| https://github.com/Ander-IbBi/alpaca-collar-overlay | The whole collar project, tests and docs, as its own repo |
| Tag `restore-collar-playbook` | The collar code state before any paper order, present in both repos |

```bash
git switch --detach restore-collar-playbook
```

That does not undo positions on Alpaca. Closing positions is the agent's job (the legacy
unwind) or yours in the dashboard.

## Wed 26 Aug — setup

- [x] Registered on lablab
- [x] Independent repo with code, tests and demo
- [ ] Team created on the lablab dashboard (even if you are going solo)
- [ ] lablab Discord
- [x] Alpaca **paper** account + `.env`
- [x] `uv run smoke-paper` green

## Thu 27 Aug

- [x] Alpaca MCP working in Cursor
- [x] Alpaca CLI installed and logged into a paper profile
- [x] Paper account confirmed at **options level 3** (spreads and condors allowed)

## Fri 28 Aug — kickoff 15:00 UTC

- [x] Update [rules-and-criteria](rules-and-criteria.md) with the real brief
- [x] Judges' criteria read: **P&L first**
- [x] Verdict: the collar cannot win a P&L contest. Archive it and pivot

## Sat 29 – Sun 30 Aug — the pivot (market closed)

- [x] Collar archived to its own public repo
- [x] Repo and package renamed to `alpaca-vrp-engine` / `vrp_engine`
- [x] VRP Engine built: signals, structures, pricing, sizing, management, reset, engine
- [x] Portfolio payoff and stress engine, post-fill portfolio risk check
- [x] Real MCP client (research plane) and CLI reconciliation (verification plane)
- [x] 586 tests, ruff clean, CI green
- [ ] Dry-run cycles against the real paper account, `DRY_RUN=true`
- [ ] Streamlit dashboard reviewed as a judge would open it

## Mon 31 Aug — P&L starts

- [ ] 09:30: unwind the inherited collar (short call, long put, then the shares)
- [ ] `run-agent --loop --execute --interval 180` under the restart wrapper
- [ ] Watch the first hour, then leave it alone

## Tue 1 – Wed 2 Sep

- [ ] Let it run. No budget changes, no strategy changes
- [ ] Once a day: equity, positions, journal tail, Discord
- [ ] Screenshots and README polish for the submission

## Thu 3 Sep — creative day

- [ ] NotebookLM video (MP4), slides PDF, cover 16:9
- [ ] Deploy the Streamlit demo, confirm the public repo and CI
- [ ] Submit tonight

## Fri 4 Sep — cutoff

- [ ] Confirm the submission went through, with margin. No new strategies today.
