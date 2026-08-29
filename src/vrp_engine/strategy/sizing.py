r"""How many contracts, and exactly why that many.

The point of this module is that no size is ever a hunch. A stake starts as a
fractional-Kelly bet on the modelled edge and is then cut by every budget that
applies, in order, and the binding constraint is recorded. Anyone reading the journal
can reconstruct the arithmetic without trusting the engine.

Math: for a binary bet with win probability $p$ and payoff odds $b$, the Kelly stake
is $f^\* = (pb - (1-p))/b$. The engine risks

$$R = \min\big(\kappa f^\* E,\; \text{caps}\big),\qquad
n = \left\lfloor R / L_{\text{contract}} \right\rfloor$$

with $\kappa$ the Kelly haircut, $E$ equity and $L$ the per-contract max loss.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from vrp_engine.config import Settings
from vrp_engine.risk.portfolio import Exposure
from vrp_engine.strategy.pricing import StructureEvaluation

# Never plan to use the last dollar of options buying power: a fill a few cents worse
# than the mid would then bounce the whole ticket.
BUYING_POWER_UTILISATION = 0.90

# Do not take a position larger than a small slice of the contracts that already
# exist at that strike, when open interest is known. Size beyond that is a fill
# problem dressed up as a risk problem.
OPEN_INTEREST_DIVISOR = 50


class RiskBudget(BaseModel):
    """Every ceiling, resolved into dollars against current equity."""

    equity: float
    kelly_haircut: float
    risk_budget_pct: float
    max_trade_loss_pct: float
    max_underlying_loss_pct: float
    max_bucket_loss_pct: float
    max_contracts_per_order: int

    @classmethod
    def from_settings(cls, settings: Settings, *, equity: float) -> RiskBudget:
        return cls(
            equity=max(equity, 0.0),
            kelly_haircut=settings.kelly_fraction,
            risk_budget_pct=settings.risk_budget_pct,
            max_trade_loss_pct=settings.max_trade_loss_pct,
            max_underlying_loss_pct=settings.max_underlying_loss_pct,
            max_bucket_loss_pct=settings.max_bucket_loss_pct,
            max_contracts_per_order=settings.max_contracts_per_order,
        )

    @property
    def aggregate_cap_usd(self) -> float:
        return self.equity * self.risk_budget_pct

    @property
    def trade_cap_usd(self) -> float:
        return self.equity * self.max_trade_loss_pct

    @property
    def underlying_cap_usd(self) -> float:
        return self.equity * self.max_underlying_loss_pct

    @property
    def bucket_cap_usd(self) -> float:
        return self.equity * self.max_bucket_loss_pct


class SizingResult(BaseModel):
    """The chosen size plus the audit trail that produced it."""

    contracts: int
    per_contract_loss_usd: float
    total_risk_usd: float
    kelly_full: float
    kelly_target_usd: float
    binding_constraint: str
    headroom: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @property
    def sizable(self) -> bool:
        return self.contracts >= 1

    def rationale(self) -> str:
        return (
            f"{self.contracts} contract(s) risking {self.total_risk_usd:.0f} USD "
            f"(Kelly {self.kelly_full:.1%} -> target {self.kelly_target_usd:.0f} USD, "
            f"bound by {self.binding_constraint})"
        )


def _liquidity_cap(evaluation: StructureEvaluation) -> int | None:
    """Contract ceiling implied by the thinnest leg's open interest, when known."""
    caps: list[int] = []
    for leg in evaluation.structure.legs:
        interest = leg.contract.open_interest
        if interest is None:
            continue
        caps.append(max(int(interest // OPEN_INTEREST_DIVISOR), 0))
    return min(caps) if caps else None


def size_structure(
    evaluation: StructureEvaluation,
    *,
    budget: RiskBudget,
    exposure: Exposure,
    bucket: str,
    options_buying_power: float,
) -> SizingResult:
    """Turn an edge into a contract count, cutting by each budget in turn."""
    per_contract = evaluation.max_loss_usd
    notes: list[str] = []

    kelly_target = evaluation.full_kelly * budget.kelly_haircut * budget.equity

    headroom = {
        "per_trade": budget.trade_cap_usd,
        "per_underlying": budget.underlying_cap_usd
        - exposure.underlying(evaluation.structure.underlying),
        "bucket": budget.bucket_cap_usd - exposure.bucket(bucket),
        "aggregate": budget.aggregate_cap_usd - exposure.total_usd,
        "options_buying_power": max(options_buying_power, 0.0) * BUYING_POWER_UTILISATION,
    }

    allowed_usd = kelly_target
    binding = "fractional Kelly"
    for name, room in headroom.items():
        if room < allowed_usd:
            allowed_usd = room
            binding = name

    if per_contract <= 0:
        return SizingResult(
            contracts=0,
            per_contract_loss_usd=per_contract,
            total_risk_usd=0.0,
            kelly_full=evaluation.full_kelly,
            kelly_target_usd=kelly_target,
            binding_constraint="invalid per-contract risk",
            headroom=headroom,
            notes=["structure reports a non-positive max loss"],
        )

    contracts = int(math.floor(max(allowed_usd, 0.0) / per_contract))

    if contracts > budget.max_contracts_per_order:
        contracts = budget.max_contracts_per_order
        binding = "per-order contract cap"

    liquidity = _liquidity_cap(evaluation)
    if liquidity is not None and contracts > liquidity:
        contracts = liquidity
        binding = "open interest"
        notes.append(f"open interest limits the ticket to {liquidity} contract(s)")

    if contracts < 1:
        notes.append(
            f"no room left: {binding} allows {max(allowed_usd, 0.0):.0f} USD but one "
            f"contract risks {per_contract:.0f} USD"
        )

    return SizingResult(
        contracts=max(contracts, 0),
        per_contract_loss_usd=per_contract,
        total_risk_usd=max(contracts, 0) * per_contract,
        kelly_full=evaluation.full_kelly,
        kelly_target_usd=kelly_target,
        binding_constraint=binding,
        headroom=headroom,
        notes=notes,
    )
