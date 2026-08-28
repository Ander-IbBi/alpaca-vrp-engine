"""Translate an approved proposal into an Alpaca order.

Equity seeds become stock tickets. Option collars become single-leg or multi-leg
day orders. Nothing here decides *whether* to trade; that already happened in `risk/`.
"""

from __future__ import annotations

from typing import Any

from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    PositionIntent,
    TimeInForce,
)
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    OptionLegRequest,
)

from options_agent.alpaca.client import PaperAlpaca
from options_agent.strategy.base import ProposedLeg, ProposedTrade


def _side(leg: ProposedLeg) -> OrderSide:
    return OrderSide.BUY if leg.side == "buy" else OrderSide.SELL


def _intent(leg: ProposedLeg) -> PositionIntent | None:
    if not leg.position_intent:
        return None
    return PositionIntent(leg.position_intent)


def build_order_request(proposal: ProposedTrade) -> MarketOrderRequest | LimitOrderRequest:
    """Equity seeds are simple stock tickets; option legs stay day orders."""
    if not proposal.legs:
        raise ValueError("cannot build an order without legs")

    if proposal.kind == "equity":
        leg = proposal.legs[0]
        return MarketOrderRequest(
            symbol=leg.symbol,
            qty=int(proposal.qty),
            side=_side(leg),
            time_in_force=TimeInForce.DAY,
        )

    # Options only support day orders on Alpaca.
    common: dict[str, Any] = {"qty": int(proposal.qty), "time_in_force": TimeInForce.DAY}

    if proposal.is_multi_leg:
        common["order_class"] = OrderClass.MLEG
        common["legs"] = [
            OptionLegRequest(
                symbol=leg.symbol,
                ratio_qty=leg.ratio_qty,
                side=_side(leg),
                position_intent=_intent(leg),
            )
            for leg in proposal.legs
        ]
    else:
        leg = proposal.legs[0]
        common["symbol"] = leg.symbol
        common["side"] = _side(leg)
        intent = _intent(leg)
        if intent is not None:
            common["position_intent"] = intent

    if proposal.limit_price is not None:
        return LimitOrderRequest(limit_price=proposal.limit_price, **common)
    return MarketOrderRequest(**common)


def submit_proposal(
    client: PaperAlpaca,
    proposal: ProposedTrade,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Send the ticket to the paper account, or describe what would be sent."""
    request = build_order_request(proposal)
    payload = request.model_dump(exclude_none=True, mode="json")
    if dry_run:
        return {"submitted": False, "dry_run": True, "request": payload}

    order = client.trading.submit_order(request)
    return {
        "submitted": True,
        "dry_run": False,
        "request": payload,
        "order_id": str(order.id),
        "status": str(order.status),
    }
