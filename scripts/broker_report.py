"""Cross-client broker report: SDK, CLI, and what the agent would do next.

Run this to prove three independent views of the same paper account agree:

    uv run python scripts/broker_report.py

  * `alpaca-py` (what the agent trades through)
  * the Alpaca CLI (`alpaca account get`, a separate binary and auth path)
  * the strategy's own read of the book

The MCP server exposes the same account to an LLM client; see docs/mcp-and-cli.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from options_agent.agent.loop import OverlayAgent  # noqa: E402
from options_agent.alpaca.cli_bridge import (  # noqa: E402
    cli_account,
    cli_positions,
    find_cli,
)
from options_agent.alpaca.client import PaperAlpaca  # noqa: E402
from options_agent.config import (  # noqa: E402
    LiveTradingForbiddenError,
    MissingCredentialsError,
    assert_paper_only,
    load_settings,
)


def main() -> int:
    try:
        settings = assert_paper_only(load_settings())
        client = PaperAlpaca(settings)
    except (LiveTradingForbiddenError, MissingCredentialsError) as exc:
        print(f"ERROR: {exc}")
        return 1

    account = client.account()
    positions = client.positions()

    print("=== alpaca-py (SDK) ===")
    print(f"account={account.account_number} equity={account.equity} cash={account.cash}")
    for position in positions:
        print(f"  {position.symbol:<24} qty={position.qty}")
    if not positions:
        print("  (flat)")

    print("\n=== Alpaca CLI ===")
    binary = find_cli()
    if binary is None:
        print("  CLI not installed; see README. The agent runs without it.")
    else:
        print(f"  binary: {binary}")
        result = cli_account()
        if result.error:
            print(f"  error: {result.error}")
        elif isinstance(result.data, dict):
            print(
                f"  account={result.data.get('account_number')} "
                f"equity={result.data.get('equity')} "
                f"options_level={result.data.get('options_approved_level')}"
            )
        listed = cli_positions()
        if isinstance(listed.data, list):
            for item in listed.data:
                print(f"  {item.get('symbol', ''):<24} qty={item.get('qty')}")
            if not listed.data:
                print("  (flat)")

    print("\n=== Agent cycle (dry run) ===")
    cycle = OverlayAgent(client).run_once(execute=False)
    print(json.dumps(cycle.model_dump(mode="json", exclude_none=True), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
