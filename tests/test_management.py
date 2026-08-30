"""The urgency ladder over the open book, plus the flatten path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import NOW, TODAY, make_candidate, occ_symbol

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER
from vrp_engine.config import Settings
from vrp_engine.risk.account import US_EASTERN
from vrp_engine.risk.portfolio import OptionHolding
from vrp_engine.strategy.base import ACTION_CLOSE
from vrp_engine.strategy.management import (
    SAFE_SIGMA_DISTANCE,
    build_exit,
    exit_limit_price,
    flatten_next,
    group_open_structures,
    infer_kind,
    net_premium_to_usd,
    next_management_action,
    total_open_contracts,
)
from vrp_engine.strategy.structures import (
    CALL_CREDIT_SPREAD,
    CALL_DEBIT_SPREAD,
    IRON_CONDOR,
    PUT_CREDIT_SPREAD,
    PUT_DEBIT_SPREAD,
)

EXPIRY = TODAY + timedelta(days=7)
TOMORROW = TODAY + timedelta(days=1)
MIDDAY = datetime(2026, 8, 31, 12, 0, tzinfo=US_EASTERN)
AFTERNOON = datetime(2026, 8, 31, 15, 30, tzinfo=US_EASTERN)


def _settings(**overrides) -> Settings:
    defaults = {
        "alpaca_api_key": "k",
        "alpaca_secret_key": "s",
        "profit_take_credit_pct": 0.55,
        "profit_take_condor_pct": 0.60,
        "profit_take_debit_pct": 1.00,
        "stop_loss_credit_multiple": 2.0,
        "assignment_delta": 0.60,
        "assignment_proximity_pct": 0.005,
        "forced_exit_hour_et": 15,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _leg(
    *,
    kind: str = "put",
    strike: float = 490.0,
    contracts: float = -1.0,
    entry: float = 3.0,
    price: float | None = None,
    pl: float = 0.0,
    expiration=EXPIRY,
    delta: float | None = None,
    underlying: str = "SPY",
) -> OptionHolding:
    mark = entry if price is None else price
    return OptionHolding(
        symbol=occ_symbol(underlying, expiration, kind, strike),
        underlying=underlying,
        option_type=kind,
        strike=strike,
        expiration=expiration,
        contracts=contracts,
        market_value=contracts * CONTRACT_MULTIPLIER * mark,
        avg_entry_price=entry,
        current_price=mark,
        unrealized_pl=pl,
        delta=delta,
    )


def _credit_spread(
    *,
    pl: float = 0.0,
    expiration=EXPIRY,
    short_strike: float = 490.0,
    short_delta: float | None = None,
    underlying: str = "SPY",
) -> list[OptionHolding]:
    """Short the 490 put at 3.00, long the 485 at 1.00: a 200 USD credit per contract."""
    return [
        _leg(
            strike=short_strike,
            contracts=-1.0,
            entry=3.0,
            pl=pl,
            expiration=expiration,
            delta=short_delta,
            underlying=underlying,
        ),
        _leg(
            strike=short_strike - 5,
            contracts=1.0,
            entry=1.0,
            expiration=expiration,
            underlying=underlying,
        ),
    ]


def _manage(structures, *, settings=None, now=MIDDAY, today=TODAY, spots=None, vols=None):
    return next_management_action(
        structures,
        settings=settings or _settings(),
        today=today,
        now=now,
        spots=spots or {"SPY": 500.0},
        vols=vols or {"SPY": 0.20},
        quotes={},
    )


# --- shape inference --------------------------------------------------------


def test_two_puts_for_a_credit_is_a_put_credit_spread():
    legs = _credit_spread()
    assert infer_kind(legs, net_premium=200.0) == PUT_CREDIT_SPREAD


def test_two_puts_for_a_debit_is_a_put_debit_spread():
    assert infer_kind(_credit_spread(), net_premium=-200.0) == PUT_DEBIT_SPREAD


def test_two_calls_for_a_credit_is_a_call_credit_spread():
    legs = [_leg(kind="call", strike=510), _leg(kind="call", strike=515, contracts=1.0)]
    assert infer_kind(legs, net_premium=150.0) == CALL_CREDIT_SPREAD


def test_two_calls_for_a_debit_is_a_call_debit_spread():
    legs = [_leg(kind="call", strike=510), _leg(kind="call", strike=515, contracts=1.0)]
    assert infer_kind(legs, net_premium=-150.0) == CALL_DEBIT_SPREAD


def test_four_legs_for_a_credit_is_an_iron_condor():
    legs = [
        *_credit_spread(),
        _leg(kind="call", strike=510, contracts=-1.0, entry=2.0),
        _leg(kind="call", strike=515, contracts=1.0, entry=1.0),
    ]
    assert infer_kind(legs, net_premium=300.0) == IRON_CONDOR


def test_an_unrecognised_shape_is_labelled_custom():
    assert infer_kind([_leg()], net_premium=300.0) == "custom"


# --- grouping ---------------------------------------------------------------


def test_legs_group_by_underlying_and_expiry():
    legs = [*_credit_spread(), *_credit_spread(expiration=TOMORROW)]
    structures = group_open_structures(legs)
    assert len(structures) == 2
    assert {s.expiration for s in structures} == {EXPIRY, TOMORROW}


def test_grouping_ignores_closed_legs():
    assert group_open_structures([_leg(contracts=0.0)]) == []


def test_the_closable_size_is_the_thinnest_leg():
    legs = [
        _leg(strike=490, contracts=-5.0, entry=3.0),
        _leg(strike=485, contracts=2.0, entry=1.0),
    ]
    assert group_open_structures(legs)[0].contracts == 2


def test_the_net_premium_of_a_credit_spread_is_positive():
    structure = group_open_structures(_credit_spread())[0]
    assert structure.net_premium_usd == pytest.approx(200.0)
    assert structure.is_credit


def test_the_net_premium_of_a_debit_spread_is_negative():
    legs = [
        _leg(kind="call", strike=500, contracts=1.0, entry=8.0),
        _leg(kind="call", strike=510, contracts=-1.0, entry=3.0),
    ]
    structure = group_open_structures(legs)[0]
    assert structure.net_premium_usd < 0
    assert not structure.is_credit


def test_capture_fraction_is_pnl_over_the_premium():
    structure = group_open_structures(_credit_spread(pl=100.0))[0]
    assert structure.capture_fraction == pytest.approx(0.5)


def test_capture_fraction_is_zero_without_a_premium():
    legs = [
        _leg(strike=490, contracts=-1.0, entry=1.0),
        _leg(strike=485, contracts=1.0, entry=1.0),
    ]
    assert group_open_structures(legs)[0].capture_fraction == 0.0


def test_short_legs_are_the_negative_ones():
    structure = group_open_structures(_credit_spread())[0]
    assert len(structure.short_legs) == 1
    assert structure.short_legs[0].strike == 490.0


def test_dte_and_description_read_cleanly():
    structure = group_open_structures(_credit_spread())[0]
    assert structure.dte(TODAY) == 7
    assert "SPY" in structure.describe()
    assert EXPIRY.isoformat() in structure.describe()


def test_open_contracts_are_summed_per_symbol():
    legs = [*_credit_spread(), *_credit_spread()]
    totals = total_open_contracts(legs)
    assert totals[occ_symbol("SPY", EXPIRY, "put", 490)] == -2.0
    assert totals[occ_symbol("SPY", EXPIRY, "put", 485)] == 2.0


def test_net_premium_converts_a_per_share_price_to_dollars():
    assert net_premium_to_usd(2.0, 3) == pytest.approx(600.0)


# --- exit tickets -----------------------------------------------------------


def test_an_exit_reverses_every_leg():
    structure = group_open_structures(_credit_spread())[0]
    exit_trade = build_exit(structure, quotes={}, reason="test", today=TODAY)
    by_symbol = {leg.symbol: leg for leg in exit_trade.legs}
    assert by_symbol[occ_symbol("SPY", EXPIRY, "put", 490)].side == "buy"
    assert by_symbol[occ_symbol("SPY", EXPIRY, "put", 485)].side == "sell"


def test_every_exit_leg_is_marked_to_close():
    structure = group_open_structures(_credit_spread())[0]
    exit_trade = build_exit(structure, quotes={}, reason="test", today=TODAY)
    assert exit_trade.is_closing
    assert exit_trade.action == ACTION_CLOSE


def test_an_exit_carries_the_reason_into_its_rationale():
    structure = group_open_structures(_credit_spread())[0]
    exit_trade = build_exit(structure, quotes={}, reason="profit target hit", today=TODAY)
    assert "profit target hit" in exit_trade.rationale


def test_the_exit_limit_is_the_net_cost_of_closing():
    # Short leg marked 2.00, long leg 0.50: closing the spread costs 1.50 net.
    legs = [
        _leg(strike=490, contracts=-1.0, entry=3.0, price=2.0),
        _leg(strike=485, contracts=1.0, entry=1.0, price=0.5),
    ]
    structure = group_open_structures(legs)[0]
    assert exit_limit_price(structure, {}) == pytest.approx(1.5)


def test_a_live_chain_quote_overrides_the_brokers_mark():
    legs = [
        _leg(strike=490, contracts=-1.0, entry=3.0, price=2.0),
        _leg(strike=485, contracts=1.0, entry=1.0, price=0.5),
    ]
    structure = group_open_structures(legs)[0]
    quotes = {
        occ_symbol("SPY", EXPIRY, "put", 490): make_candidate(
            strike=490.0, expiration=EXPIRY, bid=0.9, ask=1.1
        )
    }
    assert exit_limit_price(structure, quotes) == pytest.approx(0.5)


def test_a_zero_mark_leaves_the_exit_without_a_limit():
    legs = [
        _leg(strike=490, contracts=-1.0, entry=3.0, price=0.0),
        _leg(strike=485, contracts=1.0, entry=1.0, price=0.5),
    ]
    structure = group_open_structures(legs)[0]
    assert exit_limit_price(structure, {}) is None


def test_the_exit_quantity_matches_the_structure_size():
    legs = [
        _leg(strike=490, contracts=-4.0, entry=3.0),
        _leg(strike=485, contracts=4.0, entry=1.0),
    ]
    structure = group_open_structures(legs)[0]
    assert build_exit(structure, quotes={}, reason="x", today=TODAY).qty == 4


# --- the ladder -------------------------------------------------------------


def test_an_empty_book_reports_nothing_to_manage():
    decision = _manage([])
    assert decision.trade is None
    assert decision.checks == ["no open structures to manage"]


def test_a_healthy_position_is_left_alone():
    decision = _manage(group_open_structures(_credit_spread(pl=40.0)))
    assert decision.trade is None
    assert any("inside every exit threshold" in check for check in decision.checks)


def test_a_loss_past_the_stop_is_closed():
    # 200 USD credit, 450 USD loss: past the 2x stop.
    decision = _manage(group_open_structures(_credit_spread(pl=-450.0)))
    assert decision.trade is not None
    assert "stop" in decision.trade.rationale


def test_a_loss_inside_the_stop_is_held():
    decision = _manage(group_open_structures(_credit_spread(pl=-300.0)))
    assert decision.trade is None


def test_a_tighter_stop_fires_sooner():
    structures = group_open_structures(_credit_spread(pl=-250.0))
    assert _manage(structures).trade is None
    assert _manage(structures, settings=_settings(stop_loss_credit_multiple=1.0)).trade


def test_the_profit_target_closes_a_credit_spread():
    decision = _manage(group_open_structures(_credit_spread(pl=130.0)))
    assert decision.trade is not None
    assert "captured" in decision.trade.rationale


def test_just_below_the_profit_target_is_held():
    decision = _manage(group_open_structures(_credit_spread(pl=100.0)))
    assert decision.trade is None


def test_a_condor_uses_its_own_higher_target():
    legs = [
        *_credit_spread(pl=170.0),
        _leg(kind="call", strike=510, contracts=-1.0, entry=2.0),
        _leg(kind="call", strike=515, contracts=1.0, entry=1.0),
    ]
    structures = group_open_structures(legs)
    assert structures[0].kind == IRON_CONDOR
    # 300 USD credit, 170 USD captured: 57%, above the 55% vertical target but below
    # the 60% condor target, so the condor must still be held.
    assert _manage(structures).trade is None


def test_a_debit_spread_needs_a_hundred_percent_capture():
    # 500 USD paid, 400 USD gained: a good trade, but not yet a double.
    legs = [
        _leg(kind="call", strike=500, contracts=1.0, entry=8.0, pl=400.0),
        _leg(kind="call", strike=510, contracts=-1.0, entry=3.0),
    ]
    structures = group_open_structures(legs)
    assert not structures[0].is_credit
    assert _manage(structures).trade is None


def test_a_doubled_debit_spread_is_closed():
    legs = [
        _leg(kind="call", strike=500, contracts=1.0, entry=8.0, pl=520.0),
        _leg(kind="call", strike=510, contracts=-1.0, entry=3.0),
    ]
    assert _manage(group_open_structures(legs)).trade is not None


def test_a_pinned_short_strike_on_expiry_day_is_closed():
    structures = group_open_structures(
        _credit_spread(expiration=TOMORROW, short_strike=500.0)
    )
    decision = _manage(structures, today=TODAY, spots={"SPY": 500.5})
    assert decision.trade is not None
    assert "pinned" in decision.trade.rationale


def test_a_high_short_delta_on_expiry_day_is_closed():
    structures = group_open_structures(
        _credit_spread(expiration=TOMORROW, short_strike=495.0, short_delta=-0.72)
    )
    decision = _manage(structures, today=TODAY, spots={"SPY": 500.0})
    assert decision.trade is not None
    assert "delta reached" in decision.trade.rationale


def test_assignment_risk_is_ignored_while_expiry_is_still_far_away():
    structures = group_open_structures(
        _credit_spread(short_strike=500.0, short_delta=-0.72)
    )
    assert _manage(structures, spots={"SPY": 500.1}).trade is None


def test_the_last_session_closes_anything_not_safely_out_of_the_money():
    structures = group_open_structures(
        _credit_spread(expiration=TOMORROW, short_strike=495.0)
    )
    decision = _manage(structures, now=AFTERNOON, today=TODAY, spots={"SPY": 500.0})
    assert decision.trade is not None
    assert "two sigma" in decision.trade.rationale


def test_a_position_far_out_of_the_money_is_allowed_to_expire():
    structures = group_open_structures(
        _credit_spread(expiration=TOMORROW, short_strike=400.0)
    )
    decision = _manage(
        structures, now=AFTERNOON, today=TODAY, spots={"SPY": 500.0}, vols={"SPY": 0.15}
    )
    assert decision.trade is None
    assert any("two sigma out of the money" in check for check in decision.checks)


def test_the_forced_exit_only_applies_after_its_hour():
    structures = group_open_structures(
        _credit_spread(expiration=TOMORROW, short_strike=495.0)
    )
    assert _manage(structures, now=MIDDAY, today=TODAY).trade is None


def test_the_forced_exit_hour_is_read_in_eastern_time_not_utc():
    """The live loop stamps cycles in UTC; 15:00 UTC is late morning in New York."""
    structures = group_open_structures(
        _credit_spread(expiration=TOMORROW, short_strike=495.0)
    )
    late_morning_et = datetime(2026, 8, 31, 15, 30, tzinfo=UTC)  # 11:30 in New York
    assert _manage(structures, now=late_morning_et, today=TODAY).trade is None

    afternoon_et = datetime(2026, 8, 31, 19, 30, tzinfo=UTC)  # 15:30 in New York
    assert _manage(structures, now=afternoon_et, today=TODAY).trade is not None


def test_the_safe_distance_is_two_sigma():
    assert SAFE_SIGMA_DISTANCE == 2.0


def test_the_worst_position_is_handled_first():
    legs = [
        *_credit_spread(pl=-450.0, underlying="SPY"),
        *_credit_spread(pl=150.0, underlying="QQQ"),
    ]
    decision = _manage(
        group_open_structures(legs), spots={"SPY": 500.0, "QQQ": 500.0}, vols={"SPY": 0.2}
    )
    assert decision.trade is not None
    assert decision.trade.analytics.underlying == "SPY"


def test_only_one_action_is_returned_per_cycle():
    legs = [
        *_credit_spread(pl=-450.0, underlying="SPY"),
        *_credit_spread(pl=-450.0, underlying="QQQ"),
    ]
    decision = _manage(group_open_structures(legs), spots={"SPY": 500.0, "QQQ": 500.0})
    assert decision.trade is not None
    assert len({leg.symbol[:3] for leg in decision.trade.legs}) == 1


# --- flatten ----------------------------------------------------------------


def test_flatten_picks_the_biggest_loser():
    legs = [
        *_credit_spread(pl=-800.0, underlying="SPY"),
        *_credit_spread(pl=-100.0, underlying="QQQ"),
    ]
    trade = flatten_next(
        group_open_structures(legs), quotes={}, today=TODAY, reason="breaker fired"
    )
    assert trade is not None
    assert trade.analytics.underlying == "SPY"
    assert "breaker fired" in trade.rationale


def test_flatten_on_an_empty_book_is_a_no_op():
    assert flatten_next([], quotes={}, today=TODAY, reason="x") is None


def test_a_flatten_ticket_is_a_close():
    trade = flatten_next(
        group_open_structures(_credit_spread()), quotes={}, today=TODAY, reason="x"
    )
    assert trade.is_closing


def test_management_uses_the_wall_clock_hour_in_eastern_time():
    # NOW in the shared fixtures is 10:30 New York, which is before the forced-exit hour.
    assert NOW.astimezone(US_EASTERN).hour == 10
