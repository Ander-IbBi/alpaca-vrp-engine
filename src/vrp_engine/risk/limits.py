"""The gate. The strategy proposes, the analyst comments, this module decides.

Two things happen here that a per-order cap cannot do on its own:

1. **Defined risk is proved, not claimed.** Every short leg must be paired with a long
   leg of the same type and expiry on the protective side of it, inside the same
   ticket. That is what makes a naked short structurally impossible rather than
   merely discouraged, and it is also exactly what Alpaca's multi-leg rule requires.
2. **The book is checked, not just the ticket.** A proposal is priced as if already
   filled, and it is only approved when the resulting *portfolio* still fits inside
   every budget. Ten individually sensible spreads on correlated names are one big
   bet, and this is where that gets caught.

None of it is reachable by the LLM. There is no flag to turn it off.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from vrp_engine.alpaca.options import parse_occ_symbol
from vrp_engine.config import Settings, load_settings
from vrp_engine.risk.portfolio import PortfolioRisk
from vrp_engine.strategy.base import ProposedLeg, ProposedTrade

MAX_LEGS_PER_TICKET = 4  # Alpaca's multi-leg ceiling
CONTRACT_MULTIPLIER = 100


class RiskDecision(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        return "approved" if self.allowed else "; ".join(self.reasons)


class RiskLimits(BaseModel):
    """Budgets resolved into dollars, so nothing downstream re-derives them."""

    equity: float = 100_000.0
    max_contracts_per_order: int = 40
    max_trade_loss_usd: float = 4_500.0
    max_aggregate_loss_usd: float = 45_000.0
    max_underlying_loss_usd: float = 12_000.0
    max_bucket_loss_usd: float = 30_000.0
    max_stress_loss_usd: float = 18_000.0
    max_net_delta_usd: float = 25_000.0
    # Selling an option with no long leg behind it has unbounded loss. Never allowed.
    allow_naked_short: bool = False

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        equity: float | None = None,
    ) -> RiskLimits:
        loaded = settings or load_settings()
        base = equity if equity and equity > 0 else loaded.start_equity_usd
        return cls(
            equity=base,
            max_contracts_per_order=loaded.max_contracts_per_order,
            max_trade_loss_usd=base * loaded.max_trade_loss_pct,
            max_aggregate_loss_usd=base * loaded.risk_budget_pct,
            max_underlying_loss_usd=base * loaded.max_underlying_loss_pct,
            max_bucket_loss_usd=base * loaded.max_bucket_loss_pct,
            max_stress_loss_usd=base * loaded.max_stress_loss_pct,
            max_net_delta_usd=base * loaded.max_net_delta_pct,
        )


def limits_from_settings(
    settings: Settings | None = None,
    *,
    equity: float | None = None,
) -> RiskLimits:
    return RiskLimits.from_settings(settings, equity=equity)


def _expand(legs: list[ProposedLeg], *, side: str, qty: int) -> list[tuple[str, float, str]]:
    """One entry per contract-slot: (option_type, strike, symbol)."""
    slots: list[tuple[str, float, str]] = []
    for leg in legs:
        if leg.side != side:
            continue
        parsed = parse_occ_symbol(leg.symbol)
        if parsed is None:
            continue
        for _ in range(qty * leg.ratio_qty):
            slots.append((parsed.option_type, parsed.strike, leg.symbol))
    return slots


def uncovered_short_legs(proposal: ProposedTrade) -> list[str]:
    """Short legs with no protective long leg in the same ticket.

    Greedy matching per option type and expiry: each short call needs a long call at a
    higher strike, each short put a long put at a lower strike. Anything left over is
    a naked short and the ticket dies here.
    """
    uncovered: list[str] = []
    by_expiry: dict[tuple[str, str], dict[str, list[tuple[str, float, str]]]] = {}
    for side in ("sell", "buy"):
        for leg in proposal.legs:
            if leg.side != side:
                continue
            parsed = parse_occ_symbol(leg.symbol)
            if parsed is None:
                # A symbol we cannot read is a symbol we cannot prove is covered. On an
                # option ticket that has to fail closed: skipping it silently would let
                # a short leg through wearing the "every short leg is covered" label.
                # Equity tickets are exempt, where a sell is a share sale, not a short.
                if side == "sell" and proposal.kind == "option":
                    uncovered.append(leg.symbol)
                continue
            key = (parsed.option_type, parsed.expiration.isoformat())
            group = by_expiry.setdefault(key, {"sell": [], "buy": []})
            for _ in range(max(proposal.qty, 1) * leg.ratio_qty):
                group[side].append((parsed.option_type, parsed.strike, leg.symbol))

    for (option_type, _expiry), group in by_expiry.items():
        shorts = sorted(group["sell"], key=lambda item: item[1])
        longs = sorted(group["buy"], key=lambda item: item[1])
        # Calls are protected from above, puts from below, so walk each list in the
        # direction where the cheapest adequate long comes first.
        if option_type == "call":
            for short in shorts:
                match = next((i for i, long in enumerate(longs) if long[1] > short[1]), None)
                if match is None:
                    uncovered.append(short[2])
                else:
                    longs.pop(match)
        else:
            longs.sort(key=lambda item: -item[1])
            for short in sorted(shorts, key=lambda item: -item[1]):
                match = next((i for i, long in enumerate(longs) if long[1] < short[1]), None)
                if match is None:
                    uncovered.append(short[2])
                else:
                    longs.pop(match)
    return uncovered


def _review_closing(
    proposal: ProposedTrade,
    *,
    open_contracts: dict[str, float] | None,
) -> list[str]:
    """A close must actually close something we hold, in the right direction.

    Without this check the `_to_close` label alone would be a loophole around the
    coverage proof, since selling a long leg to close looks identical to opening a
    short.
    """
    if open_contracts is None:
        return ["cannot verify an exit without the current position list"]

    reasons: list[str] = []
    for leg in proposal.legs:
        held = open_contracts.get(leg.symbol.upper())
        if held is None or held == 0:
            reasons.append(f"{leg.symbol} is not an open position")
            continue
        wanted = max(proposal.qty, 1) * leg.ratio_qty
        # Buying to close requires a short position, selling to close a long one.
        if leg.side == "buy" and held >= 0:
            reasons.append(f"{leg.symbol} is not short, so buying it does not close")
        elif leg.side == "sell" and held <= 0:
            reasons.append(f"{leg.symbol} is not long, so selling it does not close")
        elif abs(held) < wanted:
            reasons.append(
                f"{leg.symbol}: trying to close {wanted} but only {abs(held):g} are open"
            )
    return reasons


def _review_equity(proposal: ProposedTrade) -> RiskDecision:
    """Shares may only ever be sold. The engine's edge is in options, not in a book."""
    reasons: list[str] = []
    checks: list[str] = []
    if proposal.skip:
        reasons.append("strategy asked to skip")
    if not proposal.legs:
        reasons.append("no equity symbol in proposal")
    if proposal.qty < 1:
        reasons.append("qty must be at least 1")
    if any(leg.side == "buy" for leg in proposal.legs):
        reasons.append("buying shares is not part of this strategy")
    else:
        checks.append("share ticket is a sale, reducing inherited exposure")
    return RiskDecision(allowed=not reasons, reasons=reasons, checks=checks)


def _review_portfolio(
    post_trade: PortfolioRisk,
    rules: RiskLimits,
    *,
    underlying: str | None,
    bucket: str | None,
) -> tuple[list[str], list[str]]:
    """Budgets applied to the book as it would look after the fill."""
    reasons: list[str] = []
    checks: list[str] = []

    worst = post_trade.total_worst_case_loss_usd
    if worst > rules.max_aggregate_loss_usd:
        reasons.append(
            f"post-trade worst case {worst:.0f} USD exceeds the aggregate budget "
            f"{rules.max_aggregate_loss_usd:.0f} USD"
        )
    else:
        checks.append(
            f"worst case {worst:.0f} of {rules.max_aggregate_loss_usd:.0f} USD budget"
        )

    stress = post_trade.worst_stress_loss_usd
    if stress > rules.max_stress_loss_usd:
        reasons.append(
            f"two-sigma stress loss {stress:.0f} USD exceeds the "
            f"{rules.max_stress_loss_usd:.0f} USD ceiling"
        )
    else:
        checks.append(f"stress loss {stress:.0f} of {rules.max_stress_loss_usd:.0f} USD")

    delta = post_trade.beta_weighted_delta_usd
    if abs(delta) > rules.max_net_delta_usd:
        reasons.append(
            f"beta-weighted delta {delta:+.0f} USD outside the "
            f"+/-{rules.max_net_delta_usd:.0f} USD budget"
        )
    else:
        checks.append(f"beta delta {delta:+.0f} of +/-{rules.max_net_delta_usd:.0f} USD")

    if underlying:
        per_symbol = post_trade.exposure.underlying(underlying)
        if per_symbol > rules.max_underlying_loss_usd:
            reasons.append(
                f"{underlying} risk {per_symbol:.0f} USD exceeds the per-underlying "
                f"cap {rules.max_underlying_loss_usd:.0f} USD"
            )
        else:
            checks.append(f"{underlying} risk {per_symbol:.0f} USD within cap")

    if bucket:
        per_bucket = post_trade.exposure.bucket(bucket)
        if per_bucket > rules.max_bucket_loss_usd:
            reasons.append(
                f"bucket '{bucket}' risk {per_bucket:.0f} USD exceeds the cap "
                f"{rules.max_bucket_loss_usd:.0f} USD"
            )
        else:
            checks.append(f"bucket '{bucket}' risk {per_bucket:.0f} USD within cap")

    return reasons, checks


def review_proposal(
    proposal: ProposedTrade,
    limits: RiskLimits | None = None,
    *,
    open_contracts: dict[str, float] | None = None,
    post_trade: PortfolioRisk | None = None,
    bucket: str | None = None,
) -> RiskDecision:
    """Decide whether a proposed ticket may reach Alpaca."""
    rules = limits or limits_from_settings()
    if proposal.kind == "equity":
        return _review_equity(proposal)

    reasons: list[str] = []
    checks: list[str] = []

    if proposal.skip:
        reasons.append("strategy asked to skip")
    if not proposal.legs:
        reasons.append("no option legs in proposal")
    if len(proposal.legs) > MAX_LEGS_PER_TICKET:
        reasons.append(f"{len(proposal.legs)} legs exceeds the {MAX_LEGS_PER_TICKET}-leg limit")
    if proposal.qty < 1:
        reasons.append("qty must be at least 1")
    elif proposal.qty > rules.max_contracts_per_order:
        reasons.append(
            f"qty {proposal.qty} exceeds max {rules.max_contracts_per_order} contracts per order"
        )

    if proposal.is_closing:
        reasons.extend(_review_closing(proposal, open_contracts=open_contracts))
        if not reasons:
            checks.append("exit matches an open position on every leg")
        # An exit reduces risk, so the cost and coverage tests below do not apply.
        return RiskDecision(allowed=not reasons, reasons=reasons, checks=checks)

    cost = proposal.estimated_cost_usd
    if cost is None:
        reasons.append("missing cost estimate; cannot size the risk")
    elif cost > rules.max_trade_loss_usd:
        reasons.append(
            f"collateral {cost:.0f} USD exceeds the per-trade cap "
            f"{rules.max_trade_loss_usd:.0f} USD"
        )
    else:
        checks.append(f"collateral {cost:.0f} of {rules.max_trade_loss_usd:.0f} USD per-trade cap")

    if proposal.max_loss_usd is None:
        reasons.append("proposal does not state a max loss")
    elif proposal.max_loss_usd > rules.max_trade_loss_usd:
        reasons.append(
            f"stated max loss {proposal.max_loss_usd:.0f} USD exceeds the per-trade cap"
        )

    if not rules.allow_naked_short:
        uncovered = uncovered_short_legs(proposal)
        if uncovered:
            reasons.append(
                "naked short blocked: no protective long leg for "
                + ", ".join(sorted(set(uncovered)))
            )
        elif any(leg.side == "sell" for leg in proposal.legs):
            checks.append("every short leg is covered by a long leg in the same ticket")

    if post_trade is not None:
        underlying = proposal.analytics.underlying if proposal.analytics else None
        portfolio_reasons, portfolio_checks = _review_portfolio(
            post_trade, rules, underlying=underlying, bucket=bucket
        )
        reasons.extend(portfolio_reasons)
        checks.extend(portfolio_checks)

    return RiskDecision(allowed=not reasons, reasons=reasons, checks=checks)
