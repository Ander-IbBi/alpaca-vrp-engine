"""How the command line resolves whether a run trades.

The interesting property is not the flag plumbing but the default: `run-agent` with no
arguments has to send tickets, because an agent that waits to be told is not autonomous.
"""

from __future__ import annotations

from vrp_engine.cli import execution_mode


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
