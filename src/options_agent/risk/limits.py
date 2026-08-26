"""Per-order limits. The LLM proposes; this module decides. It cannot be disabled."""

from __future__ import annotations

from pydantic import BaseModel, Field

from options_agent.config import Settings, load_settings
from options_agent.strategy.base import ProposedTrade

MAX_LEGS_PER_TICKET = 4  # Alpaca's multi-leg ceiling


class RiskDecision(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        return "approved" if self.allowed else "; ".join(self.reasons)


class RiskLimits(BaseModel):
    max_contracts_per_order: int = 5
    max_order_notional_usd: float = 2_500.0
    # Selling an option without a long leg has unbounded loss; never allowed here.
    allow_naked_short: bool = False


def limits_from_settings(settings: Settings | None = None) -> RiskLimits:
    loaded = settings or load_settings()
    return RiskLimits(
        max_contracts_per_order=loaded.max_contracts_per_order,
        max_order_notional_usd=loaded.max_order_notional_usd,
    )


def review_proposal(proposal: ProposedTrade, limits: RiskLimits | None = None) -> RiskDecision:
    """Decide whether a proposed options ticket may reach Alpaca."""
    rules = limits or limits_from_settings()
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

    return RiskDecision(allowed=not reasons, reasons=reasons)
