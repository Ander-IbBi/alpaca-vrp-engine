"""Independent read of the broker through Alpaca's official CLI.

The agent trades through `alpaca-py`. This module deliberately reads the same
account a second time, through a completely different client, so a cycle can
answer "does the broker really hold what the SDK says it holds?" before it sends
another ticket. A mismatch means our view of the book is stale, and acting on a
stale book is how an overlay ends up double-hedged or accidentally naked.

The CLI is optional infrastructure: if the binary is missing the bridge reports
that plainly instead of raising, so CI and a fresh checkout still work.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Where `gh release download` + Expand-Archive put it on Windows, plus the usual
# spots on macOS/Linux. PATH wins over all of them.
_FALLBACK_PATHS = (
    Path.home() / ".alpaca-cli" / "alpaca.exe",
    Path.home() / ".alpaca-cli" / "alpaca",
    Path.home() / "go" / "bin" / "alpaca",
    Path("/usr/local/bin/alpaca"),
)

DEFAULT_TIMEOUT_SECONDS = 20


class CliResult(BaseModel):
    """Outcome of one CLI call. `available=False` is a normal, non-fatal state."""

    available: bool
    command: list[str] = Field(default_factory=list)
    data: Any = None
    error: str | None = None


def find_cli() -> str | None:
    """Locate the `alpaca` binary, preferring whatever is on PATH."""
    found = shutil.which("alpaca")
    if found:
        return found
    for candidate in _FALLBACK_PATHS:
        if candidate.is_file():
            return str(candidate)
    return None


def run_cli(
    *args: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> CliResult:
    """Run `alpaca <args> --quiet` and parse its JSON output."""
    binary = find_cli()
    if binary is None:
        return CliResult(
            available=False,
            error="alpaca CLI not installed; see README for the one-line install",
        )

    command = [binary, *args, "--quiet"]
    try:
        completed = subprocess.run(  # noqa: S603 — fixed binary, no shell
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CliResult(available=False, command=command[1:], error=str(exc))

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return CliResult(available=True, command=command[1:], error=detail[:400])

    try:
        return CliResult(
            available=True,
            command=command[1:],
            data=json.loads(completed.stdout or "null"),
        )
    except json.JSONDecodeError:
        return CliResult(
            available=True,
            command=command[1:],
            data=(completed.stdout or "").strip(),
        )


def cli_account() -> CliResult:
    return run_cli("account", "get")


def cli_positions() -> CliResult:
    return run_cli("position", "list")


def cli_clock() -> CliResult:
    return run_cli("clock")


class BrokerCrossCheck(BaseModel):
    """Does the CLI agree with the SDK about the account we are about to trade?"""

    checked: bool
    agrees: bool = True
    notes: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        if not self.checked:
            return "CLI cross-check skipped"
        return "CLI agrees" if self.agrees else "; ".join(self.notes)


def cross_check_account(
    *,
    account_number: str,
    position_symbols: set[str],
) -> BrokerCrossCheck:
    """Compare the SDK's view of the book with the CLI's.

    Only disagreements that would change a trading decision are reported: the
    account identity, and which symbols are actually open.
    """
    if not account_number:
        # Without an identity from the SDK there is nothing meaningful to compare.
        return BrokerCrossCheck(checked=False, notes=["SDK did not report an account number"])

    account = cli_account()
    if not account.available:
        return BrokerCrossCheck(checked=False, notes=[account.error or "CLI unavailable"])
    if account.error:
        return BrokerCrossCheck(checked=False, notes=[account.error])

    notes: list[str] = []
    payload = account.data if isinstance(account.data, dict) else {}
    cli_number = str(payload.get("account_number") or "")
    if cli_number and cli_number != account_number:
        notes.append(f"CLI sees account {cli_number}, SDK sees {account_number}")

    positions = cli_positions()
    if positions.available and not positions.error and isinstance(positions.data, list):
        cli_symbols = {
            str(item.get("symbol", "")).upper()
            for item in positions.data
            if isinstance(item, dict)
        }
        cli_symbols.discard("")
        missing = position_symbols - cli_symbols
        extra = cli_symbols - position_symbols
        if missing:
            notes.append(f"SDK reports {sorted(missing)} but the CLI does not")
        if extra:
            notes.append(f"CLI reports {sorted(extra)} but the SDK does not")

    return BrokerCrossCheck(checked=True, agrees=not notes, notes=notes)
