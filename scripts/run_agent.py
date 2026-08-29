"""Run one agent cycle from a checkout: `python scripts/run_agent.py [--execute]`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vrp_engine.cli import run_agent  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_agent())
