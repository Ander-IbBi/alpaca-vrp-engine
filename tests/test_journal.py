"""The decision trail: append-only, tolerant of a truncated tail."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vrp_engine.journal import DEMO_JOURNAL_PATH, Journal, read_entries


@pytest.fixture
def journal(tmp_path) -> Journal:
    return Journal(tmp_path / "nested" / "journal.jsonl")


def test_a_missing_journal_reads_as_empty(journal):
    assert journal.read_all() == []


def test_appending_creates_the_parent_directory(journal):
    journal.append("cycle", {"equity": 100_000.0})
    assert journal.path.exists()


def test_every_entry_is_stamped_and_kinded(journal):
    entry = journal.append("cycle", {"equity": 100_000.0})
    assert entry["kind"] == "cycle"
    assert entry["ts"]


def test_entries_round_trip_through_the_file(journal):
    journal.append("cycle", {"equity": 100_000.0, "note": "first"})
    journal.append("cycle", {"equity": 101_000.0, "note": "second"})
    entries = journal.read_all()
    assert [e["note"] for e in entries] == ["first", "second"]


def test_the_file_holds_one_json_object_per_line(journal):
    journal.append("cycle", {"equity": 1.0})
    journal.append("cycle", {"equity": 2.0})
    lines = journal.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["kind"] == "cycle" for line in lines)


def test_unserialisable_values_fall_back_to_their_string_form(journal):
    class Opaque:
        def __str__(self) -> str:
            return "opaque-value"

    journal.append("cycle", {"thing": Opaque()})
    assert journal.read_all()[0]["thing"] == "opaque-value"


def test_the_tail_returns_the_most_recent_entries(journal):
    for i in range(10):
        journal.append("cycle", {"equity": float(i)})
    assert [e["equity"] for e in journal.tail(3)] == [7.0, 8.0, 9.0]


def test_the_tail_of_a_short_journal_returns_everything(journal):
    journal.append("cycle", {"equity": 1.0})
    assert len(journal.tail(20)) == 1


def test_a_truncated_final_line_is_skipped_rather_than_fatal(journal):
    journal.append("cycle", {"equity": 1.0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "cycle", "equ')
    assert len(journal.read_all()) == 1


def test_blank_lines_are_ignored(journal):
    journal.append("cycle", {"equity": 1.0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("\n\n")
    assert len(journal.read_all()) == 1


def test_the_high_water_mark_is_the_peak_equity_ever_recorded(journal):
    for equity in (100_000.0, 108_000.0, 103_000.0):
        journal.append("cycle", {"equity": equity})
    assert journal.high_water_mark() == pytest.approx(108_000.0)


def test_the_high_water_mark_is_none_without_any_equity_entries(journal):
    journal.append("note", {"message": "started"})
    assert journal.high_water_mark() is None


def test_non_numeric_equity_values_do_not_break_the_high_water_mark(journal):
    journal.append("cycle", {"equity": "unavailable"})
    journal.append("cycle", {"equity": 99_000.0})
    assert journal.high_water_mark() == pytest.approx(99_000.0)


def test_read_entries_prefers_the_live_journal_when_it_has_lines(tmp_path):
    live = tmp_path / "agent.jsonl"
    Journal(live).append("cycle", {"equity": 1.0})
    fallback = tmp_path / "demo.jsonl"
    Journal(fallback).append("cycle", {"equity": 99.0})
    entries, from_sample = read_entries(live, fallback=fallback)
    assert from_sample is False
    assert entries[0]["equity"] == pytest.approx(1.0)


def test_read_entries_falls_back_when_the_live_journal_is_missing(tmp_path):
    fallback = tmp_path / "demo.jsonl"
    Journal(fallback).append("cycle", {"equity": 2.0})
    entries, from_sample = read_entries(tmp_path / "absent.jsonl", fallback=fallback)
    assert from_sample is True
    assert entries[0]["equity"] == pytest.approx(2.0)


def test_read_entries_falls_back_when_the_live_journal_is_empty(tmp_path):
    live = tmp_path / "agent.jsonl"
    live.write_text("", encoding="utf-8")
    fallback = tmp_path / "demo.jsonl"
    Journal(fallback).append("cycle", {"equity": 3.0})
    entries, from_sample = read_entries(live, fallback=fallback)
    assert from_sample is True
    assert entries[0]["equity"] == pytest.approx(3.0)


def test_read_entries_without_a_fallback_returns_empty(tmp_path):
    entries, from_sample = read_entries(tmp_path / "absent.jsonl", fallback=None)
    assert entries == []
    assert from_sample is False


def test_the_bundled_demo_journal_replays_a_scanner_and_a_rationale():
    entries, from_sample = read_entries(Path("no-such-journal.jsonl"))
    assert from_sample is True
    assert DEMO_JOURNAL_PATH.exists()
    assert len(entries) >= 2
    assert any((entry.get("scan") or {}).get("top") for entry in entries)
    assert any((entry.get("proposal") or {}).get("rationale") for entry in entries)
