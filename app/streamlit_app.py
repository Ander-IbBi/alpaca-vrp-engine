"""VRP Engine dashboard.

Written for someone who has three minutes and has never seen the repo. Top to bottom
it answers, in order: is this safe, is it making money, how much can it lose, what does
it hold, what is it looking at, and why did it do what it did.

The page degrades instead of failing. Without API keys it still replays the decision
journal, so a judge with no credentials sees the whole reasoning trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

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
    load_settings,
)
from vrp_engine.journal import Journal
from vrp_engine.risk.portfolio import (
    PortfolioRisk,
    beta_mapped_curve,
    build_portfolio_risk,
    holdings_from_positions,
)
from vrp_engine.strategy.base import StrategyContext
from vrp_engine.strategy.engine import VrpEngine
from vrp_engine.strategy.management import group_open_structures
from vrp_engine.strategy.signals import build_signal

st.set_page_config(page_title="VRP Engine", page_icon="📈", layout="wide")

LIVE_TTL_SECONDS = 45


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


def journal_entries(settings: Settings) -> list[dict[str, Any]]:
    return Journal(settings.journal_path).read_all()


# --- sections ----------------------------------------------------------------


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
    flags[1].info(f"DRY_RUN = {settings.dry_run}")
    flags[2].info(f"Universe: {len(settings.universe_list())} symbols")
    flags[3].info(f"Expiries: {settings.min_dte}–{settings.max_dte} DTE")

    if live is None or "error" in (live or {}):
        message = (live or {}).get("error", "No API keys configured.")
        st.warning(f"Live account unavailable ({message}). Showing the decision journal only.")
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


def render_equity_curve(live: dict[str, Any]) -> None:
    st.subheader("Equity curve")
    points = live.get("history") or []
    if not points:
        st.info("No portfolio history yet; Alpaca back-fills it once the account trades.")
        return
    frame = pd.DataFrame(points, columns=["timestamp", "equity"]).set_index("timestamp")
    st.line_chart(frame, height=260)


def render_risk_budgets(settings: Settings, portfolio: PortfolioRisk) -> None:
    st.subheader("Risk budgets")
    st.caption(
        "Every structure is defined-risk, so these are not estimates. The worst case is "
        "the exact minimum of the book's payoff curve, and the stress row is that curve "
        "evaluated at a two-sigma one-week move."
    )

    rows = [
        {
            "Budget": "Aggregate worst case",
            "Used": portfolio.total_worst_case_loss_usd,
            "Limit": settings.risk_budget_pct * portfolio.equity,
        },
        {
            "Budget": "Two-sigma stress loss",
            "Used": portfolio.worst_stress_loss_usd,
            "Limit": settings.max_stress_loss_pct * portfolio.equity,
        },
        {
            "Budget": "Beta-weighted delta (absolute)",
            "Used": abs(portfolio.beta_weighted_delta_usd),
            "Limit": settings.max_net_delta_pct * portfolio.equity,
        },
    ]
    frame = pd.DataFrame(rows)
    frame["Utilisation"] = (frame["Used"] / frame["Limit"]).clip(0, 1)
    st.dataframe(
        frame,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Used": st.column_config.NumberColumn(format="$%.0f"),
            "Limit": st.column_config.NumberColumn(format="$%.0f"),
            "Utilisation": st.column_config.ProgressColumn(
                "Utilisation", min_value=0.0, max_value=1.0
            ),
        },
    )

    greeks = st.columns(3)
    greeks[0].metric("Net theta", f"{portfolio.net_theta:+,.0f} / day")
    greeks[1].metric("Net vega", f"{portfolio.net_vega:+,.0f} / vol pt")
    greeks[2].metric("Unrealised P&L", f"{portfolio.unrealized_pl_usd:+,.0f}")

    if portfolio.exposure.by_bucket:
        st.caption("Risk by concentration bucket (index ETFs share one budget)")
        st.bar_chart(
            pd.Series(portfolio.exposure.by_bucket, name="worst case USD"), height=200
        )


def render_payoff(portfolio: PortfolioRisk) -> None:
    st.subheader("Portfolio payoff at expiry")
    if not portfolio.underlyings:
        st.info("No open positions, so the payoff curve is flat at zero.")
        return
    st.caption(
        "Each underlying is shocked by its own beta times a common market move, which is "
        "the same mapping the delta budget uses. The dip is the most this book can lose."
    )
    curve = beta_mapped_curve(portfolio)
    frame = pd.DataFrame(curve, columns=["market move", "P&L at expiry"]).set_index(
        "market move"
    )
    st.line_chart(frame, height=300)

    stress = portfolio.stress
    if stress:
        st.caption("Stress scenarios (one-week move, per-underlying volatility)")
        st.dataframe(
            pd.DataFrame(
                [{"Scenario": k, "P&L": v} for k, v in sorted(stress.items())]
            ),
            hide_index=True,
            use_container_width=True,
            column_config={"P&L": st.column_config.NumberColumn(format="$%.0f")},
        )


def render_structures(live: dict[str, Any]) -> None:
    st.subheader("Open structures")
    structures = live.get("structures") or []
    if not structures:
        st.info("Flat. The engine holds nothing right now.")
        return

    today = live["today"]
    rows = []
    for structure in structures:
        rows.append(
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
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
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


def render_signals(live: dict[str, Any]) -> None:
    st.subheader("Signals across the universe")
    signals = live.get("signals") or {}
    if not signals:
        st.info("No signals computed yet.")
        return
    st.caption(
        "VRP z is (implied − realised) / realised. Positive and past the threshold means "
        "the engine sells premium; negative past it means it buys. Inside the band it "
        "stands down, which is most of the time and by design."
    )
    rows = []
    for symbol, signal in sorted(signals.items()):
        rows.append(
            {
                "Symbol": symbol,
                "Spot": signal.spot,
                "Realised vol": signal.realized_vol,
                "Implied vol": signal.implied_vol,
                "VRP z": signal.vrp_z,
                "Trend": signal.trend,
                "Beta": signal.beta,
                "Stance": signal.stance.replace("_", " "),
                "Blackout": signal.event_blackout,
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Spot": st.column_config.NumberColumn(format="$%.2f"),
            "Realised vol": st.column_config.NumberColumn(format="%.1f%%"),
            "Implied vol": st.column_config.NumberColumn(format="%.1f%%"),
            "VRP z": st.column_config.NumberColumn(format="%+.2f"),
            "Beta": st.column_config.NumberColumn(format="%.2f"),
        },
    )


def render_scanner(
    entries: list[dict[str, Any]],
    live: dict[str, Any] | None = None,
) -> None:
    st.subheader("Opportunity scanner")

    scan = (live or {}).get("scan")
    when = "just now, live"
    if not (scan and scan.get("top")):
        # Fall back to the journal so a keyless visitor still sees a real ranking.
        latest = next(
            (entry for entry in reversed(entries) if (entry.get("scan") or {}).get("top")),
            None,
        )
        if latest is None:
            st.info("No candidates right now, and none recorded yet in the journal.")
            _render_stand_downs(scan)
            return
        scan = latest["scan"]
        when = f"at {latest.get('ts', 'an unknown time')}"

    st.caption(
        f"Ranked {when} by expected value per dollar-day of risk. "
        f"{scan.get('n_accepted', 0)} of {scan.get('n_candidates', 0)} candidates "
        "cleared every gate."
    )
    rows = []
    for row in scan["top"]:
        rows.append(
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
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
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


def _render_stand_downs(scan: dict[str, Any] | None) -> None:
    """Why each skipped underlying was skipped.

    Worth showing rather than hiding: a strategy that can explain what it passed over is
    the opposite of one that trades everything it can reach.
    """
    skipped = (scan or {}).get("skipped") or {}
    if not skipped:
        return
    with st.expander(f"Stood down on {len(skipped)} underlying(s), and why"):
        st.dataframe(
            pd.DataFrame(
                [{"Symbol": k, "Reason": v} for k, v in sorted(skipped.items())]
            ),
            hide_index=True,
            use_container_width=True,
        )


def render_journal(entries: list[dict[str, Any]]) -> None:
    st.subheader("Decision journal")
    if not entries:
        st.info("The journal is empty. Every cycle appends one JSON line here.")
        return
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
                    f"**Analyst ({analyst.get('analyst', '?')}).** "
                    f"{analyst['explanation']}"
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


# --- page --------------------------------------------------------------------


def main() -> None:
    settings = load_settings()
    with st.sidebar:
        st.header("Controls")
        if st.button("Refresh live data", use_container_width=True):
            st.cache_data.clear()
        st.caption(f"Last rendered {datetime.now().strftime('%H:%M:%S')}")
        st.divider()
        st.markdown("**Hard rules**")
        st.markdown(
            "- Paper account only; there is no live flag\n"
            "- Every short leg is covered inside its own ticket\n"
            "- Orders pass `risk.review_proposal` or they do not exist\n"
            "- The LLM may explain and soft-veto, never resize"
        )

    live = None
    try:
        live = load_live(0)
    except Exception as exc:  # noqa: BLE001 — the journal view must survive anything
        live = {"error": f"{type(exc).__name__}: {exc}"}

    render_header(settings, live)
    entries = journal_entries(settings)

    if live and "error" not in live:
        st.divider()
        left, right = st.columns([3, 2])
        with left:
            render_equity_curve(live)
        with right:
            render_risk_budgets(settings, live["portfolio"])
        st.divider()
        render_payoff(live["portfolio"])
        st.divider()
        render_structures(live)
        st.divider()
        render_signals(live)

    st.divider()
    render_scanner(entries, live if live and "error" not in live else None)
    st.divider()
    render_journal(entries)


main()
