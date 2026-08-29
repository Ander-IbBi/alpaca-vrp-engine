"""OCC parsing, snapshot normalisation and the liquidity gate."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import TODAY, FakePosition, make_candidate

from vrp_engine.alpaca.options import (
    OptionCandidate,
    candidates_from_snapshots,
    expiries_in_window,
    is_option_position,
    market_date,
    parse_occ_symbol,
)


def test_parses_a_standard_put_symbol():
    parsed = parse_occ_symbol("SPY260918P00750000")
    assert parsed is not None
    assert parsed.underlying == "SPY"
    assert parsed.expiration == date(2026, 9, 18)
    assert parsed.option_type == "put"
    assert parsed.strike == pytest.approx(750.0)


def test_parses_a_standard_call_symbol():
    parsed = parse_occ_symbol("nvda260904c00185500")
    assert parsed is not None
    assert parsed.option_type == "call"
    assert parsed.strike == pytest.approx(185.5)


def test_non_option_symbols_return_none():
    assert parse_occ_symbol("SPY") is None
    assert parse_occ_symbol("") is None
    assert parse_occ_symbol("SPY2609X00750000") is None


def test_option_positions_are_detected_by_symbol_shape():
    assert is_option_position(FakePosition("SPY260918P00750000", -1))


def test_share_positions_are_not_options():
    assert not is_option_position(FakePosition("SPY", 100, asset_class="us_equity"))


def test_option_positions_are_detected_by_asset_class():
    class Odd:
        symbol = "WEIRD"
        asset_class = "us_option"

    assert is_option_position(Odd())


def test_market_date_uses_eastern_time():
    from datetime import UTC, datetime

    # 01:30 UTC on the 1st is still the previous evening in New York.
    assert market_date(datetime(2026, 9, 1, 1, 30, tzinfo=UTC)) == date(2026, 8, 31)


def test_snapshots_become_candidates_with_greeks():
    snapshots = {
        "SPY260907P00495000": {
            "latest_quote": {"bid_price": 1.0, "ask_price": 1.2},
            "greeks": {"delta": -0.21, "gamma": 0.02, "theta": -0.3, "vega": 0.4},
            "implied_volatility": 0.24,
        }
    }
    candidates = candidates_from_snapshots(snapshots, underlying="SPY")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.delta == pytest.approx(-0.21)
    assert candidate.vega == pytest.approx(0.4)
    assert candidate.implied_volatility == pytest.approx(0.24)
    assert candidate.mid_price == pytest.approx(1.1)


def test_snapshots_for_other_underlyings_are_ignored():
    snapshots = {"QQQ260907P00400000": {"latest_quote": {"bid_price": 1, "ask_price": 2}}}
    assert candidates_from_snapshots(snapshots, underlying="SPY") == []


def test_snapshots_accept_short_quote_keys():
    snapshots = {"SPY260907C00505000": {"latestQuote": {"bp": 2.0, "ap": 2.4}}}
    candidate = candidates_from_snapshots(snapshots, underlying="SPY")[0]
    assert candidate.mid_price == pytest.approx(2.2)


def test_missing_quote_leaves_mid_none():
    candidate = OptionCandidate(
        symbol="SPY260907P00495000",
        underlying="SPY",
        option_type="put",
        strike=495.0,
        expiration=date(2026, 9, 7),
    )
    assert candidate.mid_price is None
    assert candidate.spread_fraction is None


def test_spread_fraction_is_relative_to_the_mid():
    candidate = make_candidate(bid=1.0, ask=1.2)
    assert candidate.spread_fraction == pytest.approx(0.2 / 1.1)


def test_tradable_rejects_a_missing_bid():
    candidate = make_candidate(bid=0.0, ask=0.5)
    assert not candidate.tradable(max_spread_fraction=0.5)


def test_tradable_rejects_a_wide_market():
    candidate = make_candidate(bid=1.0, ask=2.0)
    assert not candidate.tradable(max_spread_fraction=0.08)


def test_tradable_accepts_a_tight_market():
    candidate = make_candidate(bid=1.00, ask=1.04)
    assert candidate.tradable(max_spread_fraction=0.08)


def test_open_interest_gate_applies_only_when_known():
    known = make_candidate(bid=1.0, ask=1.02, open_interest=10)
    unknown = make_candidate(bid=1.0, ask=1.02, open_interest=None)
    assert not known.tradable(max_spread_fraction=0.08, min_open_interest=200)
    assert unknown.tradable(max_spread_fraction=0.08, min_open_interest=200)


def test_dte_counts_calendar_days():
    candidate = make_candidate(expiration=TODAY + timedelta(days=4))
    assert candidate.dte(TODAY) == 4


def test_expiries_in_window_filters_and_sorts():
    candidates = [
        make_candidate(expiration=TODAY + timedelta(days=days)) for days in (2, 30, 5, 1, 0)
    ]
    assert expiries_in_window(candidates, today=TODAY, min_dte=1, max_dte=9) == [
        TODAY + timedelta(days=1),
        TODAY + timedelta(days=2),
        TODAY + timedelta(days=5),
    ]


def test_is_call_property():
    assert make_candidate(kind="call").is_call
    assert not make_candidate(kind="put").is_call
