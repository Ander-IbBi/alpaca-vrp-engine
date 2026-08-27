from options_agent.alpaca.orders import build_order_request
from options_agent.journal import Journal
from options_agent.strategy.base import ProposedLeg, ProposedTrade


def test_single_leg_builds_a_simple_market_order() -> None:
    proposal = ProposedTrade(
        qty=2,
        legs=[
            ProposedLeg(
                symbol="SPY260918P00570000",
                side="buy",
                position_intent="buy_to_open",
            )
        ],
    )
    request = build_order_request(proposal)
    payload = request.model_dump(exclude_none=True, mode="json")
    assert payload["symbol"] == "SPY260918P00570000"
    assert float(payload["qty"]) == 2
    assert payload["side"] == "buy"


def test_multi_leg_builds_an_mleg_ticket() -> None:
    proposal = ProposedTrade(
        qty=1,
        legs=[
            ProposedLeg(symbol="SPY260918P00570000", side="buy"),
            ProposedLeg(symbol="SPY260918P00540000", side="sell"),
        ],
    )
    request = build_order_request(proposal)
    payload = request.model_dump(exclude_none=True, mode="json")
    assert payload["order_class"] == "mleg"
    assert len(payload["legs"]) == 2


def test_multi_leg_limit_order_uses_net_price() -> None:
    proposal = ProposedTrade(
        qty=1,
        limit_price=1.40,
        legs=[
            ProposedLeg(symbol="SPY260918P00750000", side="buy"),
            ProposedLeg(symbol="SPY260918C00790000", side="sell"),
        ],
    )
    request = build_order_request(proposal)
    payload = request.model_dump(exclude_none=True, mode="json")
    assert payload["order_class"] == "mleg"
    assert payload["limit_price"] == 1.40
    assert len(payload["legs"]) == 2


def test_equity_seed_builds_a_stock_market_order() -> None:
    proposal = ProposedTrade(
        qty=100,
        kind="equity",
        legs=[ProposedLeg(symbol="SPY", side="buy")],
        estimated_cost_usd=77_000.0,
    )
    request = build_order_request(proposal)
    payload = request.model_dump(exclude_none=True, mode="json")
    assert payload["symbol"] == "SPY"
    assert float(payload["qty"]) == 100
    assert payload["side"] == "buy"
    assert "legs" not in payload
    assert "limit_price" not in payload


def test_journal_round_trip(tmp_path) -> None:
    journal = Journal(tmp_path / "agent.jsonl")
    journal.append("cycle", {"note": "first"})
    journal.append("cycle", {"note": "second"})

    entries = journal.tail(5)
    assert [e["note"] for e in entries] == ["first", "second"]
    assert all("ts" in e for e in entries)


def test_journal_survives_a_corrupt_line(tmp_path) -> None:
    path = tmp_path / "agent.jsonl"
    journal = Journal(path)
    journal.append("cycle", {"note": "good"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    assert len(journal.read_all()) == 1
