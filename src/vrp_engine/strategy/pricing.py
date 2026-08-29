r"""Expected value of a structure under two different probability measures.

Intuition: the option market quotes a structure as if the underlying will move by its
implied volatility. Our own estimate of movement comes from what the underlying has
actually been doing. Score the *same* payoff under both distributions. If the payoff
looks better under our distribution than under the market's, the market is overpaying
and there is something to collect. That difference is the only reason to open a trade.

Technical: the terminal price is lognormal. Rather than deriving a closed form per
shape, the payoff is integrated numerically over the terminal distribution, which
works identically for a two-leg vertical and a four-leg condor.

Math: with $S_T = S_0 e^{X}$ and $X \sim \mathcal N(m, \sigma^2 T)$,

$$m = \ln S_0 + \lambda \sigma\sqrt{T} - \tfrac{1}{2}\sigma^2 T,\qquad
\mathbb E[\Pi] = \int \Pi(e^{x})
    \frac{1}{\sigma\sqrt{T}}\varphi\!\left(\frac{x-m}{\sigma\sqrt T}\right) dx$$

where $\lambda$ is a small trend tilt (zero under the market's own measure). The
edge is $\mathbb E[\Pi] / \text{max loss}$ and the wedge is
$p_{\text{model}} - p_{\text{implied}}$.
"""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, Field

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER, OptionCandidate
from vrp_engine.strategy.signals import TREND_DOWN, TREND_UP, UnderlyingSignal
from vrp_engine.strategy.structures import Structure

TRADING_DAYS = 252

# How far the trend is allowed to tilt the distribution, as a share of the horizon
# standard deviation. Deliberately small: the engine's edge is meant to come from
# mispriced volatility, not from a directional forecast wearing a lab coat.
DRIFT_SIGMA_SHARE = 0.25

# Numerical integration. Six standard deviations captures the tails to well past the
# precision of the inputs, and an odd point count keeps the mean on the grid.
INTEGRATION_POINTS = 1201
INTEGRATION_SPAN_SIGMA = 6.0

# A zero-DTE structure still has a few hours of variance left; without a floor the
# distribution collapses to a point mass and every probability becomes 0 or 1.
MIN_YEARS = 1.0 / (TRADING_DAYS * 4)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def years_to_expiry(expiration: date, today: date) -> float:
    """Horizon in years, floored so same-day expiries stay numerically sane."""
    days = (expiration - today).days
    return max(days / TRADING_DAYS, MIN_YEARS)


def black_scholes_delta(
    *,
    spot: float,
    strike: float,
    sigma: float,
    years: float,
    option_type: str,
) -> float | None:
    """Delta at a zero rate, used only to fill a gap in the chain snapshot.

    Alpaca usually returns greeks; when it does not, a missing delta would silently
    remove an underlying from the scan. Recomputing it from the quoted implied vol
    keeps the universe intact without inventing data.
    """
    if spot <= 0 or strike <= 0 or sigma <= 0 or years <= 0:
        return None
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * years) / (sigma * math.sqrt(years))
    call_delta = norm_cdf(d1)
    return call_delta if option_type == "call" else call_delta - 1.0


def fill_missing_deltas(
    candidates: list[OptionCandidate],
    *,
    spot: float,
    today: date,
) -> list[OptionCandidate]:
    """Return candidates with delta populated wherever it can be derived from IV."""
    filled: list[OptionCandidate] = []
    for candidate in candidates:
        if candidate.delta is not None or not candidate.implied_volatility:
            filled.append(candidate)
            continue
        delta = black_scholes_delta(
            spot=spot,
            strike=candidate.strike,
            sigma=float(candidate.implied_volatility),
            years=years_to_expiry(candidate.expiration, today),
            option_type=candidate.option_type,
        )
        filled.append(candidate if delta is None else candidate.model_copy(update={"delta": delta}))
    return filled


def leg_intrinsic(*, option_type: str, strike: float, terminal: float) -> float:
    if option_type == "call":
        return max(terminal - strike, 0.0)
    return max(strike - terminal, 0.0)


def structure_payoff(structure: Structure, terminal: float) -> float:
    """Profit or loss per contract, in dollars, if the underlying expires at `terminal`.

    The premium is already signed by `effective_price` (positive when collected), so
    the intrinsic values only need the direction of each leg.
    """
    total = structure.effective_price
    for leg in structure.legs:
        intrinsic = leg_intrinsic(
            option_type=leg.contract.option_type,
            strike=leg.contract.strike,
            terminal=terminal,
        )
        total += intrinsic if leg.side == "buy" else -intrinsic
    return total * CONTRACT_MULTIPLIER


def _terminal_grid(
    *,
    spot: float,
    sigma: float,
    years: float,
    drift_share: float,
) -> tuple[list[float], list[float], float]:
    """Log-space grid of terminal prices with their normal densities.

    Returns (prices, densities, step) so the caller can trapezoid any payoff over it.
    """
    sd = sigma * math.sqrt(years)
    mean = math.log(spot) + drift_share * sd - 0.5 * sigma * sigma * years
    low = mean - INTEGRATION_SPAN_SIGMA * sd
    high = mean + INTEGRATION_SPAN_SIGMA * sd
    step = (high - low) / (INTEGRATION_POINTS - 1)

    prices: list[float] = []
    densities: list[float] = []
    for i in range(INTEGRATION_POINTS):
        x = low + i * step
        prices.append(math.exp(x))
        densities.append(norm_pdf((x - mean) / sd) / sd)
    return prices, densities, step


def _trapezoid(values: list[float], densities: list[float], step: float) -> float:
    total = 0.0
    for i in range(len(values) - 1):
        total += 0.5 * (values[i] * densities[i] + values[i + 1] * densities[i + 1])
    return total * step


class MeasureResult(BaseModel):
    """The same structure scored under one probability measure."""

    sigma: float
    expected_pnl_usd: float
    win_probability: float
    expected_loss_usd: float


def score_under_measure(
    structure: Structure,
    *,
    spot: float,
    sigma: float,
    years: float,
    drift_share: float = 0.0,
) -> MeasureResult | None:
    """Integrate the payoff, the win region and the loss tail over one distribution."""
    if spot <= 0 or sigma <= 0 or years <= 0:
        return None

    prices, densities, step = _terminal_grid(
        spot=spot, sigma=sigma, years=years, drift_share=drift_share
    )
    payoffs = [structure_payoff(structure, price) for price in prices]

    expected = _trapezoid(payoffs, densities, step)
    win_mask = [1.0 if p > 0 else 0.0 for p in payoffs]
    win_probability = _trapezoid(win_mask, densities, step)
    loss_only = [-p if p < 0 else 0.0 for p in payoffs]
    expected_loss = _trapezoid(loss_only, densities, step)

    # The grid is truncated at six sigma, so probabilities can land a hair off one.
    win_probability = min(max(win_probability, 0.0), 1.0)
    return MeasureResult(
        sigma=sigma,
        expected_pnl_usd=expected,
        win_probability=win_probability,
        expected_loss_usd=expected_loss,
    )


def kelly_fraction(*, win_probability: float, max_profit: float, max_loss: float) -> float:
    """Full-Kelly stake for the binary approximation of this structure.

    A vertical is not literally a binary bet, but the approximation is the standard
    one and it is what the sizing layer then scales down hard.
    """
    if max_loss <= 0 or max_profit <= 0:
        return 0.0
    odds = max_profit / max_loss
    fraction = (win_probability * odds - (1.0 - win_probability)) / odds
    return max(fraction, 0.0)


class StructureEvaluation(BaseModel):
    """A structure with everything needed to rank it and to justify it afterwards."""

    structure: Structure
    dte: int
    max_loss_usd: float
    max_profit_usd: float
    model_vol: float
    implied_vol: float
    p_win_model: float
    p_win_implied: float
    wedge: float
    expected_value_usd: float
    expected_value_implied_usd: float
    expected_loss_usd: float
    edge: float
    score: float
    full_kelly: float
    rejects: list[str] = Field(default_factory=list)

    @property
    def acceptable(self) -> bool:
        return not self.rejects

    def rationale(self) -> str:
        """One line a human can audit without opening the code."""
        return (
            f"{self.structure.describe()}: "
            f"IV {self.implied_vol:.1%} vs RV {self.model_vol:.1%}, "
            f"win {self.p_win_model:.0%} model vs {self.p_win_implied:.0%} implied "
            f"(wedge {self.wedge:+.1%}), EV {self.expected_value_usd:+.0f} USD on "
            f"{self.max_loss_usd:.0f} USD risk = {self.edge:+.1%} over {self.dte}d"
        )


def evaluate_structure(
    structure: Structure,
    signal: UnderlyingSignal,
    *,
    today: date,
    min_edge: float,
    min_wedge: float,
) -> StructureEvaluation | None:
    """Score one structure and record every reason it is not tradable.

    Returns None only when the inputs are too incomplete to score at all; a scored
    but rejected structure is still returned so the journal can show what was passed
    over and why.
    """
    if not signal.realized_vol or not signal.implied_vol or signal.spot <= 0:
        return None

    years = years_to_expiry(structure.expiration, today)
    max_loss = structure.max_loss_usd
    max_profit = structure.max_profit_usd
    if max_loss <= 0 or max_profit <= 0:
        return None

    drift = 0.0
    if signal.trend == TREND_UP:
        drift = DRIFT_SIGMA_SHARE
    elif signal.trend == TREND_DOWN:
        drift = -DRIFT_SIGMA_SHARE

    model = score_under_measure(
        structure,
        spot=signal.spot,
        sigma=signal.realized_vol,
        years=years,
        drift_share=drift,
    )
    # The market's own measure carries no trend tilt: that is the whole point of it.
    implied = score_under_measure(
        structure,
        spot=signal.spot,
        sigma=signal.implied_vol,
        years=years,
        drift_share=0.0,
    )
    if model is None or implied is None:
        return None

    edge = model.expected_pnl_usd / max_loss
    wedge = model.win_probability - implied.win_probability
    dte = max((structure.expiration - today).days, 0)
    # Rank by edge per day of capital tied up, so a 2-day and a 9-day candidate can
    # be compared honestly instead of the slower one winning on absolute size.
    score = edge / max(dte, 1)

    rejects: list[str] = []
    if model.expected_pnl_usd <= 0:
        rejects.append("expected value is not positive under the model distribution")
    if edge < min_edge:
        rejects.append(f"edge {edge:+.1%} below the {min_edge:.1%} floor")
    if wedge < min_wedge:
        rejects.append(
            f"probability wedge {wedge:+.1%} below the {min_wedge:.1%} floor: "
            "the market is not overpaying"
        )

    return StructureEvaluation(
        structure=structure,
        dte=dte,
        max_loss_usd=max_loss,
        max_profit_usd=max_profit,
        model_vol=model.sigma,
        implied_vol=implied.sigma,
        p_win_model=model.win_probability,
        p_win_implied=implied.win_probability,
        wedge=wedge,
        expected_value_usd=model.expected_pnl_usd,
        expected_value_implied_usd=implied.expected_pnl_usd,
        expected_loss_usd=model.expected_loss_usd,
        edge=edge,
        score=score,
        full_kelly=kelly_fraction(
            win_probability=model.win_probability,
            max_profit=max_profit,
            max_loss=max_loss,
        ),
        rejects=rejects,
    )


def rank_evaluations(evaluations: list[StructureEvaluation]) -> list[StructureEvaluation]:
    """Best first, by expected value per dollar-day of risk."""
    return sorted(evaluations, key=lambda e: e.score, reverse=True)
