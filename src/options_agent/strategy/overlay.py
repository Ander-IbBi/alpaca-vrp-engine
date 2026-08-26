"""Protective-put overlay: buy downside insurance on an existing equity book.

Intuition: long shares lose money when the market drops. A put pays off exactly
there, so a small, defined-cost put position caps the drawdown.

Technical: pick a contract roughly `moneyness` below spot with a sensible time to
expiry, size it against the shares held (1 contract covers 100 shares).

Math: for S shares and strike K, the hedged payoff below K is
$S \\cdot K - \\text{premium}$ instead of $S \\cdot S_T$, so the loss is bounded.
"""

from __future__ import annotations

from datetime import date

from options_agent.alpaca.options import OptionCandidate
from options_agent.strategy.base import ProposedLeg, ProposedTrade, StrategyContext

CONTRACT_MULTIPLIER = 100


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

    Pure function: no network, no client. This is where the hedging judgement
    lives, so it is the part worth unit testing.
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


def contracts_for_shares(shares: float) -> int:
    """One option contract covers 100 shares; never over-hedge."""
    return int(shares // CONTRACT_MULTIPLIER)


class ProtectivePutOverlay:
    """Baseline strategy. Replace or extend after the kickoff brief."""

    name = "protective-put-overlay"

    def __init__(
        self,
        chain_provider=None,
        *,
        moneyness: float = 0.95,
        min_dte: int = 21,
        max_dte: int = 60,
    ) -> None:
        # chain_provider(underlying) -> list[OptionCandidate]; injected so tests
        # and the live agent share the same code path.
        self.chain_provider = chain_provider
        self.moneyness = moneyness
        self.min_dte = min_dte
        self.max_dte = max_dte

    def propose(self, context: StrategyContext) -> ProposedTrade:
        if not context.equity_positions:
            return ProposedTrade(
                skip=True,
                rationale="No equity book to hedge yet. Buy an underlying first, then overlay.",
            )
        if self.chain_provider is None:
            return ProposedTrade(
                skip=True,
                rationale="No option chain provider wired; running in observation mode.",
            )

        # Hedge the largest long position first: it dominates the book's downside.
        longs = [p for p in context.equity_positions if float(getattr(p, "qty", 0)) > 0]
        if not longs:
            return ProposedTrade(skip=True, rationale="No long equity exposure to protect.")
        biggest = max(longs, key=lambda p: abs(float(getattr(p, "qty", 0))))
        symbol = str(getattr(biggest, "symbol", "")).upper()
        shares = float(getattr(biggest, "qty", 0))

        qty = contracts_for_shares(shares)
        if qty < 1:
            return ProposedTrade(
                skip=True,
                rationale=f"{symbol}: {shares:g} shares is below one contract (100).",
            )

        spot = context.spot_prices.get(symbol)
        if not spot:
            return ProposedTrade(skip=True, rationale=f"No spot price available for {symbol}.")

        chosen = select_protective_put(
            self.chain_provider(symbol),
            spot=spot,
            today=context.today,
            moneyness=self.moneyness,
            min_dte=self.min_dte,
            max_dte=self.max_dte,
        )
        if chosen is None:
            return ProposedTrade(
                skip=True,
                rationale=(
                    f"No put for {symbol} inside the "
                    f"{self.min_dte}-{self.max_dte} DTE window."
                ),
            )

        premium = chosen.mid_price
        cost = premium * CONTRACT_MULTIPLIER * qty if premium is not None else None
        return ProposedTrade(
            qty=qty,
            legs=[
                ProposedLeg(symbol=chosen.symbol, side="buy", position_intent="buy_to_open"),
            ],
            rationale=(
                f"Protect {shares:g} {symbol} shares with {qty} put(s) at strike "
                f"{chosen.strike:g} expiring {chosen.expiration} "
                f"({chosen.dte(context.today)} DTE)."
            ),
            estimated_cost_usd=cost,
            # A long put can only lose the premium paid.
            max_loss_usd=cost,
        )
