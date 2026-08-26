"""Optional LLM layer.

The agent runs fine without it: the strategy is deterministic and the risk layer is
code. The LLM adds reasoning and a readable explanation of each cycle, which is
what the hackathon judges actually watch.
"""

from __future__ import annotations

from typing import Protocol

from options_agent.config import Settings


class Advisor(Protocol):
    def explain(self, cycle_summary: str) -> str: ...


class RuleBasedAdvisor:
    """Fallback used when no API key is configured."""

    name = "rule-based"

    def explain(self, cycle_summary: str) -> str:
        return cycle_summary


class OpenAIAdvisor:
    """Thin wrapper; requires `uv sync --extra llm` and OPENAI_API_KEY."""

    name = "openai"

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI  # imported lazily so the base install stays small

        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    def explain(self, cycle_summary: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You explain an options hedging agent's decision to a trader. "
                        "Two sentences, concrete, no financial advice."
                    ),
                },
                {"role": "user", "content": cycle_summary},
            ],
        )
        return response.choices[0].message.content or ""


def build_advisor(settings: Settings) -> Advisor:
    if settings.openai_api_key:
        try:
            return OpenAIAdvisor(settings)
        except ImportError:
            return RuleBasedAdvisor()
    return RuleBasedAdvisor()
