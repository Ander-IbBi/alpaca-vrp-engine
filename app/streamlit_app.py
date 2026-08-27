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
st.caption(
    "Alpaca paper trading only — a defined-risk collar overlay (long stock, long put, short call)"
)

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
    st.info("Flat book. The first cycle seeds 100 shares of the watchlist name, then collars it.")

st.subheader("Playbook")
st.markdown(
    """
1. **Seed** 100 shares of SPY if the book is empty.
2. **Collar** — buy a put near delta −0.20, sell a call near delta +0.20, same expiry (21–45 DTE).
3. **Hold** — if the collar is already on, skip. No mid-week strategy rewrite.
4. **Gates** — account circuit breaker, per-order limits, covered short call, then an LLM
   explanation (soft veto only; code still decides).
"""
)

st.subheader("Agent")
left, right = st.columns([1, 3])
with left:
    execute = st.toggle("Send order to paper", value=False, help="Off = dry run")
    run = st.button("Run one cycle", type="primary")
with right:
    st.write(
        "Each cycle reads the account, proposes the next playbook step, runs the risk "
        "checks, asks the LLM to explain, and records the outcome."
    )

if run:
    cycle = OverlayAgent(client).run_once(execute=execute)
    for note in cycle.notes:
        st.write(f"- {note}")
    if cycle.proposal is not None and not cycle.proposal.skip:
        st.write(f"**Proposal** ({cycle.proposal.kind}): {cycle.proposal.rationale}")
        if cycle.proposal.limit_price is not None:
            st.write(f"Limit (net mid): ${cycle.proposal.limit_price:.2f}")
    if cycle.risk is not None:
        st.write(f"**Risk:** {cycle.risk.summary()}")
    if cycle.llm is not None:
        st.write(f"**LLM ({cycle.llm.advisor}):** {cycle.llm.explanation}")
    st.json(cycle.model_dump(mode="json", exclude_none=True))

st.subheader("Decision journal")
entries = Journal(settings.journal_path).tail(15)
if entries:
    st.dataframe(list(reversed(entries)), width="stretch")
else:
    st.caption("No cycles recorded yet.")
