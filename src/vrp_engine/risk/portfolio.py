r"""Portfolio payoff and stress, built from the positions the broker actually reports.

Intuition: a per-order cap cannot see a book. Ten separately reasonable spreads on
correlated names are one large bet. So the risk layer rebuilds the whole portfolio as
a payoff curve and asks the only question that matters: if the market moves, how much
can this book still lose?

Technical: every option is a piecewise-linear function of its underlying's terminal
price. Summing those functions per underlying gives an exact payoff curve, and because
a piecewise-linear function attains its minimum at a breakpoint, the worst case is
found by evaluating the strikes rather than by scanning a grid. This is the same
"universal spread rule" model Alpaca documents for its own margin calculation.

Positions on different underlyings are made comparable by mapping each one's shock
through its beta to a common market shock.

Math: with signed contract counts $q_i$ and strikes $K_i$,

$$V(S) = 100\sum_i q_i \max(\epsilon_i (S - K_i), 0),\qquad
\text{P\&L}(S) = V(S) - V_{\text{now}}$$

where $\epsilon_i = +1$ for calls and $-1$ for puts, and the worst case is
$\min_{S \in \{0\} \cup \{K_i\} \cup \{S_\infty\}} \text{P\&L}(S)$.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER, OptionCandidate, parse_occ_symbol

# The contest is scored over a week, so shocks are sized to a one-week move.
STRESS_HORIZON_DAYS = 5
TRADING_DAYS = 252
STRESS_SIGMAS = (-2.0, -1.0, 1.0, 2.0)

# Points used only for the chart; the worst case is computed exactly, not sampled.
CURVE_POINTS = 81
CURVE_SPAN = 0.20


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Exposure(BaseModel):
    """Defined risk already committed, sliced the same way the budgets are.

    Lives here rather than next to the sizing maths because the broker's positions
    are the source of truth for it, and because sizing must depend on risk, never
    the other way around.
    """

    total_usd: float = 0.0
    by_underlying: dict[str, float] = Field(default_factory=dict)
    by_bucket: dict[str, float] = Field(default_factory=dict)

    def underlying(self, symbol: str) -> float:
        return self.by_underlying.get(symbol.upper(), 0.0)

    def bucket(self, name: str) -> float:
        return self.by_bucket.get(name, 0.0)


class OptionHolding(BaseModel):
    """One open option position, signed: negative contracts mean short."""

    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiration: date
    contracts: float
    market_value: float = 0.0
    avg_entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pl: float = 0.0
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    @property
    def is_short(self) -> bool:
        return self.contracts < 0

    @property
    def sign(self) -> float:
        return 1.0 if self.option_type == "call" else -1.0

    def intrinsic_value(self, terminal: float) -> float:
        """Dollar value of this position if the underlying expires at `terminal`."""
        payoff = max(self.sign * (terminal - self.strike), 0.0)
        return self.contracts * CONTRACT_MULTIPLIER * payoff

    def dte(self, today: date) -> int:
        return (self.expiration - today).days

    def premium_paid_or_received(self) -> float:
        """Signed opening cash flow: negative when we paid, positive when we collected."""
        return -self.contracts * CONTRACT_MULTIPLIER * self.avg_entry_price


class ShareHolding(BaseModel):
    """A plain share position. The engine does not open these, but it may inherit them."""

    symbol: str
    shares: float
    market_value: float = 0.0
    current_price: float = 0.0
    unrealized_pl: float = 0.0

    def intrinsic_value(self, terminal: float) -> float:
        return self.shares * terminal


def holdings_from_positions(
    positions: list[Any],
    *,
    greeks: dict[str, OptionCandidate] | None = None,
) -> tuple[list[OptionHolding], list[ShareHolding]]:
    """Split Alpaca positions into options and shares, enriching greeks when available.

    Positions do not carry greeks, so the chain snapshot the engine already fetched is
    joined in by symbol. A position without greeks still contributes to the payoff
    curve; it only drops out of the delta and vega aggregates.
    """
    lookup = greeks or {}
    options: list[OptionHolding] = []
    shares: list[ShareHolding] = []

    for position in positions:
        symbol = str(getattr(position, "symbol", "") or "").upper()
        if not symbol:
            continue
        qty = _as_float(getattr(position, "qty", None))
        parsed = parse_occ_symbol(symbol)
        if parsed is None:
            shares.append(
                ShareHolding(
                    symbol=symbol,
                    shares=qty,
                    market_value=_as_float(getattr(position, "market_value", None)),
                    current_price=_as_float(getattr(position, "current_price", None)),
                    unrealized_pl=_as_float(getattr(position, "unrealized_pl", None)),
                )
            )
            continue

        quote = lookup.get(symbol)
        options.append(
            OptionHolding(
                symbol=symbol,
                underlying=parsed.underlying,
                option_type=parsed.option_type,
                strike=parsed.strike,
                expiration=parsed.expiration,
                contracts=qty,
                market_value=_as_float(getattr(position, "market_value", None)),
                avg_entry_price=_as_float(getattr(position, "avg_entry_price", None)),
                current_price=_as_float(getattr(position, "current_price", None)),
                unrealized_pl=_as_float(getattr(position, "unrealized_pl", None)),
                delta=quote.delta if quote else None,
                gamma=quote.gamma if quote else None,
                theta=quote.theta if quote else None,
                vega=quote.vega if quote else None,
            )
        )
    return options, shares


def expiry_pnl(
    terminal: float,
    *,
    options: list[OptionHolding],
    shares: list[ShareHolding],
) -> float:
    """Change in account value between now and expiry if the underlying ends at `terminal`.

    Netting off today's mark is what makes this honest: premium already collected is
    money in hand, and pretending otherwise would double-count it as risk.
    """
    future = sum(o.intrinsic_value(terminal) for o in options)
    future += sum(s.intrinsic_value(terminal) for s in shares)
    now = sum(o.market_value for o in options) + sum(s.market_value for s in shares)
    return future - now


def breakpoints(options: list[OptionHolding], *, spot: float) -> list[float]:
    """Prices where the payoff can bend, plus the two extremes.

    A piecewise-linear function has no interior minimum away from a kink, so these
    points are enough to find the true worst case exactly.
    """
    strikes = sorted({o.strike for o in options})
    highest = max([*strikes, spot]) if (strikes or spot) else 1.0
    # Far enough out that the terminal slope has taken over from every kink.
    far = highest * 3.0 + 100.0
    points = [0.0, *strikes, far]
    if spot > 0:
        points.append(spot)
    return sorted(set(points))


class UnderlyingExposure(BaseModel):
    """The whole book on one underlying, reduced to risk numbers."""

    symbol: str
    spot: float
    beta: float = 1.0
    realized_vol: float | None = None
    n_option_positions: int = 0
    share_count: float = 0.0
    option_holdings: list[OptionHolding] = Field(default_factory=list)
    share_holdings: list[ShareHolding] = Field(default_factory=list)
    worst_case_loss_usd: float = 0.0
    worst_case_price: float = 0.0
    net_delta_usd: float = 0.0
    net_vega: float = 0.0
    net_theta: float = 0.0
    unrealized_pl_usd: float = 0.0
    stress: dict[str, float] = Field(default_factory=dict)
    curve: list[tuple[float, float]] = Field(default_factory=list)


def _stress_prices(spot: float, sigma: float | None) -> dict[str, float]:
    if spot <= 0 or not sigma or sigma <= 0:
        return {}
    horizon = math.sqrt(STRESS_HORIZON_DAYS / TRADING_DAYS)
    return {
        f"{shock:+.0f}sigma": spot * math.exp(shock * sigma * horizon)
        for shock in STRESS_SIGMAS
    }


def build_underlying_exposure(
    symbol: str,
    *,
    options: list[OptionHolding],
    shares: list[ShareHolding],
    spot: float,
    beta: float = 1.0,
    realized_vol: float | None = None,
) -> UnderlyingExposure:
    """Payoff curve, exact worst case and greeks for one underlying."""
    share_total = sum(s.shares for s in shares)

    worst_price = spot
    worst_loss = 0.0
    if options or shares:
        candidates = breakpoints(options, spot=spot)
        results = [
            (price, expiry_pnl(price, options=options, shares=shares))
            for price in candidates
        ]
        worst_price, worst_pnl = min(results, key=lambda pair: pair[1])
        worst_loss = max(-worst_pnl, 0.0)

    net_delta_usd = 0.0
    for option in options:
        if option.delta is None:
            continue
        net_delta_usd += option.delta * option.contracts * CONTRACT_MULTIPLIER * spot
    net_delta_usd += share_total * spot

    curve: list[tuple[float, float]] = []
    if spot > 0 and (options or shares):
        low = spot * (1 - CURVE_SPAN)
        high = spot * (1 + CURVE_SPAN)
        step = (high - low) / (CURVE_POINTS - 1)
        for i in range(CURVE_POINTS):
            price = low + i * step
            curve.append((price, expiry_pnl(price, options=options, shares=shares)))

    stress = {
        label: expiry_pnl(price, options=options, shares=shares)
        for label, price in _stress_prices(spot, realized_vol).items()
    }

    return UnderlyingExposure(
        symbol=symbol.upper(),
        spot=spot,
        beta=beta,
        realized_vol=realized_vol,
        n_option_positions=len(options),
        share_count=share_total,
        option_holdings=options,
        share_holdings=shares,
        worst_case_loss_usd=worst_loss,
        worst_case_price=worst_price,
        net_delta_usd=net_delta_usd,
        net_vega=sum(
            (o.vega or 0.0) * o.contracts * CONTRACT_MULTIPLIER for o in options
        ),
        net_theta=sum(
            (o.theta or 0.0) * o.contracts * CONTRACT_MULTIPLIER for o in options
        ),
        unrealized_pl_usd=sum(o.unrealized_pl for o in options)
        + sum(s.unrealized_pl for s in shares),
        stress=stress,
        curve=curve,
    )


class PortfolioRisk(BaseModel):
    """The book's risk, aggregated across underlyings."""

    equity: float
    underlyings: list[UnderlyingExposure] = Field(default_factory=list)
    total_worst_case_loss_usd: float = 0.0
    stress: dict[str, float] = Field(default_factory=dict)
    beta_weighted_delta_usd: float = 0.0
    net_vega: float = 0.0
    net_theta: float = 0.0
    unrealized_pl_usd: float = 0.0
    exposure: Exposure = Field(default_factory=Exposure)

    @property
    def worst_case_pct(self) -> float:
        return self.total_worst_case_loss_usd / self.equity if self.equity > 0 else 0.0

    @property
    def worst_stress_loss_usd(self) -> float:
        """Largest loss across the stress scenarios; zero when every scenario gains."""
        if not self.stress:
            return 0.0
        return max(-min(self.stress.values()), 0.0)

    @property
    def stress_loss_pct(self) -> float:
        return self.worst_stress_loss_usd / self.equity if self.equity > 0 else 0.0

    @property
    def net_delta_pct(self) -> float:
        return self.beta_weighted_delta_usd / self.equity if self.equity > 0 else 0.0

    def summary(self) -> str:
        return (
            f"worst case {self.total_worst_case_loss_usd:.0f} USD "
            f"({self.worst_case_pct:.1%}), stress {self.worst_stress_loss_usd:.0f} USD "
            f"({self.stress_loss_pct:.1%}), beta delta {self.net_delta_pct:+.1%}, "
            f"theta {self.net_theta:+.0f}/day"
        )

    def digest(self) -> dict[str, Any]:
        """Scalars only. The payoff curves are for the dashboard, not the journal."""
        return {
            "equity": self.equity,
            "worst_case_loss_usd": round(self.total_worst_case_loss_usd, 2),
            "worst_case_pct": round(self.worst_case_pct, 4),
            "stress": {k: round(v, 2) for k, v in self.stress.items()},
            "worst_stress_loss_usd": round(self.worst_stress_loss_usd, 2),
            "stress_loss_pct": round(self.stress_loss_pct, 4),
            "beta_weighted_delta_usd": round(self.beta_weighted_delta_usd, 2),
            "net_delta_pct": round(self.net_delta_pct, 4),
            "net_vega": round(self.net_vega, 2),
            "net_theta": round(self.net_theta, 2),
            "unrealized_pl_usd": round(self.unrealized_pl_usd, 2),
            "risk_by_underlying": {
                k: round(v, 2) for k, v in self.exposure.by_underlying.items()
            },
            "risk_by_bucket": {k: round(v, 2) for k, v in self.exposure.by_bucket.items()},
        }


def beta_mapped_curve(
    portfolio: PortfolioRisk,
    *,
    span: float = 0.15,
    points: int = 61,
) -> list[tuple[float, float]]:
    """The whole book's payoff against one common market shock.

    Fourteen separate payoff curves cannot be read at a glance, and adding them
    directly would pretend a 1% move in SPY and a 1% move in a high-beta single name
    are the same event. Each underlying is shocked by `beta * x` instead, which is the
    same mapping the delta budget uses, so the aggregate curve is internally consistent.
    """
    curve: list[tuple[float, float]] = []
    step = (2 * span) / (points - 1)
    for i in range(points):
        shock = -span + i * step
        total = 0.0
        for exposure in portfolio.underlyings:
            if exposure.spot <= 0:
                continue
            shocked = exposure.spot * (1.0 + exposure.beta * shock)
            total += expiry_pnl(
                max(shocked, 0.0),
                options=exposure.option_holdings,
                shares=exposure.share_holdings,
            )
        curve.append((shock, total))
    return curve


def build_portfolio_risk(
    *,
    equity: float,
    positions: list[Any],
    spots: dict[str, float],
    betas: dict[str, float] | None = None,
    vols: dict[str, float] | None = None,
    greeks: dict[str, OptionCandidate] | None = None,
    bucket_of: Any = None,
) -> PortfolioRisk:
    """Aggregate every position into one risk picture.

    `bucket_of` maps a symbol to its concentration bucket, so correlated index ETFs
    share a single budget instead of quietly getting one each.
    """
    options, shares = holdings_from_positions(positions, greeks=greeks)
    beta_map = betas or {}
    vol_map = vols or {}
    to_bucket = bucket_of or (lambda symbol: symbol.upper())

    symbols = {o.underlying for o in options} | {s.symbol for s in shares}
    exposures: list[UnderlyingExposure] = []
    for symbol in sorted(symbols):
        exposures.append(
            build_underlying_exposure(
                symbol,
                options=[o for o in options if o.underlying == symbol],
                shares=[s for s in shares if s.symbol == symbol],
                spot=spots.get(symbol, 0.0),
                beta=beta_map.get(symbol, 1.0),
                realized_vol=vol_map.get(symbol),
            )
        )

    by_underlying = {e.symbol: e.worst_case_loss_usd for e in exposures}
    by_bucket: dict[str, float] = {}
    for exposure in exposures:
        bucket = to_bucket(exposure.symbol)
        by_bucket[bucket] = by_bucket.get(bucket, 0.0) + exposure.worst_case_loss_usd

    # A common market shock, so correlated names add up instead of cancelling by luck.
    aggregate_stress: dict[str, float] = {}
    for exposure in exposures:
        for label, value in exposure.stress.items():
            aggregate_stress[label] = aggregate_stress.get(label, 0.0) + value

    return PortfolioRisk(
        equity=equity,
        underlyings=exposures,
        total_worst_case_loss_usd=sum(by_underlying.values()),
        stress=aggregate_stress,
        beta_weighted_delta_usd=sum(e.net_delta_usd * e.beta for e in exposures),
        net_vega=sum(e.net_vega for e in exposures),
        net_theta=sum(e.net_theta for e in exposures),
        unrealized_pl_usd=sum(e.unrealized_pl_usd for e in exposures),
        exposure=Exposure(
            total_usd=sum(by_underlying.values()),
            by_underlying=by_underlying,
            by_bucket=by_bucket,
        ),
    )


def prospective_holdings(
    *,
    symbols_sides: list[tuple[OptionCandidate, str]],
    contracts: int,
) -> list[OptionHolding]:
    """Turn a proposed structure into holdings, so risk can price the book as if filled.

    The mark is set to the current mid, which is exactly what a fill at the net mid
    would produce: the pre-trade and post-trade curves then differ only by the risk
    the new legs add.
    """
    holdings: list[OptionHolding] = []
    for candidate, side in symbols_sides:
        signed = contracts if side == "buy" else -contracts
        mid = candidate.mid_price or 0.0
        holdings.append(
            OptionHolding(
                symbol=candidate.symbol,
                underlying=candidate.underlying,
                option_type=candidate.option_type,
                strike=candidate.strike,
                expiration=candidate.expiration,
                contracts=float(signed),
                market_value=signed * CONTRACT_MULTIPLIER * mid,
                avg_entry_price=mid,
                current_price=mid,
                delta=candidate.delta,
                gamma=candidate.gamma,
                theta=candidate.theta,
                vega=candidate.vega,
            )
        )
    return holdings
