"""Managing what is already open, which is where most of the money is actually made.

Opening a good structure is one decision. A structure left alone makes that single
decision stand for its whole life, and short-dated options change character fast: the
same spread that was a 78% winner on Monday can be a coin flip on Wednesday. So every
cycle walks a fixed ladder over the open book, most urgent first, and takes at most
one action. One action per cycle keeps the journal readable and makes it impossible
for a bug to unwind the book in a single pass.

The ladder, in order:

1. loss beyond the stop -> close
2. expiry with the short strike in play -> close (assignment guard)
3. profit target reached -> close and free the collateral
4. last day, not safely out of the money -> close rather than gamble on the pin
5. nothing to do -> report which checks ran, so a quiet cycle is still auditable

Exits are always their own ticket with every leg marked `*_to_close`. Alpaca only
accepts a multi-leg order when all its legs are covered inside that same order, which
rules out the classic "roll in one ticket"; closing and re-opening separately is not
a workaround but the supported path.
"""

from __future__ import annotations

import math
from datetime import date, datetime

from pydantic import BaseModel, Field

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER, OptionCandidate
from vrp_engine.config import Settings
from vrp_engine.risk.portfolio import OptionHolding
from vrp_engine.strategy.base import (
    ACTION_CLOSE,
    ProposedLeg,
    ProposedTrade,
    TradeAnalytics,
)
from vrp_engine.strategy.structures import (
    CALL_CREDIT_SPREAD,
    CALL_DEBIT_SPREAD,
    IRON_CONDOR,
    PUT_CREDIT_SPREAD,
    PUT_DEBIT_SPREAD,
)

TRADING_DAYS = 252
# "Safely out of the money" on the last day means this many standard deviations of
# room between the spot and the nearest short strike.
SAFE_SIGMA_DISTANCE = 2.0


class OpenStructure(BaseModel):
    """The legs the engine holds on one underlying and expiry, treated as one unit.

    Grouping by (underlying, expiry) is exact because the engine refuses to open a
    second structure on a pair it already holds. That rule is what lets the manager
    reason about a whole spread instead of orphaned legs.
    """

    underlying: str
    expiration: date
    kind: str
    legs: list[OptionHolding] = Field(default_factory=list)
    contracts: int = 0
    net_premium_usd: float = 0.0
    unrealized_pl_usd: float = 0.0

    @property
    def is_credit(self) -> bool:
        return self.net_premium_usd > 0

    @property
    def premium_at_risk_usd(self) -> float:
        """The credit collected, or the debit paid: the base for every ratio below."""
        return abs(self.net_premium_usd)

    @property
    def capture_fraction(self) -> float:
        """Share of the original premium already earned. Negative means losing."""
        base = self.premium_at_risk_usd
        if base <= 0:
            return 0.0
        return self.unrealized_pl_usd / base

    @property
    def short_legs(self) -> list[OptionHolding]:
        return [leg for leg in self.legs if leg.contracts < 0]

    def dte(self, today: date) -> int:
        return (self.expiration - today).days

    def describe(self) -> str:
        strikes = "/".join(f"{leg.strike:g}" for leg in sorted(self.legs, key=lambda x: x.strike))
        return f"{self.underlying} {self.kind} {strikes} exp {self.expiration.isoformat()}"


def infer_kind(legs: list[OptionHolding], *, net_premium: float) -> str:
    """Name the shape from its legs, for the journal and the dashboard."""
    calls = [leg for leg in legs if leg.option_type == "call"]
    puts = [leg for leg in legs if leg.option_type == "put"]
    credit = net_premium > 0
    if len(calls) == 2 and len(puts) == 2 and credit:
        return IRON_CONDOR
    if len(legs) == 2 and len(puts) == 2:
        return PUT_CREDIT_SPREAD if credit else PUT_DEBIT_SPREAD
    if len(legs) == 2 and len(calls) == 2:
        return CALL_CREDIT_SPREAD if credit else CALL_DEBIT_SPREAD
    return "custom"


def group_open_structures(options: list[OptionHolding]) -> list[OpenStructure]:
    """Collapse individual option positions into managed units."""
    buckets: dict[tuple[str, date], list[OptionHolding]] = {}
    for holding in options:
        if holding.contracts == 0:
            continue
        buckets.setdefault((holding.underlying, holding.expiration), []).append(holding)

    structures: list[OpenStructure] = []
    for (underlying, expiration), legs in sorted(buckets.items()):
        net_premium = sum(leg.premium_paid_or_received() for leg in legs)
        # The closable size is the thinnest leg: closing more would open a new short.
        contracts = int(min(abs(leg.contracts) for leg in legs))
        structures.append(
            OpenStructure(
                underlying=underlying,
                expiration=expiration,
                kind=infer_kind(legs, net_premium=net_premium),
                legs=legs,
                contracts=contracts,
                net_premium_usd=net_premium,
                unrealized_pl_usd=sum(leg.unrealized_pl for leg in legs),
            )
        )
    return structures


def _mark(leg: OptionHolding, quotes: dict[str, OptionCandidate]) -> float:
    """Best available price for a leg: the chain mid, else the broker's own mark."""
    quote = quotes.get(leg.symbol.upper())
    if quote is not None and quote.mid_price is not None and quote.mid_price > 0:
        return float(quote.mid_price)
    return float(leg.current_price)


def exit_limit_price(
    structure: OpenStructure,
    quotes: dict[str, OptionCandidate],
) -> float | None:
    """Net price to close one contract of the structure, as a positive number.

    Closing a short costs money and closing a long brings it in, so the sign follows
    the direction we hold rather than the direction of the order.
    """
    net = 0.0
    for leg in structure.legs:
        price = _mark(leg, quotes)
        if price <= 0:
            return None
        net += price if leg.contracts > 0 else -price
    return round(abs(net), 2)


def build_exit(
    structure: OpenStructure,
    *,
    quotes: dict[str, OptionCandidate],
    reason: str,
    today: date,
) -> ProposedTrade:
    """An all-legs-to-close ticket for the whole structure.

    No liquidity gate here, unlike entries. A position that has gone wide is exactly
    the one worth closing, and refusing to quote it would trap the book.
    """
    legs = [
        ProposedLeg(
            symbol=leg.symbol,
            # Reverse whatever we hold: short legs are bought back, long legs sold.
            side="buy" if leg.contracts < 0 else "sell",
            ratio_qty=1,
            position_intent="buy_to_close" if leg.contracts < 0 else "sell_to_close",
        )
        for leg in structure.legs
    ]
    return ProposedTrade(
        qty=structure.contracts,
        legs=legs,
        action=ACTION_CLOSE,
        kind="option",
        rationale=f"Close {structure.describe()}: {reason}",
        limit_price=exit_limit_price(structure, quotes),
        analytics=TradeAnalytics(
            structure_kind=structure.kind,
            underlying=structure.underlying,
            expiration=structure.expiration,
            dte=structure.dte(today),
            credit_usd=structure.net_premium_usd,
        ),
    )


def _safely_out_of_the_money(
    structure: OpenStructure,
    *,
    spot: float,
    sigma: float | None,
    today: date,
) -> bool:
    """Is every short strike far enough away to let the position simply expire?"""
    shorts = structure.short_legs
    if not shorts or spot <= 0 or not sigma or sigma <= 0:
        return False
    days = max(structure.dte(today), 0)
    horizon = math.sqrt(max(days, 1) / TRADING_DAYS)
    room = SAFE_SIGMA_DISTANCE * sigma * horizon * spot
    return all(abs(leg.strike - spot) > room for leg in shorts)


def _short_strike_in_play(
    structure: OpenStructure,
    *,
    spot: float,
    quotes: dict[str, OptionCandidate],
    proximity_pct: float,
    assignment_delta: float,
) -> str | None:
    """Is a short leg close enough to the money to risk assignment?"""
    for leg in structure.short_legs:
        if spot > 0 and abs(leg.strike - spot) / spot <= proximity_pct:
            return f"short {leg.strike:g} strike is pinned to a {spot:.2f} spot"
        quote = quotes.get(leg.symbol.upper())
        delta = quote.delta if quote is not None else leg.delta
        if delta is not None and abs(delta) >= assignment_delta:
            return f"short {leg.strike:g} delta reached {abs(delta):.2f}"
    return None


class ManagementDecision(BaseModel):
    """Either an exit to send, or the list of checks that found nothing to do."""

    trade: ProposedTrade | None = None
    checks: list[str] = Field(default_factory=list)


def next_management_action(
    structures: list[OpenStructure],
    *,
    settings: Settings,
    today: date,
    now: datetime,
    spots: dict[str, float],
    vols: dict[str, float],
    quotes: dict[str, OptionCandidate],
) -> ManagementDecision:
    """Walk the ladder over every open structure and return the first action due."""
    checks: list[str] = []
    if not structures:
        return ManagementDecision(checks=["no open structures to manage"])

    forced_exit_time = now.hour >= settings.forced_exit_hour_et

    # Losers first: a stop that fires a cycle late costs more than a profit taken late.
    for structure in sorted(structures, key=lambda s: s.capture_fraction):
        spot = spots.get(structure.underlying, 0.0)
        dte = structure.dte(today)
        capture = structure.capture_fraction

        if structure.is_credit and capture <= -settings.stop_loss_credit_multiple:
            return ManagementDecision(
                trade=build_exit(
                    structure,
                    quotes=quotes,
                    reason=(
                        f"loss reached {abs(capture):.1f}x the "
                        f"{structure.premium_at_risk_usd:.0f} USD credit, past the "
                        f"{settings.stop_loss_credit_multiple:.1f}x stop"
                    ),
                    today=today,
                ),
                checks=checks,
            )

        if dte <= 1:
            in_play = _short_strike_in_play(
                structure,
                spot=spot,
                quotes=quotes,
                proximity_pct=settings.assignment_proximity_pct,
                assignment_delta=settings.assignment_delta,
            )
            if in_play:
                return ManagementDecision(
                    trade=build_exit(
                        structure,
                        quotes=quotes,
                        reason=f"expiry in {dte}d and {in_play}",
                        today=today,
                    ),
                    checks=checks,
                )

        target = (
            settings.profit_take_condor_pct
            if structure.kind == IRON_CONDOR
            else settings.profit_take_credit_pct
            if structure.is_credit
            else settings.profit_take_debit_pct
        )
        if capture >= target:
            return ManagementDecision(
                trade=build_exit(
                    structure,
                    quotes=quotes,
                    reason=(
                        f"captured {capture:.0%} of the "
                        f"{structure.premium_at_risk_usd:.0f} USD premium, past the "
                        f"{target:.0%} target"
                    ),
                    today=today,
                ),
                checks=checks,
            )

        if dte <= 1 and forced_exit_time:
            if not _safely_out_of_the_money(
                structure, spot=spot, sigma=vols.get(structure.underlying), today=today
            ):
                return ManagementDecision(
                    trade=build_exit(
                        structure,
                        quotes=quotes,
                        reason=(
                            "last session and the short strikes are within two sigma; "
                            "not holding into the pin"
                        ),
                        today=today,
                    ),
                    checks=checks,
                )
            checks.append(
                f"{structure.describe()}: expiring but more than two sigma out of the money"
            )
            continue

        checks.append(
            f"{structure.describe()}: {capture:+.0%} of premium captured, {dte}d left, "
            "inside every exit threshold"
        )

    return ManagementDecision(checks=checks)


def flatten_next(
    structures: list[OpenStructure],
    *,
    quotes: dict[str, OptionCandidate],
    today: date,
    reason: str,
) -> ProposedTrade | None:
    """Unwind the book one structure at a time, the biggest loser first.

    Used when an account breaker demands a flatten: exits stay allowed precisely so a
    circuit breaker cannot trap the position it fired over.
    """
    if not structures:
        return None
    worst = min(structures, key=lambda s: s.unrealized_pl_usd)
    return build_exit(worst, quotes=quotes, reason=reason, today=today)


def total_open_contracts(options: list[OptionHolding]) -> dict[str, float]:
    """Signed contracts per symbol, which is what the risk layer verifies exits against."""
    totals: dict[str, float] = {}
    for holding in options:
        key = holding.symbol.upper()
        totals[key] = totals.get(key, 0.0) + holding.contracts
    return totals


def net_premium_to_usd(price: float, contracts: int) -> float:
    """Convenience for reporting: a per-share premium as a dollar amount."""
    return price * CONTRACT_MULTIPLIER * contracts
