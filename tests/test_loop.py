"""The agent cycle: step ordering, degradation, and what gets journalled.

Every network boundary is patched: bars, chains, snapshots, the CLI and MCP. What is
exercised is the sequencing, which is where the safety properties actually live.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import (
    TODAY,
    FakeAccount,
    FakeAlpaca,
    FakePosition,
    build_chain,
    build_history,
    occ_symbol,
)

from vrp_engine.agent import loop as loop_module
from vrp_engine.agent.analyst import AnalystReview, RuleBasedAnalyst
from vrp_engine.agent.loop import VrpAgent
from vrp_engine.alpaca.cli_bridge import BrokerCrossCheck, FillReconciliation
from vrp_engine.alpaca.mcp_bridge import McpResearch, McpResult
from vrp_engine.config import Settings
from vrp_engine.journal import Journal
from vrp_engine.risk.limits import RiskDecision
from vrp_engine.strategy.base import ACTION_HOLD, ProposedTrade

EXPIRY = TODAY + timedelta(days=7)
LEGACY_EXPIRY = TODAY + timedelta(days=21)
# Mid-session in New York, so the trading window never closes a test by accident.
NOON_ET = datetime(2026, 8, 31, 16, 0, tzinfo=UTC)


def _settings(tmp_path, **overrides) -> Settings:
    defaults = {
        "alpaca_api_key": "k",
        "alpaca_secret_key": "s",
        "universe": "SPY",
        "beta_bucket": "SPY",
        "dry_run": True,
        "mcp_enabled": False,
        "min_open_interest": 0,
        "max_spread_fraction": 0.10,
        "min_edge": 1e-9,
        "min_wedge": 0.0,
        "allow_legacy_unwind": False,
        "openai_api_key": "",
        "journal_path": tmp_path / "journal.jsonl",
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def wired(monkeypatch):
    """Patch every data boundary so the loop runs entirely on synthetic inputs."""

    def bars(client, symbols, *, today, **_):
        return {s: client.histories[s] for s in symbols if s in client.histories}

    def chain(client, symbol, **_):
        client.snapshot_requests.append([symbol])
        return client.chains.get(symbol, [])

    monkeypatch.setattr(loop_module, "fetch_daily_bars", bars)
    monkeypatch.setattr(loop_module, "fetch_quoted_chain", chain)
    monkeypatch.setattr(loop_module, "fetch_snapshots_for", lambda client, symbols: [])
    monkeypatch.setattr(loop_module, "market_date", lambda: TODAY)
    monkeypatch.setattr(loop_module, "_utcnow", lambda: NOON_ET)
    return None


def _client(settings, *, positions=None, account=None, market_open=True, open_orders=None):
    # A calm, high-implied-vol tape: realized vol near 13%, implied 35%, so the
    # engine has a clear reason to sell premium.
    histories = {"SPY": build_history(days=90, start=500.0, daily_vol=0.008)}
    chains = {"SPY": build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.35)}
    return FakeAlpaca(
        settings=settings,
        account=account or FakeAccount(),
        positions=positions or [],
        histories=histories,
        chains=chains,
        market_open=market_open,
        open_orders=open_orders or [],
    )


def _agent(settings, client, **overrides):
    defaults = {
        "journal": Journal(settings.journal_path),
        "analyst": RuleBasedAnalyst(),
        "cross_checker": lambda **_: BrokerCrossCheck(checked=True, agrees=True),
        "reconciler": lambda **_: FillReconciliation(checked=True, consistent=True),
        "researcher": lambda settings, calls=None: McpResearch(available=False),
    }
    defaults.update(overrides)
    return VrpAgent(client, **defaults)


def _spread_positions(*, pl: float = 0.0, price: float = 3.0):
    return [
        FakePosition(
            occ_symbol("SPY", EXPIRY, "put", 490),
            -1.0,
            avg_entry_price=3.0,
            current_price=price,
            unrealized_pl=pl,
        ),
        FakePosition(
            occ_symbol("SPY", EXPIRY, "put", 485), 1.0, avg_entry_price=1.0, current_price=1.0
        ),
    ]


# --- a clean cycle ----------------------------------------------------------


def test_a_clean_cycle_reports_the_account(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once()
    assert cycle.strategy == "vrp-engine"
    assert cycle.equity == pytest.approx(100_000.0)
    assert cycle.options_buying_power == pytest.approx(100_000.0)
    assert cycle.market_open is True


def test_signals_are_computed_for_the_universe(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once()
    assert "SPY" in cycle.signals
    assert cycle.signals["SPY"]["realized_vol"] > 0
    assert cycle.signals["SPY"]["stance"] == "sell_vol"


def test_the_portfolio_digest_is_recorded(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once()
    assert cycle.portfolio is not None
    assert "worst_case_loss_usd" in cycle.portfolio


def test_a_rich_vol_tape_produces_an_approved_dry_run_ticket(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once()
    assert cycle.proposal is not None
    assert not cycle.proposal.skip
    assert cycle.risk.allowed
    assert cycle.execution["dry_run"] is True
    assert cycle.execution["submitted"] is False


def test_the_scanner_output_is_journalled(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once()
    assert cycle.scan is not None
    assert cycle.scan["n_candidates"] >= 1


def test_every_cycle_writes_exactly_one_journal_entry(wired, tmp_path):
    settings = _settings(tmp_path)
    agent = _agent(settings, _client(settings))
    agent.run_once()
    agent.run_once()
    entries = Journal(settings.journal_path).read_all()
    assert len(entries) == 2
    assert all(entry["kind"] == "cycle" for entry in entries)


def test_the_journal_entry_carries_the_equity_for_the_drawdown_breaker(wired, tmp_path):
    settings = _settings(tmp_path)
    _agent(settings, _client(settings)).run_once()
    assert Journal(settings.journal_path).high_water_mark() == pytest.approx(100_000.0)


# --- ordering: risk before the analyst -------------------------------------


def _deny(monkeypatch, reason: str = "budget exhausted"):
    """Force the risk layer to refuse, whatever the strategy proposed."""
    monkeypatch.setattr(
        loop_module,
        "review_proposal",
        lambda *_, **__: RiskDecision(allowed=False, reasons=[reason]),
    )


def test_a_blocked_ticket_never_reaches_the_broker(wired, tmp_path, monkeypatch):
    _deny(monkeypatch)
    settings = _settings(tmp_path, dry_run=False)
    client = _client(settings)
    cycle = _agent(settings, client).run_once(execute=True)
    assert cycle.risk is not None
    assert not cycle.risk.allowed
    assert cycle.execution is None
    assert client.trading.submitted == []


def test_the_analyst_is_not_consulted_when_risk_already_said_no(wired, tmp_path, monkeypatch):
    class _Counting(RuleBasedAnalyst):
        calls = 0

        def review(self, cycle_summary, market_context=""):
            type(self).calls += 1
            return super().review(cycle_summary, market_context)

    _deny(monkeypatch)
    settings = _settings(tmp_path)
    analyst = _Counting()
    _agent(settings, _client(settings), analyst=analyst).run_once()
    assert type(analyst).calls == 0


def test_a_ticket_the_engine_cannot_size_never_reaches_risk(wired, tmp_path):
    # A per-trade cap of a few dollars makes every structure too large to size.
    settings = _settings(tmp_path, max_trade_loss_pct=0.0001)
    client = _client(settings)
    cycle = _agent(settings, client).run_once(execute=True)
    assert cycle.proposal.skip
    assert cycle.risk is None
    assert client.trading.submitted == []


def test_an_analyst_veto_stops_the_ticket_after_risk_approved(wired, tmp_path):
    class _Veto(RuleBasedAnalyst):
        def review(self, cycle_summary, market_context=""):
            return AnalystReview(
                approved=False, explanation="CPI tomorrow", reject_reason="event_risk"
            )

    settings = _settings(tmp_path)
    client = _client(settings)
    cycle = _agent(settings, client, analyst=_Veto()).run_once(execute=True)
    assert cycle.risk.allowed
    assert not cycle.analyst.approved
    assert cycle.execution is None
    assert client.trading.submitted == []


def test_a_crashing_analyst_does_not_stop_the_cycle(wired, tmp_path):
    class _Boom(RuleBasedAnalyst):
        def review(self, cycle_summary, market_context=""):
            raise RuntimeError("model down")

    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings), analyst=_Boom()).run_once()
    assert cycle.analyst.approved
    assert cycle.execution is not None


# --- the account guard ------------------------------------------------------


def test_a_daily_loss_breaker_withholds_new_risk(wired, tmp_path):
    settings = _settings(tmp_path)
    account = FakeAccount(equity=93_000.0, last_equity=100_000.0)
    cycle = _agent(settings, _client(settings, account=account)).run_once()
    assert not cycle.account_guard.new_risk_allowed
    assert cycle.proposal.skip


def test_an_equity_floor_breach_demands_a_flatten(wired, tmp_path):
    settings = _settings(tmp_path)
    account = FakeAccount(equity=70_000.0, last_equity=71_000.0)
    client = _client(settings, positions=_spread_positions(), account=account)
    cycle = _agent(settings, client).run_once()
    assert cycle.account_guard.flatten_required
    assert cycle.proposal.is_closing


def test_the_guard_reads_the_high_water_mark_from_the_journal(wired, tmp_path):
    settings = _settings(tmp_path)
    journal = Journal(settings.journal_path)
    journal.append("cycle", {"equity": 130_000.0})
    account = FakeAccount(equity=100_000.0, last_equity=100_000.0)
    cycle = _agent(settings, _client(settings, account=account), journal=journal).run_once()
    assert cycle.account_guard.high_water_mark == pytest.approx(130_000.0)
    assert cycle.account_guard.flatten_required


# --- the verification plane ------------------------------------------------


def test_a_broker_disagreement_stops_the_ticket(wired, tmp_path):
    settings = _settings(tmp_path)
    client = _client(settings)
    cycle = _agent(
        settings,
        client,
        cross_checker=lambda **_: BrokerCrossCheck(
            checked=True, agrees=False, notes=["CLI sees account PA9"]
        ),
    ).run_once(execute=True)
    assert cycle.execution is None
    assert client.trading.submitted == []


def test_an_unavailable_cli_does_not_block_the_cycle(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(
        settings,
        _client(settings),
        cross_checker=lambda **_: BrokerCrossCheck(checked=False, notes=["not installed"]),
    ).run_once()
    assert cycle.execution is not None


def test_a_crashing_cross_checker_degrades_to_unchecked(wired, tmp_path):
    def boom(**_):
        raise OSError("binary vanished")

    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings), cross_checker=boom).run_once()
    assert not cycle.broker_cross_check.checked
    assert cycle.execution is not None


def test_a_reconciliation_mismatch_freezes_new_entries(wired, tmp_path):
    settings = _settings(tmp_path, dry_run=False)
    client = _client(settings)
    agent = _agent(
        settings,
        client,
        reconciler=lambda **_: FillReconciliation(
            checked=True, consistent=False, notes=["CLI reports something else"]
        ),
    )
    first = agent.run_once(execute=True)
    assert first.execution["submitted"] is True
    assert agent.entries_frozen

    second = agent.run_once(execute=True)
    assert second.proposal.skip
    assert any("frozen" in note for note in second.notes)


def test_a_consistent_reconciliation_thaws_new_entries(wired, tmp_path):
    # Freezing stops new entries but not exits, so the position that thaws the freeze
    # is a close: a mismatch must never be able to trap the book.
    settings = _settings(tmp_path, dry_run=False)
    client = _client(settings, positions=_spread_positions(pl=150.0, price=0.8))
    agent = _agent(settings, client)
    agent.entries_frozen = True
    cycle = agent.run_once(execute=True)
    assert cycle.proposal.is_closing
    assert not agent.entries_frozen


def test_reconciliation_is_skipped_on_a_dry_run(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once(execute=False)
    assert cycle.reconciliation is None


# --- the research plane ----------------------------------------------------


def test_a_disabled_research_plane_is_recorded_without_stopping_the_cycle(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once()
    assert cycle.research["available"] is False
    assert cycle.execution is not None


def test_agreeing_mcp_quotes_let_the_ticket_through(wired, tmp_path):
    settings = _settings(tmp_path, mcp_enabled=True)
    client = _client(settings)
    chain = {c.symbol: c for c in client.chains["SPY"]}

    def researcher(_settings, calls=None):
        payload = {
            symbol: {"bid_price": candidate.bid, "ask_price": candidate.ask}
            for symbol, candidate in chain.items()
        }
        return McpResearch(
            available=True,
            results={
                "get_option_snapshot": McpResult(
                    tool="get_option_snapshot", ok=True, text="{}", data=payload
                )
            },
        )

    cycle = _agent(settings, client, researcher=researcher).run_once()
    assert cycle.quote_cross_check.checked
    assert cycle.quote_cross_check.agrees
    assert cycle.execution is not None


def test_diverging_mcp_quotes_stop_the_ticket(wired, tmp_path):
    settings = _settings(tmp_path, mcp_enabled=True)
    client = _client(settings)
    chain = {c.symbol: c for c in client.chains["SPY"]}

    def researcher(_settings, calls=None):
        payload = {
            symbol: {"bid_price": candidate.bid * 3, "ask_price": candidate.ask * 3}
            for symbol, candidate in chain.items()
        }
        return McpResearch(
            available=True,
            results={
                "get_option_snapshot": McpResult(
                    tool="get_option_snapshot", ok=True, text="{}", data=payload
                )
            },
        )

    cycle = _agent(settings, client, researcher=researcher).run_once(execute=True)
    assert not cycle.quote_cross_check.agrees
    assert cycle.execution is None
    assert client.trading.submitted == []


def test_a_crashing_researcher_is_survivable(wired, tmp_path):
    def boom(_settings, calls=None):
        raise RuntimeError("stdio closed")

    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings), researcher=boom).run_once()
    assert cycle.notes


# --- working orders --------------------------------------------------------


class _Order:
    def __init__(self, symbol: str, *, age_seconds: int = 10, order_id: str = "o1") -> None:
        self.id = order_id
        self.symbol = symbol
        self.legs = []
        self.submitted_at = NOON_ET - timedelta(seconds=age_seconds)


def test_a_working_order_on_the_same_underlying_blocks_a_second_ticket(wired, tmp_path):
    settings = _settings(tmp_path)
    orders = [_Order(occ_symbol("SPY", EXPIRY, "put", 470))]
    client = _client(settings, open_orders=orders)
    cycle = _agent(settings, client).run_once(execute=True)
    assert cycle.execution is None
    assert any("still working" in note for note in cycle.notes)


def test_a_stale_order_is_cancelled_rather_than_left_to_block(wired, tmp_path):
    settings = _settings(tmp_path)
    orders = [_Order(occ_symbol("SPY", EXPIRY, "put", 470), age_seconds=600)]
    client = _client(settings, open_orders=orders)
    cycle = _agent(settings, client).run_once()
    assert client.trading.cancelled == ["o1"]
    assert any("cancelled a stale" in note for note in cycle.notes)
    assert cycle.execution is not None


def test_an_unreadable_order_book_makes_the_cycle_stand_down(wired, tmp_path):
    settings = _settings(tmp_path)
    client = _client(settings)

    def boom():
        raise RuntimeError("orders endpoint down")

    client.open_orders = boom
    cycle = _agent(settings, client).run_once()
    assert cycle.proposal is None
    assert any("standing down" in note for note in cycle.notes)


# --- execution ------------------------------------------------------------


def test_executing_with_the_market_open_sends_the_ticket(wired, tmp_path):
    settings = _settings(tmp_path, dry_run=False)
    client = _client(settings)
    cycle = _agent(settings, client).run_once(execute=True)
    assert cycle.execution["submitted"] is True
    assert len(client.trading.submitted) == 1


def test_a_closed_market_never_sends_a_day_order(wired, tmp_path):
    settings = _settings(tmp_path, dry_run=False)
    client = _client(settings, market_open=False)
    cycle = _agent(settings, client).run_once(execute=True)
    assert cycle.execution["submitted"] is False
    assert client.trading.submitted == []
    assert any("Market closed" in note for note in cycle.notes)


def test_a_cycle_with_no_mode_argument_trades(wired, tmp_path):
    """Nobody passes execute=True in production: the loop just runs and it trades."""
    settings = _settings(tmp_path, dry_run=False)
    client = _client(settings)
    cycle = _agent(settings, client).run_once()
    assert cycle.execution["submitted"] is True
    assert len(client.trading.submitted) == 1


def test_the_dry_run_setting_holds_the_ticket_back(wired, tmp_path):
    settings = _settings(tmp_path, dry_run=True)
    client = _client(settings)
    _agent(settings, client).run_once()
    assert client.trading.submitted == []


def test_a_broker_rejection_is_caught_and_journalled(wired, tmp_path):
    settings = _settings(tmp_path, dry_run=False)
    client = _client(settings)

    def boom(request):
        raise RuntimeError("422 unprocessable")

    client.trading.submit_order = boom
    cycle = _agent(settings, client).run_once(execute=True)
    assert any("Broker rejected" in note for note in cycle.notes)
    assert Journal(settings.journal_path).read_all()


# --- degradation -----------------------------------------------------------


def test_a_failing_account_call_does_not_kill_the_loop(wired, tmp_path):
    settings = _settings(tmp_path)
    client = _client(settings)

    def boom():
        raise RuntimeError("account endpoint down")

    client.account = boom
    cycle = _agent(settings, client).run_once()
    assert any("Cycle failed" in note for note in cycle.notes)
    assert cycle.proposal is None


def test_missing_bars_leave_the_cycle_without_signals(wired, tmp_path, monkeypatch):
    def no_bars(client, symbols, *, today, **_):
        raise RuntimeError("data plan does not cover bars")

    monkeypatch.setattr(loop_module, "fetch_daily_bars", no_bars)
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once()
    assert cycle.signals == {}
    assert cycle.proposal.skip


def test_a_broken_chain_does_not_blind_the_whole_cycle(wired, tmp_path, monkeypatch):
    def bad_chain(client, symbol, **_):
        raise RuntimeError("chain endpoint down")

    monkeypatch.setattr(loop_module, "fetch_quoted_chain", bad_chain)
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings)).run_once()
    assert cycle.signals["SPY"]["implied_vol"] is None
    assert cycle.proposal.action == ACTION_HOLD


def test_a_failing_journal_write_is_noted_not_fatal(wired, tmp_path):
    class _Sealed(Journal):
        def append(self, kind, payload):
            raise OSError("disk full")

    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings), journal=_Sealed(settings.journal_path)).run_once()
    assert any("Journal write failed" in note for note in cycle.notes)


# --- inherited book -------------------------------------------------------


def test_the_unwind_gate_lets_the_cycle_clear_an_inherited_collar(wired, tmp_path):
    settings = _settings(tmp_path, allow_legacy_unwind=True)
    positions = [
        FakePosition(occ_symbol("SPY", LEGACY_EXPIRY, "call", 789), -1.0, current_price=12.0),
        FakePosition(occ_symbol("SPY", LEGACY_EXPIRY, "put", 750), 1.0, current_price=8.0),
        FakePosition("SPY", 100.0, asset_class="us_equity", current_price=500.0),
    ]
    cycle = _agent(settings, _client(settings, positions=positions)).run_once()
    assert cycle.proposal.action == "unwind"
    assert cycle.risk.allowed


def test_an_inherited_share_position_is_counted_separately(wired, tmp_path):
    settings = _settings(tmp_path)
    positions = [FakePosition("SPY", 100.0, asset_class="us_equity", current_price=500.0)]
    cycle = _agent(settings, _client(settings, positions=positions)).run_once()
    assert cycle.n_share_positions == 1
    assert cycle.n_option_positions == 0


def test_open_option_legs_are_counted(wired, tmp_path):
    settings = _settings(tmp_path)
    cycle = _agent(settings, _client(settings, positions=_spread_positions())).run_once()
    assert cycle.n_option_positions == 2


def test_a_profitable_open_structure_is_closed_before_anything_new(wired, tmp_path):
    settings = _settings(tmp_path)
    positions = _spread_positions(pl=150.0, price=0.8)
    cycle = _agent(settings, _client(settings, positions=positions)).run_once()
    assert cycle.proposal.is_closing
    assert cycle.risk.allowed


def test_a_hold_cycle_still_explains_itself(wired, tmp_path):
    settings = _settings(tmp_path, min_edge=10.0)
    cycle = _agent(settings, _client(settings)).run_once()
    assert cycle.proposal.skip
    assert cycle.notes
    assert isinstance(cycle.proposal, ProposedTrade)
