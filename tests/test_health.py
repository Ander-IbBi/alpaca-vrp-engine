"""The heartbeat: is the loop still writing cycles, and should anyone worry?

The agent journals every cycle, including the ones that failed, so the age of the last
line is the pulse. The subtlety worth testing is that the loop deliberately slows down
once the session shuts, so the same silence means different things at 15:00 and at 03:00.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from vrp_engine.health import (
    LATE,
    OK,
    STALE,
    UNKNOWN,
    assess,
    first_line,
    format_age,
    heartbeat,
    process_alive,
    read_pid,
    thresholds,
    verdict,
)

NOW = datetime(2026, 8, 31, 15, 0, tzinfo=UTC)
INTERVAL = 180


def cycle(*, minutes_ago: float = 0.0, market_open: bool = True, **overrides) -> dict:
    entry = {
        "ts": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "kind": "cycle",
        "market_open": market_open,
        "equity": 100_412.0,
        "proposal": {"action": "hold"},
    }
    entry.update(overrides)
    return entry


def state_of(*, minutes_ago: float, market_open: bool = True) -> str:
    beat = heartbeat([cycle(minutes_ago=minutes_ago, market_open=market_open)], now=NOW)
    return verdict(beat, interval=INTERVAL)


# --- reading the pulse -------------------------------------------------------


def test_an_empty_journal_has_no_pulse_to_read():
    beat = heartbeat([], now=NOW)
    assert beat.age_seconds is None
    assert beat.cycles == 0
    assert verdict(beat) == UNKNOWN


def test_the_most_recent_cycle_is_the_one_that_counts():
    beat = heartbeat(
        [cycle(minutes_ago=90), cycle(minutes_ago=1, equity=101_000.0)], now=NOW
    )
    assert beat.age_seconds == pytest.approx(60.0)
    assert beat.equity == pytest.approx(101_000.0)
    assert beat.cycles == 2


def test_a_cycle_that_decided_nothing_still_reports_hold():
    assert heartbeat([cycle(proposal=None)], now=NOW).action == "hold"


def test_a_sent_ticket_is_visible_in_the_pulse():
    beat = heartbeat([cycle(execution={"submitted": True, "order_id": "x"})], now=NOW)
    assert beat.submitted is True


def test_a_failed_cycle_is_surfaced_rather_than_looking_healthy():
    beat = heartbeat(
        [cycle(notes=["Cycle failed: APIError: 502 Bad Gateway"])], now=NOW
    )
    assert beat.failed is True
    assert "502" in beat.failure


def test_an_ordinary_note_is_not_mistaken_for_a_failure():
    assert heartbeat([cycle(notes=["Market closed; dry run only."])], now=NOW).failed is False


def test_an_unreadable_equity_reading_reports_nothing_rather_than_zero():
    assert heartbeat([cycle(equity="unavailable")], now=NOW).equity is None


def test_a_timestamp_without_a_zone_is_read_as_utc():
    naive = NOW.replace(tzinfo=None) - timedelta(minutes=2)
    beat = heartbeat([cycle(ts=naive.isoformat())], now=NOW)
    assert beat.age_seconds == pytest.approx(120.0)


def test_an_unparseable_timestamp_leaves_the_age_unknown():
    beat = heartbeat([cycle(ts="not-a-date")], now=NOW)
    assert beat.age_seconds is None
    assert verdict(beat) == UNKNOWN


# --- judging the silence -----------------------------------------------------


def test_a_cycle_a_minute_ago_during_the_session_is_healthy():
    assert state_of(minutes_ago=1) == OK


def test_two_missed_cycles_during_the_session_is_worth_flagging():
    assert state_of(minutes_ago=7) == LATE


def test_four_missed_cycles_during_the_session_means_it_stopped():
    assert state_of(minutes_ago=15) == STALE


def test_the_same_silence_out_of_hours_is_perfectly_normal():
    # The loop sleeps up to an hour once the market shuts, so 15 minutes is nothing.
    assert state_of(minutes_ago=15, market_open=False) == OK


def test_a_whole_morning_of_silence_out_of_hours_still_means_it_stopped():
    assert state_of(minutes_ago=180, market_open=False) == STALE


def test_an_unknown_session_is_treated_as_closed_so_the_panel_stays_believable():
    beat = heartbeat([cycle(minutes_ago=15, market_open=None)], now=NOW)
    assert verdict(beat, interval=INTERVAL) == OK


def test_the_open_thresholds_follow_the_loop_cadence():
    late, stale = thresholds(market_open=True, interval=INTERVAL)
    assert (late, stale) == (2 * INTERVAL, 4 * INTERVAL)


def test_the_closed_thresholds_are_fixed_hours_not_multiples_of_the_cadence():
    assert thresholds(market_open=False, interval=INTERVAL) == (3600.0, 7200.0)


# --- the pulse is not the whole story ----------------------------------------
#
# A warm last line is not proof of health. These two cases both read green until the
# panel learned to cross-check the pulse against what the process is really doing.


def test_a_dead_process_is_not_healthy_however_warm_the_last_line_is():
    beat = heartbeat([cycle(minutes_ago=1)], now=NOW)
    assert verdict(beat, interval=INTERVAL) == OK
    call = assess(beat, interval=INTERVAL, pid_present=True, process_alive=False)
    assert call.state == STALE
    assert "gone" in call.reason


def test_an_agent_failing_every_cycle_is_not_healthy_either():
    beat = heartbeat([cycle(minutes_ago=1, notes=["Cycle failed: APIError: 401"])], now=NOW)
    assert verdict(beat, interval=INTERVAL) == OK
    assert assess(beat, interval=INTERVAL, pid_present=True, process_alive=True).state == LATE


def test_a_healthy_agent_with_a_live_process_stays_green():
    beat = heartbeat([cycle(minutes_ago=1)], now=NOW)
    call = assess(beat, interval=INTERVAL, pid_present=True, process_alive=True)
    assert call.state == OK
    assert call.reason == "cycling normally"


def test_no_pid_file_does_not_by_itself_condemn_a_running_agent():
    # `run-agent --loop` started by hand writes no pid file, and that is fine.
    beat = heartbeat([cycle(minutes_ago=1)], now=NOW)
    assert assess(beat, interval=INTERVAL, pid_present=False, process_alive=False).state == OK


def test_a_long_silence_still_wins_over_a_live_process():
    beat = heartbeat([cycle(minutes_ago=15)], now=NOW)
    assert assess(beat, interval=INTERVAL, pid_present=True, process_alive=True).state == STALE


def test_an_empty_journal_is_reported_as_having_nothing_to_say():
    call = assess(heartbeat([], now=NOW), interval=INTERVAL)
    assert call.state == UNKNOWN
    assert "nothing" in call.reason


# --- presentation ------------------------------------------------------------


def test_an_html_error_page_is_cut_down_to_something_a_panel_can_show():
    page = (
        "Cycle failed: APIError: <html>\n"
        "<head><title>401 Authorization Required</title></head>\n"
        "<body>"
    )
    trimmed = first_line(page)
    assert trimmed == "Cycle failed: APIError: <html>"
    assert "\n" not in trimmed


def test_a_very_long_line_is_truncated_rather_than_wrapped():
    trimmed = first_line("x" * 400)
    assert len(trimmed) == 110
    assert trimmed.endswith("…")


def test_nothing_to_report_stays_empty():
    assert first_line("") == ""


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "never"), (47.0, "47 s"), (600.0, "10 min"), (7200.0, "2.0 h")],
)
def test_a_silence_is_reported_in_units_a_human_can_judge(seconds, expected):
    assert format_age(seconds) == expected


# --- the process behind the pulse --------------------------------------------


def test_the_pid_file_written_by_the_launcher_reads_back(tmp_path):
    path = tmp_path / "agent.pid"
    path.write_text("4242\n", encoding="utf-8")
    assert read_pid(path) == 4242


@pytest.mark.parametrize("contents", ["", "not-a-pid", "0", "-5"])
def test_a_damaged_pid_file_reads_as_nothing(tmp_path, contents):
    path = tmp_path / "agent.pid"
    path.write_text(contents, encoding="utf-8")
    assert read_pid(path) is None


def test_a_missing_pid_file_reads_as_nothing(tmp_path):
    assert read_pid(tmp_path / "absent.pid") is None


def test_this_very_process_counts_as_alive():
    assert process_alive(os.getpid()) is True


def test_a_process_id_nobody_owns_counts_as_gone():
    assert process_alive(2_147_483_646) is False


def test_no_pid_at_all_counts_as_gone():
    assert process_alive(None) is False
