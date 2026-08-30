"""How the command line resolves whether a run trades, and how long it waits.

The interesting property is not the flag plumbing but the default: `run-agent` with no
arguments has to send tickets, because an agent that waits to be told is not autonomous.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vrp_engine.cli import execution_mode, sleep_seconds


def test_a_plain_run_trades():
    executes, announcement = execution_mode(dry_run_requested=False, dry_run_setting=False)
    assert executes is True
    assert "AUTONOMOUS" in announcement


def test_the_command_line_can_ask_for_a_rehearsal():
    executes, announcement = execution_mode(dry_run_requested=True, dry_run_setting=False)
    assert executes is False
    assert "--dry-run" in announcement


def test_a_leftover_env_flag_is_named_in_the_announcement():
    """A silent dry run looks exactly like an agent that found nothing. Say which it is."""
    executes, announcement = execution_mode(dry_run_requested=False, dry_run_setting=True)
    assert executes is False
    assert "DRY_RUN=true" in announcement


def test_the_command_line_wins_when_both_ask_for_a_rehearsal():
    executes, announcement = execution_mode(dry_run_requested=True, dry_run_setting=True)
    assert executes is False
    assert "--dry-run" in announcement


# --- how long the loop waits ------------------------------------------------

NOW = datetime(2026, 8, 31, 13, 20, tzinfo=UTC)


def test_an_open_market_keeps_the_normal_cadence():
    assert sleep_seconds(is_open=True, interval=180, now=NOW, next_open=NOW) == 180


def test_a_shut_market_polls_lazily():
    """Nothing to do overnight, so stop hammering the API for a closed clock."""
    tomorrow = NOW + timedelta(hours=18)
    assert sleep_seconds(is_open=False, interval=180, now=NOW, next_open=tomorrow) == 1800


def test_the_loop_never_sleeps_through_the_opening_bell():
    """Waking at 09:59 means missing the first half hour of the only session that counts."""
    opens_in_ten_minutes = NOW + timedelta(minutes=10)
    waited = sleep_seconds(is_open=False, interval=180, now=NOW, next_open=opens_in_ten_minutes)
    assert 600 <= waited <= 660


def test_the_wake_up_lands_after_the_bell_not_before_it():
    """A cycle a second early sees a shut market and goes back to sleep for half an hour."""
    opens_soon = NOW + timedelta(seconds=300)
    assert sleep_seconds(is_open=False, interval=180, now=NOW, next_open=opens_soon) > 300


def test_a_bell_already_rung_resumes_the_normal_cadence():
    already_open = NOW - timedelta(minutes=5)
    assert sleep_seconds(is_open=False, interval=180, now=NOW, next_open=already_open) == 180


def test_the_wait_never_drops_below_the_interval():
    """Otherwise a clock a few seconds from the open would spin the loop."""
    imminent = NOW + timedelta(seconds=1)
    assert sleep_seconds(is_open=False, interval=180, now=NOW, next_open=imminent) == 180


def test_an_unreadable_clock_falls_back_to_the_lazy_poll():
    assert sleep_seconds(is_open=False, interval=180, now=None, next_open=None) == 1800
