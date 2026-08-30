"""The LLM analyst: a tiny, bounded soft veto that always fails open."""

from __future__ import annotations

import json
import sys
import types

from vrp_engine.agent.analyst import (
    SOFT_VETO_REASONS,
    AnalystReview,
    OpenAIAnalyst,
    RuleBasedAnalyst,
    build_analyst,
    parse_analyst_review,
    safe_brief,
    safe_review,
)
from vrp_engine.config import Settings


class _Boom:
    name = "boom"

    def review(self, cycle_summary: str, market_context: str = "") -> AnalystReview:
        raise RuntimeError("model timed out")

    def brief(self, market_context: str) -> str:
        raise RuntimeError("model timed out")


class _Vetoing:
    name = "stub"

    def __init__(self, reason: str = "event_risk") -> None:
        self.reason = reason
        self.seen_context = ""

    def review(self, cycle_summary: str, market_context: str = "") -> AnalystReview:
        self.seen_context = market_context
        return AnalystReview(
            approved=False,
            explanation="headline risk",
            reject_reason=self.reason,
            analyst=self.name,
        )

    def brief(self, market_context: str) -> str:
        return "calm tape"


def _payload(**overrides) -> str:
    data = {"approved": True, "explanation": "looks fine", "reject_reason": None}
    data.update(overrides)
    return json.dumps(data)


# --- the veto vocabulary ----------------------------------------------------


def test_the_veto_reasons_are_a_closed_set():
    assert SOFT_VETO_REASONS == {
        "stale_quote",
        "duplicate",
        "wide_spread",
        "event_risk",
        "illiquid",
    }


# --- parsing model output ---------------------------------------------------


def test_a_clean_approval_parses():
    review = parse_analyst_review(_payload(), analyst="openai")
    assert review.approved
    assert review.explanation == "looks fine"
    assert review.analyst == "openai"


def test_a_recognised_veto_is_honoured():
    review = parse_analyst_review(
        _payload(approved=False, reject_reason="event_risk"), analyst="openai"
    )
    assert not review.approved
    assert review.reject_reason == "event_risk"


def test_an_invented_veto_reason_is_discarded():
    review = parse_analyst_review(
        _payload(approved=False, reject_reason="the vibes are off"), analyst="openai"
    )
    assert review.approved
    assert review.reject_reason is None


def test_a_veto_without_a_reason_is_discarded():
    review = parse_analyst_review(_payload(approved=False), analyst="openai")
    assert review.approved


def test_json_wrapped_in_prose_is_still_parsed():
    text = f"Sure, here is my review:\n{_payload(approved=False, reject_reason='illiquid')}\nDone."
    review = parse_analyst_review(text, analyst="openai")
    assert not review.approved
    assert review.reject_reason == "illiquid"


def test_free_text_without_json_becomes_an_approval_with_the_text_kept():
    review = parse_analyst_review("I think this is fine.", analyst="openai")
    assert review.approved
    assert review.explanation == "I think this is fine."


def test_malformed_json_fails_open():
    review = parse_analyst_review('{"approved": fals', analyst="openai")
    assert review.approved


def test_a_json_array_is_not_treated_as_a_review():
    review = parse_analyst_review("[1, 2, 3]", analyst="openai")
    assert review.approved


def test_an_empty_response_fails_open():
    assert parse_analyst_review("", analyst="openai").approved


def test_a_missing_explanation_falls_back_to_the_raw_text():
    review = parse_analyst_review('{"approved": true}', analyst="openai")
    assert review.explanation


# --- the rule-based fallback -----------------------------------------------


def test_the_rule_based_analyst_always_approves():
    review = RuleBasedAnalyst().review("summary")
    assert review.approved
    assert review.analyst == "rule-based"


def test_the_rule_based_analyst_briefs_honestly():
    assert "No LLM configured" in RuleBasedAnalyst().brief("context")


def test_without_an_api_key_the_rule_based_analyst_is_built():
    settings = Settings(alpaca_api_key="k", alpaca_secret_key="s", openai_api_key="")
    assert isinstance(build_analyst(settings), RuleBasedAnalyst)


def test_an_unbuildable_llm_analyst_falls_back_instead_of_crashing(monkeypatch):
    """A missing extra or a malformed key must not take the whole loop down at startup."""

    def explode(_settings):
        raise RuntimeError("no transport")

    monkeypatch.setattr("vrp_engine.agent.analyst.OpenAIAnalyst", explode)
    settings = Settings(alpaca_api_key="k", alpaca_secret_key="s", openai_api_key="sk-test")
    assert isinstance(build_analyst(settings), RuleBasedAnalyst)


def test_the_llm_client_is_built_with_a_bounded_timeout(monkeypatch):
    """The SDK default is a 600s read with two retries, longer than the cycle itself."""
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    settings = Settings(
        alpaca_api_key="k",
        alpaca_secret_key="s",
        openai_api_key="sk-test",
        openai_timeout_seconds=7.5,
        openai_max_retries=0,
    )
    analyst = build_analyst(settings)

    assert isinstance(analyst, OpenAIAnalyst)
    assert captured["timeout"] == 7.5
    assert captured["max_retries"] == 0


# --- failing open -----------------------------------------------------------


def test_a_crashing_analyst_becomes_an_approval():
    review = safe_review(_Boom(), "summary")
    assert review.approved
    assert "LLM unavailable" in review.explanation
    assert review.analyst == "boom"


def test_a_crashing_briefing_returns_a_note_rather_than_raising():
    assert "Briefing unavailable" in safe_brief(_Boom(), "context")


def test_a_working_veto_survives_the_safe_wrapper():
    review = safe_review(_Vetoing(), "summary")
    assert not review.approved
    assert review.reject_reason == "event_risk"


def test_the_market_context_reaches_the_analyst():
    analyst = _Vetoing()
    safe_review(analyst, "summary", "CPI print tomorrow")
    assert analyst.seen_context == "CPI print tomorrow"


def test_a_working_briefing_passes_straight_through():
    assert safe_brief(_Vetoing(), "context") == "calm tape"


def test_the_default_review_is_an_approval():
    assert AnalystReview().approved
