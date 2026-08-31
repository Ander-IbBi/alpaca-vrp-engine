"""The verification plane: a second, independent read of the broker.

No subprocess is ever launched here. The tests replace `run_cli` and
`cli_position_symbols`, which is where the boundary to the outside world sits.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from vrp_engine.alpaca import cli_bridge
from vrp_engine.alpaca.cli_bridge import (
    BrokerCrossCheck,
    CliResult,
    cli_market_open,
    cli_position_symbols,
    cross_check_account,
    find_cli,
    reconcile_after_submit,
    run_cli,
)


class _Completed:
    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def fake_binary(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: "/usr/local/bin/alpaca")
    return "/usr/local/bin/alpaca"


def _stub_run(monkeypatch, completed):
    def runner(command, **_):
        runner.command = command
        return completed

    monkeypatch.setattr(cli_bridge.subprocess, "run", runner)
    return runner


# --- locating the binary ----------------------------------------------------


def test_path_wins_when_the_binary_is_installed(fake_binary):
    assert find_cli() == fake_binary


def test_a_missing_binary_is_reported_rather_than_raised(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: None)
    monkeypatch.setattr(cli_bridge, "_FALLBACK_PATHS", ())
    assert find_cli() is None


def test_a_missing_binary_makes_every_call_unavailable(monkeypatch):
    monkeypatch.setattr(cli_bridge, "find_cli", lambda: None)
    result = run_cli("account", "get")
    assert not result.available
    assert "not installed" in result.error


# --- running a command ------------------------------------------------------


def test_a_successful_call_parses_json(monkeypatch, fake_binary):
    _stub_run(monkeypatch, _Completed(stdout=json.dumps({"account_number": "PA1"})))
    result = run_cli("account", "get")
    assert result.available
    assert result.data == {"account_number": "PA1"}


def test_every_call_asks_for_quiet_output(monkeypatch, fake_binary):
    runner = _stub_run(monkeypatch, _Completed(stdout="null"))
    run_cli("clock")
    assert runner.command[-1] == "--quiet"
    assert runner.command[0] == fake_binary


def test_the_recorded_command_omits_the_binary_path(monkeypatch, fake_binary):
    _stub_run(monkeypatch, _Completed(stdout="null"))
    assert run_cli("clock").command == ["clock", "--quiet"]


def test_non_json_output_is_kept_as_text(monkeypatch, fake_binary):
    _stub_run(monkeypatch, _Completed(stdout="  not json  "))
    assert run_cli("clock").data == "not json"


def test_a_nonzero_exit_reports_the_error_but_stays_available(monkeypatch, fake_binary):
    _stub_run(monkeypatch, _Completed(stderr="unauthorized", returncode=1))
    result = run_cli("account", "get")
    assert result.available
    assert result.error == "unauthorized"


def test_a_timeout_is_reported_as_unavailable(monkeypatch, fake_binary):
    def boom(command, **_):
        raise subprocess.TimeoutExpired(cmd=command, timeout=1)

    monkeypatch.setattr(cli_bridge.subprocess, "run", boom)
    result = run_cli("account", "get")
    assert not result.available
    assert result.error


def test_an_os_error_is_reported_as_unavailable(monkeypatch, fake_binary):
    def boom(command, **_):
        raise OSError("no such file")

    monkeypatch.setattr(cli_bridge.subprocess, "run", boom)
    assert not run_cli("clock").available


# --- derived readers --------------------------------------------------------


def test_position_symbols_are_upper_cased(monkeypatch):
    monkeypatch.setattr(
        cli_bridge, "cli_positions", lambda: CliResult(available=True, data=[{"symbol": "spy"}])
    )
    assert cli_position_symbols() == {"SPY"}


def test_position_symbols_are_none_when_the_cli_cannot_answer(monkeypatch):
    monkeypatch.setattr(
        cli_bridge, "cli_positions", lambda: CliResult(available=False, error="missing")
    )
    assert cli_position_symbols() is None


def test_position_symbols_are_none_on_an_unexpected_shape(monkeypatch):
    monkeypatch.setattr(
        cli_bridge, "cli_positions", lambda: CliResult(available=True, data={"oops": 1})
    )
    assert cli_position_symbols() is None


def test_empty_symbols_are_dropped(monkeypatch):
    monkeypatch.setattr(
        cli_bridge,
        "cli_positions",
        lambda: CliResult(available=True, data=[{"symbol": ""}, {"symbol": "QQQ"}]),
    )
    assert cli_position_symbols() == {"QQQ"}


def test_the_cli_clock_is_an_independent_market_hours_source(monkeypatch):
    monkeypatch.setattr(
        cli_bridge, "cli_clock", lambda: CliResult(available=True, data={"is_open": True})
    )
    assert cli_market_open() is True


def test_the_clock_reader_accepts_the_camel_case_field(monkeypatch):
    monkeypatch.setattr(
        cli_bridge, "cli_clock", lambda: CliResult(available=True, data={"isOpen": False})
    )
    assert cli_market_open() is False


def test_an_unreadable_clock_answers_none(monkeypatch):
    monkeypatch.setattr(
        cli_bridge, "cli_clock", lambda: CliResult(available=True, data={"other": 1})
    )
    assert cli_market_open() is None


def test_an_unavailable_clock_answers_none(monkeypatch):
    monkeypatch.setattr(
        cli_bridge, "cli_clock", lambda: CliResult(available=False, error="missing")
    )
    assert cli_market_open() is None


# --- pre-trade cross-check --------------------------------------------------


def _stub_account(monkeypatch, payload, *, available=True, error=None):
    monkeypatch.setattr(
        cli_bridge,
        "cli_account",
        lambda: CliResult(available=available, data=payload, error=error),
    )


def test_matching_views_agree(monkeypatch):
    _stub_account(monkeypatch, {"account_number": "PA1"})
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: {"SPY"})
    check = cross_check_account(account_number="PA1", position_symbols={"SPY"})
    assert check.checked
    assert check.agrees
    assert check.summary() == "CLI agrees"


def test_a_different_account_number_is_flagged(monkeypatch):
    _stub_account(monkeypatch, {"account_number": "PA2"})
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: set())
    check = cross_check_account(account_number="PA1", position_symbols=set())
    assert not check.agrees
    assert "PA2" in check.summary()


def test_a_position_the_cli_cannot_see_is_flagged(monkeypatch):
    _stub_account(monkeypatch, {"account_number": "PA1"})
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: set())
    check = cross_check_account(account_number="PA1", position_symbols={"SPY"})
    assert not check.agrees
    assert "SDK reports" in check.notes[0]


def test_a_position_only_the_cli_sees_is_flagged(monkeypatch):
    _stub_account(monkeypatch, {"account_number": "PA1"})
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: {"QQQ"})
    check = cross_check_account(account_number="PA1", position_symbols=set())
    assert not check.agrees
    assert "CLI reports" in check.notes[0]


def test_without_an_sdk_account_number_the_check_is_skipped(monkeypatch):
    check = cross_check_account(account_number="", position_symbols=set())
    assert not check.checked
    assert check.summary() == "CLI cross-check skipped"


def test_an_unavailable_cli_skips_the_check_rather_than_blocking(monkeypatch):
    _stub_account(monkeypatch, None, available=False, error="not installed")
    check = cross_check_account(account_number="PA1", position_symbols=set())
    assert not check.checked
    assert "not installed" in check.notes


def test_a_cli_error_skips_the_check(monkeypatch):
    _stub_account(monkeypatch, None, available=True, error="unauthorized")
    assert not cross_check_account(account_number="PA1", position_symbols=set()).checked


def test_a_default_cross_check_is_optimistic_but_unchecked():
    check = BrokerCrossCheck(checked=False)
    assert check.agrees
    assert not check.checked


# --- post-fill reconciliation ----------------------------------------------


def test_an_opening_fill_is_confirmed_when_the_legs_appear(monkeypatch):
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: {"SPY_A", "SPY_B"})
    result = reconcile_after_submit(
        sdk_symbols={"SPY_A", "SPY_B"}, expected_symbols=["SPY_A", "SPY_B"], opening=True
    )
    assert result.checked
    assert result.consistent
    assert result.pending_symbols == []
    assert result.summary() == "CLI confirms the book after submission"


def test_a_resting_limit_order_is_pending_not_broken(monkeypatch):
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: set())
    result = reconcile_after_submit(
        sdk_symbols=set(), expected_symbols=["SPY_A"], opening=True
    )
    assert result.consistent
    assert result.pending_symbols == ["SPY_A"]
    assert "not yet filled" in result.summary()


def test_a_closing_fill_is_confirmed_when_the_legs_disappear(monkeypatch):
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: set())
    result = reconcile_after_submit(
        sdk_symbols=set(), expected_symbols=["SPY_A"], opening=False
    )
    assert result.confirmed_symbols == ["SPY_A"]
    assert result.pending_symbols == []


def test_a_close_still_showing_in_the_book_is_pending(monkeypatch):
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: {"SPY_A"})
    result = reconcile_after_submit(
        sdk_symbols={"SPY_A"}, expected_symbols=["SPY_A"], opening=False
    )
    assert result.pending_symbols == ["SPY_A"]


def test_diverging_broker_views_are_inconsistent(monkeypatch):
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: {"QQQ_A"})
    result = reconcile_after_submit(
        sdk_symbols={"SPY_A"}, expected_symbols=["SPY_A"], opening=True
    )
    assert not result.consistent
    assert "diverged" in result.summary()


def test_a_fill_the_cli_sees_first_is_confirmed_not_a_split_book(monkeypatch):
    # Paper fills show up on the CLI a moment before a stale SDK snapshot.
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: {"SPY_A", "SPY_B"})
    result = reconcile_after_submit(
        sdk_symbols=set(), expected_symbols=["SPY_A", "SPY_B"], opening=True
    )
    assert result.consistent
    assert result.confirmed_symbols == ["SPY_A", "SPY_B"]
    assert result.pending_symbols == []


def test_a_close_the_cli_sees_first_is_confirmed_not_a_split_book(monkeypatch):
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: set())
    result = reconcile_after_submit(
        sdk_symbols={"SPY_A"}, expected_symbols=["SPY_A"], opening=False
    )
    assert result.consistent
    assert result.confirmed_symbols == ["SPY_A"]


def test_reconciliation_is_skipped_when_the_cli_cannot_read_positions(monkeypatch):
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: None)
    result = reconcile_after_submit(
        sdk_symbols=set(), expected_symbols=["SPY_A"], opening=True
    )
    assert not result.checked
    assert "skipped" in result.summary()


def test_expected_symbols_are_matched_case_insensitively(monkeypatch):
    monkeypatch.setattr(cli_bridge, "cli_position_symbols", lambda: {"SPY_A"})
    result = reconcile_after_submit(
        sdk_symbols={"SPY_A"}, expected_symbols=["spy_a"], opening=True
    )
    assert result.confirmed_symbols == ["SPY_A"]
