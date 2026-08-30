"""Command-line entry points: `smoke-paper`, `run-agent`, `scan` and `agent-health`."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from vrp_engine.agent.loop import VrpAgent
from vrp_engine.alpaca.client import PaperAlpaca
from vrp_engine.config import (
    LiveTradingForbiddenError,
    MissingCredentialsError,
    Settings,
    assert_paper_only,
    load_settings,
)
from vrp_engine.health import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_PID_PATH,
    LATE,
    OK,
    STALE,
    UNKNOWN,
    assess,
    first_line,
    format_age,
    heartbeat,
    process_alive,
    read_pid,
    thresholds,
)
from vrp_engine.journal import Journal


def _connect() -> tuple[Settings, PaperAlpaca] | None:
    try:
        settings = assert_paper_only(load_settings())
        return settings, PaperAlpaca(settings)
    except (LiveTradingForbiddenError, MissingCredentialsError) as exc:
        print(f"ERROR: {exc}")
        return None


def smoke() -> int:
    """Prove the paper keys work: market clock plus account snapshot."""
    connected = _connect()
    if connected is None:
        return 1
    _, client = connected

    clock = client.clock()
    account = client.account()
    print(f"paper=True  market_open={clock.is_open}  next_open={clock.next_open}")
    print(f"account={account.account_number}  equity={account.equity}  cash={account.cash}")
    print(f"options_buying_power={client.options_buying_power():.2f}")
    print("Smoke test OK (paper).")
    return 0


def scan() -> int:
    """Print the ranked opportunity table without proposing or sending anything.

    Uses `dry_scan`, which ranks the universe even outside the session window, so this
    still shows what the engine sees on a weekend.
    """
    connected = _connect()
    if connected is None:
        return 1
    _, client = connected

    cycle, _ = VrpAgent(client).dry_scan()
    guard = cycle.account_guard
    payload = {
        "equity": cycle.equity,
        "market_open": cycle.market_open,
        "account_guard": guard.model_dump(mode="json") if guard else None,
        "signals": cycle.signals,
        "portfolio": cycle.portfolio,
        "scan": cycle.scan,
        "notes": cycle.notes,
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


_VERDICT_LABELS = {
    OK: "OPERATING",
    LATE: "SLOW",
    STALE: "NOT RUNNING",
    UNKNOWN: "NO CYCLES YET",
}


def agent_health() -> int:
    """Is the loop still writing cycles? Reads the journal, never the broker.

    Deliberately keyless: the point is to check on the agent from a second window,
    and asking for credentials to do that would only tempt someone to copy them.
    """
    parser = argparse.ArgumentParser(description="Check that the agent loop is alive.")
    parser.add_argument("--json", action="store_true", help="Machine-readable, for the watcher.")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="The loop's cadence in seconds; it sets how long a silence is tolerable.",
    )
    parser.add_argument(
        "--pid-file",
        default=str(DEFAULT_PID_PATH),
        help="Where the launcher wrote the loop's process id.",
    )
    args = parser.parse_args()

    try:
        settings = assert_paper_only(load_settings())
    except LiveTradingForbiddenError as exc:
        print(f"ERROR: {exc}")
        return 1

    beat = heartbeat(Journal(settings.journal_path).read_all())
    late_after, stale_after = thresholds(market_open=beat.market_open, interval=args.interval)
    pid = read_pid(Path(args.pid_file))
    alive = process_alive(pid)
    call = assess(
        beat, interval=args.interval, pid_present=pid is not None, process_alive=alive
    )
    state = call.state
    failure = first_line(beat.failure)

    if args.json:
        print(
            json.dumps(
                {
                    "verdict": state,
                    "label": _VERDICT_LABELS[state],
                    "reason": call.reason,
                    "ts": beat.ts,
                    "age_seconds": beat.age_seconds,
                    "age": format_age(beat.age_seconds),
                    "market_open": beat.market_open,
                    "action": beat.action,
                    "equity": beat.equity,
                    "submitted": beat.submitted,
                    "failed": beat.failed,
                    "failure": failure,
                    "cycles": beat.cycles,
                    "pid": pid,
                    "process_alive": alive,
                    "late_after_seconds": late_after,
                    "stale_after_seconds": stale_after,
                }
            )
        )
    else:
        session = "unknown" if beat.market_open is None else (
            "market open" if beat.market_open else "market closed"
        )
        equity = f"${beat.equity:,.0f}" if beat.equity is not None else "unknown"
        process = f"alive (pid {pid})" if alive else (
            f"gone (pid {pid})" if pid else "no pid file"
        )
        print(f"VRP Engine - {_VERDICT_LABELS[state]} ({call.reason})")
        print(f"  last cycle   {format_age(beat.age_seconds)} ago, {session}")
        print(f"  decision     {beat.action or 'none'}, equity {equity}")
        print(f"  process      {process}")
        print(f"  journal      {beat.cycles} cycle(s)")
        if beat.failed:
            print(f"  warning      {failure}")

    return 0 if state in (OK, LATE) else 1


def execution_mode(*, dry_run_requested: bool, dry_run_setting: bool) -> tuple[bool, str]:
    """Resolve whether this run trades, and the line that says so out loud.

    Trading is the default because starting the agent is the decision to trade. The
    only two ways to end up rehearsing are asking for it on the command line or leaving
    `DRY_RUN=true` in the environment, and both are announced: a silent dry run costs a
    whole session and looks exactly like an agent that found nothing worth doing.
    """
    if dry_run_requested:
        return False, "DRY RUN (--dry-run): the engine decides and journals, sends nothing."
    if dry_run_setting:
        return False, (
            "DRY RUN (DRY_RUN=true in the environment): the engine decides and journals, "
            "sends nothing. Remove it from .env to let the agent trade."
        )
    return True, "AUTONOMOUS: approved tickets go straight to the Alpaca paper account."


def run_agent() -> int:
    """Run one agent cycle, or a loop during the session. Starting it means trading."""
    parser = argparse.ArgumentParser(description="Run the VRP Engine agent.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rehearse a cycle: decide and journal, send nothing. For development.",
    )
    parser.add_argument(
        # Executing is the default now, so this only exists to keep an older shortcut
        # or scheduled task from failing at argument parsing.
        "--execute",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Repeat the cycle until interrupted.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=180,
        help="Seconds between cycles when --loop is set (default: 180).",
    )
    args = parser.parse_args()

    connected = _connect()
    if connected is None:
        return 1
    settings, client = connected

    execute, announcement = execution_mode(
        dry_run_requested=args.dry_run, dry_run_setting=settings.dry_run
    )
    print(announcement)

    agent = VrpAgent(client)
    if args.loop:
        return _run_loop(agent, execute=execute, interval=max(args.interval, 30))

    cycle = agent.run_once(execute=execute)
    print(json.dumps(cycle.model_dump(mode="json", exclude_none=True), indent=2, default=str))
    return 0


def _sleep_seconds(agent: VrpAgent, interval: int) -> int:
    """When the session is shut, poll less often so an overnight --loop stays quiet."""
    try:
        clock = agent.client.clock()
    except Exception:  # noqa: BLE001
        return interval
    if clock.is_open:
        return interval
    return min(max(interval * 6, 1800), 3600)


def _run_loop(agent: VrpAgent, *, execute: bool, interval: int) -> int:
    print(
        f"Looping every {interval}s while the market is open. "
        f"Ctrl+C to stop. execute={execute}"
    )
    try:
        while True:
            try:
                cycle = agent.run_once(execute=execute)
                print(
                    json.dumps(
                        cycle.model_dump(mode="json", exclude_none=True),
                        indent=2,
                        default=str,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                print(f"ERROR: {type(exc).__name__}: {exc}")
            time.sleep(_sleep_seconds(agent, interval))
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
