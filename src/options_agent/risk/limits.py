"""Per-order limits. The LLM proposes; this module decides. It cannot be disabled."""

from __future__ import annotations

from pydantic import BaseModel, Field

from options_agent.alpaca.options import parse_occ_symbol
from options_agent.config import Settings, load_settings
from options_agent.strategy.base import ProposedTrade

MAX_LEGS_PER_TICKET = 4  # Alpaca's multi-leg ceiling
CONTRACT_MULTIPLIER = 100


class RiskDecision(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        return "approved" if self.allowed else "; ".join(self.reasons)


class RiskLimits(BaseModel):
    max_contracts_per_order: int = 5
    max_order_notional_usd: float = 2_500.0
    max_equity_notional_usd: float = 80_000.0
    # Selling an option without a long leg has unbounded loss; never allowed here.
    allow_naked_short: bool = False


def limits_from_settings(settings: Settings | None = None) -> RiskLimits:
    loaded = settings or load_settings()
    return RiskLimits(
        max_contracts_per_order=loaded.max_contracts_per_order,
        max_order_notional_usd=loaded.max_order_notional_usd,
        max_equity_notional_usd=loaded.max_equity_notional_usd,
    )


def _short_call_contracts(proposal: ProposedTrade) -> int:
    total = 0
    for leg in proposal.legs:
        if leg.side != "sell":
            continue
        parsed = parse_occ_symbol(leg.symbol)
        if parsed is not None and parsed.option_type == "call":
            total += proposal.qty * leg.ratio_qty
    return total


def _review_equity(proposal: ProposedTrade, rules: RiskLimits) -> RiskDecision:
    reasons: list[str] = []
    if proposal.skip:
        reasons.append("strategy asked to skip")
    if not proposal.legs:
        reasons.append("no equity symbol in proposal")
    if proposal.qty < 1:
        reasons.append("qty must be at least 1")
    cost = proposal.estimated_cost_usd
    if cost is None:
        reasons.append("missing cost estimate; cannot size the risk")
    elif cost > rules.max_equity_notional_usd:
        reasons.append(
            f"estimated cost {cost:.0f} USD exceeds max "
            f"{rules.max_equity_notional_usd:.0f} USD equity notional"
        )
    return RiskDecision(allowed=not reasons, reasons=reasons)


def review_proposal(proposal: ProposedTrade, limits: RiskLimits | None = None) -> RiskDecision:
    """Decide whether a proposed ticket may reach Alpaca."""
    rules = limits or limits_from_settings()
    if proposal.kind == "equity":
        return _review_equity(proposal, rules)

    reasons: list[str] = []

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

    cost = proposal.estimated_cost_usd
    if cost is None:
        reasons.append("missing cost estimate; cannot size the risk")
    elif cost > rules.max_order_notional_usd:
        reasons.append(
            f"estimated cost {cost:.0f} USD exceeds max {rules.max_order_notional_usd:.0f} USD"
        )

    if proposal.max_loss_usd is not None and proposal.max_loss_usd > rules.max_order_notional_usd:
        reasons.append("stated max loss exceeds the per-order cap")

    sells = [leg for leg in proposal.legs if leg.side == "sell"]
    buys = [leg for leg in proposal.legs if leg.side == "buy"]
    if sells and not buys and not rules.allow_naked_short:
        reasons.append("naked short options are blocked")

    short_calls = _short_call_contracts(proposal)
    if short_calls:
        covering = proposal.covering_shares or 0.0
        required = short_calls * CONTRACT_MULTIPLIER
        if covering < required:
            reasons.append(
                f"short call needs {required:g} covering shares; have {covering:g}"
            )

    return RiskDecision(allowed=not reasons, reasons=reasons)
