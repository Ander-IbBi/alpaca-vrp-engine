"""Order building: Alpaca's multi-leg rules, encoded as local errors."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TODAY, FakeAlpaca, occ_symbol

from vrp_engine.alpaca.orders import (
    OrderBuildError,
    build_order_request,
    submit_proposal,
)
from vrp_engine.strategy.base import (
    ACTION_CLOSE,
    ACTION_OPEN,
    ProposedLeg,
    ProposedTrade,
)

EXPIRY = TODAY + timedelta(days=7)


def _leg(kind: str, strike: float, side: str, intent: str | None = None) -> ProposedLeg:
    return ProposedLeg(
        symbol=occ_symbol("SPY", EXPIRY, kind, strike), side=side, position_intent=intent
    )


def _open_spread(*, qty: int = 2, limit: float | None = 2.0) -> ProposedTrade:
    return ProposedTrade(
        qty=qty,
        legs=[
            _leg("put", 490, "sell", "sell_to_open"),
            _leg("put", 485, "buy", "buy_to_open"),
        ],
        action=ACTION_OPEN,
        limit_price=limit,
    )


def _close_spread(*, qty: int = 2) -> ProposedTrade:
    return ProposedTrade(
        qty=qty,
        legs=[
            _leg("put", 490, "buy", "buy_to_close"),
            _leg("put", 485, "sell", "sell_to_close"),
        ],
        action=ACTION_CLOSE,
        limit_price=0.6,
    )


# --- guards -----------------------------------------------------------------


def test_a_proposal_without_legs_cannot_be_built():
    with pytest.raises(OrderBuildError):
        build_order_request(ProposedTrade(qty=1))


def test_a_zero_quantity_proposal_cannot_be_built():
    with pytest.raises(OrderBuildError):
        build_order_request(_open_spread(qty=0))


def test_mixing_opening_and_closing_legs_is_refused():
    proposal = ProposedTrade(
        qty=1,
        legs=[
            _leg("put", 490, "buy", "buy_to_close"),
            _leg("put", 480, "sell", "sell_to_open"),
        ],
        limit_price=1.0,
    )
    with pytest.raises(OrderBuildError, match="cannot both open and close"):
        build_order_request(proposal)


def test_a_single_leg_ticket_is_not_subject_to_the_roll_check():
    proposal = ProposedTrade(
        qty=1, legs=[_leg("put", 490, "buy", "buy_to_close")], limit_price=1.0
    )
    assert build_order_request(proposal) is not None


def test_an_equity_ticket_with_two_symbols_is_refused():
    proposal = ProposedTrade(
        qty=100,
        kind="equity",
        legs=[
            ProposedLeg(symbol="SPY", side="sell"),
            ProposedLeg(symbol="QQQ", side="sell"),
        ],
    )
    with pytest.raises(OrderBuildError, match="exactly one symbol"):
        build_order_request(proposal)


# --- opening tickets --------------------------------------------------------


def test_a_two_leg_open_becomes_a_multi_leg_limit_order():
    request = build_order_request(_open_spread())
    payload = request.model_dump(exclude_none=True, mode="json")
    assert payload["order_class"] == "mleg"
    assert len(payload["legs"]) == 2
    assert payload["limit_price"] == 2.0


def test_the_quantity_is_the_contract_count():
    payload = build_order_request(_open_spread(qty=4)).model_dump(exclude_none=True, mode="json")
    assert float(payload["qty"]) == 4


def test_options_go_out_as_day_orders():
    payload = build_order_request(_open_spread()).model_dump(exclude_none=True, mode="json")
    assert payload["time_in_force"] == "day"


def test_leg_sides_and_intents_survive_the_translation():
    payload = build_order_request(_open_spread()).model_dump(exclude_none=True, mode="json")
    by_symbol = {leg["symbol"]: leg for leg in payload["legs"]}
    short = by_symbol[occ_symbol("SPY", EXPIRY, "put", 490)]
    long = by_symbol[occ_symbol("SPY", EXPIRY, "put", 485)]
    assert short["side"] == "sell"
    assert short["position_intent"] == "sell_to_open"
    assert long["side"] == "buy"
    assert long["position_intent"] == "buy_to_open"


def test_a_four_leg_condor_builds_one_ticket():
    proposal = ProposedTrade(
        qty=1,
        legs=[
            _leg("put", 490, "sell", "sell_to_open"),
            _leg("put", 485, "buy", "buy_to_open"),
            _leg("call", 510, "sell", "sell_to_open"),
            _leg("call", 515, "buy", "buy_to_open"),
        ],
        limit_price=3.0,
    )
    payload = build_order_request(proposal).model_dump(exclude_none=True, mode="json")
    assert len(payload["legs"]) == 4


def test_a_leg_ratio_is_carried_through():
    proposal = ProposedTrade(
        qty=1,
        legs=[
            ProposedLeg(
                symbol=occ_symbol("SPY", EXPIRY, "put", 490),
                side="sell",
                ratio_qty=1,
                position_intent="sell_to_open",
            ),
            ProposedLeg(
                symbol=occ_symbol("SPY", EXPIRY, "put", 485),
                side="buy",
                ratio_qty=2,
                position_intent="buy_to_open",
            ),
        ],
        limit_price=1.0,
    )
    payload = build_order_request(proposal).model_dump(exclude_none=True, mode="json")
    ratios = {leg["symbol"]: leg["ratio_qty"] for leg in payload["legs"]}
    assert float(ratios[occ_symbol("SPY", EXPIRY, "put", 485)]) == 2


def test_without_a_limit_price_the_ticket_is_a_market_order():
    payload = build_order_request(_open_spread(limit=None)).model_dump(
        exclude_none=True, mode="json"
    )
    assert payload["type"] == "market"


def test_a_zero_limit_price_falls_back_to_a_market_order():
    payload = build_order_request(_open_spread(limit=0.0)).model_dump(
        exclude_none=True, mode="json"
    )
    assert payload["type"] == "market"


def test_a_single_leg_option_ticket_names_its_symbol_directly():
    proposal = ProposedTrade(
        qty=1, legs=[_leg("put", 490, "buy", "buy_to_close")], limit_price=1.2
    )
    payload = build_order_request(proposal).model_dump(exclude_none=True, mode="json")
    assert payload["symbol"] == occ_symbol("SPY", EXPIRY, "put", 490)
    assert "legs" not in payload
    assert payload["position_intent"] == "buy_to_close"


def test_a_leg_without_an_intent_omits_it():
    proposal = ProposedTrade(qty=1, legs=[_leg("put", 490, "buy")], limit_price=1.2)
    payload = build_order_request(proposal).model_dump(exclude_none=True, mode="json")
    assert "position_intent" not in payload


# --- closing tickets --------------------------------------------------------


def test_a_close_is_its_own_all_to_close_ticket():
    payload = build_order_request(_close_spread()).model_dump(exclude_none=True, mode="json")
    intents = {leg["position_intent"] for leg in payload["legs"]}
    assert intents == {"buy_to_close", "sell_to_close"}


def test_a_close_is_still_a_multi_leg_order():
    payload = build_order_request(_close_spread()).model_dump(exclude_none=True, mode="json")
    assert payload["order_class"] == "mleg"


# --- equity tickets ---------------------------------------------------------


def test_an_equity_sale_is_a_plain_market_order():
    proposal = ProposedTrade(
        qty=100, kind="equity", legs=[ProposedLeg(symbol="SPY", side="sell")]
    )
    payload = build_order_request(proposal).model_dump(exclude_none=True, mode="json")
    assert payload["symbol"] == "SPY"
    assert payload["side"] == "sell"
    assert float(payload["qty"]) == 100
    assert "legs" not in payload


def test_an_equity_ticket_ignores_a_limit_price():
    # Shares are only ever sold to free collateral; a resting limit would defeat that.
    proposal = ProposedTrade(
        qty=100,
        kind="equity",
        legs=[ProposedLeg(symbol="SPY", side="sell")],
        limit_price=500.0,
    )
    payload = build_order_request(proposal).model_dump(exclude_none=True, mode="json")
    assert payload["type"] == "market"


# --- submission -------------------------------------------------------------


def test_a_dry_run_describes_the_ticket_without_sending_it(settings):
    client = FakeAlpaca(settings=settings)
    result = submit_proposal(client, _open_spread(), dry_run=True)
    assert result["submitted"] is False
    assert result["dry_run"] is True
    assert result["request"]["order_class"] == "mleg"
    assert client.trading.submitted == []


def test_executing_reaches_the_trading_client(settings):
    client = FakeAlpaca(settings=settings)
    result = submit_proposal(client, _open_spread(), dry_run=False)
    assert result["submitted"] is True
    assert len(client.trading.submitted) == 1
    assert result["order_id"] == "order-1"


def test_a_submitted_ticket_reports_the_symbols_to_reconcile(settings):
    client = FakeAlpaca(settings=settings)
    result = submit_proposal(client, _open_spread(), dry_run=False)
    assert result["expected_symbols"] == [leg.symbol for leg in _open_spread().legs]


def test_a_dry_run_does_not_promise_symbols(settings):
    client = FakeAlpaca(settings=settings)
    assert "expected_symbols" not in submit_proposal(client, _open_spread(), dry_run=True)


def test_an_invalid_proposal_never_reaches_the_broker(settings):
    client = FakeAlpaca(settings=settings)
    with pytest.raises(OrderBuildError):
        submit_proposal(client, ProposedTrade(qty=1), dry_run=False)
    assert client.trading.submitted == []
