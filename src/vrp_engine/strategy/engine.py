"""The engine: one cycle of the VRP playbook, start to finish.

Priority order, and the reasoning behind it:

1. **Unwind inherited positions.** Capital tied up in a book the engine cannot model
   is capital it cannot risk-manage either.
2. **Flatten if an account breaker demands it.** Nothing else matters at that point.
3. **Manage what is open.** An existing structure has real money on it; a hypothetical
   new one does not.
4. **Bring portfolio delta back inside its budget.** And do it by *selling* a spread
   on the offsetting side, so the hedge earns premium instead of costing it.
5. **Open the best new structure.** Best meaning highest expected value per dollar-day
   of risk, across the entire universe, that also survives sizing and risk.
6. **Otherwise hold**, and write down which checks ran, because a quiet cycle still
   has to be auditable.

Exactly one ticket leaves per cycle. That keeps the journal legible and makes it
impossible for a single bad pass to reshape the whole book.

One structure per underlying and expiry: the manager groups open legs by that pair, so
allowing two would make a profit target ambiguous. It also spreads the book across
names and dates instead of stacking one.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from vrp_engine.alpaca.options import CONTRACT_MULTIPLIER, OptionCandidate
from vrp_engine.config import Settings, load_settings
from vrp_engine.risk.portfolio import (
    Exposure,
    OptionHolding,
    ShareHolding,
    holdings_from_positions,
)
from vrp_engine.strategy.base import (
    ACTION_HEDGE,
    ACTION_HOLD,
    ACTION_OPEN,
    ProposedLeg,
    ProposedTrade,
    StrategyContext,
    TradeAnalytics,
)
from vrp_engine.strategy.management import (
    OpenStructure,
    flatten_next,
    group_open_structures,
    next_management_action,
)
from vrp_engine.strategy.pricing import (
    StructureEvaluation,
    evaluate_structure,
    fill_missing_deltas,
    rank_evaluations,
)
from vrp_engine.strategy.reset import legacy_options, next_unwind_action
from vrp_engine.strategy.signals import UnderlyingSignal
from vrp_engine.strategy.sizing import RiskBudget, SizingResult, size_structure
from vrp_engine.strategy.structures import (
    SelectionParams,
    Structure,
    credit_spread_variants,
    structures_for_signal,
)

# How many ranked candidates to try sizing before giving up for this cycle. The top
# pick can fail on a budget that the second pick fits inside.
SIZING_ATTEMPTS = 6


class ScanRow(BaseModel):
    """One evaluated candidate, in a shape the dashboard can tabulate."""

    underlying: str
    structure: str
    expiration: date
    dte: int
    strikes: list[float] = Field(default_factory=list)
    realized_vol: float | None = None
    implied_vol: float | None = None
    vrp_z: float | None = None
    trend: str | None = None
    credit_usd: float = 0.0
    max_loss_usd: float = 0.0
    p_win_model: float = 0.0
    p_win_implied: float = 0.0
    wedge: float = 0.0
    expected_value_usd: float = 0.0
    edge: float = 0.0
    score: float = 0.0
    accepted: bool = False
    rejects: list[str] = Field(default_factory=list)


class ScanResult(BaseModel):
    """Everything the scanner saw this cycle, accepted or not."""

    rows: list[ScanRow] = Field(default_factory=list)
    considered_symbols: list[str] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)

    @property
    def accepted(self) -> list[ScanRow]:
        return [row for row in self.rows if row.accepted]

    def digest(self, *, limit: int = 8) -> dict[str, object]:
        """The head of the ranking plus the tally, small enough to journal every cycle."""
        return {
            "n_candidates": len(self.rows),
            "n_accepted": len(self.accepted),
            "considered": self.considered_symbols,
            "skipped": self.skipped,
            "top": [row.model_dump(mode="json") for row in self.rows[:limit]],
        }


def _scan_row(evaluation: StructureEvaluation, signal: UnderlyingSignal) -> ScanRow:
    structure = evaluation.structure
    return ScanRow(
        underlying=structure.underlying,
        structure=structure.kind,
        expiration=structure.expiration,
        dte=evaluation.dte,
        strikes=sorted(leg.contract.strike for leg in structure.legs),
        realized_vol=signal.realized_vol,
        implied_vol=signal.implied_vol,
        vrp_z=signal.vrp_z,
        trend=signal.trend,
        credit_usd=structure.credit_usd or -structure.debit_usd,
        max_loss_usd=evaluation.max_loss_usd,
        p_win_model=evaluation.p_win_model,
        p_win_implied=evaluation.p_win_implied,
        wedge=evaluation.wedge,
        expected_value_usd=evaluation.expected_value_usd,
        edge=evaluation.edge,
        score=evaluation.score,
        accepted=evaluation.acceptable,
        rejects=evaluation.rejects,
    )


def structure_to_proposal(
    evaluation: StructureEvaluation,
    sizing: SizingResult,
    signal: UnderlyingSignal,
    *,
    action: str = ACTION_OPEN,
    rationale_prefix: str = "",
) -> ProposedTrade:
    """Turn a sized, evaluated structure into the ticket the risk layer will judge."""
    structure = evaluation.structure
    legs = [
        ProposedLeg(
            symbol=leg.contract.symbol,
            side=leg.side,
            ratio_qty=1,
            position_intent=leg.open_intent,
        )
        for leg in structure.legs
    ]
    rationale = f"{evaluation.rationale()}. {sizing.rationale()}"
    if rationale_prefix:
        rationale = f"{rationale_prefix} {rationale}"

    return ProposedTrade(
        qty=sizing.contracts,
        legs=legs,
        action=action,
        kind="option",
        rationale=rationale,
        limit_price=structure.limit_price,
        estimated_cost_usd=sizing.total_risk_usd,
        max_loss_usd=sizing.total_risk_usd,
        analytics=TradeAnalytics(
            structure_kind=structure.kind,
            underlying=structure.underlying,
            expiration=structure.expiration,
            dte=evaluation.dte,
            realized_vol=signal.realized_vol,
            implied_vol=signal.implied_vol,
            vrp=signal.vrp,
            vrp_z=signal.vrp_z,
            trend=signal.trend,
            credit_usd=structure.credit_usd * sizing.contracts,
            expected_value_usd=evaluation.expected_value_usd * sizing.contracts,
            expected_value_implied_usd=evaluation.expected_value_implied_usd
            * sizing.contracts,
            model_win_prob=evaluation.p_win_model,
            implied_win_prob=evaluation.p_win_implied,
            wedge=evaluation.wedge,
            edge=evaluation.edge,
            score=evaluation.score,
            full_kelly=evaluation.full_kelly,
            binding_constraint=sizing.binding_constraint,
            breakevens=structure.breakevens(),
        ),
        sizing=sizing.model_dump(mode="json"),
    )


class VrpEngine:
    """Variance-risk-premium playbook. Implements the `Strategy` protocol."""

    name = "vrp-engine"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.last_scan = ScanResult()

    # --- selection helpers ---------------------------------------------------

    def _params(self) -> SelectionParams:
        return SelectionParams(
            target_short_delta=self.settings.target_short_delta,
            target_condor_delta=self.settings.target_condor_delta,
            debit_long_delta=self.settings.debit_long_delta,
            debit_short_delta=self.settings.debit_short_delta,
            max_spread_fraction=self.settings.max_spread_fraction,
            min_open_interest=self.settings.min_open_interest,
        )

    def _budget(self, context: StrategyContext) -> RiskBudget:
        return RiskBudget.from_settings(self.settings, equity=context.equity)

    def _held_pairs(self, structures: list[OpenStructure]) -> set[tuple[str, date]]:
        return {(s.underlying, s.expiration) for s in structures}

    def scan(self, context: StrategyContext) -> ScanResult:
        """Evaluate every candidate structure across the universe, ranked best first."""
        params = self._params()
        result = ScanResult()
        options, _ = self._holdings(context)
        held = self._held_pairs(group_open_structures(self._engine_options(options, context)))

        evaluations: list[tuple[StructureEvaluation, UnderlyingSignal]] = []
        for symbol in context.universe:
            signal = context.signals.get(symbol)
            if signal is None:
                result.skipped[symbol] = "no signal computed"
                continue
            if not signal.actionable:
                result.skipped[symbol] = "; ".join(signal.notes) or f"stance {signal.stance}"
                continue
            if (symbol, signal.expiration) in held:
                result.skipped[symbol] = (
                    f"already holding a structure expiring {signal.expiration}"
                )
                continue

            chain = fill_missing_deltas(
                context.chains.get(symbol, []), spot=signal.spot, today=context.today
            )
            if not chain:
                result.skipped[symbol] = "empty option chain"
                continue

            result.considered_symbols.append(symbol)
            for structure in structures_for_signal(signal, chain, params=params):
                evaluation = evaluate_structure(
                    structure,
                    signal,
                    today=context.today,
                    min_edge=self.settings.min_edge,
                    min_wedge=self.settings.min_wedge,
                )
                if evaluation is None:
                    continue
                evaluations.append((evaluation, signal))

        ranked = rank_evaluations([evaluation for evaluation, _ in evaluations])
        by_id = {id(evaluation): signal for evaluation, signal in evaluations}
        result.rows = [_scan_row(evaluation, by_id[id(evaluation)]) for evaluation in ranked]
        self.last_scan = result
        return result

    # --- position helpers ---------------------------------------------------

    def _holdings(
        self, context: StrategyContext
    ) -> tuple[list[OptionHolding], list[ShareHolding]]:
        return holdings_from_positions(context.positions, greeks=context.quotes)

    def _engine_options(
        self, options: list[OptionHolding], context: StrategyContext
    ) -> list[OptionHolding]:
        """Option positions the engine considers its own, i.e. not inherited."""
        stale = {
            holding.symbol
            for holding in legacy_options(
                options, today=context.today, max_dte=self.settings.max_dte
            )
        }
        return [holding for holding in options if holding.symbol not in stale]

    # --- the cycle ---------------------------------------------------------

    def propose(self, context: StrategyContext) -> ProposedTrade:
        checks: list[str] = []
        options, shares = self._holdings(context)

        if self.settings.allow_legacy_unwind:
            unwind = next_unwind_action(
                options=options,
                shares=shares,
                today=context.today,
                max_dte=self.settings.max_dte,
                quotes=context.quotes,
            )
            if unwind is not None:
                return unwind
            checks.append("no inherited positions left to unwind")

        engine_options = self._engine_options(options, context)
        structures = group_open_structures(engine_options)

        if context.flatten_required:
            exit_trade = flatten_next(
                structures,
                quotes=context.quotes,
                today=context.today,
                reason="account breaker demands a flatten",
            )
            if exit_trade is not None:
                return exit_trade
            return self._hold(
                ["account breaker demands a flatten and the book is already empty"]
            )

        managed = next_management_action(
            structures,
            settings=self.settings,
            today=context.today,
            now=context.now,
            spots=context.spots,
            vols={
                symbol: signal.realized_vol
                for symbol, signal in context.signals.items()
                if signal.realized_vol
            },
            quotes=context.quotes,
        )
        checks.extend(managed.checks)
        if managed.trade is not None:
            return managed.trade

        if not context.new_risk_allowed:
            return self._hold([*checks, "account guard withholds new risk this cycle"])

        hedge = self._delta_hedge(context, checks)
        if hedge is not None:
            return hedge

        return self._open_best(context, checks)

    def _hold(self, checks: list[str]) -> ProposedTrade:
        return ProposedTrade(
            qty=0,
            legs=[],
            action=ACTION_HOLD,
            skip=True,
            rationale=" | ".join(checks) if checks else "nothing to do this cycle",
        )

    # --- delta hedge -------------------------------------------------------

    def _delta_hedge(
        self, context: StrategyContext, checks: list[str]
    ) -> ProposedTrade | None:
        """Sell a spread on the offsetting side when the book leans too far.

        Hedging with a credit structure rather than an outright option means the
        correction is paid for by the market instead of by the account.
        """
        portfolio = context.portfolio
        if portfolio is None:
            return None
        budget = context.equity * self.settings.max_net_delta_pct
        delta_usd = portfolio.beta_weighted_delta_usd
        excess = abs(delta_usd) - budget
        if excess <= 0:
            checks.append(
                f"beta-weighted delta {portfolio.net_delta_pct:+.1%} inside the "
                f"+/-{self.settings.max_net_delta_pct:.0%} budget"
            )
            return None

        symbol = self.settings.hedge_symbol.upper()
        signal = context.signals.get(symbol)
        if signal is None or signal.expiration is None or signal.spot <= 0:
            checks.append(f"delta is {delta_usd:+.0f} USD but {symbol} has no usable signal")
            return None

        chain = fill_missing_deltas(
            context.chains.get(symbol, []), spot=signal.spot, today=context.today
        )
        # Too long means sell calls; too short means sell puts. Either way the
        # structure collects premium while it corrects the lean.
        option_type = "call" if delta_usd > 0 else "put"
        variants = credit_spread_variants(
            chain,
            underlying=symbol,
            spot=signal.spot,
            expiration=signal.expiration,
            option_type=option_type,
            target_delta=self.settings.target_short_delta,
            params=self._params(),
        )
        candidates = [
            structure
            for structure in variants
            if self._structure_delta_usd(structure, spot=signal.spot) * delta_usd < 0
        ]
        if not candidates:
            checks.append(
                f"delta is {delta_usd:+.0f} USD but no {option_type} spread on {symbol} "
                "offsets it right now"
            )
            return None

        # Prefer the tightest correction that still moves the needle.
        structure = max(
            candidates, key=lambda s: abs(self._structure_delta_usd(s, spot=signal.spot))
        )
        per_contract_delta = abs(self._structure_delta_usd(structure, spot=signal.spot))
        if per_contract_delta <= 0:
            return None

        needed = int(-(-excess // per_contract_delta))  # ceil
        affordable = int(
            (context.equity * self.settings.max_trade_loss_pct) // max(structure.max_loss_usd, 1)
        )
        contracts = max(min(needed, affordable, self.settings.max_contracts_per_order), 0)
        if contracts < 1:
            checks.append(
                f"delta hedge on {symbol} would need {needed} contract(s), more than the "
                "per-trade cap allows"
            )
            return None

        evaluation = evaluate_structure(
            structure,
            signal,
            today=context.today,
            # A hedge is a risk action, so it is not held to the edge thresholds an
            # opportunistic entry must clear. It must still be a credit and defined risk.
            min_edge=-float("inf"),
            min_wedge=-float("inf"),
        )
        sizing = SizingResult(
            contracts=contracts,
            per_contract_loss_usd=structure.max_loss_usd,
            total_risk_usd=contracts * structure.max_loss_usd,
            kelly_full=evaluation.full_kelly if evaluation else 0.0,
            kelly_target_usd=0.0,
            binding_constraint="delta budget",
            notes=[f"sized to pull {excess:.0f} USD of beta-weighted delta back inside budget"],
        )
        if evaluation is None:
            return None

        return structure_to_proposal(
            evaluation,
            sizing,
            signal,
            action=ACTION_HEDGE,
            rationale_prefix=(
                f"Delta hedge: book is {delta_usd:+.0f} USD beta-weighted against a "
                f"{budget:.0f} USD budget."
            ),
        )

    def _structure_delta_usd(self, structure: Structure, *, spot: float) -> float:
        """Dollar delta of one contract of a structure."""
        total = 0.0
        for leg in structure.legs:
            delta = leg.contract.delta
            if delta is None:
                continue
            sign = 1.0 if leg.side == "buy" else -1.0
            total += sign * delta * CONTRACT_MULTIPLIER * spot
        return total

    # --- new entries -------------------------------------------------------

    def _open_best(self, context: StrategyContext, checks: list[str]) -> ProposedTrade:
        scan = self.scan(context)
        accepted = [row for row in scan.rows if row.accepted]
        if not scan.rows:
            return self._hold(
                [*checks, "scanner found no tradable structure across the universe"]
            )
        if not accepted:
            best = scan.rows[0]
            return self._hold(
                [
                    *checks,
                    f"best candidate {best.underlying} {best.structure} rejected: "
                    + "; ".join(best.rejects),
                ]
            )

        budget = self._budget(context)
        exposure = (
            context.portfolio.exposure if context.portfolio is not None else None
        )
        params = self._params()

        attempts = 0
        for row in accepted:
            if attempts >= SIZING_ATTEMPTS:
                break
            signal = context.signals.get(row.underlying)
            if signal is None:
                continue
            chain = fill_missing_deltas(
                context.chains.get(row.underlying, []), spot=signal.spot, today=context.today
            )
            evaluation = self._rebuild_evaluation(row, signal, chain, params, context.today)
            if evaluation is None:
                continue
            attempts += 1
            sizing = size_structure(
                evaluation,
                budget=budget,
                exposure=exposure or Exposure(),
                bucket=self.settings.bucket_of(row.underlying),
                options_buying_power=context.options_buying_power,
            )
            if sizing.sizable:
                return structure_to_proposal(evaluation, sizing, signal)
            checks.append(
                f"{row.underlying} {row.structure}: {'; '.join(sizing.notes) or 'not sizable'}"
            )

        return self._hold([*checks, "no ranked candidate fitted inside the risk budgets"])

    def _rebuild_evaluation(
        self,
        row: ScanRow,
        signal: UnderlyingSignal,
        chain: list[OptionCandidate],
        params: SelectionParams,
        today: date,
    ) -> StructureEvaluation | None:
        """Re-derive the exact structure behind a scan row.

        The scan stores rows rather than live objects so it can be journalled and
        rendered; rebuilding here keeps a single source of truth for how a structure
        is constructed instead of caching two copies that could drift apart.
        """
        for structure in structures_for_signal(signal, chain, params=params):
            if structure.kind != row.structure:
                continue
            if sorted(leg.contract.strike for leg in structure.legs) != row.strikes:
                continue
            return evaluate_structure(
                structure,
                signal,
                today=today,
                min_edge=self.settings.min_edge,
                min_wedge=self.settings.min_wedge,
            )
        return None


def build_strategy(settings: Settings | None = None) -> VrpEngine:
    return VrpEngine(settings)


__all__ = [
    "ScanResult",
    "ScanRow",
    "VrpEngine",
    "build_strategy",
    "structure_to_proposal",
]
