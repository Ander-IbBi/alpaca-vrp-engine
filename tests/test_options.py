from datetime import date
from types import SimpleNamespace

from options_agent.alpaca.options import (
    OptionCandidate,
    candidates_from_snapshots,
    fetch_quoted_chain,
    parse_occ_symbol,
)


def test_parse_occ_put() -> None:
    parsed = parse_occ_symbol("SPY260918P00750000")
    assert parsed is not None
    assert parsed.underlying == "SPY"
    assert parsed.expiration == date(2026, 9, 18)
    assert parsed.option_type == "put"
    assert parsed.strike == 750.0


def test_parse_occ_call() -> None:
    parsed = parse_occ_symbol("spy260918c00790000")
    assert parsed is not None
    assert parsed.option_type == "call"
    assert parsed.strike == 790.0


def test_parse_occ_rejects_stock_ticker() -> None:
    assert parse_occ_symbol("SPY") is None


def test_candidates_from_snapshots_attach_quotes_and_greeks() -> None:
    snapshot = SimpleNamespace(
        latest_quote=SimpleNamespace(bid_price=3.2, ask_price=3.3),
        greeks=SimpleNamespace(delta=-0.20),
        implied_volatility=0.15,
    )
    candidates = candidates_from_snapshots(
        {"SPY260918P00750000": snapshot},
        underlying="SPY",
    )
    assert len(candidates) == 1
    chosen = candidates[0]
    assert chosen.bid == 3.2
    assert chosen.ask == 3.3
    assert chosen.mid_price == 3.25
    assert chosen.delta == -0.20
    assert chosen.implied_volatility == 0.15
    assert chosen.option_type == "put"
    assert chosen.strike == 750.0


def test_spread_fraction() -> None:
    candidate = OptionCandidate(
        symbol="SPY260918P00750000",
        underlying="SPY",
        option_type="put",
        strike=750,
        expiration=date(2026, 9, 18),
        bid=4.0,
        ask=6.0,
    )
    assert candidate.spread_fraction == 0.4


def test_fetch_quoted_chain_widens_when_the_window_is_empty() -> None:
    snapshot = SimpleNamespace(
        latest_quote=SimpleNamespace(bid_price=3.2, ask_price=3.3),
        greeks=SimpleNamespace(delta=-0.20),
        implied_volatility=0.15,
    )

    class FakeData:
        def __init__(self) -> None:
            self.calls = 0

        def get_option_chain(self, request):
            self.calls += 1
            if self.calls == 1:
                return {}
            return {"SPY260918P00750000": snapshot}

    client = SimpleNamespace(option_data=FakeData())
    found = fetch_quoted_chain(client, "SPY", today=date(2026, 8, 27))  # type: ignore[arg-type]
    assert len(found) == 1
    assert client.option_data.calls == 2


def test_fetch_quoted_chain_survives_a_null_payload() -> None:
    class FakeData:
        def get_option_chain(self, request):
            return None

    found = fetch_quoted_chain(
        SimpleNamespace(option_data=FakeData()),  # type: ignore[arg-type]
        "SPY",
        today=date(2026, 8, 27),
    )
    assert found == []
