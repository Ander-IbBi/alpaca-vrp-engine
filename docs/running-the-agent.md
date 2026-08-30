# Running the agent on Windows

Two shortcuts sit in the repository root. Everything on this page is about those two
files; the terminal commands in the [README](../README.md) do the same job for anyone
who prefers a shell.

| Shortcut | What it does |
| --- | --- |
| `start-agent.cmd` | Checks the machine, then starts an agent that trades |
| `stop-agent.cmd` | Stops the agent, its restart wrapper and the panel |

Double-clicking `start-agent.cmd` **is** the decision to trade. It asks nothing. From
that moment the engine opens, manages and closes positions on the Alpaca paper account
on its own judgement, and does nothing on the cycles where it sees nothing worth doing.

## Starting

Double-click `start-agent.cmd`. Two windows open.

The **agent window** runs three checks, then hands off to the loop:

```
  [1/3] uv on PATH        ok
  [2/3] .env file         ok
  [3/3] paper account     ok

  AUTONOMOUS: approved tickets go straight to the Alpaca paper account.
  Nothing will ask you to confirm a trade, now or later.
  Cadence: one cycle every 180s while the market is open.
```

After that it prints one JSON block per cycle: what it saw, what it decided, and why.
That same block is appended to the decision journal.

The **panel window** is `agent-health`. It refreshes every 30 seconds and reads only the
journal, never the broker, so it cannot get in the agent's way:

```
  VRP Engine - OPERATING
    last cycle   42s ago, market open
    decision     open, equity $100,412
    process      alive (pid 24188)
    journal      37 cycle(s)
```

Green `OPERATING` is the state you want. `SLOW` means a cycle is running late, usually a
sluggish API. Red `NOT RUNNING` means no cycle has been written for a while and the
agent needs restarting. The panel beeps once when it turns red, and closing it changes
nothing about the agent.

## Stopping

Double-click `stop-agent.cmd`. It kills the loop, the restart wrapper and the panel, and
clears the pid file so the next start begins clean.

**Open positions stay open on Alpaca.** Every one of them is defined risk, so the worst
case is already bounded and known — but nothing manages them, takes profit or closes
them until you start the agent again. If you are stopping for the day with positions on,
that is a deliberate choice to leave them unmanaged overnight.

Closing the agent window by hand also works, but it tends to leave the panel running and
a stale pid file behind, which then makes the panel report a process that died minutes
ago. The shortcut tidies up all of it.

`Ctrl+C` in the agent window is also a real stop: the loop treats it as a person asking
it to end, and the wrapper does not restart after one.

## When the connection drops

The agent is built to be left alone for a week, so a network problem is a normal event
rather than a failure. Nothing needs doing at any of these layers:

- **Mid-cycle.** Every API call is wrapped. A failed cycle is written to the journal
  with the reason and the next cycle runs as scheduled.
- **A dead process.** The wrapper restarts it after 30 seconds. It keeps doing that all
  week, but it stops if the loop dies five times in a row within a minute of starting,
  because that is a setup problem no amount of retrying will fix.
- **A sleeping machine.** The wrapper asks Windows to stay awake for as long as its
  window is open. The screen may still go dark; the request disappears when the process
  ends, so nothing is left changed on your machine.
- **A closed market.** The loop polls far less often out of hours and never sends an
  order, because options are day orders. It still wakes up for the opening bell: an
  overnight wait is cut short so the first cycle of the session lands on time.
- **No connection at start-up.** The launcher retries three times, then starts anyway
  and lets the agent join in when the line comes back.

The one thing it refuses to sit through is **rejected keys**, because that never heals by
itself. If Alpaca answers 401 or 403 the launcher stops and tells you to fix `.env`
rather than retry a key that will keep being wrong. `-Unattended` overrides even that.

## When something is actually wrong

| Symptom | What it means | What to do |
| --- | --- | --- |
| `uv is not installed or not on PATH` | Missing toolchain | Install [uv](https://docs.astral.sh/uv/), reopen the shortcut |
| `No .env file` | Never configured | Copy `.env.example` to `.env`, paste your paper keys |
| `Alpaca rejected those keys` | Wrong or expired keys | Repaste from the [paper dashboard](https://app.alpaca.markets/paper/dashboard/overview) |
| `An agent is already running (pid …)` | A second start was attempted | Run `stop-agent.cmd` first; two loops on one account would fight |
| Panel shows `NOT RUNNING` | No cycles being written | `stop-agent.cmd`, then `start-agent.cmd` |
| Panel shows `NO CYCLES YET` | Started, first cycle not finished | Wait one cadence |

The full output of a failed preflight is kept at `data\preflight-error.log`.

## Switches, if you ever need them

The shortcuts pass any arguments straight through, and none of them are needed for a
normal day.

| Switch | Effect |
| --- | --- |
| `-DryRun` | Rehearse: decide and journal, send nothing. Development only |
| `-NoWatcher` | Start without the second window |
| `-Interval 120` | Change the cadence in seconds (default 180) |
| `-Unattended` | Start even when Alpaca rejects the keys |
| `-KeepPanel` | On `stop-agent.cmd`: stop trading but leave the panel open |

## Checking on it from somewhere else

`agent-health` needs no API keys, because it reads the journal rather than the broker.
Open any terminal in the repository:

```bash
uv run agent-health
```

Exit code 0 means operating or merely slow, 1 means it is not running — handy if you
want to wire it into anything.
