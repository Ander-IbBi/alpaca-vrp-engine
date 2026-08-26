"""Command-line entry points: `uv run smoke-paper` and `uv run run-agent`."""

from __future__ import annotations

import argparse
import json

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
    """Run one agent cycle. Executing real paper orders requires --execute."""
    parser = argparse.ArgumentParser(description="Run one options overlay agent cycle.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually submit the order to the paper account (default: dry run).",
    )
    args = parser.parse_args()

    try:
        settings = assert_paper_only(load_settings())
        client = PaperAlpaca(settings)
    except (LiveTradingForbiddenError, MissingCredentialsError) as exc:
        print(f"ERROR: {exc}")
        return 1

    cycle = OverlayAgent(client).run_once(execute=args.execute or None)
    print(json.dumps(cycle.model_dump(mode="json", exclude_none=True), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(smoke())
