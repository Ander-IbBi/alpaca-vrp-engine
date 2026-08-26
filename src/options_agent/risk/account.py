"""Account-level circuit breaker.

A single order can look fine while the account is already bleeding, so the agent
checks the whole account before it is allowed to trade at all.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from options_agent.config import Settings, load_settings


class AccountGuardResult(BaseModel):
    trading_allowed: bool
    reasons: list[str] = Field(default_factory=list)
    equity: float | None = None
    day_pl: float | None = None


def check_account_guardrails(
    *,
    equity: float,
    last_equity: float,
    settings: Settings | None = None,
) -> AccountGuardResult:
    """Stop trading on an equity floor breach or a daily loss beyond the budget."""
    config = settings or load_settings()
    reasons: list[str] = []

    day_pl = equity - last_equity
    if equity < config.min_equity_usd:
        reasons.append(f"equity {equity:.0f} below floor {config.min_equity_usd:.0f}")
    if day_pl < -config.max_daily_loss_usd:
        reasons.append(
            f"day P&L {day_pl:.0f} beyond daily loss budget {config.max_daily_loss_usd:.0f}"
        )

    return AccountGuardResult(
        trading_allowed=not reasons,
        reasons=reasons,
        equity=equity,
        day_pl=day_pl,
    )
