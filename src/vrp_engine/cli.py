"""Command-line entry points: `smoke-paper`, `run-agent` and `scan`."""

from __future__ import annotations

import argparse
import json
import time

from vrp_engine.agent.loop import VrpAgent
from vrp_engine.alpaca.client import PaperAlpaca
from vrp_engine.config import (
    LiveTradingForbiddenError,
    MissingCredentialsError,
    Settings,
    assert_paper_only,
    load_settings,
)


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


def run_agent() -> int:
    """Run one agent cycle, or a loop during the session. `--execute` sends paper orders."""
    parser = argparse.ArgumentParser(description="Run the VRP Engine agent.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually submit orders to the paper account (default: dry run).",
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
    _, client = connected

    agent = VrpAgent(client)
    execute = True if args.execute else None
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


def _run_loop(agent: VrpAgent, *, execute: bool | None, interval: int) -> int:
    print(
        f"Looping every {interval}s while the market is open. "
        f"Ctrl+C to stop. execute={bool(execute)}"
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
