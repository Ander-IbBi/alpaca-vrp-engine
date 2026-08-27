"""Shared strategy vocabulary.

Everything downstream (risk, orders, journal, UI) speaks `ProposedTrade`, so the
strategy itself can be swapped after the kickoff brief without touching the rest.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Side = Literal["buy", "sell"]
TradeKind = Literal["option", "equity"]


class ProposedLeg(BaseModel):
    """One OCC contract inside a ticket (single-leg tickets have exactly one)."""

    symbol: str
    side: Side
    ratio_qty: int = Field(default=1, ge=1)
    # Alpaca uses this to tell opening from closing trades; it also documents intent.
    position_intent: str | None = None


class ProposedTrade(BaseModel):
    """A trade the strategy wants to make. Not yet approved, not yet sent."""

    qty: int = Field(default=1, ge=0)
    legs: list[ProposedLeg] = Field(default_factory=list)
    rationale: str = ""
    # Cash actually at stake, used by the risk layer as the sizing check.
    estimated_cost_usd: float | None = None
    max_loss_usd: float | None = None
    limit_price: float | None = None
    skip: bool = False
    # Equity seeds are stock tickets; they use a separate notional cap.
    kind: TradeKind = "option"
    # Long shares backing any short call in this ticket (collar coverage).
    covering_shares: float | None = None

    @property
    def is_multi_leg(self) -> bool:
        return len(self.legs) > 1


class StrategyContext(BaseModel):
    """Snapshot handed to the strategy. Plain data keeps strategies testable."""

    model_config = {"arbitrary_types_allowed": True}

    today: date
    market_open: bool
    equity: float
    cash: float
    underlyings: list[str] = Field(default_factory=list)
    equity_positions: list[Any] = Field(default_factory=list)
    option_positions: list[Any] = Field(default_factory=list)
    spot_prices: dict[str, float] = Field(default_factory=dict)


class Strategy(Protocol):
    """Contract every strategy module must satisfy."""

    name: str

    def propose(self, context: StrategyContext) -> ProposedTrade: ...
