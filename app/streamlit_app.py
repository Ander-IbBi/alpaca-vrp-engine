"""Judge-facing demo: paper account, positions, agent cycle and decision journal."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run app/streamlit_app.py` from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from options_agent.agent.loop import OverlayAgent  # noqa: E402
from options_agent.alpaca.client import PaperAlpaca  # noqa: E402
from options_agent.config import (  # noqa: E402
    LiveTradingForbiddenError,
    MissingCredentialsError,
    assert_paper_only,
    load_settings,
)
from options_agent.journal import Journal  # noqa: E402

st.set_page_config(page_title="Alpaca Options Overlay Agent", layout="wide")
st.title("Options Overlay Agent")
st.caption("Alpaca paper trading only - an AI agent that hedges an equity book with options")

try:
    settings = assert_paper_only(load_settings())
except LiveTradingForbiddenError as exc:
    st.error(str(exc))
    st.stop()

st.success("Mode: Alpaca **paper**. This app has no live-trading code path.")

try:
    client = PaperAlpaca(settings)
except MissingCredentialsError as exc:
    st.warning(str(exc))
    st.stop()

account = client.account()
clock = client.clock()
positions = client.positions()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Equity", f"${float(account.equity):,.0f}")
col2.metric("Cash", f"${float(account.cash):,.0f}")
col3.metric("Buying power", f"${float(account.buying_power):,.0f}")
col4.metric("Market", "Open" if clock.is_open else "Closed")

st.subheader("Positions")
if positions:
    st.dataframe(
        [
            {
                "symbol": p.symbol,
                "asset_class": str(getattr(p, "asset_class", "")),
                "qty": str(p.qty),
                "avg_entry": str(getattr(p, "avg_entry_price", "")),
                "unrealized_pl": str(getattr(p, "unrealized_pl", "")),
            }
            for p in positions
        ],
        width="stretch",
    )
else:
    st.info("No positions yet. The overlay needs an equity book before it can hedge.")

st.subheader("Agent")
left, right = st.columns([1, 3])
with left:
    execute = st.toggle("Send order to paper", value=False, help="Off = dry run")
    run = st.button("Run one cycle", type="primary")
with right:
    st.write(
        "Each cycle reads the account, proposes a defined-risk options overlay, "
        "runs the risk checks, and records the outcome."
    )

if run:
    cycle = OverlayAgent(client).run_once(execute=execute)
    for note in cycle.notes:
        st.write(f"- {note}")
    st.json(cycle.model_dump(mode="json", exclude_none=True))

st.subheader("Decision journal")
entries = Journal(settings.journal_path).tail(15)
if entries:
    st.dataframe(list(reversed(entries)), width="stretch")
else:
    st.caption("No cycles recorded yet.")
