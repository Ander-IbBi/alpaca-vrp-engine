r"""Per-underlying signals: how rich are the options, and which way is the tape leaning.

Intuition: an option's price contains a forecast of future movement. Compare that
forecast with what the underlying has actually been delivering. When the forecast is
too high the engine sells premium; when it is too low the engine buys it. The trend
only decides the *shape* of the trade, never whether there is an edge.

Technical: realized volatility blends two close-to-close windows with a Parkinson
high/low estimator; implied volatility is read at the money from the chain snapshot.

Math: with log returns $r_t = \ln(S_t / S_{t-1})$,

$$\sigma_{cc} = \sqrt{\tfrac{252}{n-1}\sum (r_t - \bar r)^2},\qquad
\sigma_{P} = \sqrt{\tfrac{252}{4 n \ln 2}\sum \ln^2\!\left(\tfrac{H_t}{L_t}\right)}$$

and the variance risk premium is $\mathrm{VRP} = \sigma_{IV} - \sigma_{RV}$,
normalised as $z = \mathrm{VRP} / \sigma_{RV}$ so a 3 vol-point gap means something
different on a 10-vol name than on a 60-vol name.
"""

from __future__ import annotations

import math
from datetime import date
from statistics import fmean

from pydantic import BaseModel, Field

from vrp_engine.alpaca.market_data import Bar, PriceHistory
from vrp_engine.alpaca.options import OptionCandidate

TRADING_DAYS = 252

# Weights for the realized-vol blend. The short close-to-close window carries the
# current regime, the long one damps single-day noise, and Parkinson adds intraday
# range information that closing prices throw away.
WEIGHT_CC_SHORT = 0.40
WEIGHT_CC_LONG = 0.20
WEIGHT_PARKINSON = 0.40

# Trend needs both a structural signal (moving averages) and a statistical one
# (a move large relative to the noise) before it is allowed to tilt a structure.
TREND_MIN_EMA_GAP = 0.002
TREND_MIN_Z = 0.35
TREND_LOOKBACK_DAYS = 5

STANCE_SELL_VOL = "sell_vol"
STANCE_BUY_VOL = "buy_vol"
STANCE_STAND_DOWN = "stand_down"

TREND_UP = "up"
TREND_DOWN = "down"
TREND_FLAT = "flat"


def close_to_close_vol(returns: list[float], window: int) -> float | None:
    """Annualised sample standard deviation of the last `window` log returns."""
    sample = returns[-window:]
    if len(sample) < 3:
        return None
    mean = fmean(sample)
    variance = sum((r - mean) ** 2 for r in sample) / (len(sample) - 1)
    return math.sqrt(variance * TRADING_DAYS)


def parkinson_vol(bars: list[Bar], window: int) -> float | None:
    """Annualised Parkinson estimator from the high/low range.

    Roughly five times more efficient than close-to-close for the same sample, which
    matters when only a few weeks of history are available.
    """
    sample = [b for b in bars[-window:] if b.high > 0 and b.low > 0 and b.high >= b.low]
    if len(sample) < 3:
        return None
    total = sum(math.log(b.high / b.low) ** 2 for b in sample)
    daily_variance = total / (4.0 * len(sample) * math.log(2.0))
    return math.sqrt(daily_variance * TRADING_DAYS)


def blended_realized_vol(
    history: PriceHistory,
    *,
    short_window: int = 10,
    long_window: int = 21,
) -> float | None:
    """Weighted blend of the available estimators, or None when history is too thin."""
    returns = history.log_returns()
    parts: list[tuple[float, float]] = []
    cc_short = close_to_close_vol(returns, short_window)
    if cc_short is not None:
        parts.append((WEIGHT_CC_SHORT, cc_short))
    cc_long = close_to_close_vol(returns, long_window)
    if cc_long is not None:
        parts.append((WEIGHT_CC_LONG, cc_long))
    park = parkinson_vol(history.bars, long_window)
    if park is not None:
        parts.append((WEIGHT_PARKINSON, park))
    if not parts:
        return None
    total_weight = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / total_weight


def ema(values: list[float], span: int) -> float | None:
    """Exponential moving average with the conventional 2/(span+1) smoothing."""
    if not values or span < 1:
        return None
    alpha = 2.0 / (span + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def trend_state(
    history: PriceHistory,
    *,
    realized_vol: float | None,
    fast: int = 8,
    slow: int = 21,
) -> str:
    """Classify the tape as up, down or flat.

    Flat is the default and the most common answer on purpose: without a genuine
    directional signal the engine should sell both wings rather than guess a side.
    """
    closes = history.closes
    if len(closes) < slow + 1:
        return TREND_FLAT

    ema_fast = ema(closes[-slow * 2 :], fast)
    ema_slow = ema(closes[-slow * 2 :], slow)
    if ema_fast is None or ema_slow is None or ema_slow <= 0:
        return TREND_FLAT
    ema_gap = (ema_fast - ema_slow) / ema_slow

    if len(closes) <= TREND_LOOKBACK_DAYS or closes[-TREND_LOOKBACK_DAYS - 1] <= 0:
        return TREND_FLAT
    window_return = closes[-1] / closes[-TREND_LOOKBACK_DAYS - 1] - 1.0

    # Scale the recent move by the noise it swims in, so a 2% week is a signal on a
    # quiet ETF and nothing at all on a high-vol single name.
    if not realized_vol or realized_vol <= 0:
        return TREND_FLAT
    daily_vol = realized_vol / math.sqrt(TRADING_DAYS)
    noise = daily_vol * math.sqrt(TREND_LOOKBACK_DAYS)
    if noise <= 0:
        return TREND_FLAT
    z = window_return / noise

    if ema_gap > TREND_MIN_EMA_GAP and z > TREND_MIN_Z:
        return TREND_UP
    if ema_gap < -TREND_MIN_EMA_GAP and z < -TREND_MIN_Z:
        return TREND_DOWN
    return TREND_FLAT


def beta_to_market(symbol_returns: list[float], market_returns: list[float]) -> float:
    """OLS slope of the symbol on the market, over the overlapping tail.

    Used to express every position's delta in SPY-equivalent dollars, so the
    portfolio has one directional number instead of fourteen incomparable ones.
    """
    n = min(len(symbol_returns), len(market_returns))
    if n < 10:
        return 1.0
    xs = market_returns[-n:]
    ys = symbol_returns[-n:]
    mean_x = fmean(xs)
    mean_y = fmean(ys)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 0:
        return 1.0
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    beta = covariance / variance
    # Clamp: a regression on a handful of noisy days can produce absurd slopes, and
    # an absurd beta would corrupt the whole portfolio delta budget.
    return max(-3.0, min(3.0, beta))


def atm_implied_vol(
    candidates: list[OptionCandidate],
    *,
    spot: float,
    expiration: date,
) -> float | None:
    """Implied vol at the money for one expiry, averaged across the call and the put.

    Averaging the two sides cancels most of the put/call skew at the strike and is
    more stable than picking whichever contract happens to be quoted better.
    """
    if spot <= 0:
        return None
    per_type: dict[str, float] = {}
    for option_type in ("call", "put"):
        pool = [
            c
            for c in candidates
            if c.expiration == expiration
            and c.option_type == option_type
            and c.implied_volatility is not None
            and c.implied_volatility > 0
        ]
        if not pool:
            continue
        nearest = min(pool, key=lambda c: abs(c.strike - spot))
        per_type[option_type] = float(nearest.implied_volatility or 0.0)
    if not per_type:
        return None
    return fmean(per_type.values())


def term_slope(
    candidates: list[OptionCandidate],
    *,
    spot: float,
    expiries: list[date],
) -> float | None:
    """Front-expiry ATM IV minus the next expiry's.

    A strongly positive slope means the market is pricing a dated event inside the
    front expiry. Selling premium into that is selling insurance right before the
    fire, so the engine treats it as a blackout instead of a bargain.
    """
    if len(expiries) < 2:
        return None
    front = atm_implied_vol(candidates, spot=spot, expiration=expiries[0])
    following = atm_implied_vol(candidates, spot=spot, expiration=expiries[1])
    if front is None or following is None:
        return None
    return front - following


def classify_stance(vrp_z: float | None, *, entry: float) -> str:
    """Sell premium when it is rich, buy it when it is cheap, otherwise stand down."""
    if vrp_z is None:
        return STANCE_STAND_DOWN
    if vrp_z >= entry:
        return STANCE_SELL_VOL
    if vrp_z <= -entry:
        return STANCE_BUY_VOL
    return STANCE_STAND_DOWN


class UnderlyingSignal(BaseModel):
    """Everything the engine knows about one underlying this cycle."""

    symbol: str
    spot: float
    expiration: date | None = None
    horizon_days: int = 0
    realized_vol: float | None = None
    implied_vol: float | None = None
    vrp: float | None = None
    vrp_z: float | None = None
    term_slope: float | None = None
    event_blackout: bool = False
    trend: str = TREND_FLAT
    beta: float = 1.0
    stance: str = STANCE_STAND_DOWN
    notes: list[str] = Field(default_factory=list)

    @property
    def actionable(self) -> bool:
        return (
            self.stance != STANCE_STAND_DOWN
            and not self.event_blackout
            and self.expiration is not None
            and self.spot > 0
            and bool(self.realized_vol)
        )


def build_signal(
    *,
    symbol: str,
    spot: float,
    history: PriceHistory,
    candidates: list[OptionCandidate],
    expiries: list[date],
    market_returns: list[float],
    today: date,
    vrp_z_entry: float,
    term_slope_blackout: float,
) -> UnderlyingSignal:
    """Assemble one underlying's signal from its history and its chain.

    Pure: every input is data, so the whole decision surface is testable offline.
    """
    notes: list[str] = []
    realized = blended_realized_vol(history)
    if realized is None:
        notes.append("not enough price history for a realized-vol estimate")

    expiration = expiries[0] if expiries else None
    if expiration is None:
        notes.append("no expiry inside the DTE window")

    implied = (
        atm_implied_vol(candidates, spot=spot, expiration=expiration)
        if expiration is not None
        else None
    )
    if expiration is not None and implied is None:
        notes.append("chain did not quote an at-the-money implied vol")

    vrp = implied - realized if (implied is not None and realized) else None
    vrp_z = vrp / realized if (vrp is not None and realized) else None

    slope = term_slope(candidates, spot=spot, expiries=expiries)
    blackout = slope is not None and slope > term_slope_blackout
    if blackout:
        notes.append(
            f"front IV richer than the next expiry by {slope:.1%}: reads as a dated event"
        )

    trend = trend_state(history, realized_vol=realized)
    beta = beta_to_market(history.log_returns(), market_returns)
    stance = classify_stance(vrp_z, entry=vrp_z_entry)
    if stance == STANCE_STAND_DOWN and vrp_z is not None:
        notes.append(f"VRP z={vrp_z:+.2f} inside the +/-{vrp_z_entry:.2f} no-trade band")

    return UnderlyingSignal(
        symbol=symbol.upper(),
        spot=spot,
        expiration=expiration,
        horizon_days=max((expiration - today).days, 0) if expiration else 0,
        realized_vol=realized,
        implied_vol=implied,
        vrp=vrp,
        vrp_z=vrp_z,
        term_slope=slope,
        event_blackout=blackout,
        trend=trend,
        beta=beta,
        stance=STANCE_STAND_DOWN if blackout else stance,
        notes=notes,
    )
