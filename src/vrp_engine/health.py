"""Is the agent still alive, and is it still trading?

The loop writes one journal line per cycle — including the cycles that failed, since
`VrpAgent.run_once` records the exception rather than swallowing it. That makes the
age of the last line a reliable heartbeat: if it stops advancing, the agent stopped,
the machine went to sleep, or the broker stopped answering.

Everything here reads the journal and nothing else. No network, no API keys, so the
operator can check on the agent from a second window without a second set of
credentials.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vrp_engine.config import PROJECT_ROOT

# The launcher drops the loop's process id here so the watcher can tell a dead
# process from a wedged one.
DEFAULT_PID_PATH = PROJECT_ROOT / "data" / "agent.pid"

DEFAULT_INTERVAL_SECONDS = 180

# The loop paces itself by the session: `cli._sleep_seconds` waits `interval` while the
# market is open and up to an hour once it shuts. One threshold for both would cry wolf
# every night about an agent that is behaving exactly as designed.
LATE_MULTIPLE = 2
STALE_MULTIPLE = 4
CLOSED_LATE_SECONDS = 60 * 60
CLOSED_STALE_SECONDS = 2 * 60 * 60

OK = "ok"
LATE = "late"
STALE = "stale"
UNKNOWN = "unknown"

FAILURE_PREFIX = "Cycle failed:"


@dataclass(frozen=True)
class Heartbeat:
    """What the last journalled cycle says about the agent."""

    ts: str
    age_seconds: float | None
    market_open: bool | None
    action: str
    equity: float | None
    submitted: bool
    failed: bool
    failure: str
    cycles: int


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamped = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # A journal written by an older build may carry a naive timestamp; treat it as UTC
    # rather than refusing to compare it against now.
    return stamped if stamped.tzinfo else stamped.replace(tzinfo=UTC)


def heartbeat(
    entries: Sequence[Mapping[str, Any]], *, now: datetime | None = None
) -> Heartbeat:
    """Read the pulse off the tail of the decision journal."""
    moment = now or datetime.now(UTC)
    if not entries:
        return Heartbeat(
            ts="",
            age_seconds=None,
            market_open=None,
            action="",
            equity=None,
            submitted=False,
            failed=False,
            failure="",
            cycles=0,
        )

    last = entries[-1]
    stamped = _parse_ts(last.get("ts"))
    age = None if stamped is None else max((moment - stamped).total_seconds(), 0.0)
    proposal = last.get("proposal") or {}
    execution = last.get("execution") or {}
    notes = [str(note) for note in (last.get("notes") or [])]
    failure = next((note for note in notes if note.startswith(FAILURE_PREFIX)), "")
    equity = last.get("equity")

    return Heartbeat(
        ts=str(last.get("ts", "")),
        age_seconds=age,
        market_open=last.get("market_open"),
        action=str(proposal.get("action") or "hold"),
        equity=float(equity) if isinstance(equity, int | float) else None,
        submitted=bool(execution.get("submitted")),
        failed=bool(failure),
        failure=failure,
        cycles=len(entries),
    )


def thresholds(
    *, market_open: bool | None, interval: int = DEFAULT_INTERVAL_SECONDS
) -> tuple[float, float]:
    """How long a silence is tolerable, in seconds, as (late, stale).

    An unknown session state is treated as closed: the point of the panel is to be
    believed, and a false alarm at midnight is how an operator learns to ignore it.
    """
    if market_open:
        return float(interval * LATE_MULTIPLE), float(interval * STALE_MULTIPLE)
    return float(CLOSED_LATE_SECONDS), float(CLOSED_STALE_SECONDS)


def verdict(beat: Heartbeat, *, interval: int = DEFAULT_INTERVAL_SECONDS) -> str:
    """`ok`, `late`, `stale`, or `unknown` when there is nothing to judge."""
    if beat.age_seconds is None:
        return UNKNOWN
    late, stale = thresholds(market_open=beat.market_open, interval=interval)
    if beat.age_seconds >= stale:
        return STALE
    if beat.age_seconds >= late:
        return LATE
    return OK


@dataclass(frozen=True)
class Assessment:
    """The panel's colour, and the reason behind it in words."""

    state: str
    reason: str


def assess(
    beat: Heartbeat,
    *,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    pid_present: bool = False,
    process_alive: bool = False,
) -> Assessment:
    """The pulse, corrected by what the process is actually doing.

    A fresh journal line is not on its own proof of health: the loop can be dead with
    its last line still warm, or alive and failing every cycle against a broker that
    keeps refusing it. Both of those looked green until this function existed.
    """
    if pid_present and not process_alive:
        return Assessment(STALE, "the launcher started something and it is gone")

    state = verdict(beat, interval=interval)
    if state == UNKNOWN:
        return Assessment(UNKNOWN, "nothing journalled yet")
    if state == STALE:
        return Assessment(STALE, "no cycle written for far too long")
    if beat.failed:
        return Assessment(LATE, "the last cycle ended in an error")
    if state == LATE:
        return Assessment(LATE, "a cycle is overdue")
    return Assessment(OK, "cycling normally")


def first_line(text: str, *, limit: int = 110) -> str:
    """One readable line out of something that may be a whole HTML error page."""
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def format_age(seconds: float | None) -> str:
    """A silence a human can judge at a glance."""
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def read_pid(path: Path | str) -> int | None:
    """The agent's process id, as the launcher last wrote it."""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


def process_alive(pid: int | None) -> bool:
    """True while that process id is still running.

    `os.kill(pid, 0)` is the POSIX idiom, but on Windows `os.kill` terminates the
    target for any signal other than the two CTRL events, so asking there has to go
    through the Win32 API instead of the portable-looking call.
    """
    if not pid or pid <= 0:
        return False

    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
