"""Standalone smoke test, runnable without installing the package."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from options_agent.cli import smoke  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(smoke())
