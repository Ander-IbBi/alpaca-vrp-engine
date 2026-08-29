"""Optional LLM analyst.

The engine runs without it. The strategy is deterministic, the risk layer is code, and
the sizing is arithmetic. So what is the LLM actually for?

One thing the code genuinely cannot do: read the news. The term-structure blackout is a
good proxy for a *scheduled* event, but it cannot see an unscheduled one. The analyst
gets the research plane's headlines alongside the already-built ticket and may raise a
**soft veto** on one of a fixed set of reasons.

Its powers are deliberately tiny. It cannot change a strike, cannot resize, cannot
approve something risk rejected, and cannot invent a veto reason. A hallucinated or
unrecognised veto is discarded, and any failure or timeout fails open so the unattended
loop keeps running.
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel

from vrp_engine.config import Settings

# The only grounds on which the analyst may stop a ticket the code already approved.
SOFT_VETO_REASONS = frozenset(
    {"stale_quote", "duplicate", "wide_spread", "event_risk", "illiquid"}
)

SYSTEM_PROMPT = (
    "You review an options agent's already-built, already-risk-approved ticket. "
    "The strategy sells option premium when implied volatility exceeds realised "
    "volatility and buys it when the reverse holds, always as a defined-risk spread. "
    "You cannot change strikes, sizes or prices. "
    "Reply with JSON only: "
    '{"approved": true, "explanation": "two sentences", "reject_reason": null}. '
    "Set approved false only when the market context reveals a problem the numbers "
    "cannot see, and only with reject_reason in "
    "[stale_quote, duplicate, wide_spread, event_risk, illiquid]. "
    "Prefer approving. No financial advice."
)

BRIEFING_PROMPT = (
    "You are writing a three-sentence market briefing for a volatility-selling "
    "options agent. Cover the tone of the tape and any event risk in the headlines. "
    "State facts only, no recommendations, no financial advice."
)


class AnalystReview(BaseModel):
    """Structured second look. `approved=True` means 'no objection', not a risk bypass."""

    approved: bool = True
    explanation: str = ""
    reject_reason: str | None = None
    analyst: str = "rule-based"


class Analyst(Protocol):
    name: str

    def review(self, cycle_summary: str, market_context: str = "") -> AnalystReview: ...

    def brief(self, market_context: str) -> str: ...


class RuleBasedAnalyst:
    """Fallback used when no API key is configured, or as a fail-open stand-in."""

    name = "rule-based"

    def review(self, cycle_summary: str, market_context: str = "") -> AnalystReview:
        return AnalystReview(
            approved=True,
            explanation="Rule-based analyst: code checks passed; no LLM configured.",
            analyst=self.name,
        )

    def brief(self, market_context: str) -> str:
        return "No LLM configured; the engine is running on its own signals."


def parse_analyst_review(text: str, *, analyst: str) -> AnalystReview:
    """Parse model JSON. Unknown veto reasons and garbage output fail open."""
    data = _extract_json_object(text)
    if data is None:
        return AnalystReview(approved=True, explanation=text.strip(), analyst=analyst)

    reason = data.get("reject_reason")
    if reason is not None:
        reason = str(reason)
    approved = bool(data.get("approved", True))
    if not approved and reason not in SOFT_VETO_REASONS:
        # A veto the model made up must not stall the playbook.
        approved = True
        reason = None
    explanation = str(data.get("explanation") or text.strip())
    return AnalystReview(
        approved=approved,
        explanation=explanation,
        reject_reason=None if approved else reason,
        analyst=analyst,
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


class OpenAIAnalyst:
    """Thin wrapper; requires `uv sync --extra llm` and OPENAI_API_KEY."""

    name = "openai"

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI  # imported lazily so the base install stays small

        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    def _complete(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

    def review(self, cycle_summary: str, market_context: str = "") -> AnalystReview:
        user = cycle_summary
        if market_context:
            user = f"{cycle_summary}\n\n--- market context ---\n{market_context}"
        return parse_analyst_review(self._complete(SYSTEM_PROMPT, user), analyst=self.name)

    def brief(self, market_context: str) -> str:
        return self._complete(BRIEFING_PROMPT, market_context).strip()


def build_analyst(settings: Settings) -> Analyst:
    if settings.openai_api_key:
        try:
            return OpenAIAnalyst(settings)
        except ImportError:
            return RuleBasedAnalyst()
    return RuleBasedAnalyst()


def safe_review(
    analyst: Analyst,
    cycle_summary: str,
    market_context: str = "",
) -> AnalystReview:
    """Any LLM failure becomes an approval so the unattended loop does not stall."""
    try:
        return analyst.review(cycle_summary, market_context)
    except Exception as exc:  # noqa: BLE001 — fail-open is the product rule
        return AnalystReview(
            approved=True,
            explanation=f"LLM unavailable ({type(exc).__name__}); proceeding on code checks.",
            analyst=getattr(analyst, "name", "unknown"),
        )


def safe_brief(analyst: Analyst, market_context: str) -> str:
    try:
        return analyst.brief(market_context)
    except Exception as exc:  # noqa: BLE001
        return f"Briefing unavailable ({type(exc).__name__})."
