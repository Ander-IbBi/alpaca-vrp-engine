"""The engine cycle: scan, priority order, and one ticket per pass."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import NOW, TODAY, FakePosition, build_chain, build_history, occ_symbol

from vrp_engine.alpaca.market_data import PriceHistory
from vrp_engine.config import Settings
from vrp_engine.risk.account import US_EASTERN
from vrp_engine.risk.portfolio import build_portfolio_risk
from vrp_engine.strategy.base import (
    ACTION_CLOSE,
    ACTION_HEDGE,
    ACTION_HOLD,
    ACTION_OPEN,
    ACTION_UNWIND,
    StrategyContext,
)
from vrp_engine.strategy.engine import VrpEngine, build_strategy
from vrp_engine.strategy.signals import (
    STANCE_SELL_VOL,
    STANCE_STAND_DOWN,
    TREND_FLAT,
    TREND_UP,
    UnderlyingSignal,
)

EXPIRY = TODAY + timedelta(days=7)
LEGACY_EXPIRY = TODAY + timedelta(days=21)
MIDDAY = datetime(2026, 8, 31, 12, 0, tzinfo=US_EASTERN)


def _settings(**overrides) -> Settings:
    defaults = {
        "alpaca_api_key": "k",
        "alpaca_secret_key": "s",
        "universe": "SPY,QQQ",
        "beta_bucket": "SPY,QQQ",
        "min_open_interest": 0,
        "max_spread_fraction": 0.10,
        # The gates are exercised on their own; here they stay open so the priority
        # order is what each test actually observes.
        "min_edge": 1e-9,
        "min_wedge": 0.0,
        "allow_legacy_unwind": False,
        "max_dte": 9,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _signal(
    symbol: str = "SPY",
    *,
    spot: float = 500.0,
    realized: float = 0.15,
    implied: float = 0.30,
    trend: str = TREND_FLAT,
    stance: str = STANCE_SELL_VOL,
    expiration=EXPIRY,
) -> UnderlyingSignal:
    return UnderlyingSignal(
        symbol=symbol,
        spot=spot,
        expiration=expiration,
        horizon_days=7,
        realized_vol=realized,
        implied_vol=implied,
        vrp=implied - realized,
        vrp_z=(implied - realized) / realized if realized else None,
        trend=trend,
        stance=stance,
    )


def _context(
    *,
    settings: Settings | None = None,
    symbols: tuple[str, ...] = ("SPY",),
    signals: dict[str, UnderlyingSignal] | None = None,
    positions: list | None = None,
    equity: float = 100_000.0,
    now=MIDDAY,
    new_risk_allowed: bool = True,
    flatten_required: bool = False,
    spots: dict[str, float] | None = None,
    quotes: dict | None = None,
    with_portfolio: bool = True,
) -> StrategyContext:
    config = settings or _settings()
    resolved_signals = signals or {symbol: _signal(symbol) for symbol in symbols}
    chains = {
        symbol: build_chain(
            underlying=symbol,
            spot=resolved_signals[symbol].spot,
            expiration=resolved_signals[symbol].expiration or EXPIRY,
            implied_vol=resolved_signals[symbol].implied_vol or 0.30,
        )
        for symbol in resolved_signals
    }
    resolved_spots = spots or {s: sig.spot for s, sig in resolved_signals.items()}
    held = positions or []
    portfolio = (
        build_portfolio_risk(
            equity=equity,
            positions=held,
            spots=resolved_spots,
            bucket_of=config.bucket_of,
        )
        if with_portfolio
        else None
    )
    return StrategyContext(
        today=TODAY,
        now=now,
        market_open=True,
        equity=equity,
        cash=equity,
        options_buying_power=equity,
        universe=list(resolved_signals),
        spots=resolved_spots,
        signals=resolved_signals,
        chains=chains,
        portfolio=portfolio,
        positions=held,
        quotes=quotes or {},
        new_risk_allowed=new_risk_allowed,
        flatten_required=flatten_required,
    )


def _engine(settings: Settings | None = None) -> VrpEngine:
    return VrpEngine(settings or _settings())


# --- the scanner ------------------------------------------------------------


def test_the_scanner_finds_candidates_for_a_rich_vol_signal():
    scan = _engine().scan(_context())
    assert scan.rows
    assert scan.considered_symbols == ["SPY"]


def test_the_scanner_ranks_by_score():
    scan = _engine().scan(_context())
    scores = [row.score for row in scan.rows]
    assert scores == sorted(scores, reverse=True)


def test_a_symbol_without_a_signal_is_skipped_with_a_reason():
    context = _context()
    context.universe.append("NVDA")
    scan = _engine().scan(context)
    assert scan.skipped["NVDA"] == "no signal computed"


def test_a_stand_down_signal_is_skipped():
    signals = {"SPY": _signal("SPY", stance=STANCE_STAND_DOWN)}
    scan = _engine().scan(_context(signals=signals))
    assert "SPY" in scan.skipped
    assert scan.rows == []


def test_an_empty_chain_is_skipped():
    context = _context()
    context.chains["SPY"] = []
    scan = _engine().scan(context)
    assert scan.skipped["SPY"] == "empty option chain"


def test_a_symbol_we_already_hold_is_skipped():
    positions = [
        FakePosition(occ_symbol("SPY", EXPIRY, "put", 490), -1.0, current_price=3.0),
        FakePosition(occ_symbol("SPY", EXPIRY, "put", 485), 1.0, current_price=1.0),
    ]
    scan = _engine().scan(_context(positions=positions))
    assert "already holding" in scan.skipped["SPY"]


def test_a_position_at_a_different_expiry_does_not_block_a_new_one():
    positions = [
        FakePosition(occ_symbol("SPY", TODAY + timedelta(days=3), "put", 490), -1.0),
        FakePosition(occ_symbol("SPY", TODAY + timedelta(days=3), "put", 485), 1.0),
    ]
    scan = _engine().scan(_context(positions=positions))
    assert scan.rows


def test_scan_rows_record_the_wedge_and_the_rejects():
    scan = _engine().scan(_context())
    row = scan.rows[0]
    assert row.wedge != 0.0
    assert isinstance(row.rejects, list)


def test_selling_cheap_vol_lands_in_the_scan_as_rejected():
    signals = {"SPY": _signal("SPY", realized=0.40, implied=0.12)}
    scan = _engine(_settings(min_wedge=0.02)).scan(_context(signals=signals))
    assert scan.rows
    assert scan.accepted == []


def test_the_scan_digest_is_small_enough_to_journal():
    scan = _engine().scan(_context())
    digest = scan.digest(limit=3)
    assert digest["n_candidates"] == len(scan.rows)
    assert len(digest["top"]) <= 3


def test_the_engine_remembers_its_last_scan():
    engine = _engine()
    scan = engine.scan(_context())
    assert engine.last_scan is scan


# --- opening a new structure ------------------------------------------------


def test_a_clean_account_with_rich_vol_opens_a_structure():
    proposal = _engine().propose(_context())
    assert proposal.action == ACTION_OPEN
    assert proposal.qty >= 1
    assert not proposal.skip


def test_the_opening_proposal_carries_its_analytics():
    proposal = _engine().propose(_context())
    analytics = proposal.analytics
    assert analytics.underlying == "SPY"
    assert analytics.wedge is not None
    assert analytics.edge is not None
    assert analytics.binding_constraint


def test_the_opening_proposal_states_its_own_max_loss():
    proposal = _engine().propose(_context())
    assert proposal.max_loss_usd == pytest.approx(proposal.estimated_cost_usd)
    assert proposal.max_loss_usd > 0


def test_every_opening_leg_is_marked_to_open():
    proposal = _engine().propose(_context())
    assert all(leg.position_intent.endswith("_to_open") for leg in proposal.legs)


def test_the_opening_proposal_records_the_sizing_trail():
    proposal = _engine().propose(_context())
    assert proposal.sizing["binding_constraint"]
    assert proposal.sizing["headroom"]


def test_the_rationale_reads_as_a_full_argument():
    proposal = _engine().propose(_context())
    assert "wedge" in proposal.rationale
    assert "contract(s)" in proposal.rationale


def test_a_flat_tape_and_rich_vol_gives_a_condor():
    proposal = _engine().propose(_context())
    assert proposal.analytics.structure_kind == "iron_condor"


def test_an_uptrend_gives_a_put_credit_spread():
    signals = {"SPY": _signal("SPY", trend=TREND_UP)}
    proposal = _engine().propose(_context(signals=signals))
    assert proposal.analytics.structure_kind == "put_credit_spread"


def test_the_best_underlying_across_the_universe_wins():
    signals = {
        "SPY": _signal("SPY", implied=0.18),
        "QQQ": _signal("QQQ", implied=0.45),
    }
    proposal = _engine().propose(_context(signals=signals))
    assert proposal.analytics.underlying == "QQQ"


def test_nothing_is_opened_when_no_signal_is_actionable():
    signals = {"SPY": _signal("SPY", stance=STANCE_STAND_DOWN)}
    proposal = _engine().propose(_context(signals=signals))
    assert proposal.action == ACTION_HOLD
    assert proposal.skip
    assert "no tradable structure" in proposal.rationale


def test_a_high_edge_floor_holds_and_explains_the_best_rejection():
    proposal = _engine(_settings(min_edge=5.0)).propose(_context())
    assert proposal.skip
    assert "rejected" in proposal.rationale


def test_no_room_in_the_budget_holds_with_the_sizing_note():
    proposal = _engine().propose(_context(equity=200.0))
    assert proposal.skip
    assert "no room left" in proposal.rationale or "risk budgets" in proposal.rationale


# --- priority order ---------------------------------------------------------


def test_a_flatten_beats_opening_a_new_structure():
    positions = [
        FakePosition(occ_symbol("SPY", EXPIRY, "put", 490), -1.0, current_price=3.0),
        FakePosition(occ_symbol("SPY", EXPIRY, "put", 485), 1.0, current_price=1.0),
    ]
    proposal = _engine().propose(_context(positions=positions, flatten_required=True))
    assert proposal.action == ACTION_CLOSE
    assert "flatten" in proposal.rationale


def test_a_flatten_with_an_empty_book_just_holds():
    proposal = _engine().propose(_context(flatten_required=True))
    assert proposal.skip
    assert "already empty" in proposal.rationale


def test_managing_a_loser_beats_opening_a_new_structure():
    positions = [
        FakePosition(
            occ_symbol("SPY", EXPIRY, "put", 490),
            -1.0,
            avg_entry_price=3.0,
            current_price=9.0,
            unrealized_pl=-600.0,
        ),
        FakePosition(
            occ_symbol("SPY", EXPIRY, "put", 485), 1.0, avg_entry_price=1.0, current_price=2.0
        ),
    ]
    proposal = _engine().propose(_context(positions=positions))
    assert proposal.action == ACTION_CLOSE
    assert proposal.is_closing


def test_an_inherited_book_is_unwound_first_when_the_gate_is_open():
    positions = [
        FakePosition(occ_symbol("SPY", LEGACY_EXPIRY, "call", 789), -1.0, current_price=12.0),
        FakePosition("SPY", 100.0, asset_class="us_equity", current_price=770.88),
    ]
    engine = _engine(_settings(allow_legacy_unwind=True))
    proposal = engine.propose(_context(positions=positions))
    assert proposal.action == ACTION_UNWIND
    assert proposal.legs[0].symbol == occ_symbol("SPY", LEGACY_EXPIRY, "call", 789)


def test_the_unwind_gate_defaults_to_leaving_inherited_positions_alone():
    positions = [
        FakePosition(occ_symbol("SPY", LEGACY_EXPIRY, "call", 789), -1.0, current_price=12.0)
    ]
    proposal = _engine().propose(_context(positions=positions))
    assert proposal.action != ACTION_UNWIND


def test_inherited_options_are_not_managed_as_engine_structures():
    positions = [
        FakePosition(
            occ_symbol("SPY", LEGACY_EXPIRY, "call", 789),
            -1.0,
            avg_entry_price=3.0,
            current_price=20.0,
            unrealized_pl=-1_700.0,
        )
    ]
    proposal = _engine().propose(_context(positions=positions))
    assert proposal.action != ACTION_CLOSE


def test_a_withheld_new_risk_flag_still_allows_management():
    positions = [
        FakePosition(
            occ_symbol("SPY", EXPIRY, "put", 490),
            -1.0,
            avg_entry_price=3.0,
            current_price=9.0,
            unrealized_pl=-600.0,
        ),
        FakePosition(
            occ_symbol("SPY", EXPIRY, "put", 485), 1.0, avg_entry_price=1.0, current_price=2.0
        ),
    ]
    proposal = _engine().propose(_context(positions=positions, new_risk_allowed=False))
    assert proposal.action == ACTION_CLOSE


def test_a_withheld_new_risk_flag_blocks_new_entries():
    proposal = _engine().propose(_context(new_risk_allowed=False))
    assert proposal.skip
    assert "withholds new risk" in proposal.rationale


# --- delta hedge ------------------------------------------------------------


def test_a_book_leaning_long_is_hedged_with_a_call_spread():
    positions = [FakePosition("SPY", 400.0, asset_class="us_equity", current_price=500.0)]
    engine = _engine(_settings(max_net_delta_pct=0.10, hedge_symbol="SPY"))
    proposal = engine.propose(_context(positions=positions))
    assert proposal.action == ACTION_HEDGE
    assert proposal.analytics.structure_kind == "call_credit_spread"


def test_the_hedge_collects_premium_rather_than_paying_for_protection():
    positions = [FakePosition("SPY", 400.0, asset_class="us_equity", current_price=500.0)]
    engine = _engine(_settings(max_net_delta_pct=0.10, hedge_symbol="SPY"))
    proposal = engine.propose(_context(positions=positions))
    assert proposal.analytics.credit_usd > 0


def test_the_hedge_explains_the_delta_it_is_correcting():
    positions = [FakePosition("SPY", 400.0, asset_class="us_equity", current_price=500.0)]
    engine = _engine(_settings(max_net_delta_pct=0.10, hedge_symbol="SPY"))
    proposal = engine.propose(_context(positions=positions))
    assert "Delta hedge" in proposal.rationale


def test_a_balanced_book_is_not_hedged():
    proposal = _engine().propose(_context())
    assert proposal.action == ACTION_OPEN


def test_without_a_portfolio_view_no_hedge_is_attempted():
    proposal = _engine().propose(_context(with_portfolio=False))
    assert proposal.action in {ACTION_OPEN, ACTION_HOLD}


def test_a_hedge_without_a_usable_signal_on_the_hedge_symbol_is_skipped():
    positions = [FakePosition("SPY", 400.0, asset_class="us_equity", current_price=500.0)]
    engine = _engine(_settings(max_net_delta_pct=0.10, hedge_symbol="IWM"))
    proposal = engine.propose(_context(positions=positions))
    assert proposal.action != ACTION_HEDGE


# --- construction -----------------------------------------------------------


def test_the_factory_returns_a_named_engine():
    engine = build_strategy(_settings())
    assert engine.name == "vrp-engine"


def test_the_engine_reads_its_selection_knobs_from_settings():
    engine = _engine(_settings(target_short_delta=0.30, target_condor_delta=0.12))
    params = engine._params()
    assert params.target_short_delta == 0.30
    assert params.target_condor_delta == 0.12


def test_a_price_history_fixture_is_available_for_signal_level_tests():
    # Sanity check that the engine's data dependencies are constructible offline.
    history = build_history(days=60)
    assert isinstance(history, PriceHistory)
    assert history.last_close is not None
    assert NOW.astimezone(US_EASTERN).hour == 10
