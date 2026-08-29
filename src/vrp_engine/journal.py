"""Append-only decision log.

Judges care about auditability: every proposal, veto and order lands here as one
JSON object per line, which the Streamlit demo replays.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "kind": kind,
            **payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
        return entry

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                # A truncated final line must not break the demo.
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        return self.read_all()[-n:]

    def high_water_mark(self) -> float | None:
        """Peak equity the agent has ever recorded.

        The drawdown breaker measures from the peak rather than from the starting
        balance, so a good week cannot be given back without the breaker noticing.
        """
        peaks = [
            float(entry["equity"])
            for entry in self.read_all()
            if isinstance(entry.get("equity"), int | float)
        ]
        return max(peaks) if peaks else None
