r"""Defined-risk option structures, built from a quoted chain.

Every structure here is bounded on both sides by construction: each short leg is
paid for by a long leg of the same type and expiry inside the same ticket. That is
not a stylistic choice, it is what makes the risk layer's arithmetic possible and
what keeps Alpaca's multi-leg "all legs covered" rule satisfied.

The stance from `signals` decides whether the engine sells or buys premium; the trend
decides the shape:

- sell premium, flat tape  -> iron condor
- sell premium, up tape    -> put credit spread
- sell premium, down tape  -> call credit spread
- buy premium, up tape     -> call debit spread
- buy premium, down tape   -> put debit spread

Rather than committing to one strike distance, each shape is emitted at several
widths so the expected-value layer can pick the one that actually pays best.

Math: for a vertical of width $w$ opened for a net credit $c$ (per share),

$$\text{max profit} = 100c,\qquad \text{max loss} = 100(w - c)$$

and the credit is only economic while $0 < c < w$.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER, OptionCandidate
from vrp_engine.strategy.signals import (
    STANCE_BUY_VOL,
    STANCE_SELL_VOL,
    TREND_DOWN,
    TREND_FLAT,
    TREND_UP,
    UnderlyingSignal,
)

PUT_CREDIT_SPREAD = "put_credit_spread"
CALL_CREDIT_SPREAD = "call_credit_spread"
IRON_CONDOR = "iron_condor"
CALL_DEBIT_SPREAD = "call_debit_spread"
PUT_DEBIT_SPREAD = "put_debit_spread"

CREDIT_STRUCTURES = frozenset({PUT_CREDIT_SPREAD, CALL_CREDIT_SPREAD, IRON_CONDOR})

# How much of the distance between the mid and the unfavourable side of the quote we
# assume to give away on the fill. Half is a realistic expectation for a limit order
# resting at the net mid, and pricing the edge off the mid alone flatters every trade.
SLIPPAGE_SHARE = 0.5

# Enough widths to find the good one, few enough to keep a cycle quick.
DEFAULT_WIDTH_VARIANTS = 4


class SelectionParams(BaseModel):
    """Strike-selection and liquidity knobs, lifted straight from Settings."""

    target_short_delta: float = 0.22
    target_condor_delta: float = 0.18
    debit_long_delta: float = 0.45
    debit_short_delta: float = 0.25
    max_spread_fraction: float = 0.08
    min_open_interest: int = 0
    width_variants: int = DEFAULT_WIDTH_VARIANTS


class StructureLeg(BaseModel):
    """One leg, with the side we intend to take."""

    contract: OptionCandidate
    side: Literal["buy", "sell"]

    @property
    def open_intent(self) -> str:
        return "buy_to_open" if self.side == "buy" else "sell_to_open"

    @property
    def close_intent(self) -> str:
        # Closing reverses the side: a leg we are short is bought back.
        return "sell_to_close" if self.side == "buy" else "buy_to_close"

    @property
    def signed_mid(self) -> float | None:
        """Cash flow at the mid, positive when the leg brings money in."""
        mid = self.contract.mid_price
        if mid is None:
            return None
        return mid if self.side == "sell" else -mid

    @property
    def signed_worst(self) -> float | None:
        """Cash flow at the unfavourable side of the quote: sell the bid, buy the ask."""
        if self.contract.bid is None or self.contract.ask is None:
            return None
        return self.contract.bid if self.side == "sell" else -self.contract.ask


class Structure(BaseModel):
    """A complete defined-risk position, priced per contract."""

    kind: str
    underlying: str
    expiration: date
    legs: list[StructureLeg] = Field(default_factory=list)
    # Signed premium per share: positive is a credit received, negative a debit paid.
    net_price_mid: float
    net_price_worst: float
    width: float
    notes: list[str] = Field(default_factory=list)

    @property
    def is_credit(self) -> bool:
        return self.kind in CREDIT_STRUCTURES

    @property
    def effective_price(self) -> float:
        """Net premium after assuming we give away part of the spread on the fill."""
        return self.net_price_mid - SLIPPAGE_SHARE * (self.net_price_mid - self.net_price_worst)

    @property
    def credit_usd(self) -> float:
        """Premium collected per contract. Zero for debit structures."""
        if not self.is_credit:
            return 0.0
        return max(self.effective_price, 0.0) * CONTRACT_MULTIPLIER

    @property
    def debit_usd(self) -> float:
        """Premium paid per contract. Zero for credit structures."""
        if self.is_credit:
            return 0.0
        return max(-self.effective_price, 0.0) * CONTRACT_MULTIPLIER

    @property
    def max_loss_usd(self) -> float:
        """Worst case per contract, which is also the collateral Alpaca will hold."""
        if self.is_credit:
            return max(self.width * CONTRACT_MULTIPLIER - self.credit_usd, 0.0)
        return self.debit_usd

    @property
    def max_profit_usd(self) -> float:
        if self.is_credit:
            return self.credit_usd
        return max(self.width * CONTRACT_MULTIPLIER - self.debit_usd, 0.0)

    @property
    def short_legs(self) -> list[StructureLeg]:
        return [leg for leg in self.legs if leg.side == "sell"]

    @property
    def limit_price(self) -> float:
        """Net limit at the mid, as a positive number, rounded to a penny.

        Alpaca takes the absolute net price for a multi-leg ticket; the leg sides
        already encode whether we are paying or receiving.
        """
        return round(abs(self.net_price_mid), 2)

    @property
    def symbols(self) -> list[str]:
        return [leg.contract.symbol for leg in self.legs]

    def breakevens(self) -> list[float]:
        """Underlying prices where the structure turns from profit to loss."""
        credit = self.effective_price
        points: list[float] = []
        for leg in self.short_legs:
            if leg.contract.is_call:
                points.append(leg.contract.strike + abs(credit))
            else:
                points.append(leg.contract.strike - abs(credit))
        if not self.is_credit:
            # A debit spread breaks even at the long strike plus what we paid.
            longs = [leg for leg in self.legs if leg.side == "buy"]
            points = []
            for leg in longs:
                if leg.contract.is_call:
                    points.append(leg.contract.strike + abs(credit))
                else:
                    points.append(leg.contract.strike - abs(credit))
        return sorted(points)

    def describe(self) -> str:
        strikes = "/".join(f"{leg.contract.strike:g}" for leg in self.legs)
        return f"{self.underlying} {self.kind} {strikes} exp {self.expiration.isoformat()}"


def _usable(
    candidates: list[OptionCandidate],
    *,
    option_type: str,
    expiration: date,
    params: SelectionParams,
) -> list[OptionCandidate]:
    """Contracts of one type and expiry that we could actually trade."""
    return [
        c
        for c in candidates
        if c.option_type == option_type
        and c.expiration == expiration
        and c.delta is not None
        and c.tradable(
            max_spread_fraction=params.max_spread_fraction,
            min_open_interest=params.min_open_interest,
        )
    ]


def nearest_delta(
    pool: list[OptionCandidate],
    *,
    target: float,
) -> OptionCandidate | None:
    """Contract whose absolute delta sits closest to the target."""
    if not pool:
        return None
    return min(pool, key=lambda c: abs(abs(c.delta or 0.0) - target))


def _vertical(
    *,
    kind: str,
    short: OptionCandidate,
    long: OptionCandidate,
    underlying: str,
) -> Structure | None:
    """Assemble and sanity-check a two-leg vertical."""
    legs = [
        StructureLeg(contract=short, side="sell"),
        StructureLeg(contract=long, side="buy"),
    ]
    return _assemble(kind=kind, underlying=underlying, legs=legs, expiration=short.expiration)


def _assemble(
    *,
    kind: str,
    underlying: str,
    legs: list[StructureLeg],
    expiration: date,
) -> Structure | None:
    """Price a set of legs and reject anything economically impossible.

    A credit above the width, or a debit above it, means the quotes are crossed or
    stale. Trading on that arithmetic would book a phantom edge.
    """
    mids = [leg.signed_mid for leg in legs]
    worsts = [leg.signed_worst for leg in legs]
    if any(m is None for m in mids) or any(w is None for w in worsts):
        return None

    width = _structure_width(legs)
    if width <= 0:
        return None

    net_mid = sum(m for m in mids if m is not None)
    net_worst = sum(w for w in worsts if w is not None)
    structure = Structure(
        kind=kind,
        underlying=underlying,
        expiration=expiration,
        legs=legs,
        net_price_mid=net_mid,
        net_price_worst=net_worst,
        width=width,
    )

    if structure.is_credit:
        if structure.credit_usd <= 0:
            return None
        if structure.credit_usd >= width * CONTRACT_MULTIPLIER:
            return None
    else:
        if structure.debit_usd <= 0:
            return None
        if structure.debit_usd >= width * CONTRACT_MULTIPLIER:
            return None
    return structure


def _structure_width(legs: list[StructureLeg]) -> float:
    """Widest single-side strike distance.

    For a condor only one wing can finish in the money, so the risk is the wider of
    the two wings rather than their sum.
    """
    widths: list[float] = []
    for option_type in ("call", "put"):
        strikes = [
            leg.contract.strike for leg in legs if leg.contract.option_type == option_type
        ]
        if len(strikes) >= 2:
            widths.append(max(strikes) - min(strikes))
    return max(widths) if widths else 0.0


def _width_partners(
    pool: list[OptionCandidate],
    *,
    short: OptionCandidate,
    further_otm_is_higher: bool,
    limit: int,
) -> list[OptionCandidate]:
    """The nearest strikes beyond the short leg, which bound the risk."""
    if further_otm_is_higher:
        partners = [c for c in pool if c.strike > short.strike]
        partners.sort(key=lambda c: c.strike)
    else:
        partners = [c for c in pool if c.strike < short.strike]
        partners.sort(key=lambda c: -c.strike)
    return partners[:limit]


def credit_spread_variants(
    candidates: list[OptionCandidate],
    *,
    underlying: str,
    spot: float,
    expiration: date,
    option_type: str,
    target_delta: float,
    params: SelectionParams,
) -> list[Structure]:
    """Short leg near the target delta, long leg at several widths beyond it."""
    pool = _usable(
        candidates, option_type=option_type, expiration=expiration, params=params
    )
    # Only sell what is already out of the money: the premium should be time value,
    # not intrinsic value we are simply handing back.
    otm = [c for c in pool if (c.strike > spot if option_type == "call" else c.strike < spot)]
    short = nearest_delta(otm, target=target_delta)
    if short is None:
        return []

    kind = CALL_CREDIT_SPREAD if option_type == "call" else PUT_CREDIT_SPREAD
    structures: list[Structure] = []
    for long in _width_partners(
        pool,
        short=short,
        further_otm_is_higher=(option_type == "call"),
        limit=params.width_variants,
    ):
        built = _vertical(kind=kind, short=short, long=long, underlying=underlying)
        if built is not None:
            structures.append(built)
    return structures


def debit_spread_variants(
    candidates: list[OptionCandidate],
    *,
    underlying: str,
    spot: float,
    expiration: date,
    option_type: str,
    params: SelectionParams,
) -> list[Structure]:
    """Long leg near the money, short leg further out to cap the cost."""
    pool = _usable(
        candidates, option_type=option_type, expiration=expiration, params=params
    )
    long = nearest_delta(pool, target=params.debit_long_delta)
    if long is None:
        return []

    kind = CALL_DEBIT_SPREAD if option_type == "call" else PUT_DEBIT_SPREAD
    structures: list[Structure] = []
    for short in _width_partners(
        pool,
        short=long,
        further_otm_is_higher=(option_type == "call"),
        limit=params.width_variants,
    ):
        legs = [
            StructureLeg(contract=long, side="buy"),
            StructureLeg(contract=short, side="sell"),
        ]
        built = _assemble(
            kind=kind, underlying=underlying, legs=legs, expiration=expiration
        )
        if built is not None:
            structures.append(built)
    return structures


def iron_condor_variants(
    candidates: list[OptionCandidate],
    *,
    underlying: str,
    spot: float,
    expiration: date,
    params: SelectionParams,
) -> list[Structure]:
    """Both wings sold near the condor delta, with matched protective wings."""
    puts = credit_spread_variants(
        candidates,
        underlying=underlying,
        spot=spot,
        expiration=expiration,
        option_type="put",
        target_delta=params.target_condor_delta,
        params=params,
    )
    calls = credit_spread_variants(
        candidates,
        underlying=underlying,
        spot=spot,
        expiration=expiration,
        option_type="call",
        target_delta=params.target_condor_delta,
        params=params,
    )
    if not puts or not calls:
        return []

    structures: list[Structure] = []
    # Pair equal-rank wings so the two sides stay comparable in width; mismatched
    # wings turn a neutral condor into a lopsided directional bet.
    for put_side, call_side in zip(puts, calls, strict=False):
        legs = [*put_side.legs, *call_side.legs]
        if len(legs) != 4:
            continue
        built = _assemble(
            kind=IRON_CONDOR, underlying=underlying, legs=legs, expiration=expiration
        )
        if built is not None:
            structures.append(built)
    return structures


def structures_for_signal(
    signal: UnderlyingSignal,
    candidates: list[OptionCandidate],
    *,
    params: SelectionParams,
) -> list[Structure]:
    """Every shape worth considering for one underlying this cycle.

    Returns an empty list whenever the signal says stand down, so the caller never
    has to re-check the stance.
    """
    if not signal.actionable or signal.expiration is None:
        return []

    common = {
        "underlying": signal.symbol,
        "spot": signal.spot,
        "expiration": signal.expiration,
        "params": params,
    }

    if signal.stance == STANCE_SELL_VOL:
        if signal.trend == TREND_FLAT:
            return iron_condor_variants(candidates, **common)
        if signal.trend == TREND_UP:
            return credit_spread_variants(
                candidates,
                option_type="put",
                target_delta=params.target_short_delta,
                **common,
            )
        return credit_spread_variants(
            candidates,
            option_type="call",
            target_delta=params.target_short_delta,
            **common,
        )

    if signal.stance == STANCE_BUY_VOL:
        # Cheap options are only worth owning with a direction to point them at.
        if signal.trend == TREND_UP:
            return debit_spread_variants(candidates, option_type="call", **common)
        if signal.trend == TREND_DOWN:
            return debit_spread_variants(candidates, option_type="put", **common)
        return []

    return []
