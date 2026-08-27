from datetime import date
from types import SimpleNamespace

from options_agent.alpaca.options import (
    OptionCandidate,
    candidates_from_snapshots,
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
