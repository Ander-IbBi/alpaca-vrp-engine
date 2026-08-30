# The VRP Engine strategy

This is the full writeup: what the engine measures, how it turns a measurement into a
position, and how big that position is allowed to be. Every formula here corresponds to
a pure function in `src/vrp_engine/strategy/`, and every one of those functions is
covered by tests that run with no network access.

## 1. Why the variance risk premium

An option's price embeds a forecast of how much the underlying will move. Historically
that forecast is, on average, too high: sellers of options are compensated for carrying
gap risk, and buyers pay for convexity they mostly do not need. The difference is the
**variance risk premium**.

"On average" is not a trading edge, though. Two things turn it into one:

1. **Measure it per underlying, per cycle**, instead of assuming it. Sometimes implied
   volatility sits *below* realised, and then the correct trade is to buy premium, not
   sell it. The engine is two-sided for exactly this reason.
2. **Require the specific structure to be mispriced**, not just the underlying. A rich
   implied volatility does not guarantee that a particular 5-wide put credit spread has
   positive expected value. Section 4 is the test that decides.

## 2. Signals — `strategy/signals.py`

All of these are pure functions of a price history and a chain snapshot.

### Realised volatility

Two estimators, blended, both annualised with $A = \sqrt{252}$.

Close-to-close over the last $n$ log returns $r_i = \ln(S_i / S_{i-1})$:

$$\hat\sigma^{cc}_n = A\sqrt{\frac{1}{n-1}\sum_{i=1}^{n}\left(r_i - \bar r\right)^2}$$

and a Parkinson high–low estimator, which uses the intraday range and is therefore far
less noisy per observation:

$$\hat\sigma^{P}_n = A\sqrt{\frac{1}{4n\ln 2}\sum_{i=1}^{n}\left(\ln\frac{H_i}{L_i}\right)^2}$$

The blend weights a 10-day and a 21-day close-to-close estimate together with Parkinson:

$$RV = w_{10}\hat\sigma^{cc}_{10} + w_{21}\hat\sigma^{cc}_{21} + w_{P}\hat\sigma^{P}_{21}$$

Two windows because a single one either lags a regime change or jitters; Parkinson
because a day that opened and closed flat after a 3% swing did not have zero volatility.
Weights live in `signals.py` and are renormalised over whichever estimators had enough
data, so a short history degrades gracefully instead of returning nothing.

### Implied volatility and the term slope

$IV$ is the mean of the at-the-money call and put implied volatilities at the target
expiry, taken straight from Alpaca's chain snapshot.

The term slope is $IV(\text{front}) - IV(\text{next})$. A strongly positive slope means
the market is pricing a dated event inside the front expiry — earnings, a data print, a
decision. Selling premium into that is selling insurance right before the fire, so a
slope above `TERM_SLOPE_BLACKOUT` (default 8 vol points) blacks the underlying out for
the cycle. This replaces an external earnings calendar with a signal derived from the
same data the engine already fetched, and it catches unscheduled catalysts the calendar
would miss.

### The premium, normalised

$$VRP = IV - RV, \qquad VRP_z = \frac{IV - RV}{RV}$$

Normalising by $RV$ makes the number comparable across a 12%-vol index ETF and a 60%-vol
single name: 5 vol points of premium means something very different in each. The engine
acts when $|VRP_z| \ge$ `VRP_Z_ENTRY` (default 0.15) and stands down inside the band.

### Trend and beta

Trend is EMA(8) versus EMA(21) on daily closes, confirmed by a 5-day return scaled by
realised volatility, mapped to `up` / `down` / `flat`. It never decides *whether* to
trade — only the shape, once the VRP has decided the side.

Beta is ordinary least squares of the underlying's log returns on SPY's over 60 days:

$$\beta = \frac{\operatorname{Cov}(r, r_{\text{SPY}})}{\operatorname{Var}(r_{\text{SPY}})}$$

used later to make positions on different underlyings comparable under one market shock.

## 3. Structures — `strategy/structures.py`

The VRP sign picks the side, the trend picks the shape (see the matrix in the README).
Everything is a defined-risk vertical or a condor, and each shape is built at several
widths so section 4 can choose.

For a vertical of width $w$ (strike distance) opened for a net credit $c$ per share:

$$\text{max profit} = 100c, \qquad \text{max loss} = 100(w - c)$$

and the trade only makes arithmetic sense while $0 < c < w$. A quoted credit at or above
the width means the quotes are crossed or stale, and the structure is discarded rather
than booked as a phantom edge.

For an iron condor the width is the **wider single wing**, not the sum, because only one
wing can finish in the money.

Two pricing conventions matter:

- **Slippage is assumed, not hoped away.** The net premium used in every calculation is
  the mid pulled halfway toward the unfavourable side of the quote. Pricing the edge off
  the mid alone flatters every trade.
- Contracts must clear a liquidity gate before they are eligible at all. The **quote is
  the operative test**: a non-zero bid on both sides, and a quoted spread under
  `MAX_SPREAD_FRACTION` of the mid. A contract with no bid cannot be sold at any price,
  and a wide market eats the whole edge before the thesis gets a chance. Alpaca's chain
  snapshot does not carry open interest, so `MIN_OPEN_INTEREST` is applied only where the
  data is actually present — which in practice is the stricter of the two anyway, since a
  contract nobody holds also has no bid.

## 4. Expected value — `strategy/pricing.py`

This is the module that decides whether a structure is worth owning. The same payoff is
scored under **two different probability measures**.

Let $S_T$ be the terminal price, lognormal with

$$S_T = S_0 e^{X}, \qquad X \sim \mathcal N\!\left(m, \sigma^2 T\right), \qquad
m = \ln S_0 + \lambda\sigma\sqrt{T} - \tfrac{1}{2}\sigma^2 T$$

where $T = \text{DTE}/252$ and $\lambda$ is a small trend tilt, capped at
`DRIFT_SIGMA_SHARE = 0.25` standard deviations. The cap is deliberate: the engine's edge
is meant to come from mispriced volatility, not from a directional forecast wearing a
lab coat. Under the market's own measure $\lambda = 0$ — that is the whole point of it.

The structure's payoff at expiry, per contract, is piecewise linear:

$$\Pi(S_T) = 100\left[c + \sum_i \epsilon_i \max\!\left(\eta_i(S_T - K_i), 0\right)\right]$$

with $\epsilon_i = +1$ for legs we own and $-1$ for legs we are short, and $\eta_i = +1$
for calls, $-1$ for puts.

Three quantities are then integrated numerically over the terminal distribution:

$$\mathbb E[\Pi] = \int \Pi(e^{x})\,\phi_{m,\sigma\sqrt T}(x)\,dx, \qquad
p_{\text{win}} = \int \mathbf 1\{\Pi(e^x) > 0\}\,\phi\,dx, \qquad
\mathbb E[\text{loss}] = \int \max(-\Pi(e^x), 0)\,\phi\,dx$$

Numerical integration rather than a closed form, because the same code then works
identically for a two-leg vertical and a four-leg condor, with one barrier or two, and
because **expected loss is integrated over the loss region instead of being assumed to
be the maximum loss**. Assuming max loss is the standard way premium-selling strategies
understate their own edge and then oversize on the correction.

Scoring the *same* payoff under $\sigma = RV$ (ours) and $\sigma = IV$ (the market's)
gives the two numbers that authorise a trade:

$$\text{edge} = \frac{\mathbb E_{RV}[\Pi]}{\text{max loss}}, \qquad
\text{wedge} = p^{RV}_{\text{win}} - p^{IV}_{\text{win}}$$

The **wedge** is the thesis reduced to one number: our distribution says this structure
wins more often than the price implies. It goes into every journal entry, and a negative
wedge is a hard rejection.

### Ranking: expected value per dollar-day of risk

$$\text{score} = \frac{\text{edge}}{\max(\text{DTE}, 1)}$$

A 2-DTE candidate and a 9-DTE candidate are not comparable on absolute edge — the slower
one wins simply by having more time to accumulate it, while tying up collateral four
times longer. Dividing by days held makes them comparable and naturally favours the fast
theta decay that a one-week contest window rewards.

A candidate is rejected on any of: non-positive expected value, edge below `MIN_EDGE`,
wedge below `MIN_WEDGE`, a failed liquidity gate, or a term-slope blackout. Rejected
candidates are still scored and journalled, so the record shows what was passed over and
why.

## 5. Sizing — `strategy/sizing.py`

Approximate the structure as a binary bet with win probability $p$ (the model's) and
payoff odds $b = \text{max profit} / \text{max loss}$. The Kelly stake is

$$f^{*} = \frac{pb - (1-p)}{b}$$

clipped at zero. A vertical is not literally a binary bet, but this is the standard
approximation and the next step cuts it hard anyway. The engine risks

$$R = \min\left(\kappa f^{*}E,\; C_{\text{trade}},\; C_{\text{sym}} - U_{\text{sym}},\;
C_{\text{bucket}} - U_{\text{bucket}},\; C_{\text{agg}} - U_{\text{agg}},\;
0.9\,\text{BP}\right)$$

$$n = \left\lfloor \frac{R}{L_{\text{contract}}} \right\rfloor$$

with $\kappa$ = `KELLY_FRACTION` (0.35), $E$ equity, $C_\bullet$ the budgets from the
README table, $U_\bullet$ the risk already committed at that level, and BP the options
buying power. The 0.9 factor exists because planning to use the last dollar of buying
power means a fill a few cents worse than the mid bounces the whole ticket.

Three final clips, none of which is denominated in dollars of risk. The per-order
contract cap; a liquidity clip of $n \le \text{open interest} / 50$ wherever open
interest is known, because size beyond that is a fill problem dressed up as a risk
problem; and the beta-weighted delta budget.

That last one needs its own arithmetic, because it is measured in directional notional
rather than in loss. With $\delta$ the structure's beta-weighted dollar delta per
contract, $D$ the book's current beta-weighted delta and $B$ = `MAX_NET_DELTA_PCT`
$\times E$ the band,

$$n \le \left\lfloor \frac{B - \operatorname{sgn}(\delta)\,D}{|\delta|} \right\rfloor$$

A ticket leaning the same way as the book gets only the remaining headroom; one leaning
against it may run all the way to the far side of the band, because a correction is not
a lean. A delta-neutral structure — which is what an iron condor is for — has no ceiling
here at all.

The delta budget is enforced again in `risk/limits.py`, but by the time it gets there it
should never bind: clipping the size beforehand is what stops a candidate that leans too
hard from killing the whole cycle instead of simply arriving smaller.

**Which constraint bound the size is recorded on the ticket.** That single field is what
makes the sizing auditable: anyone reading the journal can reconstruct the arithmetic
without trusting the engine.

## 6. Portfolio risk — `risk/portfolio.py`

Per-order caps cannot see a book. Ten individually reasonable spreads on correlated
names are one large bet, so the risk layer rebuilds the entire portfolio as a payoff
curve.

With signed contract counts $q_i$ (negative when short) and strikes $K_i$:

$$V(S) = 100\sum_i q_i \max\!\left(\eta_i(S - K_i), 0\right), \qquad
\text{P\&L}(S) = V(S) - V_{\text{now}}$$

Netting off today's mark is what makes this honest: premium already collected is money in
hand, and pretending otherwise would double-count it as risk.

Because $\text{P\&L}$ is piecewise linear, its minimum is attained at a breakpoint, so
the theoretical worst case is computed **exactly**:

$$\text{worst case} = -\min_{S \in \{0\} \cup \{K_i\} \cup \{S_0\} \cup \{S_\infty\}} \text{P\&L}(S)$$

This is the same "universal spread rule" model Alpaca documents for its own margin
calculation, which is a useful property: the engine's worst case and the broker's
collateral requirement agree.

### Stress and the common shock

The theoretical worst case is a bound every position would only reach simultaneously, so
the operative ceiling is a **modelled one-week shock**:

$$S^{(k)} = S_0 \exp\!\left(k\,\sigma\sqrt{5/252}\right), \qquad k \in \{-2,-1,+1,+2\}$$

evaluated for every underlying and summed per scenario, so correlated names add up
instead of cancelling by luck.

For the aggregate payoff curve each underlying is shocked by $\beta_i x$ against one
common market move $x$, which is the same mapping the delta budget uses. Adding fourteen
curves directly would pretend a 1% move in SPY and a 1% move in a high-beta single name
are the same event.

The greeks aggregate the same way: beta-weighted dollar delta
$\sum_i \beta_i \delta_i q_i \cdot 100 \cdot S_i$, plus net vega and theta.

### The pre-trade check that matters

Each proposal is converted into hypothetical holdings marked at the current mid — which
is exactly what a fill at the net mid produces — and the portfolio is rebuilt with them
included. The trade is approved only if that **post-fill** portfolio still satisfies the
aggregate, stress, delta, per-underlying and per-bucket budgets. That is a real pre-trade
portfolio check rather than a per-order cap, and it is what lets the budgets be
aggressive without being reckless.

## 7. Managing what is open — `strategy/management.py`

Opening a good structure is one decision. Leaving it alone makes that single decision
stand for the position's whole life, and short-dated options change character fast: a
spread that was a 78% winner on Monday can be a coin flip on Wednesday.

Every cycle walks a fixed ladder over the open book, worst position first, and takes at
most one action:

| # | Trigger | Action |
| --- | --- | --- |
| 1 | Unrealised loss ≥ 2.0× the credit received | close |
| 2 | DTE ≤ 1 and a short strike within 0.5% of spot, or \|short delta\| ≥ 0.60 | close (assignment guard) |
| 3 | Captured ≥ 55% of the credit (60% for condors, 100% of the debit for debit spreads) | close, free the collateral |
| 4 | DTE ≤ 1 after 15:00 ET and not more than 2σ out of the money | close rather than gamble on the pin |
| 5 | Nothing due | record which checks ran |

One action per cycle keeps the journal readable and makes it impossible for a single bad
pass to unwind the whole book.

Profit targets are not arbitrary. A credit spread that has captured 55% of its premium
has given up most of its remaining reward while keeping all of its gamma risk; closing
recycles the collateral into a fresh structure with a full premium ahead of it.

All exits are their own all-`*_to_close` multi-leg ticket. **Alpaca accepts a multi-leg
order only when every leg is covered inside that same order**, which rules out the
classic single-ticket roll. Closing and re-opening separately is not a workaround, it is
the supported path — and `orders.py` raises a local error rather than letting a mixed
ticket reach the broker.

## 8. Account breakers — `risk/account.py`

Three gates, and one distinction that matters more than the numbers: *stopping new risk*
is not *stopping trading*. A breaker that blocked exits would trap the book it fired
over.

- **Daily loss** beyond 6% of starting equity: stop opening, keep managing exits.
- **Drawdown** beyond 18% from the high-water mark (read from the journal, not from the
  starting balance, so a good week cannot be given back unnoticed): flatten.
- **Equity floor** below 82% of the starting balance: flatten and stand down for good.

Plus a session window: no new risk in the first 15 minutes (opening quotes are wide and
unstable, and they flatter every edge estimate) or the last 20 (an unfilled day order
near the bell becomes unwanted overnight exposure).

## 9. Inherited positions — `strategy/reset.py`

A paper account handed over from a previous strategy arrives with a book the engine
cannot model, tying up collateral. Ownership is decided structurally with no stored
state: the engine only ever holds options expiring inside its own window, so anything
with a longer tail was not opened by it, and shares are never its doing at all.

Sequencing matters. Short options first, then long options, then shares. Selling the
shares out from under a covered short call would leave a naked short standing for as long
as the next ticket takes to fill — precisely the state the risk layer exists to make
impossible.

## Where the numbers come from

Every threshold in this document is a setting in `config.py`, expressed as a **fraction of
account equity** rather than a dollar constant, so the same configuration behaves
identically on a $10k and a $100k account and the sizing layer never has a stale absolute
number to trip over.
