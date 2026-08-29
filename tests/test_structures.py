"""Structure construction: shapes, widths, and the arithmetic that bounds the risk."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TODAY, build_chain, build_history, make_candidate

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER
from vrp_engine.strategy.signals import (
    STANCE_BUY_VOL,
    STANCE_SELL_VOL,
    STANCE_STAND_DOWN,
    TREND_DOWN,
    TREND_FLAT,
    TREND_UP,
    UnderlyingSignal,
)
from vrp_engine.strategy.structures import (
    CALL_CREDIT_SPREAD,
    CALL_DEBIT_SPREAD,
    IRON_CONDOR,
    PUT_CREDIT_SPREAD,
    PUT_DEBIT_SPREAD,
    SelectionParams,
    StructureLeg,
    credit_spread_variants,
    debit_spread_variants,
    iron_condor_variants,
    nearest_delta,
    structures_for_signal,
)

EXPIRY = TODAY + timedelta(days=7)
PARAMS = SelectionParams(max_spread_fraction=0.10, min_open_interest=0)


def _signal(
    *,
    stance: str = STANCE_SELL_VOL,
    trend: str = TREND_FLAT,
    spot: float = 500.0,
    realized: float = 0.15,
    implied: float = 0.25,
) -> UnderlyingSignal:
    return UnderlyingSignal(
        symbol="SPY",
        spot=spot,
        expiration=EXPIRY,
        horizon_days=7,
        realized_vol=realized,
        implied_vol=implied,
        vrp=implied - realized,
        vrp_z=(implied - realized) / realized,
        trend=trend,
        stance=stance,
    )


# --- strike selection -------------------------------------------------------


def test_nearest_delta_picks_the_closest_absolute_delta():
    pool = [
        make_candidate(strike=490, delta=-0.10),
        make_candidate(strike=495, delta=-0.23),
        make_candidate(strike=498, delta=-0.35),
    ]
    assert nearest_delta(pool, target=0.22).strike == 495


def test_nearest_delta_on_an_empty_pool_is_none():
    assert nearest_delta([], target=0.22) is None


def test_nearest_delta_handles_positive_call_deltas():
    pool = [make_candidate(kind="call", strike=505, delta=0.30)]
    assert nearest_delta(pool, target=0.22).strike == 505


# --- credit spreads ---------------------------------------------------------


def test_put_credit_spread_sells_below_spot_and_buys_lower():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    variants = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )
    assert variants
    for structure in variants:
        short = structure.short_legs[0].contract
        long = next(leg.contract for leg in structure.legs if leg.side == "buy")
        assert short.strike < 500.0
        assert long.strike < short.strike
        assert structure.kind == PUT_CREDIT_SPREAD


def test_call_credit_spread_sells_above_spot_and_buys_higher():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    variants = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="call",
        target_delta=0.22,
        params=PARAMS,
    )
    assert variants
    for structure in variants:
        short = structure.short_legs[0].contract
        long = next(leg.contract for leg in structure.legs if leg.side == "buy")
        assert short.strike > 500.0
        assert long.strike > short.strike
        assert structure.kind == CALL_CREDIT_SPREAD


def test_credit_spread_variants_span_several_widths():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    variants = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )
    widths = sorted({structure.width for structure in variants})
    assert len(widths) >= 2
    assert widths == sorted(widths)


def test_credit_is_positive_and_below_the_width():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    variants = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )
    for structure in variants:
        assert structure.credit_usd > 0
        assert structure.credit_usd < structure.width * CONTRACT_MULTIPLIER


def test_max_loss_plus_max_profit_equals_the_width():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )[0]
    total = structure.max_loss_usd + structure.max_profit_usd
    assert total == pytest.approx(structure.width * CONTRACT_MULTIPLIER)


def test_no_variants_when_every_quote_is_too_wide():
    chain = build_chain(spot=500.0, expiration=EXPIRY, spread_fraction=0.60)
    assert (
        credit_spread_variants(
            chain,
            underlying="SPY",
            spot=500.0,
            expiration=EXPIRY,
            option_type="put",
            target_delta=0.22,
            params=SelectionParams(max_spread_fraction=0.08),
        )
        == []
    )


def test_open_interest_gate_can_empty_the_pool():
    chain = build_chain(spot=500.0, expiration=EXPIRY, open_interest=10)
    assert (
        credit_spread_variants(
            chain,
            underlying="SPY",
            spot=500.0,
            expiration=EXPIRY,
            option_type="put",
            target_delta=0.22,
            params=SelectionParams(max_spread_fraction=0.10, min_open_interest=1_000),
        )
        == []
    )


def test_contracts_without_a_delta_are_skipped():
    chain = [c.model_copy(update={"delta": None}) for c in build_chain(expiration=EXPIRY)]
    assert (
        credit_spread_variants(
            chain,
            underlying="SPY",
            spot=500.0,
            expiration=EXPIRY,
            option_type="put",
            target_delta=0.22,
            params=PARAMS,
        )
        == []
    )


# --- debit spreads ----------------------------------------------------------


def test_call_debit_spread_buys_low_and_sells_high():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    variants = debit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="call",
        params=PARAMS,
    )
    assert variants
    for structure in variants:
        long = next(leg.contract for leg in structure.legs if leg.side == "buy")
        short = next(leg.contract for leg in structure.legs if leg.side == "sell")
        assert short.strike > long.strike
        assert structure.kind == CALL_DEBIT_SPREAD


def test_debit_spread_pays_rather_than_collects():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = debit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="call",
        params=PARAMS,
    )[0]
    assert not structure.is_credit
    assert structure.debit_usd > 0
    assert structure.credit_usd == 0
    assert structure.max_loss_usd == pytest.approx(structure.debit_usd)


def test_put_debit_spread_buys_high_and_sells_low():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    variants = debit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        params=PARAMS,
    )
    assert variants
    for structure in variants:
        long = next(leg.contract for leg in structure.legs if leg.side == "buy")
        short = next(leg.contract for leg in structure.legs if leg.side == "sell")
        assert short.strike < long.strike
        assert structure.kind == PUT_DEBIT_SPREAD


# --- iron condors -----------------------------------------------------------


def test_iron_condor_has_four_legs_two_of_each_type():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    variants = iron_condor_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, params=PARAMS
    )
    assert variants
    structure = variants[0]
    assert structure.kind == IRON_CONDOR
    assert len(structure.legs) == 4
    assert sum(1 for leg in structure.legs if leg.contract.is_call) == 2
    assert sum(1 for leg in structure.legs if not leg.contract.is_call) == 2


def test_iron_condor_sells_two_legs_and_buys_two():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = iron_condor_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, params=PARAMS
    )[0]
    assert len(structure.short_legs) == 2
    assert sum(1 for leg in structure.legs if leg.side == "buy") == 2


def test_iron_condor_width_is_the_wider_wing_not_the_sum():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = iron_condor_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, params=PARAMS
    )[0]
    call_strikes = [leg.contract.strike for leg in structure.legs if leg.contract.is_call]
    put_strikes = [leg.contract.strike for leg in structure.legs if not leg.contract.is_call]
    call_width = max(call_strikes) - min(call_strikes)
    put_width = max(put_strikes) - min(put_strikes)
    assert structure.width == pytest.approx(max(call_width, put_width))
    assert structure.width < call_width + put_width


def test_iron_condor_collects_more_than_either_single_wing():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    condor = iron_condor_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, params=PARAMS
    )[0]
    put_wing = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=PARAMS.target_condor_delta,
        params=PARAMS,
    )[0]
    assert condor.credit_usd > put_wing.credit_usd


# --- pricing conventions ----------------------------------------------------


def test_effective_price_is_worse_than_the_mid_for_a_credit():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )[0]
    assert structure.effective_price < structure.net_price_mid
    assert structure.effective_price > structure.net_price_worst


def test_limit_price_is_the_absolute_net_mid_rounded():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )[0]
    assert structure.limit_price == pytest.approx(round(abs(structure.net_price_mid), 2))
    assert structure.limit_price > 0


def test_breakeven_of_a_put_credit_spread_sits_below_the_short_strike():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )[0]
    short_strike = structure.short_legs[0].contract.strike
    assert structure.breakevens()[0] < short_strike


def test_condor_has_two_breakevens():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = iron_condor_variants(
        chain, underlying="SPY", spot=500.0, expiration=EXPIRY, params=PARAMS
    )[0]
    assert len(structure.breakevens()) == 2


def test_leg_intents_are_opening_by_default():
    leg = StructureLeg(contract=make_candidate(), side="sell")
    assert leg.open_intent == "sell_to_open"
    assert leg.close_intent == "buy_to_close"


def test_buy_leg_intents_mirror_correctly():
    leg = StructureLeg(contract=make_candidate(), side="buy")
    assert leg.open_intent == "buy_to_open"
    assert leg.close_intent == "sell_to_close"


def test_describe_mentions_the_underlying_and_expiry():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structure = credit_spread_variants(
        chain,
        underlying="SPY",
        spot=500.0,
        expiration=EXPIRY,
        option_type="put",
        target_delta=0.22,
        params=PARAMS,
    )[0]
    text = structure.describe()
    assert "SPY" in text
    assert EXPIRY.isoformat() in text


# --- the selection matrix ---------------------------------------------------


def test_sell_vol_and_flat_tape_gives_a_condor():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structures = structures_for_signal(
        _signal(stance=STANCE_SELL_VOL, trend=TREND_FLAT), chain, params=PARAMS
    )
    assert structures
    assert all(s.kind == IRON_CONDOR for s in structures)


def test_sell_vol_and_uptrend_gives_a_put_credit_spread():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structures = structures_for_signal(
        _signal(stance=STANCE_SELL_VOL, trend=TREND_UP), chain, params=PARAMS
    )
    assert structures
    assert all(s.kind == PUT_CREDIT_SPREAD for s in structures)


def test_sell_vol_and_downtrend_gives_a_call_credit_spread():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structures = structures_for_signal(
        _signal(stance=STANCE_SELL_VOL, trend=TREND_DOWN), chain, params=PARAMS
    )
    assert structures
    assert all(s.kind == CALL_CREDIT_SPREAD for s in structures)


def test_buy_vol_and_uptrend_gives_a_call_debit_spread():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structures = structures_for_signal(
        _signal(stance=STANCE_BUY_VOL, trend=TREND_UP), chain, params=PARAMS
    )
    assert structures
    assert all(s.kind == CALL_DEBIT_SPREAD for s in structures)


def test_buy_vol_and_downtrend_gives_a_put_debit_spread():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    structures = structures_for_signal(
        _signal(stance=STANCE_BUY_VOL, trend=TREND_DOWN), chain, params=PARAMS
    )
    assert structures
    assert all(s.kind == PUT_DEBIT_SPREAD for s in structures)


def test_buy_vol_without_a_direction_stands_down():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    assert (
        structures_for_signal(
            _signal(stance=STANCE_BUY_VOL, trend=TREND_FLAT), chain, params=PARAMS
        )
        == []
    )


def test_stand_down_stance_produces_nothing():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    assert (
        structures_for_signal(
            _signal(stance=STANCE_STAND_DOWN, trend=TREND_FLAT), chain, params=PARAMS
        )
        == []
    )


def test_blacked_out_signal_produces_nothing():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    signal = _signal(stance=STANCE_SELL_VOL, trend=TREND_FLAT).model_copy(
        update={"event_blackout": True}
    )
    assert structures_for_signal(signal, chain, params=PARAMS) == []


def test_every_structure_has_a_long_leg_for_each_short_leg():
    chain = build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.25)
    for trend in (TREND_FLAT, TREND_UP, TREND_DOWN):
        for structure in structures_for_signal(
            _signal(stance=STANCE_SELL_VOL, trend=trend), chain, params=PARAMS
        ):
            shorts = [leg for leg in structure.legs if leg.side == "sell"]
            longs = [leg for leg in structure.legs if leg.side == "buy"]
            assert len(longs) == len(shorts)


def test_history_helper_produces_a_usable_spot():
    # Guards the fixture itself: a broken builder would silently weaken every test.
    history = build_history(days=30, start=500.0)
    assert history.last_close is not None
    assert history.last_close == pytest.approx(500.0, rel=0.1)
