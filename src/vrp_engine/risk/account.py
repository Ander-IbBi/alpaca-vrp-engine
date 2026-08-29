"""Account-level circuit breakers and the session window.

A single ticket can look perfect while the account is already bleeding, so the whole
account is checked before any new risk is allowed. The distinction that matters here
is between *stopping new risk* and *stopping trading*: when a breaker fires the engine
must still be able to close positions, otherwise a breaker would trap the book it was
meant to protect.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from vrp_engine.config import Settings, load_settings

US_EASTERN = ZoneInfo("America/New_York")
# Regular US equity session. Early closes are rare and the window only shifts when a
# ticket is allowed to open, never whether one may be closed.
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


class TradingWindow(BaseModel):
    """Is the clock in the part of the session where opening a position makes sense?"""

    open_for_new_risk: bool
    reason: str = ""
    minutes_to_close: int | None = None


def trading_window(
    now: datetime,
    *,
    open_delay_minutes: int,
    no_new_risk_before_close_minutes: int,
) -> TradingWindow:
    """Skip the opening auction and stop opening near the bell.

    The first minutes print wide, unstable quotes that flatter every edge estimate;
    the last minutes turn an unfilled day order into unwanted overnight exposure.
    """
    moment = now.astimezone(US_EASTERN) if now.tzinfo else now.replace(tzinfo=US_EASTERN)
    current = moment.timetz().replace(tzinfo=None)

    minutes_now = current.hour * 60 + current.minute
    open_minutes = SESSION_OPEN.hour * 60 + SESSION_OPEN.minute
    close_minutes = SESSION_CLOSE.hour * 60 + SESSION_CLOSE.minute

    if minutes_now < open_minutes:
        return TradingWindow(
            open_for_new_risk=False,
            reason="the session has not opened yet",
            minutes_to_close=max(close_minutes - minutes_now, 0),
        )
    if minutes_now < open_minutes + open_delay_minutes:
        return TradingWindow(
            open_for_new_risk=False,
            reason=f"within the first {open_delay_minutes} minutes of the session",
            minutes_to_close=max(close_minutes - minutes_now, 0),
        )
    if minutes_now >= close_minutes:
        return TradingWindow(
            open_for_new_risk=False,
            reason="the session has closed",
            minutes_to_close=0,
        )
    if minutes_now > close_minutes - no_new_risk_before_close_minutes:
        return TradingWindow(
            open_for_new_risk=False,
            reason=f"within {no_new_risk_before_close_minutes} minutes of the close",
            minutes_to_close=max(close_minutes - minutes_now, 0),
        )
    return TradingWindow(
        open_for_new_risk=True,
        minutes_to_close=max(close_minutes - minutes_now, 0),
    )


class AccountGuardResult(BaseModel):
    """What the account permits right now."""

    new_risk_allowed: bool = True
    flatten_required: bool = False
    reasons: list[str] = Field(default_factory=list)
    equity: float | None = None
    day_pl: float | None = None
    high_water_mark: float | None = None
    drawdown_pct: float | None = None
    minutes_to_close: int | None = None

    def summary(self) -> str:
        if self.flatten_required:
            return "flatten required: " + "; ".join(self.reasons)
        if not self.new_risk_allowed:
            return "no new risk: " + "; ".join(self.reasons)
        return "account clear for new risk"


def check_account_guardrails(
    *,
    equity: float,
    last_equity: float,
    high_water_mark: float | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> AccountGuardResult:
    """Equity floor, daily loss budget, drawdown from the high-water mark, and clock."""
    config = settings or load_settings()
    reasons: list[str] = []
    flatten = False

    peak = max(high_water_mark or config.start_equity_usd, equity)
    floor = config.start_equity_usd * config.equity_floor_pct
    drawdown = (peak - equity) / peak if peak > 0 else 0.0
    day_pl = equity - last_equity
    daily_budget = config.start_equity_usd * config.max_daily_loss_pct

    if equity < floor:
        reasons.append(f"equity {equity:.0f} below the hard floor {floor:.0f}")
        flatten = True
    if drawdown > config.max_drawdown_pct:
        reasons.append(
            f"drawdown {drawdown:.1%} from the {peak:.0f} high-water mark exceeds "
            f"{config.max_drawdown_pct:.1%}"
        )
        flatten = True
    if day_pl < -daily_budget:
        reasons.append(
            f"day P&L {day_pl:.0f} beyond the daily budget {daily_budget:.0f}; "
            "managing exits only"
        )

    window = trading_window(
        now or datetime.now(US_EASTERN),
        open_delay_minutes=config.open_delay_minutes,
        no_new_risk_before_close_minutes=config.no_new_risk_before_close_minutes,
    )
    if not window.open_for_new_risk:
        reasons.append(window.reason)

    return AccountGuardResult(
        new_risk_allowed=not reasons,
        flatten_required=flatten,
        reasons=reasons,
        equity=equity,
        day_pl=day_pl,
        high_water_mark=peak,
        drawdown_pct=drawdown,
        minutes_to_close=window.minutes_to_close,
    )
