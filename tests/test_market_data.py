"""Bar normalisation: the input to every volatility estimate."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from vrp_engine.alpaca.market_data import history_from_bars


class RawBar:
    def __init__(self, timestamp, open_, high, low, close, volume=1000):
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def test_alpaca_style_objects_are_normalised():
    history = history_from_bars(
        [RawBar(datetime(2026, 8, 28, 20, 0, tzinfo=UTC), 100, 102, 99, 101)],
        symbol="spy",
    )
    assert history.symbol == "SPY"
    assert len(history.bars) == 1
    bar = history.bars[0]
    assert bar.day == date(2026, 8, 28)
    assert bar.close == pytest.approx(101)


def test_short_key_dicts_are_accepted():
    history = history_from_bars(
        [{"t": "2026-08-28T20:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 5}],
        symbol="QQQ",
    )
    assert history.bars[0].high == pytest.approx(11)
    assert history.bars[0].volume == pytest.approx(5)


def test_bars_are_sorted_oldest_first():
    history = history_from_bars(
        [
            {"t": "2026-08-28T20:00:00Z", "o": 2, "h": 2, "l": 2, "c": 2},
            {"t": "2026-08-26T20:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1},
        ],
        symbol="SPY",
    )
    assert [bar.close for bar in history.bars] == [1, 2]


def test_incomplete_bars_are_dropped_rather_than_guessed():
    history = history_from_bars(
        [
            {"t": "2026-08-28T20:00:00Z", "o": 1, "h": 1, "l": 1},
            {"t": "2026-08-27T20:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1},
        ],
        symbol="SPY",
    )
    assert len(history.bars) == 1


def test_bars_without_a_timestamp_are_dropped():
    history = history_from_bars([{"o": 1, "h": 1, "l": 1, "c": 1}], symbol="SPY")
    assert history.bars == []


def test_empty_payload_gives_an_empty_history():
    history = history_from_bars(None, symbol="SPY")
    assert history.bars == []
    assert history.last_close is None


def test_log_returns_length_is_one_less_than_bars():
    history = history_from_bars(
        [
            {"t": f"2026-08-{day:02d}T20:00:00Z", "o": 1, "h": 1, "l": 1, "c": 100 + day}
            for day in (10, 11, 12, 13)
        ],
        symbol="SPY",
    )
    assert len(history.log_returns()) == 3


def test_log_returns_skip_non_positive_prices():
    history = history_from_bars(
        [
            {"t": "2026-08-10T20:00:00Z", "o": 1, "h": 1, "l": 1, "c": 100},
            {"t": "2026-08-11T20:00:00Z", "o": 1, "h": 1, "l": 1, "c": 0},
            {"t": "2026-08-12T20:00:00Z", "o": 1, "h": 1, "l": 1, "c": 101},
        ],
        symbol="SPY",
    )
    assert history.log_returns() == []


def test_last_close_is_the_newest_bar():
    history = history_from_bars(
        [
            {"t": "2026-08-10T20:00:00Z", "o": 1, "h": 1, "l": 1, "c": 100},
            {"t": "2026-08-12T20:00:00Z", "o": 1, "h": 1, "l": 1, "c": 105},
        ],
        symbol="SPY",
    )
    assert history.last_close == pytest.approx(105)
