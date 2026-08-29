"""Translate an approved proposal into an Alpaca order.

Nothing here decides *whether* to trade; that already happened in `risk/`. What this
module does encode are three hard facts about Alpaca's options API that shape the whole
strategy:

- A multi-leg order is accepted only when every leg is covered inside that same order.
  So opening and closing are separate tickets and a "roll in one order" is never built.
- A multi-leg order may not contain an equity leg. Inherited shares are sold on their
  own ticket.
- Options are day orders only.

Structures go out as limit orders at the net mid. A market order on a four-leg condor
is how a good edge becomes a bad fill.
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

from vrp_engine.alpaca.client import PaperAlpaca
from vrp_engine.strategy.base import ProposedLeg, ProposedTrade


class OrderBuildError(ValueError):
    """The proposal cannot be expressed as a valid Alpaca ticket."""


def _side(leg: ProposedLeg) -> OrderSide:
    return OrderSide.BUY if leg.side == "buy" else OrderSide.SELL


def _intent(leg: ProposedLeg) -> PositionIntent | None:
    if not leg.position_intent:
        return None
    return PositionIntent(leg.position_intent)


def _assert_consistent_intents(proposal: ProposedTrade) -> None:
    """Refuse to mix opening and closing legs in one multi-leg ticket.

    A ticket that closes two legs and opens two others is the roll Alpaca rejects for
    leaving an uncovered short inside the order. Catching it here turns a confusing
    broker error into a clear local one.
    """
    if len(proposal.legs) < 2:
        return
    intents = {(leg.position_intent or "") for leg in proposal.legs}
    opening = {i for i in intents if i.endswith("_to_open")}
    closing = {i for i in intents if i.endswith("_to_close")}
    if opening and closing:
        raise OrderBuildError(
            "a multi-leg ticket cannot both open and close legs; Alpaca requires every "
            "leg to be covered within the same order, so close and re-open separately"
        )


def build_order_request(proposal: ProposedTrade) -> MarketOrderRequest | LimitOrderRequest:
    """Build the alpaca-py request object for an approved proposal."""
    if not proposal.legs:
        raise OrderBuildError("cannot build an order without legs")
    if proposal.qty < 1:
        raise OrderBuildError("cannot build an order for zero contracts")

    if proposal.kind == "equity":
        if len(proposal.legs) != 1:
            raise OrderBuildError("an equity ticket carries exactly one symbol")
        leg = proposal.legs[0]
        return MarketOrderRequest(
            symbol=leg.symbol,
            qty=int(proposal.qty),
            side=_side(leg),
            time_in_force=TimeInForce.DAY,
        )

    _assert_consistent_intents(proposal)

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

    if proposal.limit_price is not None and proposal.limit_price > 0:
        return LimitOrderRequest(limit_price=proposal.limit_price, **common)
    return MarketOrderRequest(**common)


def submit_proposal(
    client: PaperAlpaca,
    proposal: ProposedTrade,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Send the ticket to the paper account, or describe exactly what would be sent."""
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
        "expected_symbols": [leg.symbol for leg in proposal.legs],
    }
