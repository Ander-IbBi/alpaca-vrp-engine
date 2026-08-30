"""Chart data: the shapes the dashboard draws, with no Streamlit in sight.

The dashboard has to render from two different sources — live pydantic models and the
JSON digests the journal stores — so most of these tests check that both roads lead to
the same row.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TODAY, FakePosition, occ_symbol

from vrp_engine.config import Settings
from vrp_engine.risk.portfolio import build_portfolio_risk
from vrp_engine.strategy.signals import UnderlyingSignal
from vrp_engine.viz import (
    bucket_rows,
    budget_rows,
    entry_band_rows,
    equity_rows,
    journal_timeline,
    latest_block,
    payoff_rows,
    portfolio_scalars,
    stress_rows,
    volatility_rows,
    wedge_points,
    wedge_rows,
    worst_case_point,
)

EXPIRY = TODAY + timedelta(days=7)


def _digest(**overrides) -> dict:
    row = {
        "spot": 500.0,
        "realized_vol": 0.12,
        "implied_vol": 0.18,
        "vrp": 0.06,
        "vrp_z": 0.5,
        "trend": "flat",
        "beta": 1.0,
        "stance": "sell_vol",
        "event_blackout": False,
    }
    row.update(overrides)
    return row


# --- signals -----------------------------------------------------------------


def test_a_signal_becomes_one_point_on_the_volatility_plane():
    rows = volatility_rows({"SPY": _digest()})
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["realized_vol"] == pytest.approx(0.12)
    assert rows[0]["implied_vol"] == pytest.approx(0.18)


def test_a_symbol_without_both_volatilities_is_dropped_not_plotted_at_zero():
    signals = {
        "SPY": _digest(),
        "GLD": _digest(implied_vol=None),
        "TLT": _digest(realized_vol=None),
    }
    assert [row["symbol"] for row in volatility_rows(signals)] == ["SPY"]


def test_the_stance_gets_a_label_a_reader_understands():
    labels = {
        row["stance"]: row["stance_label"]
        for row in volatility_rows(
            {
                "A": _digest(stance="sell_vol"),
                "B": _digest(stance="buy_vol"),
                "C": _digest(stance="stand_down"),
            }
        )
    }
    assert labels == {
        "sell_vol": "Sell premium",
        "buy_vol": "Buy premium",
        "stand_down": "Stand down",
    }


def test_a_live_signal_and_its_journal_digest_produce_the_same_row():
    live = UnderlyingSignal(
        symbol="SPY",
        spot=500.0,
        realized_vol=0.12,
        implied_vol=0.18,
        vrp_z=0.5,
        trend="flat",
        beta=1.0,
        stance="sell_vol",
    )
    assert volatility_rows({"SPY": live}) == volatility_rows({"SPY": _digest()})


def test_symbols_come_out_in_a_stable_alphabetical_order():
    rows = volatility_rows({"TLT": _digest(), "AAPL": _digest(), "SPY": _digest()})
    assert [row["symbol"] for row in rows] == ["AAPL", "SPY", "TLT"]


# --- the stand-down band -----------------------------------------------------


def test_an_empty_universe_draws_no_band():
    assert entry_band_rows([], vrp_z_entry=0.15) == []


def test_the_band_edges_sit_the_threshold_either_side_of_fair_value():
    rows = volatility_rows({"SPY": _digest()})
    edge = entry_band_rows(rows, vrp_z_entry=0.15)[-1]
    assert edge["lower"] == pytest.approx(edge["fair"] * 0.85)
    assert edge["upper"] == pytest.approx(edge["fair"] * 1.15)


def test_the_band_spans_past_the_widest_point_so_no_dot_falls_off_it():
    rows = volatility_rows({"SPY": _digest(realized_vol=0.12, implied_vol=0.40)})
    assert entry_band_rows(rows, vrp_z_entry=0.15)[-1]["realized_vol"] > 0.40


# --- the scanner -------------------------------------------------------------


def _scan(**overrides) -> dict:
    row = {
        "underlying": "SPY",
        "structure": "iron_condor",
        "strikes": [625.0, 630.0, 655.0, 660.0],
        "p_win_model": 0.9,
        "p_win_implied": 0.78,
        "wedge": 0.12,
        "expected_value_usd": 105.0,
        "max_loss_usd": 340.0,
        "accepted": True,
        "rejects": [],
    }
    row.update(overrides)
    return {"top": [row]}


def test_a_candidate_is_labelled_by_underlying_and_strikes():
    assert wedge_rows(_scan())[0]["label"] == "SPY 625/630/655/660"


def test_a_candidate_without_strikes_still_gets_a_label():
    assert wedge_rows(_scan(strikes=[]))[0]["label"] == "SPY"


def test_an_absent_scan_yields_no_candidates():
    assert wedge_rows(None) == []


def test_the_candidate_list_is_capped_so_the_chart_stays_readable():
    scan = {"top": [_scan()["top"][0] for _ in range(30)]}
    assert len(wedge_rows(scan, limit=4)) == 4


def test_each_candidate_becomes_two_points_a_model_one_and_a_market_one():
    points = wedge_points(wedge_rows(_scan()))
    assert [point["kind"] for point in points] == ["Model", "Market"]
    assert points[0]["probability"] == pytest.approx(0.9)
    assert points[1]["probability"] == pytest.approx(0.78)


# --- budgets -----------------------------------------------------------------


@pytest.fixture
def plain_settings(tmp_path) -> Settings:
    return Settings(
        alpaca_api_key="k",
        alpaca_secret_key="s",
        journal_path=tmp_path / "journal.jsonl",
    )


def test_every_budget_reports_what_it_used_and_what_it_may_use(plain_settings):
    rows = budget_rows(
        plain_settings,
        {"equity": 100_000.0, "worst_case_usd": 9_000.0, "stress_usd": 0.0, "net_delta_usd": 0.0},
    )
    worst_case = rows[0]
    assert worst_case["limit_usd"] == pytest.approx(plain_settings.risk_budget_pct * 100_000)
    assert worst_case["utilisation"] == pytest.approx(9_000 / worst_case["limit_usd"])
    assert worst_case["headroom_usd"] == pytest.approx(worst_case["limit_usd"] - 9_000)


def test_the_delta_budget_ignores_the_sign_because_both_sides_cost_the_same(plain_settings):
    long_book = budget_rows(plain_settings, {"equity": 100_000.0, "net_delta_usd": 8_000.0})
    short_book = budget_rows(plain_settings, {"equity": 100_000.0, "net_delta_usd": -8_000.0})
    assert long_book[2]["used_usd"] == short_book[2]["used_usd"] == pytest.approx(8_000.0)


def test_a_breached_budget_still_reports_a_utilisation_a_bar_can_draw(plain_settings):
    rows = budget_rows(plain_settings, {"equity": 100_000.0, "worst_case_usd": 999_999.0})
    assert rows[0]["utilisation"] == pytest.approx(1.0)
    assert rows[0]["headroom_usd"] == pytest.approx(0.0)


def test_an_account_with_no_equity_does_not_divide_by_zero(plain_settings):
    rows = budget_rows(plain_settings, {"equity": 0.0, "worst_case_usd": 100.0})
    assert all(row["utilisation"] == 0.0 for row in rows)


# --- the portfolio, live or journalled ---------------------------------------


def _spread_portfolio():
    """One SPY put credit spread: short the 490, long the 485."""
    positions = [
        FakePosition(occ_symbol("SPY", EXPIRY, "P", 490.0), -2, current_price=2.0),
        FakePosition(occ_symbol("SPY", EXPIRY, "P", 485.0), 2, current_price=1.0),
    ]
    return build_portfolio_risk(
        equity=100_000.0,
        positions=positions,
        spots={"SPY": 500.0},
        betas={"SPY": 1.0},
        vols={"SPY": 0.15},
        greeks={},
        bucket_of=lambda _symbol: "index",
    )


def test_the_live_portfolio_and_its_journal_digest_agree_on_the_headline_numbers():
    portfolio = _spread_portfolio()
    live = portfolio_scalars(portfolio)
    replayed = portfolio_scalars(portfolio.digest())
    assert live["worst_case_usd"] == pytest.approx(replayed["worst_case_usd"], abs=0.01)
    assert live["stress_usd"] == pytest.approx(replayed["stress_usd"], abs=0.01)
    assert live["by_bucket"] == pytest.approx(replayed["by_bucket"], abs=0.01)


def test_a_missing_digest_key_reads_as_zero_rather_than_raising():
    assert portfolio_scalars({})["equity"] == pytest.approx(0.0)


def test_the_payoff_curve_dips_where_the_book_loses_the_most():
    rows = payoff_rows(_spread_portfolio())
    worst = worst_case_point(rows)
    assert worst["pnl"] == pytest.approx(min(row["pnl"] for row in rows))
    assert worst["pnl"] < 0


def test_a_flat_book_has_no_worst_case_to_mark():
    assert worst_case_point([]) is None


def test_stress_scenarios_are_ordered_by_shock_not_by_name():
    rows = stress_rows({"+2sigma": 10.0, "-2sigma": -50.0, "+1sigma": 5.0, "-1sigma": -20.0})
    assert [row["scenario"] for row in rows] == ["-2sigma", "-1sigma", "+1sigma", "+2sigma"]


def test_an_unparseable_scenario_label_does_not_break_the_ordering():
    assert [row["sigma"] for row in stress_rows({"crash": -100.0})] == [0.0]


def test_buckets_come_out_heaviest_first():
    rows = bucket_rows({"AAPL": 1_000.0, "index": 4_000.0, "NVDA": 2_000.0})
    assert [row["bucket"] for row in rows] == ["index", "NVDA", "AAPL"]


# --- the journal -------------------------------------------------------------


def test_portfolio_history_becomes_timestamped_equity_rows():
    rows = equity_rows([("2026-08-28T13:00", 100_000.0), ("2026-08-28T14:00", 100_500.0)])
    assert [row["equity"] for row in rows] == [100_000.0, 100_500.0]


def test_a_malformed_history_point_is_skipped_rather_than_fatal():
    assert equity_rows([("2026-08-28T13:00", 1.0), None, 42]) == [
        {"timestamp": "2026-08-28T13:00", "equity": 1.0}
    ]


def test_every_cycle_becomes_one_point_on_the_timeline_including_the_quiet_ones():
    rows = journal_timeline(
        [
            {"ts": "t1", "equity": 100_000.0, "proposal": {"action": "open"}},
            {"ts": "t2", "equity": 100_100.0},
        ]
    )
    assert [row["cycle"] for row in rows] == [1, 2]
    assert [row["action"] for row in rows] == ["open", "hold"]


def test_a_cycle_without_a_usable_equity_reading_reports_none():
    assert journal_timeline([{"ts": "t1", "equity": "unavailable"}])[0]["equity"] is None


def test_the_latest_block_wins_so_the_freshest_snapshot_is_drawn():
    entries = [
        {"portfolio": {"equity": 1.0}},
        {"portfolio": {}},
        {"portfolio": {"equity": 3.0}},
        {},
    ]
    assert latest_block(entries, "portfolio") == {"equity": 3.0}


def test_a_journal_without_that_block_returns_nothing_to_draw():
    assert latest_block([{"ts": "t1"}], "signals") is None
