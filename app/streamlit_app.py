"""VRP Engine dashboard.

Written for someone who has three minutes and has never seen the repo. The tabs
answer, in order: is it making money, how much can it lose, what is it looking at,
why did it do what it did, and how does the thing actually work.

The page degrades instead of failing. Without API keys it replays the recorded
decision journal, so every tab still has real content and a visitor with no
credentials sees the whole reasoning trail.

This module never submits an order. Execution stays on the operator machine, in the
`run-agent` loop, which trades on its own judgement without consulting this page.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Streamlit Community Cloud installs with pip and does not run `uv sync`, so the
# src/ layout is not on PYTHONPATH unless we add it before importing the package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import altair as alt
import pandas as pd
import streamlit as st

from vrp_engine import viz
from vrp_engine.alpaca.market_data import fetch_daily_bars
from vrp_engine.alpaca.options import (
    expiries_in_window,
    fetch_quoted_chain,
    fetch_snapshots_for,
    is_option_position,
    market_date,
    parse_occ_symbol,
)
from vrp_engine.config import (
    LiveTradingForbiddenError,
    MissingCredentialsError,
    Settings,
    assert_paper_only,
    hydrate_env_from_mapping,
    load_settings,
)
from vrp_engine.journal import read_entries
from vrp_engine.risk.portfolio import build_portfolio_risk, holdings_from_positions
from vrp_engine.strategy.base import StrategyContext
from vrp_engine.strategy.engine import VrpEngine
from vrp_engine.strategy.management import group_open_structures
from vrp_engine.strategy.signals import build_signal

st.set_page_config(page_title="VRP Engine", page_icon="📈", layout="wide")

LIVE_TTL_SECONDS = 45

# Semantic colours, chosen to stay legible on the dark theme in .streamlit/config.toml.
SELL_COLOUR = "#22c55e"
BUY_COLOUR = "#38bdf8"
NEUTRAL_COLOUR = "#94a3b8"
LOSS_COLOUR = "#f87171"

STANCE_SCALE = alt.Scale(
    domain=["Sell premium", "Buy premium", "Stand down"],
    range=[SELL_COLOUR, BUY_COLOUR, NEUTRAL_COLOUR],
)

CYCLE_DOT = """
digraph cycle {
  rankdir=LR;
  bgcolor="transparent";
  node [shape=box, style="rounded,filled", fillcolor="#1e293b", color="#334155",
        fontcolor="#e2e8f0", fontname="Helvetica", fontsize=11];
  edge [color="#64748b", arrowsize=0.7];

  observe [label="observe\\naccount + chains"];
  guard [label="guard\\nbreakers, window"];
  signals [label="signals\\nrealised vs implied"];
  propose [label="propose\\ndefined-risk structure"];
  risk [label="risk\\nbudgets + payoff", fillcolor="#3f2222", color="#7f1d1d"];
  research [label="research\\nMCP briefing"];
  analyst [label="analyst\\nsoft veto only"];
  verify [label="verify\\nCLI reads the book"];
  execute [label="execute\\nalpaca-py, paper", fillcolor="#14361f", color="#166534"];
  reconcile [label="reconcile\\nCLI reads it again"];
  journal [label="journal\\none JSON line"];

  observe -> guard -> signals -> propose -> risk -> research -> analyst
      -> verify -> execute -> reconcile -> journal;
}
"""


# --- data access -------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def _settings() -> Settings:
    return assert_paper_only(load_settings())


@st.cache_data(ttl=LIVE_TTL_SECONDS, show_spinner="Reading the paper account...")
def load_live(_nonce: int) -> dict[str, Any] | None:
    """One round trip for everything the page needs, cached so reruns stay cheap."""
    from vrp_engine.alpaca.client import PaperAlpaca

    settings = _settings()
    try:
        client = PaperAlpaca(settings)
    except (LiveTradingForbiddenError, MissingCredentialsError) as exc:
        return {"error": str(exc)}

    today = market_date()
    account = client.account()
    positions = client.positions()
    universe = settings.universe_list()

    histories = fetch_daily_bars(client, universe, today=today)
    chains: dict[str, list] = {}
    for symbol in universe:
        try:
            chains[symbol] = fetch_quoted_chain(
                client, symbol, today=today, min_dte=settings.min_dte, max_dte=settings.max_dte
            )
        except Exception:  # noqa: BLE001 — one bad chain must not blank the page
            chains[symbol] = []

    spots: dict[str, float] = {
        symbol: float(history.last_close)
        for symbol, history in histories.items()
        if history.last_close
    }
    quotes = {}
    for candidates in chains.values():
        for candidate in candidates:
            quotes[candidate.symbol.upper()] = candidate
    held = {
        str(getattr(p, "symbol", "")).upper() for p in positions if is_option_position(p)
    }
    missing = sorted(held - set(quotes))
    if missing:
        try:
            for candidate in fetch_snapshots_for(client, missing):
                quotes[candidate.symbol.upper()] = candidate
        except Exception:  # noqa: BLE001
            pass
    for symbol in held:
        parsed = parse_occ_symbol(symbol)
        if parsed and parsed.underlying not in spots:
            try:
                spots[parsed.underlying] = float(client.last_price(parsed.underlying) or 0.0)
            except Exception:  # noqa: BLE001
                spots[parsed.underlying] = 0.0

    market = histories.get("SPY")
    market_returns = market.log_returns() if market else []
    signals = {}
    for symbol in universe:
        history = histories.get(symbol)
        if history is None or not history.bars:
            continue
        signals[symbol] = build_signal(
            symbol=symbol,
            spot=spots.get(symbol, 0.0),
            history=history,
            candidates=chains.get(symbol, []),
            expiries=expiries_in_window(
                chains.get(symbol, []),
                today=today,
                min_dte=settings.min_dte,
                max_dte=settings.max_dte,
            ),
            market_returns=market_returns,
            today=today,
            vrp_z_entry=settings.vrp_z_entry,
            term_slope_blackout=settings.term_slope_blackout,
        )

    equity = float(getattr(account, "equity", 0.0) or 0.0)
    portfolio = build_portfolio_risk(
        equity=equity,
        positions=positions,
        spots=spots,
        betas={s: sig.beta for s, sig in signals.items()},
        vols={s: sig.realized_vol for s, sig in signals.items() if sig.realized_vol},
        greeks=quotes,
        bucket_of=settings.bucket_of,
    )
    option_holdings, _ = holdings_from_positions(positions, greeks=quotes)

    # Rank the universe live rather than replaying the last journalled scan, so the
    # scanner is populated even when the agent has not run since the market closed.
    scan = None
    try:
        scan = VrpEngine(settings).scan(
            StrategyContext(
                today=today,
                now=datetime.now(),
                market_open=False,
                equity=equity,
                cash=float(getattr(account, "cash", 0.0) or 0.0),
                options_buying_power=client.options_buying_power(),
                universe=universe,
                spots=spots,
                signals=signals,
                chains=chains,
                portfolio=portfolio,
                positions=positions,
                quotes=quotes,
            )
        ).digest(limit=15)
    except Exception:  # noqa: BLE001 — the scanner is a view, not a dependency
        scan = None

    return {
        "account_number": getattr(account, "account_number", ""),
        "equity": equity,
        "last_equity": float(getattr(account, "last_equity", equity) or equity),
        "cash": float(getattr(account, "cash", 0.0) or 0.0),
        "options_buying_power": client.options_buying_power(),
        "market_open": bool(client.clock().is_open),
        "history": client.portfolio_history(period="1W", timeframe="1H"),
        "portfolio": portfolio,
        "structures": group_open_structures(option_holdings),
        "signals": signals,
        "scan": scan,
        "today": today,
    }


def _hydrate_streamlit_secrets() -> None:
    """Pull paper keys out of st.secrets when running on Community Cloud.

    `st.secrets` is lazy: it hits the filesystem on the first lookup, not when the
    handle is taken, so the guard has to wrap the read itself. A local run with only
    a `.env` file has no secrets at all, and that is a normal way to start the page.
    """
    try:
        hydrate_env_from_mapping(st.secrets)
    except Exception:  # noqa: BLE001 — no secrets file is the common local case
        return


def journal_entries(settings: Settings) -> tuple[list[dict[str, Any]], bool]:
    return read_entries(settings.journal_path)


def resolve_sources(
    live: dict[str, Any] | None,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prefer the live account, fall back to the journal, per block.

    Signals, the portfolio and the scan are resolved independently: a hosted
    instance with keys but a flat book still shows a recorded stress table, and one
    with no keys at all still shows every chart from the recorded trail.
    """
    has_live = bool(live) and "error" not in (live or {})
    source: dict[str, Any] = {"live": live if has_live else None, "has_live": has_live}

    for key in ("signals", "portfolio", "scan"):
        value = (live or {}).get(key) if has_live else None
        origin = "live"
        if not value:
            value = viz.latest_block(entries, key)
            origin = "journal" if value else "none"
        source[key] = value
        source[f"{key}_origin"] = origin
    return source


def _origin_note(origin: str) -> str:
    return "" if origin == "live" else " (from the recorded journal)"


# --- header ------------------------------------------------------------------


def render_header(settings: Settings, live: dict[str, Any] | None) -> None:
    st.title("VRP Engine")
    st.caption(
        "An autonomous agent that trades the **variance risk premium**: it sells option "
        "premium when the market's implied volatility is richer than what the underlying "
        "actually delivers, buys it when the reverse holds, and only ever in "
        "defined-risk spreads. **Alpaca paper account only.**"
    )

    flags = st.columns(4)
    flags[0].success("PAPER ONLY — no live code path")
    flags[1].success("AUTONOMOUS — no approval step")
    flags[2].info(f"Universe: {len(settings.universe_list())} symbols")
    flags[3].info(f"Expiries: {settings.min_dte}–{settings.max_dte} DTE")

    if live is None or "error" in (live or {}):
        message = (live or {}).get("error", "No API keys configured.")
        st.warning(
            f"Live account unavailable ({message}). Every tab below is replaying the "
            "recorded decision journal instead."
        )
        return

    equity = live["equity"]
    day_pl = equity - live["last_equity"]
    total_pl = equity - settings.start_equity_usd
    cols = st.columns(5)
    cols[0].metric("Equity", f"${equity:,.0f}", f"{day_pl:+,.0f} today")
    cols[1].metric(
        "Since start",
        f"{total_pl:+,.0f}",
        f"{total_pl / settings.start_equity_usd:+.2%}",
    )
    cols[2].metric("Options buying power", f"${live['options_buying_power']:,.0f}")
    cols[3].metric("Open structures", len(live["structures"]))
    cols[4].metric("Market", "open" if live["market_open"] else "closed")


# --- overview ----------------------------------------------------------------


def render_equity_curve(live: dict[str, Any] | None, settings: Settings) -> None:
    st.subheader("Equity curve")
    rows = viz.equity_rows((live or {}).get("history"))
    if not rows:
        st.info(
            "No portfolio history yet. Alpaca back-fills it once the account trades, "
            "and the loop starts trading at the open."
        )
        return

    frame = pd.DataFrame(rows)
    base = alt.Chart(frame).encode(
        x=alt.X("timestamp:T", title=None),
        y=alt.Y(
            "equity:Q",
            title="Equity (USD)",
            scale=alt.Scale(zero=False),
            axis=alt.Axis(format="$,.0f"),
        ),
    )
    area = base.mark_area(opacity=0.2, color=SELL_COLOUR)
    line = base.mark_line(strokeWidth=2, color=SELL_COLOUR)
    start = (
        alt.Chart(pd.DataFrame({"equity": [settings.start_equity_usd]}))
        .mark_rule(strokeDash=[4, 4], color=NEUTRAL_COLOUR)
        .encode(y="equity:Q")
    )
    st.altair_chart((area + line + start).properties(height=280), width="stretch")
    st.caption("The dashed line is the account's starting equity.")


def render_timeline(entries: list[dict[str, Any]]) -> None:
    st.subheader("Decision timeline")
    rows = [row for row in viz.journal_timeline(entries) if row["equity"] is not None]
    if not rows:
        st.info("No journalled cycles yet. Each cycle appends one line.")
        return

    frame = pd.DataFrame(rows)
    line = (
        alt.Chart(frame)
        .mark_line(strokeWidth=2, color=NEUTRAL_COLOUR)
        .encode(
            x=alt.X("cycle:Q", title="Cycle", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y(
                "equity:Q",
                title="Equity (USD)",
                scale=alt.Scale(zero=False),
                axis=alt.Axis(format="$,.0f"),
            ),
        )
    )
    points = line.mark_point(size=110, filled=True).encode(
        color=alt.Color("action:N", title="Decision"),
        tooltip=[
            alt.Tooltip("ts:N", title="When"),
            alt.Tooltip("action:N", title="Decision"),
            alt.Tooltip("equity:Q", title="Equity", format="$,.0f"),
            alt.Tooltip("rationale:N", title="Why"),
        ],
    )
    st.altair_chart((line + points).properties(height=240), width="stretch")
    st.caption(
        "Every cycle is a point, including the ones that decided to do nothing. "
        "Standing down is a decision the journal records like any other."
    )


def render_structures(live: dict[str, Any] | None) -> None:
    st.subheader("Open structures")
    structures = (live or {}).get("structures") or []
    if not structures:
        st.info("Flat. The engine holds nothing right now.")
        return

    today = live["today"]
    rows = [
        {
            "Underlying": structure.underlying,
            "Structure": structure.kind.replace("_", " "),
            "Strikes": " / ".join(
                f"{leg.strike:g}" for leg in sorted(structure.legs, key=lambda x: x.strike)
            ),
            "Expiry": structure.expiration.isoformat(),
            "DTE": structure.dte(today),
            "Contracts": structure.contracts,
            "Premium": structure.net_premium_usd,
            "Unrealised": structure.unrealized_pl_usd,
            "Captured": structure.capture_fraction,
        }
        for structure in structures
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Premium": st.column_config.NumberColumn(
                format="$%.0f", help="Positive is a credit collected, negative a debit paid"
            ),
            "Unrealised": st.column_config.NumberColumn(format="$%.0f"),
            "Captured": st.column_config.NumberColumn(
                format="%.0f%%", help="Share of the original premium already earned"
            ),
        },
    )


# --- risk --------------------------------------------------------------------


def render_risk_budgets(settings: Settings, portfolio: Any, origin: str) -> None:
    st.subheader("Risk budgets")
    if portfolio is None:
        st.info("No portfolio snapshot yet, live or journalled.")
        return

    scalars = viz.portfolio_scalars(portfolio)
    st.caption(
        "Every structure is defined-risk, so these are not estimates. The bar is what "
        "the book has committed, the tick is the budget" + _origin_note(origin) + "."
    )

    rows = viz.budget_rows(settings, scalars)
    frame = pd.DataFrame(rows)
    order = [row["budget"] for row in rows]
    bars = (
        alt.Chart(frame)
        .mark_bar(color=SELL_COLOUR, opacity=0.8, height=22)
        .encode(
            y=alt.Y("budget:N", title=None, sort=order),
            x=alt.X("used_usd:Q", title="USD", axis=alt.Axis(format="$,.0f")),
            tooltip=[
                alt.Tooltip("budget:N", title="Budget"),
                alt.Tooltip("used_usd:Q", title="Used", format="$,.0f"),
                alt.Tooltip("limit_usd:Q", title="Limit", format="$,.0f"),
                alt.Tooltip("utilisation:Q", title="Utilisation", format=".0%"),
            ],
        )
    )
    limits = (
        alt.Chart(frame)
        .mark_tick(color=LOSS_COLOUR, thickness=3, size=26)
        .encode(y=alt.Y("budget:N", title=None, sort=order), x="limit_usd:Q")
    )
    st.altair_chart((bars + limits).properties(height=160), width="stretch")

    greeks = st.columns(3)
    greeks[0].metric("Net theta", f"{scalars['net_theta']:+,.0f} / day")
    greeks[1].metric("Net vega", f"{scalars['net_vega']:+,.0f} / vol pt")
    greeks[2].metric("Unrealised P&L", f"{scalars['unrealized_pl_usd']:+,.0f}")

    buckets = viz.bucket_rows(scalars["by_bucket"])
    if buckets:
        st.caption("Worst case by concentration bucket (index ETFs share one budget)")
        st.altair_chart(
            alt.Chart(pd.DataFrame(buckets))
            .mark_bar(color=NEUTRAL_COLOUR, height=20)
            .encode(
                y=alt.Y("bucket:N", title=None, sort="-x"),
                x=alt.X("worst_case_usd:Q", title="Worst case (USD)",
                        axis=alt.Axis(format="$,.0f")),
                tooltip=[
                    alt.Tooltip("bucket:N", title="Bucket"),
                    alt.Tooltip("worst_case_usd:Q", title="Worst case", format="$,.0f"),
                ],
            )
            .properties(height=max(120, 28 * len(buckets))),
            width="stretch",
        )


def render_payoff(live: dict[str, Any] | None) -> None:
    st.subheader("Portfolio payoff at expiry")
    portfolio = (live or {}).get("portfolio")
    if portfolio is None or not getattr(portfolio, "underlyings", None):
        st.info(
            "The payoff curve needs live positions to price. With an empty book, or "
            "without API keys, it would be a flat line at zero."
        )
        return

    st.caption(
        "Each underlying is shocked by its own beta times a common market move, which "
        "is the same mapping the delta budget uses. The marked dip is the exact worst "
        "case, found at a strike rather than sampled on a grid."
    )
    rows = viz.payoff_rows(portfolio)
    frame = pd.DataFrame(rows)
    x_axis = alt.X("shock:Q", title="Market move", axis=alt.Axis(format="+.0%"))
    curve = (
        alt.Chart(frame)
        .mark_line(strokeWidth=2, color=SELL_COLOUR)
        .encode(
            x=x_axis,
            y=alt.Y("pnl:Q", title="P&L at expiry (USD)", axis=alt.Axis(format="$,.0f")),
            tooltip=[
                alt.Tooltip("shock:Q", title="Move", format="+.1%"),
                alt.Tooltip("pnl:Q", title="P&L", format="$,.0f"),
            ],
        )
    )
    zero = (
        alt.Chart(pd.DataFrame({"pnl": [0.0]}))
        .mark_rule(strokeDash=[4, 4], color=NEUTRAL_COLOUR)
        .encode(y="pnl:Q")
    )
    layers = [curve, zero]

    worst = viz.worst_case_point(rows)
    if worst is not None:
        layers.append(
            alt.Chart(pd.DataFrame([worst]))
            .mark_point(size=160, filled=True, color=LOSS_COLOUR)
            .encode(
                x=x_axis,
                y="pnl:Q",
                tooltip=[alt.Tooltip("pnl:Q", title="Worst case", format="$,.0f")],
            )
        )
    st.altair_chart(alt.layer(*layers).properties(height=320), width="stretch")


def render_stress(portfolio: Any, origin: str) -> None:
    scalars = viz.portfolio_scalars(portfolio) if portfolio is not None else {"stress": {}}
    rows = viz.stress_rows(scalars.get("stress"))
    if not rows:
        return
    st.caption(
        "Stress scenarios: a one-week move at each underlying's own volatility"
        + _origin_note(origin)
        + "."
    )
    frame = pd.DataFrame(rows)
    st.altair_chart(
        alt.Chart(frame)
        .mark_bar(height=20)
        .encode(
            y=alt.Y("scenario:N", title=None, sort=[row["scenario"] for row in rows]),
            x=alt.X("pnl:Q", title="P&L (USD)", axis=alt.Axis(format="$,.0f")),
            color=alt.condition(
                alt.datum.pnl < 0, alt.value(LOSS_COLOUR), alt.value(SELL_COLOUR)
            ),
            tooltip=[
                alt.Tooltip("scenario:N", title="Scenario"),
                alt.Tooltip("pnl:Q", title="P&L", format="$,.0f"),
            ],
        )
        .properties(height=max(120, 26 * len(rows))),
        width="stretch",
    )


# --- opportunities -----------------------------------------------------------


def render_volatility_map(settings: Settings, signals: Any, origin: str) -> None:
    st.subheader("Where volatility is mispriced")
    rows = viz.volatility_rows(signals or {})
    if not rows:
        st.info("No signals available yet, live or journalled.")
        return

    st.caption(
        "Each dot is an underlying: what the option market charges (vertical) against "
        "what the stock actually delivers (horizontal). Above the shaded corridor the "
        "engine sells premium, below it buys, and inside it stands down"
        + _origin_note(origin)
        + "."
    )

    frame = pd.DataFrame(rows)
    band_frame = pd.DataFrame(viz.entry_band_rows(rows, vrp_z_entry=settings.vrp_z_entry))
    x_axis = alt.X(
        "realized_vol:Q",
        title="Realised volatility",
        axis=alt.Axis(format=".0%"),
        scale=alt.Scale(zero=True),
    )
    y_axis = alt.Y(
        "implied_vol:Q",
        title="Implied volatility",
        axis=alt.Axis(format=".0%"),
        scale=alt.Scale(zero=True),
    )

    band = (
        alt.Chart(band_frame)
        .mark_area(opacity=0.16, color=NEUTRAL_COLOUR)
        .encode(
            x=alt.X("realized_vol:Q", title="Realised volatility",
                    axis=alt.Axis(format=".0%")),
            y=alt.Y("lower:Q", title="Implied volatility", axis=alt.Axis(format=".0%")),
            y2="upper:Q",
        )
    )
    fair = (
        alt.Chart(band_frame)
        .mark_line(strokeDash=[4, 4], color=NEUTRAL_COLOUR)
        .encode(x="realized_vol:Q", y=alt.Y("fair:Q", title="Implied volatility"))
    )
    dots = (
        alt.Chart(frame)
        .mark_circle(size=190, opacity=0.9)
        .encode(
            x=x_axis,
            y=y_axis,
            color=alt.Color("stance_label:N", title="Stance", scale=STANCE_SCALE),
            tooltip=[
                alt.Tooltip("symbol:N", title="Symbol"),
                alt.Tooltip("realized_vol:Q", title="Realised", format=".1%"),
                alt.Tooltip("implied_vol:Q", title="Implied", format=".1%"),
                alt.Tooltip("vrp_z:Q", title="VRP z", format="+.2f"),
                alt.Tooltip("trend:N", title="Trend"),
                alt.Tooltip("stance_label:N", title="Stance"),
            ],
        )
    )
    labels = (
        alt.Chart(frame)
        .mark_text(dy=-16, fontSize=11, color=NEUTRAL_COLOUR)
        .encode(x=x_axis, y=y_axis, text="symbol:N")
    )
    st.altair_chart(
        alt.layer(band, fair, dots, labels).properties(height=380), width="stretch"
    )
    st.caption(
        f"The corridor is |VRP z| < {settings.vrp_z_entry:.2f}, the threshold below "
        "which the mispricing is too small to pay for the spread it would cost to trade."
    )


def render_wedge(settings: Settings, scan: Any, origin: str) -> None:
    st.subheader("The wedge: our odds against the market's")
    rows = viz.wedge_rows(scan, limit=10)
    if not rows:
        st.info("No ranked candidates right now, live or journalled.")
        return

    st.caption(
        "For each candidate structure, the win probability under the engine's own "
        "distribution versus the one the market's price implies. The gap is the "
        "**wedge**, and it is what authorises a trade" + _origin_note(origin) + "."
    )

    wide = pd.DataFrame(rows)
    long = pd.DataFrame(viz.wedge_points(rows))
    order = [row["label"] for row in rows]

    connector = (
        alt.Chart(wide)
        .mark_rule(strokeWidth=3, color=NEUTRAL_COLOUR, opacity=0.6)
        .encode(
            y=alt.Y("label:N", title=None, sort=order),
            x=alt.X("p_win_implied:Q", title="Win probability", axis=alt.Axis(format=".0%")),
            x2="p_win_model:Q",
        )
    )
    dots = (
        alt.Chart(long)
        .mark_point(size=140, filled=True)
        .encode(
            y=alt.Y("label:N", title=None, sort=order),
            x=alt.X("probability:Q", title="Win probability", axis=alt.Axis(format=".0%")),
            color=alt.Color(
                "kind:N",
                title=None,
                scale=alt.Scale(domain=["Model", "Market"], range=[SELL_COLOUR, NEUTRAL_COLOUR]),
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Structure"),
                alt.Tooltip("kind:N", title="Distribution"),
                alt.Tooltip("probability:Q", title="P(win)", format=".1%"),
            ],
        )
    )
    st.altair_chart(
        (connector + dots).properties(height=max(180, 34 * len(rows))), width="stretch"
    )
    st.caption(
        f"A candidate needs a wedge of at least {settings.min_wedge:+.1%} to be "
        "considered. A negative wedge is a rejection, however attractive the credit."
    )


def render_scanner(scan: Any, origin: str) -> None:
    st.subheader("Opportunity scanner")
    if not (scan and scan.get("top")):
        st.info("No candidates ranked yet.")
        return

    st.caption(
        f"Ranked by expected value per dollar-day of risk{_origin_note(origin)}. "
        f"{scan.get('n_accepted', 0)} of {scan.get('n_candidates', 0)} candidates "
        "cleared every gate."
    )
    rows = [
        {
            "Underlying": row["underlying"],
            "Structure": str(row["structure"]).replace("_", " "),
            "Strikes": " / ".join(f"{s:g}" for s in row.get("strikes", [])),
            "DTE": row["dte"],
            "Credit": row.get("credit_usd"),
            "Max loss": row.get("max_loss_usd"),
            "P(win) model": row.get("p_win_model"),
            "P(win) implied": row.get("p_win_implied"),
            "Wedge": row.get("wedge"),
            "EV": row.get("expected_value_usd"),
            "Edge": row.get("edge"),
            "Accepted": row.get("accepted"),
            "Why not": "; ".join(row.get("rejects") or []),
        }
        for row in scan["top"]
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Credit": st.column_config.NumberColumn(format="$%.0f"),
            "Max loss": st.column_config.NumberColumn(format="$%.0f"),
            "P(win) model": st.column_config.NumberColumn(format="%.0f%%"),
            "P(win) implied": st.column_config.NumberColumn(format="%.0f%%"),
            "Wedge": st.column_config.NumberColumn(
                format="%+.1f%%",
                help="Model win probability minus the market's own. Must be positive.",
            ),
            "EV": st.column_config.NumberColumn(format="$%+.0f"),
            "Edge": st.column_config.NumberColumn(format="%+.1f%%"),
        },
    )
    _render_stand_downs(scan)


def _render_stand_downs(scan: Any) -> None:
    """Why each skipped underlying was skipped.

    Worth showing rather than hiding: a strategy that can explain what it passed over
    is the opposite of one that trades everything it can reach.
    """
    skipped = (scan or {}).get("skipped") or {}
    if not skipped:
        return
    with st.expander(f"Stood down on {len(skipped)} underlying(s), and why"):
        st.dataframe(
            pd.DataFrame([{"Symbol": k, "Reason": v} for k, v in sorted(skipped.items())]),
            hide_index=True,
            width="stretch",
        )


def render_signals_table(signals: Any, origin: str) -> None:
    rows = viz.volatility_rows(signals or {})
    if not rows:
        return
    with st.expander(f"Signal detail for {len(rows)} underlyings{_origin_note(origin)}"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Symbol": row["symbol"],
                        "Spot": row["spot"],
                        "Realised vol": row["realized_vol"],
                        "Implied vol": row["implied_vol"],
                        "VRP z": row["vrp_z"],
                        "Trend": row["trend"],
                        "Beta": row["beta"],
                        "Stance": row["stance_label"],
                        "Blackout": row["event_blackout"],
                    }
                    for row in rows
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Spot": st.column_config.NumberColumn(format="$%.2f"),
                "Realised vol": st.column_config.NumberColumn(format="%.1f%%"),
                "Implied vol": st.column_config.NumberColumn(format="%.1f%%"),
                "VRP z": st.column_config.NumberColumn(format="%+.2f"),
                "Beta": st.column_config.NumberColumn(format="%.2f"),
            },
        )


# --- journal -----------------------------------------------------------------


def render_journal(entries: list[dict[str, Any]], *, from_sample: bool = False) -> None:
    st.subheader("Decision journal")
    if not entries:
        st.info("The journal is empty. Every cycle appends one JSON line here.")
        return
    if from_sample:
        st.caption(
            "Showing a recorded trail from a paper session. This hosted instance has "
            "no local journal file; the operator's agent writes that file on the "
            "machine that runs the loop."
        )
    else:
        st.caption(
            "Append-only, one JSON object per cycle. Skipped cycles are recorded too, "
            "because the reason not to trade is part of the strategy."
        )

    for entry in reversed(entries[-25:]):
        proposal = entry.get("proposal") or {}
        action = proposal.get("action", "hold")
        equity = entry.get("equity")
        title = f"{entry.get('ts', '?')} — {action}"
        if equity:
            title += f" — equity ${float(equity):,.0f}"
        with st.expander(title):
            if proposal.get("rationale"):
                st.markdown(f"**Rationale.** {proposal['rationale']}")
            analytics = proposal.get("analytics") or {}
            if analytics:
                st.json(analytics, expanded=False)
            risk = entry.get("risk") or {}
            if risk.get("checks"):
                st.markdown("**Risk checks passed.** " + "; ".join(risk["checks"]))
            if risk.get("reasons"):
                st.error("Risk blocked: " + "; ".join(risk["reasons"]))
            analyst = entry.get("analyst") or {}
            if analyst.get("explanation"):
                st.markdown(
                    f"**Analyst ({analyst.get('analyst', '?')}).** {analyst['explanation']}"
                )
            execution = entry.get("execution") or {}
            if execution:
                st.markdown(
                    "**Execution.** "
                    + ("sent to paper" if execution.get("submitted") else "dry run")
                    + (f" — order {execution['order_id']}" if execution.get("order_id") else "")
                )
            for note in entry.get("notes") or []:
                st.caption(note)


# --- how it works ------------------------------------------------------------


def render_how_it_works(settings: Settings) -> None:
    st.subheader("One cycle, start to finish")
    st.caption(
        "The ordering is the design: the risk layer runs after the strategy and before "
        "the LLM, so no model output can talk its way past a budget."
    )
    st.graphviz_chart(CYCLE_DOT, width="stretch")

    st.subheader("Three Alpaca surfaces, one account")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Plane": "Execution",
                    "Surface": "alpaca-py",
                    "Job": "Places every order, always paper=True",
                    "If it fails": "Hard: no order, and the journal says so",
                },
                {
                    "Plane": "Verification",
                    "Surface": "Alpaca CLI",
                    "Job": "Reads the book before every ticket and again after each fill",
                    "If it fails": "Open: the cycle records checked=false",
                },
                {
                    "Plane": "Research",
                    "Surface": "Alpaca MCP server",
                    "Job": "Regime briefing and a second quote source, read-only",
                    "If it fails": "Open: the cycle continues without it",
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    st.subheader("What cannot happen")
    st.markdown(
        "- **No live trading.** `TradingClient` is always built with `paper=True`, and "
        "startup aborts if `ALPACA_LIVE_TRADE=true`. There is no flag to flip.\n"
        "- **No naked shorts.** Every short leg must be paired with a protective long "
        "leg of the same type and expiry inside the same ticket, and the risk layer "
        "proves it from the legs rather than trusting the ticket's label.\n"
        "- **No order without a risk review.** Nothing reaches `submit_order` without "
        "passing `review_proposal` first.\n"
        "- **No human approval step.** Switching the loop on is the only decision an "
        "operator makes. After that it opens, manages and closes positions on its own; "
        "there is no confirmation prompt and no way to wave a trade through.\n"
        "- **No trading from this page.** The dashboard is read-only; the agent runs "
        "as a separate process.\n"
        "- **The LLM cannot resize.** It may explain, and may raise a soft veto on one "
        "of five fixed reasons. A hallucinated veto is discarded and failures fail open."
    )

    st.subheader("The budgets it answers to")
    st.dataframe(
        pd.DataFrame(
            [
                {"Budget": "Aggregate theoretical max loss",
                 "Limit": settings.risk_budget_pct, "Scope": "whole book"},
                {"Budget": "Per trade max loss",
                 "Limit": settings.max_trade_loss_pct, "Scope": "one ticket"},
                {"Budget": "Per underlying",
                 "Limit": settings.max_underlying_loss_pct, "Scope": "one symbol"},
                {"Budget": "Index bucket",
                 "Limit": settings.max_bucket_loss_pct, "Scope": "SPY, QQQ, IWM, DIA"},
                {"Budget": "Two-sigma one-week stress",
                 "Limit": settings.max_stress_loss_pct, "Scope": "whole book"},
                {"Budget": "Beta-weighted net delta",
                 "Limit": settings.max_net_delta_pct, "Scope": "SPY-equivalent"},
                {"Budget": "Daily loss breaker",
                 "Limit": settings.max_daily_loss_pct, "Scope": "stops opening"},
                {"Budget": "Hard equity floor",
                 "Limit": 1.0 - settings.equity_floor_pct, "Scope": "flatten"},
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Limit": st.column_config.NumberColumn(
                format="%.1f%%", help="As a fraction of account equity"
            )
        },
    )


# --- page --------------------------------------------------------------------


def main() -> None:
    _hydrate_streamlit_secrets()
    try:
        settings = assert_paper_only(load_settings())
    except LiveTradingForbiddenError as exc:
        st.error(f"Refusing to render: {exc}")
        st.stop()
        return

    with st.sidebar:
        st.header("Controls")
        if st.button("Refresh live data", width="stretch"):
            st.cache_data.clear()
        st.caption(f"Last rendered {datetime.now().strftime('%H:%M:%S')}")
        st.divider()
        st.markdown("**Hard rules**")
        st.markdown(
            "- Paper account only; there is no live flag\n"
            "- Every short leg is covered inside its own ticket\n"
            "- Orders pass `risk.review_proposal` or they do not exist\n"
            "- The LLM may explain and soft-veto, never resize\n"
            "- Nobody approves a trade; the loop decides and acts\n"
            "- This page never sends an order"
        )

    live: dict[str, Any] | None = None
    try:
        live = load_live(0)
    except Exception as exc:  # noqa: BLE001 — the journal view must survive anything
        live = {"error": f"{type(exc).__name__}: {exc}"}

    entries, from_sample = journal_entries(settings)
    sources = resolve_sources(live, entries)

    render_header(settings, live)

    overview, risk, opportunities, journal, how = st.tabs(
        ["Overview", "Risk", "Opportunities", "Journal", "How it works"]
    )

    with overview:
        render_equity_curve(sources["live"], settings)
        st.divider()
        render_timeline(entries)
        st.divider()
        render_structures(sources["live"])

    with risk:
        render_risk_budgets(settings, sources["portfolio"], sources["portfolio_origin"])
        st.divider()
        render_payoff(sources["live"])
        st.divider()
        render_stress(sources["portfolio"], sources["portfolio_origin"])

    with opportunities:
        render_volatility_map(settings, sources["signals"], sources["signals_origin"])
        st.divider()
        render_wedge(settings, sources["scan"], sources["scan_origin"])
        st.divider()
        render_scanner(sources["scan"], sources["scan_origin"])
        render_signals_table(sources["signals"], sources["signals_origin"])

    with journal:
        render_journal(entries, from_sample=from_sample)

    with how:
        render_how_it_works(settings)


main()
