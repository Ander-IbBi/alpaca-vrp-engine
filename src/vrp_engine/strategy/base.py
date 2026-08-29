"""The vocabulary every layer shares.

A strategy turns a `StrategyContext` into a `ProposedTrade`. The risk layer reads the
same object and either approves it or does not. Because the analytics that justified
the trade travel *with* the proposal, the journal ends up holding the full argument
for every ticket rather than just its strikes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from vrp_engine.alpaca.options import OptionCandidate
from vrp_engine.risk.portfolio import PortfolioRisk
from vrp_engine.strategy.signals import UnderlyingSignal

Side = Literal["buy", "sell"]
TradeKind = Literal["option", "equity"]

ACTION_OPEN = "open"
ACTION_CLOSE = "close"
ACTION_HEDGE = "hedge"
ACTION_UNWIND = "unwind"
ACTION_HOLD = "hold"


class ProposedLeg(BaseModel):
    symbol: str
    side: Side
    ratio_qty: int = Field(default=1, ge=1)
    position_intent: str | None = None  # e.g. "buy_to_open", "sell_to_close"

    @property
    def is_opening(self) -> bool:
        return (self.position_intent or "").endswith("_to_open")


class TradeAnalytics(BaseModel):
    """Why this trade, in numbers. Empty for exits, which are driven by rules."""

    structure_kind: str | None = None
    underlying: str | None = None
    expiration: date | None = None
    dte: int | None = None
    realized_vol: float | None = None
    implied_vol: float | None = None
    vrp: float | None = None
    vrp_z: float | None = None
    trend: str | None = None
    credit_usd: float | None = None
    expected_value_usd: float | None = None
    expected_value_implied_usd: float | None = None
    model_win_prob: float | None = None
    implied_win_prob: float | None = None
    wedge: float | None = None
    edge: float | None = None
    score: float | None = None
    full_kelly: float | None = None
    binding_constraint: str | None = None
    breakevens: list[float] = Field(default_factory=list)


class ProposedTrade(BaseModel):
    qty: int = Field(default=1, ge=0)
    legs: list[ProposedLeg] = Field(default_factory=list)
    rationale: str = ""
    action: str = ACTION_HOLD
    kind: TradeKind = "option"
    skip: bool = False
    limit_price: float | None = None
    # Collateral the broker will hold for this ticket, which for a defined-risk
    # structure is the same number as its worst case.
    estimated_cost_usd: float | None = None
    max_loss_usd: float | None = None
    analytics: TradeAnalytics | None = None
    sizing: dict[str, Any] | None = None

    @property
    def is_multi_leg(self) -> bool:
        return len(self.legs) > 1

    @property
    def is_closing(self) -> bool:
        return bool(self.legs) and all(
            (leg.position_intent or "").endswith("_to_close") for leg in self.legs
        )


class StrategyContext(BaseModel):
    """Everything observed this cycle, already normalised.

    The loop does the fetching so the strategy stays a function of data: the whole
    decision surface can then be replayed offline from a saved context.
    """

    model_config = {"arbitrary_types_allowed": True}

    today: date
    now: datetime
    market_open: bool
    equity: float
    cash: float
    options_buying_power: float = 0.0
    universe: list[str] = Field(default_factory=list)
    spots: dict[str, float] = Field(default_factory=dict)
    signals: dict[str, UnderlyingSignal] = Field(default_factory=dict)
    chains: dict[str, list[OptionCandidate]] = Field(default_factory=dict)
    portfolio: PortfolioRisk | None = None
    positions: list[Any] = Field(default_factory=list)
    quotes: dict[str, OptionCandidate] = Field(default_factory=dict)
    # Set by the account guard. The engine may always manage and exit; opening new
    # risk is a privilege the account can withdraw without trapping the book.
    new_risk_allowed: bool = True
    flatten_required: bool = False


class Strategy(Protocol):
    name: str

    def propose(self, context: StrategyContext) -> ProposedTrade: ...
