"""Clearing out positions the engine did not open.

A paper account handed over from a previous strategy arrives with a book the engine
cannot model: shares it would never buy, options at expiries outside its window. Worse,
that book ties up collateral. This module recognises those positions and unwinds them
one ticket at a time so the engine starts from capital it can actually deploy.

Ownership is decided structurally, with no stored state: the engine only ever holds
options expiring inside its own trading window, so anything with a longer tail was
not opened by it. Shares are never its doing at all.

Sequencing matters. Short options come first, then long options, then shares. Selling
the shares out from under a covered short call would leave a naked short standing for
as long as it takes the next ticket to fill, which is exactly the state the whole risk
layer exists to make impossible.
"""

from __future__ import annotations

from datetime import date

from vrp_engine.alpaca.options import OptionCandidate
from vrp_engine.risk.portfolio import OptionHolding, ShareHolding
from vrp_engine.strategy.base import (
    ACTION_UNWIND,
    ProposedLeg,
    ProposedTrade,
    TradeAnalytics,
)

# How far past the trading window an expiry has to sit before it reads as inherited.
LEGACY_DTE_SLACK = 5


def is_legacy_option(holding: OptionHolding, *, today: date, max_dte: int) -> bool:
    """True when this contract expires beyond anything the engine would have opened."""
    return holding.dte(today) > max_dte + LEGACY_DTE_SLACK


def legacy_options(
    options: list[OptionHolding],
    *,
    today: date,
    max_dte: int,
) -> list[OptionHolding]:
    return [
        holding
        for holding in options
        if holding.contracts != 0 and is_legacy_option(holding, today=today, max_dte=max_dte)
    ]


def legacy_shares(shares: list[ShareHolding]) -> list[ShareHolding]:
    """Any long share position: the engine trades options, never a stock book."""
    return [holding for holding in shares if holding.shares > 0]


def _close_single_option(
    holding: OptionHolding,
    *,
    quotes: dict[str, OptionCandidate],
    today: date,
) -> ProposedTrade:
    """A one-leg close. Single-leg exits sidestep the multi-leg coverage rule entirely."""
    quote = quotes.get(holding.symbol.upper())
    mid = quote.mid_price if quote is not None else None
    price = float(mid) if mid else float(holding.current_price)
    is_short = holding.contracts < 0
    return ProposedTrade(
        qty=int(abs(holding.contracts)),
        legs=[
            ProposedLeg(
                symbol=holding.symbol,
                side="buy" if is_short else "sell",
                position_intent="buy_to_close" if is_short else "sell_to_close",
            )
        ],
        action=ACTION_UNWIND,
        kind="option",
        rationale=(
            f"Unwind inherited {holding.symbol}: expires "
            f"{holding.expiration.isoformat()}, outside the engine's window, and its "
            "collateral is needed for structures the engine can model"
        ),
        limit_price=round(price, 2) if price > 0 else None,
        analytics=TradeAnalytics(
            underlying=holding.underlying,
            expiration=holding.expiration,
            dte=holding.dte(today),
        ),
    )


def _sell_shares(holding: ShareHolding) -> ProposedTrade:
    return ProposedTrade(
        qty=int(holding.shares),
        legs=[ProposedLeg(symbol=holding.symbol, side="sell")],
        action=ACTION_UNWIND,
        kind="equity",
        rationale=(
            f"Sell {int(holding.shares)} inherited {holding.symbol} shares: the engine "
            "holds no stock book, and this frees the collateral behind it"
        ),
        estimated_cost_usd=abs(holding.market_value),
        analytics=TradeAnalytics(underlying=holding.symbol),
    )


def next_unwind_action(
    *,
    options: list[OptionHolding],
    shares: list[ShareHolding],
    today: date,
    max_dte: int,
    quotes: dict[str, OptionCandidate],
) -> ProposedTrade | None:
    """The next single ticket in the unwind, or None when the book is already clean."""
    stale = legacy_options(options, today=today, max_dte=max_dte)

    # Shorts before longs: closing a short only ever reduces risk, while closing a
    # long first could briefly leave a short leg without its protection.
    shorts = [holding for holding in stale if holding.contracts < 0]
    if shorts:
        return _close_single_option(shorts[0], quotes=quotes, today=today)

    longs = [holding for holding in stale if holding.contracts > 0]
    if longs:
        return _close_single_option(longs[0], quotes=quotes, today=today)

    # Shares last, once no option position depends on them for cover.
    remaining = legacy_shares(shares)
    if remaining:
        return _sell_shares(remaining[0])

    return None
