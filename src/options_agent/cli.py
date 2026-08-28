"""Command-line entry points: `uv run smoke-paper` and `uv run run-agent`."""

from __future__ import annotations

import argparse
import json
import time

from options_agent.agent.loop import OverlayAgent
from options_agent.alpaca.client import PaperAlpaca
from options_agent.config import (
    LiveTradingForbiddenError,
    MissingCredentialsError,
    assert_paper_only,
    load_settings,
)


def smoke() -> int:
    """Prove the paper keys work: market clock plus account snapshot."""
    try:
        settings = assert_paper_only(load_settings())
        client = PaperAlpaca(settings)
    except (LiveTradingForbiddenError, MissingCredentialsError) as exc:
        print(f"ERROR: {exc}")
        return 1

    clock = client.clock()
    account = client.account()
    print(f"paper=True  market_open={clock.is_open}  next_open={clock.next_open}")
    print(f"account={account.account_number}  equity={account.equity}  cash={account.cash}")
    print("Smoke test OK (paper).")
    return 0


def run_agent() -> int:
    """Run one agent cycle, or a loop during the session. `--execute` sends paper orders."""
    parser = argparse.ArgumentParser(description="Run the options overlay agent.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually submit the order to the paper account (default: dry run).",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Repeat the cycle until interrupted.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Seconds between cycles when --loop is set (default: 900).",
    )
    args = parser.parse_args()

    try:
        settings = assert_paper_only(load_settings())
        client = PaperAlpaca(settings)
    except (LiveTradingForbiddenError, MissingCredentialsError) as exc:
        print(f"ERROR: {exc}")
        return 1

    agent = OverlayAgent(client)
    execute = True if args.execute else None
    if args.loop:
        return _run_loop(agent, execute=execute, interval=max(args.interval, 30))

    cycle = agent.run_once(execute=execute)
    print(json.dumps(cycle.model_dump(mode="json", exclude_none=True), indent=2))
    return 0


def _sleep_seconds(agent: OverlayAgent, interval: int) -> int:
    """When the session is shut, poll less often so overnight --loop is quiet."""
    try:
        clock = agent.client.clock()
    except Exception:  # noqa: BLE001
        return interval
    if clock.is_open:
        return interval
    return min(max(interval * 4, 1800), 3600)


def _run_loop(agent: OverlayAgent, *, execute: bool | None, interval: int) -> int:
    print(
        f"Looping every {interval}s while the market is open. "
        f"Ctrl+C to stop. execute={bool(execute)}"
    )
    try:
        while True:
            try:
                cycle = agent.run_once(execute=execute)
                print(json.dumps(cycle.model_dump(mode="json", exclude_none=True), indent=2))
            except Exception as exc:  # noqa: BLE001
                print(f"ERROR: {type(exc).__name__}: {exc}")
            time.sleep(_sleep_seconds(agent, interval))
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
