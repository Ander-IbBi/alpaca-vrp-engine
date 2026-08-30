"""Chart-ready views of what the engine sees.

The dashboard has to draw the same picture from two different places: the live
account when API keys are present, and the recorded decision journal when they are
not. Those arrive in different shapes — pydantic models on one side, the JSON
digests the journal stores on the other — so everything here reads a field the same
way from either, and returns plain rows a chart can consume.

Nothing in this module imports Streamlit. That is what lets the entire visual
surface be exercised offline by the test suite.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vrp_engine.config import Settings
from vrp_engine.risk.portfolio import beta_mapped_curve
from vrp_engine.strategy.signals import (
    STANCE_BUY_VOL,
    STANCE_SELL_VOL,
    STANCE_STAND_DOWN,
    TREND_FLAT,
)

STANCE_LABELS = {
    STANCE_SELL_VOL: "Sell premium",
    STANCE_BUY_VOL: "Buy premium",
    STANCE_STAND_DOWN: "Stand down",
}


def _field(source: Any, name: str, default: Any = None) -> Any:
    """Read one field from a pydantic model or from the JSON digest of one."""
    value = source.get(name) if isinstance(source, Mapping) else getattr(source, name, None)
    return default if value is None else value


# --- signals -----------------------------------------------------------------


def volatility_rows(signals: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One point per underlying on the realised-versus-implied plane.

    Symbols missing either volatility are dropped rather than plotted at zero: an
    absent chain is not the same claim as "this name is priced at nothing".
    """
    rows: list[dict[str, Any]] = []
    for symbol, signal in sorted(signals.items()):
        realized = _field(signal, "realized_vol")
        implied = _field(signal, "implied_vol")
        if not realized or not implied:
            continue
        stance = str(_field(signal, "stance", STANCE_STAND_DOWN))
        rows.append(
            {
                "symbol": str(symbol).upper(),
                "realized_vol": float(realized),
                "implied_vol": float(implied),
                "vrp_z": float(_field(signal, "vrp_z", 0.0)),
                "trend": str(_field(signal, "trend", TREND_FLAT)),
                "beta": float(_field(signal, "beta", 1.0)),
                "spot": float(_field(signal, "spot", 0.0)),
                "stance": stance,
                "stance_label": STANCE_LABELS.get(stance, stance.replace("_", " ")),
                "event_blackout": bool(_field(signal, "event_blackout", False)),
            }
        )
    return rows


def entry_band_rows(
    rows: Sequence[Mapping[str, Any]], *, vrp_z_entry: float
) -> list[dict[str, Any]]:
    """The corridor where volatility is not mispriced enough to act.

    VRP z is (implied − realised) / realised, so both edges of the stand-down band
    are straight lines through the origin. Two points each are enough to draw them.
    """
    if not rows:
        return []
    span = max(
        max(float(row["realized_vol"]), float(row["implied_vol"])) for row in rows
    ) * 1.15
    return [
        {"realized_vol": 0.0, "lower": 0.0, "upper": 0.0, "fair": 0.0},
        {
            "realized_vol": span,
            "lower": span * (1.0 - vrp_z_entry),
            "upper": span * (1.0 + vrp_z_entry),
            "fair": span,
        },
    ]


# --- scanner -----------------------------------------------------------------


def wedge_points(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The same candidates in long form: two probabilities per structure.

    Drawn as a dumbbell, the distance between the pair *is* the wedge, which is the
    one number that authorises a trade.
    """
    points: list[dict[str, Any]] = []
    for row in rows:
        points.append(
            {"label": row["label"], "kind": "Model", "probability": float(row["p_win_model"])}
        )
        points.append(
            {"label": row["label"], "kind": "Market", "probability": float(row["p_win_implied"])}
        )
    return points


def wedge_rows(scan: Mapping[str, Any] | None, *, limit: int = 10) -> list[dict[str, Any]]:
    """Model win probability against the market's own, per ranked candidate."""
    rows: list[dict[str, Any]] = []
    for row in ((scan or {}).get("top") or [])[:limit]:
        underlying = str(row.get("underlying", "?"))
        strikes = [f"{float(s):g}" for s in (row.get("strikes") or [])]
        label = f"{underlying} {'/'.join(strikes)}" if strikes else underlying
        rows.append(
            {
                "label": label,
                "underlying": underlying,
                "structure": str(row.get("structure", "")).replace("_", " "),
                "p_win_model": float(row.get("p_win_model") or 0.0),
                "p_win_implied": float(row.get("p_win_implied") or 0.0),
                "wedge": float(row.get("wedge") or 0.0),
                "expected_value_usd": float(row.get("expected_value_usd") or 0.0),
                "max_loss_usd": float(row.get("max_loss_usd") or 0.0),
                "accepted": bool(row.get("accepted")),
                "rejects": list(row.get("rejects") or []),
            }
        )
    return rows


# --- risk --------------------------------------------------------------------


def portfolio_scalars(portfolio: Any) -> dict[str, Any]:
    """The book's headline numbers, from a `PortfolioRisk` or its journal digest."""
    if isinstance(portfolio, Mapping):
        return {
            "equity": float(portfolio.get("equity") or 0.0),
            "worst_case_usd": float(portfolio.get("worst_case_loss_usd") or 0.0),
            "stress_usd": float(portfolio.get("worst_stress_loss_usd") or 0.0),
            "net_delta_usd": float(portfolio.get("beta_weighted_delta_usd") or 0.0),
            "net_theta": float(portfolio.get("net_theta") or 0.0),
            "net_vega": float(portfolio.get("net_vega") or 0.0),
            "unrealized_pl_usd": float(portfolio.get("unrealized_pl_usd") or 0.0),
            "stress": dict(portfolio.get("stress") or {}),
            "by_bucket": dict(portfolio.get("risk_by_bucket") or {}),
        }
    return {
        "equity": float(portfolio.equity),
        "worst_case_usd": float(portfolio.total_worst_case_loss_usd),
        "stress_usd": float(portfolio.worst_stress_loss_usd),
        "net_delta_usd": float(portfolio.beta_weighted_delta_usd),
        "net_theta": float(portfolio.net_theta),
        "net_vega": float(portfolio.net_vega),
        "unrealized_pl_usd": float(portfolio.unrealized_pl_usd),
        "stress": dict(portfolio.stress),
        "by_bucket": dict(portfolio.exposure.by_bucket),
    }


def budget_rows(settings: Settings, scalars: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every portfolio budget as used, limit and the fraction of it spent."""
    equity = float(scalars.get("equity") or 0.0)
    definitions = (
        ("Aggregate worst case", scalars.get("worst_case_usd"), settings.risk_budget_pct),
        ("Two-sigma stress loss", scalars.get("stress_usd"), settings.max_stress_loss_pct),
        (
            "Beta-weighted delta",
            abs(float(scalars.get("net_delta_usd") or 0.0)),
            settings.max_net_delta_pct,
        ),
    )
    rows: list[dict[str, Any]] = []
    for name, used_raw, pct in definitions:
        used = float(used_raw or 0.0)
        limit = float(pct) * equity
        rows.append(
            {
                "budget": name,
                "used_usd": used,
                "limit_usd": limit,
                "limit_pct": float(pct),
                "utilisation": min(max(used / limit, 0.0), 1.0) if limit > 0 else 0.0,
                "headroom_usd": max(limit - used, 0.0),
            }
        )
    return rows


def payoff_rows(portfolio: Any) -> list[dict[str, Any]]:
    """The whole book's payoff against one common market shock."""
    return [{"shock": shock, "pnl": pnl} for shock, pnl in beta_mapped_curve(portfolio)]


def worst_case_point(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The dip in the payoff curve: the most this book can lose, and where."""
    if not rows:
        return None
    point = min(rows, key=lambda row: float(row["pnl"]))
    return {"shock": float(point["shock"]), "pnl": float(point["pnl"])}


def _sigma_of(label: str) -> float:
    try:
        return float(str(label).replace("sigma", ""))
    except ValueError:
        return 0.0


def stress_rows(stress: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Stress scenarios ordered by shock size rather than alphabetically."""
    rows = [
        {"scenario": str(label), "pnl": float(pnl), "sigma": _sigma_of(str(label))}
        for label, pnl in (stress or {}).items()
    ]
    return sorted(rows, key=lambda row: row["sigma"])


def bucket_rows(by_bucket: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Worst-case risk per concentration bucket, heaviest first."""
    rows = [
        {"bucket": str(bucket), "worst_case_usd": float(value)}
        for bucket, value in (by_bucket or {}).items()
    ]
    return sorted(rows, key=lambda row: row["worst_case_usd"], reverse=True)


# --- journal -----------------------------------------------------------------


def equity_rows(history: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Alpaca's portfolio history as timestamp/equity rows."""
    rows: list[dict[str, Any]] = []
    for point in history or []:
        try:
            when, equity = point
        except (TypeError, ValueError):
            continue
        rows.append({"timestamp": when, "equity": float(equity)})
    return rows


def journal_timeline(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per journalled cycle: when, what was decided, and the equity then."""
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        proposal = entry.get("proposal") or {}
        execution = entry.get("execution") or {}
        equity = entry.get("equity")
        rows.append(
            {
                "cycle": index,
                "ts": str(entry.get("ts", "")),
                "equity": float(equity) if isinstance(equity, int | float) else None,
                "action": str(proposal.get("action") or "hold"),
                "submitted": bool(execution.get("submitted")),
                "rationale": str(proposal.get("rationale") or ""),
            }
        )
    return rows


def latest_block(entries: Sequence[Mapping[str, Any]], key: str) -> Any | None:
    """The most recent non-empty `key` block in the journal, or None."""
    for entry in reversed(list(entries)):
        block = entry.get(key)
        if block:
            return block
    return None
