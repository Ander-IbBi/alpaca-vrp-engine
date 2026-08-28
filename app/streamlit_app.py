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

try:
    account = client.account()
    clock = client.clock()
    positions = client.positions()
except Exception as exc:  # noqa: BLE001 — judges should see a message, not a traceback
    st.error(f"Alpaca paper API unavailable: {type(exc).__name__}: {exc}")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Equity", f"${float(account.equity):,.0f}")
col2.metric("Cash", f"${float(account.cash):,.0f}")
col3.metric("Buying power", f"${float(account.buying_power):,.0f}")
col4.metric("Market", "Open" if clock.is_open else "Closed")

st.subheader("Equity curve")
st.caption("Alpaca's own portfolio history for this paper account: the contest scoreboard.")
try:
    history = client.portfolio_history(period="1W", timeframe="1H")
except Exception as exc:  # noqa: BLE001
    history = []
    st.caption(f"Portfolio history unavailable: {type(exc).__name__}: {exc}")

if history:
    start_equity = history[0][1]
    now_equity = history[-1][1]
    change = now_equity - start_equity
    left, right = st.columns(2)
    left.metric("Since the account opened", f"${now_equity:,.0f}", f"{change:+,.0f}")
    right.metric(
        "Return",
        f"{(change / start_equity * 100 if start_equity else 0):+.2f}%",
    )
    st.line_chart(
        {"equity": [point[1] for point in history]},
        height=260,
    )
else:
    st.info("No equity history yet. It fills in once the agent starts trading.")

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
2. **Collar** — buy a put near delta −0.20, then sell a call that pays for it
   (same expiry, 21–45 DTE), so the hedge does not bleed premium while SPY goes nowhere.
3. **Manage**, every cycle, in order of urgency:
   - short call **in the money** → roll it up and out (restores upside, removes
     assignment risk),
   - any leg **near expiry** → roll the collar out,
   - put **worth 2x its cost** → harvest it and re-arm a lower floor,
   - otherwise hold, and say which checks were run.
4. **Gates** — account circuit breaker, CLI cross-check of the broker, per-order limits,
   covered short call, then an LLM explanation (soft veto only; code still decides).
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
    try:
        cycle = OverlayAgent(client).run_once(execute=execute)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Cycle failed: {type(exc).__name__}: {exc}")
        st.stop()
    for note in cycle.notes:
        st.write(f"- {note}")
    if cycle.broker_cross_check is not None:
        st.write(f"**Broker cross-check (CLI):** {cycle.broker_cross_check.summary()}")
    if cycle.proposal is not None:
        label = "Hold" if cycle.proposal.skip else f"Proposal ({cycle.proposal.kind})"
        st.write(f"**{label}:** {cycle.proposal.rationale}")
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
