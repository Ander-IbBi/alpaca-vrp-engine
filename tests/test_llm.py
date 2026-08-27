from options_agent.agent.llm import (
    RuleBasedAdvisor,
    parse_advisor_review,
    safe_review,
)


def test_valid_soft_veto_is_honoured() -> None:
    review = parse_advisor_review(
        '{"approved": false, "explanation": "Quote is a minute stale.", '
        '"reject_reason": "stale_quote"}',
        advisor="openai",
    )
    assert review.approved is False
    assert review.reject_reason == "stale_quote"


def test_unknown_veto_reason_fails_open() -> None:
    review = parse_advisor_review(
        '{"approved": false, "explanation": "I do not like this strike.", '
        '"reject_reason": "bad_vibes"}',
        advisor="openai",
    )
    assert review.approved is True
    assert review.reject_reason is None


def test_garbage_output_fails_open() -> None:
    review = parse_advisor_review("sorry, cannot help", advisor="openai")
    assert review.approved is True
    assert "sorry" in review.explanation


def test_json_embedded_in_prose_is_parsed() -> None:
    review = parse_advisor_review(
        'Sure.\n{"approved": true, "explanation": "Collar is covered.", '
        '"reject_reason": null}\n',
        advisor="openai",
    )
    assert review.approved is True
    assert "covered" in review.explanation


def test_safe_review_swallows_advisor_exceptions() -> None:
    class Boom:
        name = "boom"

        def review(self, cycle_summary: str):
            raise RuntimeError("timeout")

    review = safe_review(Boom(), "anything")  # type: ignore[arg-type]
    assert review.approved is True
    assert "LLM unavailable" in review.explanation


def test_rule_based_advisor_always_approves() -> None:
    review = RuleBasedAdvisor().review("seed 100 SPY")
    assert review.approved is True
    assert review.advisor == "rule-based"
    assert "code checks passed" in review.explanation
