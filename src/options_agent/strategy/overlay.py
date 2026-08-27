"""Aggressive collar overlay: long shares, long put, short call, same expiry.

Intuition: a protective put caps the drawdown but its premium is a P&L drag. Selling
a call against the same book finances most of that put. The trade-off is a ceiling
on the rally.

Technical: seed 100 shares of the watchlist name if the book is empty, then pick a
put near delta -0.20 and a call near delta +0.20 in the 21-45 DTE window. One
contract covers 100 shares; never over-hedge, and this playbook sizes to one collar.

Math: long stock at $S$, long put $K_p$, short call $K_c$, net debit $D = P_p - P_c$.
P&L per share is $\\mathrm{clip}(S_T, K_p, K_c) - S - D$. Max loss is
$(S - K_p + D) \\times 100$ per contract.
"""

from __future__ import annotations

from datetime import date
from typing import Any, NamedTuple

from options_agent.alpaca.options import OptionCandidate, parse_occ_symbol
from options_agent.strategy.base import ProposedLeg, ProposedTrade, StrategyContext

CONTRACT_MULTIPLIER = 100
TARGET_PUT_DELTA = -0.20
TARGET_CALL_DELTA = 0.20
MAX_QUOTE_SPREAD = 0.35
# One-collar playbook: a second contract would blow the $2,500 options loss cap.
MAX_COLLAR_CONTRACTS = 1


class CollarSelection(NamedTuple):
    put: OptionCandidate
    call: OptionCandidate


def select_protective_put(
    candidates: list[OptionCandidate],
    *,
    spot: float,
    today: date,
    moneyness: float = 0.95,
    min_dte: int = 21,
    max_dte: int = 60,
) -> OptionCandidate | None:
    """Closest put to the target strike within the accepted expiry window.

    Kept as a pure helper (the live overlay uses `select_collar`).
    """
    target_strike = spot * moneyness
    viable = [
        c
        for c in candidates
        if c.option_type == "put"
        and c.strike <= spot  # a protective put is bought out of the money
        and min_dte <= c.dte(today) <= max_dte
    ]
    if not viable:
        return None
    # Prefer the strike nearest the target; break ties with the shorter expiry.
    return min(viable, key=lambda c: (abs(c.strike - target_strike), c.dte(today)))


def _quoted_in_window(
    candidate: OptionCandidate,
    *,
    today: date,
    min_dte: int,
    max_dte: int,
) -> bool:
    if candidate.mid_price is None or candidate.delta is None:
        return False
    spread = candidate.spread_fraction
    if spread is None or spread > MAX_QUOTE_SPREAD:
        return False
    return min_dte <= candidate.dte(today) <= max_dte


def select_collar(
    candidates: list[OptionCandidate],
    *,
    spot: float,
    today: date,
    target_put_delta: float = TARGET_PUT_DELTA,
    target_call_delta: float = TARGET_CALL_DELTA,
    min_dte: int = 21,
    max_dte: int = 45,
) -> CollarSelection | None:
    """OTM put near target delta, then a same-expiry OTM call near its target.

    Pure function: no network. Delta is the live selection rule; moneyness is a
    fallback only inside `select_protective_put`.
    """
    puts = [
        c
        for c in candidates
        if c.option_type == "put"
        and c.strike < spot
        and _quoted_in_window(c, today=today, min_dte=min_dte, max_dte=max_dte)
    ]
    if not puts:
        return None
    put = min(puts, key=lambda c: (abs((c.delta or 0.0) - target_put_delta), c.dte(today)))

    calls = [
        c
        for c in candidates
        if c.option_type == "call"
        and c.strike > spot
        and c.strike > put.strike
        and c.expiration == put.expiration
        and _quoted_in_window(c, today=today, min_dte=min_dte, max_dte=max_dte)
    ]
    if not calls:
        return None
    call = min(calls, key=lambda c: abs((c.delta or 0.0) - target_call_delta))
    return CollarSelection(put=put, call=call)


def contracts_for_shares(shares: float) -> int:
    """One option contract covers 100 shares; never over-hedge."""
    return int(shares // CONTRACT_MULTIPLIER)


def net_limit_price(put_mid: float, call_mid: float) -> float:
    """Net debit (positive) or credit (negative), rounded to a valid options tick."""
    net = round(put_mid - call_mid, 2)
    if net == 0:
        # A true zero-cost mid still needs a price Alpaca will accept.
        return 0.01
    return net


def collar_cash_and_max_loss(
    *,
    spot: float,
    put: OptionCandidate,
    call: OptionCandidate,
    qty: int,
) -> tuple[float, float]:
    """Net debit (may be negative) and defined max loss of the collared stock."""
    put_mid = put.mid_price
    call_mid = call.mid_price
    if put_mid is None or call_mid is None:
        raise ValueError("collar legs need a mid price")
    net_debit = (put_mid - call_mid) * CONTRACT_MULTIPLIER * qty
    gap = (spot - put.strike) * CONTRACT_MULTIPLIER * qty
    return net_debit, gap + net_debit


def _qty(position: Any) -> float:
    return float(getattr(position, "qty", 0) or 0)


def _symbol(position: Any) -> str:
    return str(getattr(position, "symbol", "")).upper()


def long_shares(equity_positions: list[Any], symbol: str) -> float:
    total = 0.0
    for position in equity_positions:
        if _symbol(position) == symbol.upper() and _qty(position) > 0:
            total += _qty(position)
    return total


def covering_option_contracts(option_positions: list[Any], symbol: str) -> tuple[int, int]:
    """Net long puts and short calls on `symbol`, in contracts."""
    ticker = symbol.upper()
    long_puts = 0
    short_calls = 0
    for position in option_positions:
        parsed = parse_occ_symbol(_symbol(position))
        if parsed is None or parsed.underlying != ticker:
            continue
        qty = _qty(position)
        if parsed.option_type == "put" and qty > 0:
            long_puts += int(qty)
        elif parsed.option_type == "call" and qty < 0:
            short_calls += int(-qty)
    return long_puts, short_calls


def already_collared(
    *,
    shares: float,
    option_positions: list[Any],
    symbol: str,
) -> bool:
    """True when long puts and short calls already cover the playbook size."""
    needed = min(contracts_for_shares(shares), MAX_COLLAR_CONTRACTS)
    if needed < 1:
        return False
    long_puts, short_calls = covering_option_contracts(option_positions, symbol)
    return long_puts >= needed and short_calls >= needed


def _target_symbol(context: StrategyContext) -> str:
    return context.underlyings[0] if context.underlyings else "SPY"


class AggressiveCollarOverlay:
    """Hackathon playbook: seed SPY, open one collar, then hold."""

    name = "aggressive-collar-overlay"

    def __init__(
        self,
        chain_provider=None,
        *,
        target_put_delta: float = TARGET_PUT_DELTA,
        target_call_delta: float = TARGET_CALL_DELTA,
        min_dte: int = 21,
        max_dte: int = 45,
        seed_shares: int = 100,
        max_equity_notional_usd: float = 80_000.0,
    ) -> None:
        # chain_provider(underlying) -> list[OptionCandidate]; injected so tests
        # and the live agent share the same code path.
        self.chain_provider = chain_provider
        self.target_put_delta = target_put_delta
        self.target_call_delta = target_call_delta
        self.min_dte = min_dte
        self.max_dte = max_dte
        self.seed_shares = seed_shares
        self.max_equity_notional_usd = max_equity_notional_usd

    def propose(self, context: StrategyContext) -> ProposedTrade:
        symbol = _target_symbol(context)
        shares = long_shares(context.equity_positions, symbol)
        if shares < CONTRACT_MULTIPLIER:
            return self._propose_seed(context, symbol, shares)

        if already_collared(
            shares=shares,
            option_positions=context.option_positions,
            symbol=symbol,
        ):
            return ProposedTrade(
                skip=True,
                rationale=(
                    f"{symbol} already collared ({shares:g} shares). Holding the playbook."
                ),
            )

        if self.chain_provider is None:
            return ProposedTrade(
                skip=True,
                rationale="No option chain provider wired; running in observation mode.",
            )

        spot = context.spot_prices.get(symbol)
        if not spot:
            return ProposedTrade(skip=True, rationale=f"No spot price available for {symbol}.")

        qty = min(contracts_for_shares(shares), MAX_COLLAR_CONTRACTS)
        if qty < 1:
            return ProposedTrade(
                skip=True,
                rationale=f"{symbol}: {shares:g} shares is below one contract (100).",
            )

        chosen = select_collar(
            self.chain_provider(symbol),
            spot=spot,
            today=context.today,
            target_put_delta=self.target_put_delta,
            target_call_delta=self.target_call_delta,
            min_dte=self.min_dte,
            max_dte=self.max_dte,
        )
        if chosen is None:
            return ProposedTrade(
                skip=True,
                rationale=(
                    f"No quoted collar for {symbol} inside the "
                    f"{self.min_dte}-{self.max_dte} DTE window."
                ),
            )

        put, call = chosen.put, chosen.call
        put_mid, call_mid = put.mid_price, call.mid_price
        if put_mid is None or call_mid is None:
            return ProposedTrade(skip=True, rationale="Collar legs are missing a mid price.")

        net_debit, max_loss = collar_cash_and_max_loss(spot=spot, put=put, call=call, qty=qty)
        return ProposedTrade(
            qty=qty,
            kind="option",
            covering_shares=shares,
            limit_price=net_limit_price(put_mid, call_mid),
            legs=[
                ProposedLeg(symbol=put.symbol, side="buy", position_intent="buy_to_open"),
                ProposedLeg(symbol=call.symbol, side="sell", position_intent="sell_to_open"),
            ],
            rationale=(
                f"Collar {shares:g} {symbol} with {qty}x "
                f"{put.strike:g}p/{call.strike:g}c expiring {put.expiration} "
                f"({put.dte(context.today)} DTE); net "
                f"{'debit' if net_debit >= 0 else 'credit'} ${abs(net_debit):.0f}."
            ),
            estimated_cost_usd=net_debit,
            max_loss_usd=max_loss,
        )

    def _propose_seed(
        self,
        context: StrategyContext,
        symbol: str,
        shares_held: float,
    ) -> ProposedTrade:
        needed = max(self.seed_shares - int(shares_held), 0)
        if needed < 1:
            return ProposedTrade(
                skip=True,
                rationale=f"{symbol}: {shares_held:g} shares is below one contract (100).",
            )
        spot = context.spot_prices.get(symbol)
        if not spot:
            return ProposedTrade(
                skip=True,
                rationale=f"No spot price available to seed {needed} {symbol} shares.",
            )
        cost = needed * spot
        if cost > self.max_equity_notional_usd:
            return ProposedTrade(
                skip=True,
                rationale=(
                    f"Seed {needed} {symbol} at ${spot:.2f} costs ${cost:.0f}, "
                    f"above the ${self.max_equity_notional_usd:.0f} equity cap."
                ),
            )
        if context.cash < cost:
            return ProposedTrade(
                skip=True,
                rationale=(
                    f"Need ${cost:.0f} cash to seed {needed} {symbol}; have ${context.cash:.0f}."
                ),
            )
        return ProposedTrade(
            qty=needed,
            kind="equity",
            legs=[ProposedLeg(symbol=symbol, side="buy")],
            rationale=(
                f"No overlay yet: buy {needed} {symbol} shares to seed a 1-contract collar."
            ),
            estimated_cost_usd=cost,
            max_loss_usd=cost,
        )
