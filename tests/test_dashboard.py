"""The dashboard, executed headless.

`AppTest` runs `app/streamlit_app.py` end to end in-process. That is the only thing
that catches a rendering error which would otherwise surface for the first time on
Streamlit Community Cloud, in front of whoever opened the demo link.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import streamlit as st
from conftest import TODAY, FakeAccount, FakeAlpaca, FakePosition, build_chain, build_history
from streamlit.testing.v1 import AppTest

from vrp_engine.alpaca import client as client_module
from vrp_engine.alpaca import market_data as market_data_module
from vrp_engine.alpaca import options as options_module

APP_PATH = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
EXPIRY = TODAY + timedelta(days=7)
TAB_LABELS = ["Overview", "Risk", "Opportunities", "Journal", "How it works"]


@pytest.fixture(autouse=True)
def dashboard_env(monkeypatch, tmp_path):
    """A hosted instance with no local journal, so the bundled sample is used.

    The caches are process-wide, so they have to be cleared between runs or the
    second test would replay the first one's account.
    """
    monkeypatch.setenv("JOURNAL_PATH", str(tmp_path / "absent.jsonl"))
    monkeypatch.setenv("UNIVERSE", "SPY")
    monkeypatch.setenv("BETA_BUCKET", "SPY")
    st.cache_data.clear()
    st.cache_resource.clear()
    yield
    st.cache_data.clear()
    st.cache_resource.clear()


def run_app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=60).run()


# --- without credentials, which is how a visitor first sees it ----------------


def test_the_page_renders_without_any_credentials():
    at = run_app()
    assert not at.exception


def test_all_five_tabs_are_there_even_with_no_account():
    assert [tab.label for tab in run_app().tabs] == TAB_LABELS


def test_a_visitor_is_told_the_live_account_is_missing():
    warnings = run_app().warning
    # Exactly one: a second would mean Streamlit is flagging a deprecated call.
    assert len(warnings) == 1
    assert "Live account unavailable" in warnings[0].value


def test_the_recorded_journal_stands_in_for_the_missing_account():
    notices = " ".join(str(element.value) for element in run_app().caption)
    assert "recorded trail from a paper session" in notices


def test_the_charts_are_drawn_from_the_sample_rather_than_left_blank():
    # Volatility map, wedge dumbbell, budgets, buckets, stress, decision timeline.
    assert len(run_app().get("vega_lite_chart")) >= 5


def test_the_cycle_diagram_explains_the_agent_to_a_first_time_reader():
    assert len(run_app().get("graphviz_chart")) == 1


def test_the_hard_rules_are_stated_where_a_reader_will_find_them():
    body = " ".join(str(element.value) for element in run_app().markdown)
    assert "No naked shorts." in body
    assert "No live trading." in body


# --- with a paper account wired to the fakes ----------------------------------


@pytest.fixture
def wired_account(monkeypatch):
    """Patch every network boundary the page touches, before the app imports them."""
    histories = {"SPY": build_history(days=90, start=500.0, daily_vol=0.008)}
    chains = {"SPY": build_chain(spot=500.0, expiration=EXPIRY, implied_vol=0.35)}
    positions = [
        FakePosition("SPY260904P00490000", -2, current_price=2.0, unrealized_pl=40.0),
        FakePosition("SPY260904P00485000", 2, current_price=1.0, unrealized_pl=-10.0),
    ]

    class DashboardAlpaca(FakeAlpaca):
        def __init__(self, settings):
            super().__init__(
                settings=settings,
                account=FakeAccount(equity=100_412.0, last_equity=100_000.0),
                positions=positions,
                histories=histories,
                chains=chains,
                market_open=True,
            )

    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setattr(client_module, "PaperAlpaca", DashboardAlpaca)
    monkeypatch.setattr(
        market_data_module,
        "fetch_daily_bars",
        lambda client, symbols, **_: {s: client.histories[s] for s in symbols
                                      if s in client.histories},
    )
    monkeypatch.setattr(
        options_module,
        "fetch_quoted_chain",
        lambda client, symbol, **_: client.chains.get(symbol, []),
    )
    monkeypatch.setattr(options_module, "fetch_snapshots_for", lambda client, symbols: [])
    monkeypatch.setattr(options_module, "market_date", lambda now=None: TODAY)


def test_the_page_renders_against_a_live_paper_account(wired_account):
    at = run_app()
    assert not at.exception


def test_the_account_metrics_appear_once_the_keys_work(wired_account):
    labels = [metric.label for metric in run_app().metric]
    assert "Equity" in labels
    assert "Options buying power" in labels


def test_a_working_account_is_not_reported_as_unavailable(wired_account):
    assert not run_app().warning


def test_the_open_book_is_drawn_as_a_payoff_curve(wired_account):
    at = run_app()
    assert "Portfolio payoff at expiry" in [element.value for element in at.subheader]
    # The equity curve and the payoff curve are the two the sample cannot produce.
    assert len(at.get("vega_lite_chart")) >= 8
    assert not any("payoff curve needs live positions" in info.value for info in at.info)


# --- the safety rule the page must never break --------------------------------


def test_the_page_refuses_to_render_if_the_live_flag_is_set(monkeypatch):
    monkeypatch.setenv("ALPACA_LIVE_TRADE", "true")
    at = run_app()
    assert not at.exception
    assert any("Refusing to render" in error.value for error in at.error)
