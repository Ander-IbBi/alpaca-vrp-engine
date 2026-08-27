"""Optional LLM layer.

The agent runs without it: the strategy is deterministic and the risk layer is
code. The LLM explains each cycle and may apply a *soft* veto on an allow-listed
reason. It cannot change strikes, cannot approve a risk reject, and a timeout
fails open so the unattended loop keeps working.
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel

from options_agent.config import Settings

SOFT_VETO_REASONS = frozenset({"stale_quote", "duplicate", "wide_spread"})


class AdvisorReview(BaseModel):
    """Structured second look. `approved=True` means 'no objection', not a risk bypass."""

    approved: bool = True
    explanation: str = ""
    reject_reason: str | None = None
    advisor: str = "rule-based"


class Advisor(Protocol):
    name: str

    def review(self, cycle_summary: str) -> AdvisorReview: ...


class RuleBasedAdvisor:
    """Fallback used when no API key is configured, or as a fail-open stand-in."""

    name = "rule-based"

    def review(self, cycle_summary: str) -> AdvisorReview:
        return AdvisorReview(
            approved=True,
            explanation="Rule-based advisor: code checks passed; no LLM configured.",
            advisor=self.name,
        )


def parse_advisor_review(text: str, *, advisor: str) -> AdvisorReview:
    """Parse model JSON. Unknown veto reasons and garbage output fail open."""
    data = _extract_json_object(text)
    if data is None:
        return AdvisorReview(approved=True, explanation=text.strip(), advisor=advisor)

    reason = data.get("reject_reason")
    if reason is not None:
        reason = str(reason)
    approved = bool(data.get("approved", True))
    if not approved and reason not in SOFT_VETO_REASONS:
        # Hallucinated veto (or missing reason) must not stall the playbook.
        approved = True
        reason = None
    explanation = str(data.get("explanation") or text.strip())
    return AdvisorReview(
        approved=approved,
        explanation=explanation,
        reject_reason=None if approved else reason,
        advisor=advisor,
    )


def _extract_json_object(text: str) -> dict | None:
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        loaded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


class OpenAIAdvisor:
    """Thin wrapper; requires `uv sync --extra llm` and OPENAI_API_KEY."""

    name = "openai"

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI  # imported lazily so the base install stays small

        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    def review(self, cycle_summary: str) -> AdvisorReview:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You review an options overlay agent's already-built ticket. "
                        "Reply with JSON only: "
                        '{"approved": true, "explanation": "two sentences", '
                        '"reject_reason": null}. '
                        "Set approved false only for stale_quote, duplicate, or "
                        "wide_spread. Do not change strikes. No financial advice."
                    ),
                },
                {"role": "user", "content": cycle_summary},
            ],
        )
        content = response.choices[0].message.content or ""
        return parse_advisor_review(content, advisor=self.name)


def build_advisor(settings: Settings) -> Advisor:
    if settings.openai_api_key:
        try:
            return OpenAIAdvisor(settings)
        except ImportError:
            return RuleBasedAdvisor()
    return RuleBasedAdvisor()


def safe_review(advisor: Advisor, cycle_summary: str) -> AdvisorReview:
    """Any LLM failure becomes an approval so the unattended loop does not stall."""
    try:
        return advisor.review(cycle_summary)
    except Exception as exc:  # noqa: BLE001 — fail-open is the product rule
        return AdvisorReview(
            approved=True,
            explanation=f"LLM unavailable ({type(exc).__name__}); proceeding on code checks.",
            advisor=getattr(advisor, "name", "unknown"),
        )
