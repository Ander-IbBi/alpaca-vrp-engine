"""Legacy unwind: recognise inherited positions and clear them in a safe order."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TODAY, make_candidate, occ_symbol

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER
from vrp_engine.risk.portfolio import OptionHolding, ShareHolding
from vrp_engine.strategy.base import ACTION_UNWIND
from vrp_engine.strategy.reset import (
    LEGACY_DTE_SLACK,
    is_legacy_option,
    legacy_options,
    legacy_shares,
    next_unwind_action,
)

MAX_DTE = 9
ENGINE_EXPIRY = TODAY + timedelta(days=7)
LEGACY_EXPIRY = TODAY + timedelta(days=21)


def _option(
    *,
    kind: str = "call",
    strike: float = 789.0,
    contracts: float = -1.0,
    expiration=LEGACY_EXPIRY,
    price: float = 12.0,
) -> OptionHolding:
    return OptionHolding(
        symbol=occ_symbol("SPY", expiration, kind, strike),
        underlying="SPY",
        option_type=kind,
        strike=strike,
        expiration=expiration,
        contracts=contracts,
        market_value=contracts * CONTRACT_MULTIPLIER * price,
        avg_entry_price=price,
        current_price=price,
    )


def _shares(count: float = 100.0) -> ShareHolding:
    return ShareHolding(
        symbol="SPY", shares=count, market_value=count * 770.88, current_price=770.88
    )


# --- recognising inherited positions ---------------------------------------


def test_an_expiry_well_past_the_window_is_inherited():
    assert is_legacy_option(_option(), today=TODAY, max_dte=MAX_DTE)


def test_an_expiry_inside_the_window_is_the_engines_own():
    assert not is_legacy_option(
        _option(expiration=ENGINE_EXPIRY), today=TODAY, max_dte=MAX_DTE
    )


def test_the_slack_prevents_a_borderline_expiry_from_looking_inherited():
    borderline = TODAY + timedelta(days=MAX_DTE + LEGACY_DTE_SLACK)
    assert not is_legacy_option(
        _option(expiration=borderline), today=TODAY, max_dte=MAX_DTE
    )


def test_one_day_past_the_slack_is_inherited():
    beyond = TODAY + timedelta(days=MAX_DTE + LEGACY_DTE_SLACK + 1)
    assert is_legacy_option(_option(expiration=beyond), today=TODAY, max_dte=MAX_DTE)


def test_only_inherited_options_are_listed():
    holdings = [_option(), _option(expiration=ENGINE_EXPIRY, strike=490.0)]
    stale = legacy_options(holdings, today=TODAY, max_dte=MAX_DTE)
    assert len(stale) == 1
    assert stale[0].expiration == LEGACY_EXPIRY


def test_closed_positions_are_not_listed():
    assert legacy_options([_option(contracts=0.0)], today=TODAY, max_dte=MAX_DTE) == []


def test_every_long_share_position_counts_as_inherited():
    assert len(legacy_shares([_shares()])) == 1


def test_a_short_share_position_is_not_treated_as_inherited():
    assert legacy_shares([ShareHolding(symbol="SPY", shares=-100.0)]) == []


# --- the unwind sequence ---------------------------------------------------


def _next(options, shares, quotes=None):
    return next_unwind_action(
        options=options,
        shares=shares,
        today=TODAY,
        max_dte=MAX_DTE,
        quotes=quotes or {},
    )


def test_a_clean_book_needs_no_unwind():
    assert _next([], []) is None


def test_an_engine_owned_position_is_left_alone():
    assert _next([_option(expiration=ENGINE_EXPIRY)], []) is None


def test_the_short_option_is_closed_before_the_long_one():
    short = _option(kind="call", strike=789.0, contracts=-1.0)
    long = _option(kind="put", strike=750.0, contracts=1.0)
    trade = _next([long, short], [])
    assert trade.legs[0].symbol == short.symbol
    assert trade.legs[0].position_intent == "buy_to_close"


def test_the_long_option_goes_next():
    long = _option(kind="put", strike=750.0, contracts=1.0)
    trade = _next([long], [])
    assert trade.legs[0].symbol == long.symbol
    assert trade.legs[0].position_intent == "sell_to_close"


def test_shares_are_sold_only_once_the_options_are_gone():
    short = _option(contracts=-1.0)
    assert _next([short], [_shares()]).kind == "option"
    assert _next([], [_shares()]).kind == "equity"


def test_the_share_ticket_sells_the_whole_position():
    trade = _next([], [_shares(100.0)])
    assert trade.qty == 100
    assert trade.legs[0].side == "sell"
    assert trade.action == ACTION_UNWIND


def test_the_share_ticket_reports_the_collateral_it_frees():
    trade = _next([], [_shares(100.0)])
    assert trade.estimated_cost_usd == pytest.approx(100 * 770.88)


def test_an_option_unwind_is_a_single_leg_ticket():
    trade = _next([_option()], [])
    assert len(trade.legs) == 1
    assert trade.is_closing


def test_the_unwind_limit_uses_the_live_mid_when_available():
    holding = _option(price=12.0)
    quotes = {
        holding.symbol: make_candidate(
            kind="call", strike=789.0, expiration=LEGACY_EXPIRY, bid=9.0, ask=9.4
        )
    }
    assert _next([holding], [], quotes).limit_price == pytest.approx(9.2)


def test_the_unwind_limit_falls_back_to_the_brokers_mark():
    assert _next([_option(price=12.0)], []).limit_price == pytest.approx(12.0)


def test_a_worthless_mark_leaves_the_unwind_without_a_limit():
    assert _next([_option(price=0.0)], []).limit_price is None


def test_the_unwind_rationale_explains_itself():
    trade = _next([_option()], [])
    assert "inherited" in trade.rationale
    assert "collateral" in trade.rationale


def test_the_unwind_carries_the_underlying_for_the_journal():
    trade = _next([_option()], [])
    assert trade.analytics.underlying == "SPY"
    assert trade.analytics.dte == 21
