"""One cycle of the agent, and the order the steps have to happen in.

    observe -> guard -> research -> signals -> propose -> risk -> analyst
            -> verify -> execute -> reconcile -> journal

The ordering is the design. Risk runs *after* the strategy and *before* the analyst, so
no amount of LLM output can talk its way past a budget. The CLI verifies the book right
before a ticket goes out and again right after, because acting on a stale view of the
book is how an autonomous agent doubles a position it thought it had closed.

Everything is wrapped so a single API fault degrades the cycle instead of killing the
loop: the agent is meant to run unattended for a week.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from vrp_engine.agent.analyst import (
    Analyst,
    AnalystReview,
    build_analyst,
    safe_brief,
    safe_review,
)
from vrp_engine.alpaca.cli_bridge import (
    BrokerCrossCheck,
    FillReconciliation,
    cross_check_account,
    reconcile_after_submit,
)
from vrp_engine.alpaca.client import PaperAlpaca
from vrp_engine.alpaca.market_data import PriceHistory, fetch_daily_bars
from vrp_engine.alpaca.mcp_bridge import (
    McpResearch,
    QuoteCrossCheck,
    ToolCall,
    cross_check_option_quotes,
    default_calls,
    gather_research,
    snapshot_calls,
)
from vrp_engine.alpaca.options import (
    OptionCandidate,
    expiries_in_window,
    fetch_quoted_chain,
    fetch_snapshots_for,
    is_option_position,
    market_date,
    parse_occ_symbol,
)
from vrp_engine.alpaca.orders import submit_proposal
from vrp_engine.config import Settings
from vrp_engine.journal import Journal
from vrp_engine.risk.account import AccountGuardResult, check_account_guardrails
from vrp_engine.risk.limits import RiskDecision, RiskLimits, review_proposal
from vrp_engine.risk.portfolio import (
    PortfolioRisk,
    build_portfolio_risk,
    holdings_from_positions,
    prospective_holdings,
)
from vrp_engine.strategy.base import ACTION_OPEN, ProposedTrade, Strategy, StrategyContext
from vrp_engine.strategy.engine import ScanResult, VrpEngine
from vrp_engine.strategy.management import total_open_contracts
from vrp_engine.strategy.signals import UnderlyingSignal, build_signal

MARKET_SYMBOL = "SPY"
# A limit order resting at a mid that has since moved will not fill. Pull it rather
# than let it block the underlying for the rest of the session.
ORDER_STALE_SECONDS = 150


def _utcnow() -> datetime:
    """The cycle's single clock seam, so a run can be replayed at a fixed moment."""
    return datetime.now(UTC)


class AgentCycle(BaseModel):
    """Everything one iteration did. This is what the demo and the journal show."""

    strategy: str
    market_open: bool | None = None
    market_open_cli: bool | None = None
    equity: float | None = None
    cash: float | None = None
    options_buying_power: float | None = None
    n_option_positions: int = 0
    n_share_positions: int = 0
    account_guard: AccountGuardResult | None = None
    broker_cross_check: BrokerCrossCheck | None = None
    signals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    portfolio: dict[str, Any] | None = None
    scan: dict[str, Any] | None = None
    research: dict[str, Any] | None = None
    quote_cross_check: QuoteCrossCheck | None = None
    proposal: ProposedTrade | None = None
    risk: RiskDecision | None = None
    analyst: AnalystReview | None = None
    execution: dict[str, Any] | None = None
    reconciliation: FillReconciliation | None = None
    notes: list[str] = Field(default_factory=list)


@dataclass
class Observation:
    """The world as one cycle found it, before any decision was taken."""

    cycle: AgentCycle
    context: StrategyContext
    account: Any
    positions: list[Any]
    option_holdings: list[Any]
    portfolio: PortfolioRisk
    guard: AccountGuardResult


def _signal_digest(signal: UnderlyingSignal) -> dict[str, Any]:
    return {
        "spot": round(signal.spot, 2),
        "realized_vol": round(signal.realized_vol, 4) if signal.realized_vol else None,
        "implied_vol": round(signal.implied_vol, 4) if signal.implied_vol else None,
        "vrp": round(signal.vrp, 4) if signal.vrp else None,
        "vrp_z": round(signal.vrp_z, 3) if signal.vrp_z else None,
        "term_slope": round(signal.term_slope, 4) if signal.term_slope else None,
        "trend": signal.trend,
        "beta": round(signal.beta, 3),
        "stance": signal.stance,
        "event_blackout": signal.event_blackout,
        "expiration": signal.expiration.isoformat() if signal.expiration else None,
        "notes": signal.notes,
    }


class VrpAgent:
    """Wires the strategy, the risk layer and the three broker planes together."""

    def __init__(
        self,
        client: PaperAlpaca,
        *,
        strategy: Strategy | None = None,
        journal: Journal | None = None,
        analyst: Analyst | None = None,
        cross_checker: Callable[..., BrokerCrossCheck] | None = None,
        reconciler: Callable[..., FillReconciliation] | None = None,
        researcher: Callable[..., McpResearch] | None = None,
    ) -> None:
        self.client = client
        self.settings: Settings = client.settings
        self.strategy = strategy or VrpEngine(self.settings)
        self.journal = journal or Journal(self.settings.journal_path)
        self.analyst = analyst if analyst is not None else build_analyst(self.settings)
        # Injected so tests exercise the cycle without shelling out or spawning servers.
        self.cross_checker = cross_checker or cross_check_account
        self.reconciler = reconciler or reconcile_after_submit
        self.researcher = researcher or gather_research

        # Cycle-to-cycle state. Deliberately small: everything else is re-read.
        self.entries_frozen = False
        self._briefing_date: Any = None
        self._briefing_text = ""

    # --- observation --------------------------------------------------------

    def _histories(self, symbols: list[str], today: Any) -> dict[str, PriceHistory]:
        try:
            return fetch_daily_bars(self.client, symbols, today=today)
        except Exception as exc:  # noqa: BLE001 — no bars means no signals, not a crash
            self._last_data_error = f"bars unavailable: {type(exc).__name__}: {exc}"
            return {}

    def _chains(self, symbols: list[str], today: Any) -> dict[str, list[OptionCandidate]]:
        chains: dict[str, list[OptionCandidate]] = {}
        for symbol in symbols:
            try:
                chains[symbol] = fetch_quoted_chain(
                    self.client,
                    symbol,
                    today=today,
                    min_dte=self.settings.min_dte,
                    max_dte=self.settings.max_dte,
                )
            except Exception:  # noqa: BLE001 — one bad chain must not blind the scan
                chains[symbol] = []
        return chains

    def _held_quotes(
        self,
        positions: list[Any],
        chains: dict[str, list[OptionCandidate]],
    ) -> dict[str, OptionCandidate]:
        """Live marks for every option symbol in play, held or quoted.

        Held legs can sit outside the chain window (inherited, or about to be closed),
        so those are fetched explicitly rather than left without a mark.
        """
        quotes: dict[str, OptionCandidate] = {}
        for candidates in chains.values():
            for candidate in candidates:
                quotes[candidate.symbol.upper()] = candidate

        held = {
            str(getattr(p, "symbol", "")).upper()
            for p in positions
            if is_option_position(p)
        }
        missing = sorted(held - set(quotes))
        if missing:
            try:
                for candidate in fetch_snapshots_for(self.client, missing):
                    quotes[candidate.symbol.upper()] = candidate
            except Exception:  # noqa: BLE001 — fall back to the broker's own mark
                pass
        return quotes

    def _signals(
        self,
        *,
        universe: list[str],
        histories: dict[str, PriceHistory],
        chains: dict[str, list[OptionCandidate]],
        spots: dict[str, float],
        today: Any,
    ) -> dict[str, UnderlyingSignal]:
        market = histories.get(MARKET_SYMBOL)
        market_returns = market.log_returns() if market else []
        signals: dict[str, UnderlyingSignal] = {}
        for symbol in universe:
            history = histories.get(symbol)
            if history is None or not history.bars:
                continue
            candidates = chains.get(symbol, [])
            spot = spots.get(symbol) or history.last_close or 0.0
            signals[symbol] = build_signal(
                symbol=symbol,
                spot=spot,
                history=history,
                candidates=candidates,
                expiries=expiries_in_window(
                    candidates,
                    today=today,
                    min_dte=self.settings.min_dte,
                    max_dte=self.settings.max_dte,
                ),
                market_returns=market_returns,
                today=today,
                vrp_z_entry=self.settings.vrp_z_entry,
                term_slope_blackout=self.settings.term_slope_blackout,
            )
        return signals

    def _spots(self, positions: list[Any], histories: dict[str, PriceHistory]) -> dict[str, float]:
        """Underlying prices, preferring what the broker already marked our book at."""
        spots: dict[str, float] = {}
        for symbol, history in histories.items():
            if history.last_close:
                spots[symbol] = float(history.last_close)
        for position in positions:
            symbol = str(getattr(position, "symbol", "") or "").upper()
            price = getattr(position, "current_price", None)
            if not symbol or not price or is_option_position(position):
                continue
            try:
                spots[symbol] = float(price)
            except (TypeError, ValueError):
                continue
        # A held option whose underlying is outside the universe still needs a spot.
        for position in positions:
            symbol = str(getattr(position, "symbol", "") or "").upper()
            parsed = parse_occ_symbol(symbol)
            if parsed is None or parsed.underlying in spots:
                continue
            try:
                spots[parsed.underlying] = float(self.client.last_price(parsed.underlying) or 0.0)
            except Exception:  # noqa: BLE001
                spots[parsed.underlying] = 0.0
        return spots

    # --- working orders ----------------------------------------------------

    def _working_underlyings(self, notes: list[str]) -> set[str] | None:
        """Cancel stale limits and report which underlyings still have live orders.

        Returns None when the order list could not be read, which the caller treats as
        a reason to stand down: stacking tickets on an invisible order book is how an
        agent ends up doubled.
        """
        try:
            orders = self.client.open_orders()
        except Exception as exc:  # noqa: BLE001
            notes.append(f"could not read working orders ({type(exc).__name__}); standing down")
            return None

        now = _utcnow()
        busy: set[str] = set()
        for order in orders:
            symbols = [str(getattr(order, "symbol", "") or "").upper()]
            for leg in getattr(order, "legs", None) or []:
                symbols.append(str(getattr(leg, "symbol", "") or "").upper())
            underlyings = set()
            for symbol in symbols:
                parsed = parse_occ_symbol(symbol)
                underlyings.add(parsed.underlying if parsed else symbol)
            underlyings.discard("")

            stamp = getattr(order, "submitted_at", None) or getattr(order, "created_at", None)
            age = None
            if isinstance(stamp, datetime):
                reference = stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
                age = (now - reference).total_seconds()

            if age is not None and age > ORDER_STALE_SECONDS:
                try:
                    self.client.cancel_order(str(getattr(order, "id", "")))
                    notes.append(
                        f"cancelled a stale {'/'.join(sorted(underlyings))} order after "
                        f"{age:.0f}s at a mid that has moved on"
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"could not cancel a stale order ({type(exc).__name__})")
            busy |= underlyings
        return busy

    # --- research plane ---------------------------------------------------

    def _research(self, *, today: Any, snapshot_symbols: list[str]) -> McpResearch:
        """One MCP session per cycle, carrying the briefing only once a day."""
        calls: list[ToolCall] = []
        needs_briefing = self._briefing_date != today
        if needs_briefing:
            calls.extend(default_calls(self.settings))
        if snapshot_symbols:
            calls.extend(snapshot_calls(snapshot_symbols))
        if not calls:
            return McpResearch(available=False, notes=["nothing to research this cycle"])

        research = self.researcher(self.settings, calls=calls)
        if needs_briefing and research.available:
            self._briefing_date = today
            self._briefing_text = safe_brief(self.analyst, research.briefing())
        return research

    # --- the cycle --------------------------------------------------------

    def run_once(self, *, execute: bool | None = None) -> AgentCycle:
        """One full pass. Defaults to the DRY_RUN setting; pass execute=True to trade."""
        try:
            return self._run_once_inner(execute=execute)
        except Exception as exc:  # noqa: BLE001 — keep --loop alive through API faults
            cycle = AgentCycle(
                strategy=getattr(self.strategy, "name", type(self.strategy).__name__),
                notes=[f"Cycle failed: {type(exc).__name__}: {exc}"],
            )
            try:
                self._record(cycle)
            except Exception:  # noqa: BLE001
                pass
            return cycle

    def observe(self) -> Observation:
        """Read the world and build the strategy's context, without deciding anything.

        Split out from the cycle so the scanner can be inspected on demand (`scan`,
        `broker_report.py`) through exactly the same observation code the live loop uses.
        """
        today = market_date()
        now = _utcnow()

        clock = self.client.clock()
        account = self.client.account()
        positions = self.client.positions()
        universe = self.settings.universe_list()

        options_bp = self.client.options_buying_power()
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        cash = float(getattr(account, "cash", 0.0) or 0.0)

        option_holdings, share_holdings = holdings_from_positions(positions)
        cycle = AgentCycle(
            strategy=getattr(self.strategy, "name", type(self.strategy).__name__),
            market_open=bool(getattr(clock, "is_open", False)),
            equity=equity,
            cash=cash,
            options_buying_power=options_bp,
            n_option_positions=len(option_holdings),
            n_share_positions=len(share_holdings),
        )

        guard = check_account_guardrails(
            equity=equity,
            last_equity=float(getattr(account, "last_equity", equity) or equity),
            high_water_mark=self._high_water_mark(),
            now=now,
            settings=self.settings,
        )
        cycle.account_guard = guard
        if not guard.new_risk_allowed:
            cycle.notes.append(guard.summary())

        histories = self._histories(universe, today)
        chains = self._chains(universe, today)
        spots = self._spots(positions, histories)
        quotes = self._held_quotes(positions, chains)
        signals = self._signals(
            universe=universe, histories=histories, chains=chains, spots=spots, today=today
        )
        cycle.signals = {symbol: _signal_digest(signal) for symbol, signal in signals.items()}

        portfolio = build_portfolio_risk(
            equity=equity,
            positions=positions,
            spots=spots,
            betas={s: sig.beta for s, sig in signals.items()},
            vols={s: sig.realized_vol for s, sig in signals.items() if sig.realized_vol},
            greeks=quotes,
            bucket_of=self.settings.bucket_of,
        )
        cycle.portfolio = portfolio.digest()

        context = StrategyContext(
            today=today,
            now=now,
            market_open=bool(cycle.market_open),
            equity=equity,
            cash=cash,
            options_buying_power=options_bp,
            universe=universe,
            spots=spots,
            signals=signals,
            chains=chains,
            portfolio=portfolio,
            positions=positions,
            quotes=quotes,
            new_risk_allowed=guard.new_risk_allowed and not self.entries_frozen,
            flatten_required=guard.flatten_required,
        )
        return Observation(
            cycle=cycle,
            context=context,
            account=account,
            positions=positions,
            option_holdings=option_holdings,
            portfolio=portfolio,
            guard=guard,
        )

    def dry_scan(self) -> tuple[AgentCycle, ScanResult]:
        """Rank the whole universe, ignoring the session window and the account guard.

        The live cycle skips the scan when it already knows it may not open risk, which
        is correct but leaves nothing to look at out of hours. This runs the ranking
        anyway, so a dry-run report can prove the engine sees the market properly on a
        Saturday. It never proposes, sizes or sends.
        """
        observation = self.observe()
        scanner = getattr(self.strategy, "scan", None)
        if scanner is None:
            return observation.cycle, ScanResult()
        scan = scanner(observation.context)
        observation.cycle.scan = scan.digest(limit=12)
        return observation.cycle, scan

    def _run_once_inner(self, *, execute: bool | None = None) -> AgentCycle:
        should_execute = (not self.settings.dry_run) if execute is None else execute

        observed = self.observe()
        cycle = observed.cycle
        context = observed.context
        account = observed.account
        positions = observed.positions
        portfolio = observed.portfolio
        equity = context.equity
        option_holdings = observed.option_holdings

        busy = self._working_underlyings(cycle.notes)
        if busy is None:
            self._record(cycle)
            return cycle

        today = context.today
        if self.entries_frozen:
            cycle.notes.append(
                "New entries are frozen after a broker reconciliation mismatch; "
                "managing exits only until the two views agree again."
            )

        proposal = self.strategy.propose(context)
        cycle.proposal = proposal
        scan = getattr(self.strategy, "last_scan", None)
        if isinstance(scan, ScanResult):
            cycle.scan = scan.digest()

        if proposal.skip or not proposal.legs:
            cycle.notes.append(proposal.rationale or "Strategy proposed nothing.")
            self._record(cycle)
            return cycle

        # Price the book as if this ticket had already filled: the budget test is on
        # the resulting portfolio, not on the ticket in isolation.
        post_trade = self._post_trade_risk(
            proposal, portfolio=portfolio, context=context, positions=positions
        )
        decision = review_proposal(
            proposal,
            RiskLimits.from_settings(self.settings, equity=equity),
            open_contracts=total_open_contracts(option_holdings),
            post_trade=post_trade,
            bucket=(
                self.settings.bucket_of(proposal.analytics.underlying)
                if proposal.analytics and proposal.analytics.underlying
                else None
            ),
        )
        cycle.risk = decision
        if not decision.allowed:
            cycle.notes.append("Risk layer blocked the trade: " + decision.summary())
            self._record(cycle)
            return cycle

        research = self._research(
            today=today,
            snapshot_symbols=[leg.symbol for leg in proposal.legs],
        )
        cycle.research = {
            "available": research.available,
            "server": research.server,
            "tools_seen": research.tools_seen,
            "notes": research.notes,
            "briefing": self._briefing_text,
        }
        proposal_quotes = [
            context.quotes[leg.symbol.upper()]
            for leg in proposal.legs
            if leg.symbol.upper() in context.quotes
        ]
        cycle.quote_cross_check = cross_check_option_quotes(research, proposal_quotes)
        if cycle.quote_cross_check.checked and not cycle.quote_cross_check.agrees:
            cycle.notes.append(
                "Research plane disagrees with the SDK on this structure's quotes, "
                "refusing to size a possibly stale edge: "
                + cycle.quote_cross_check.summary()
            )
            self._record(cycle)
            return cycle

        cycle.analyst = safe_review(
            self.analyst, _cycle_summary(cycle), self._briefing_text or research.briefing()
        )
        if not cycle.analyst.approved:
            cycle.notes.append(
                "Analyst soft veto ("
                + (cycle.analyst.reject_reason or "unspecified")
                + "): "
                + cycle.analyst.explanation
            )
            self._record(cycle)
            return cycle
        cycle.notes.append("Analyst: " + cycle.analyst.explanation)

        underlying = proposal.analytics.underlying if proposal.analytics else None
        if underlying and underlying in busy:
            cycle.notes.append(
                f"An order on {underlying} is still working; not stacking another ticket."
            )
            self._record(cycle)
            return cycle

        # Last look before the ticket goes out: does a second client agree about the book?
        cycle.broker_cross_check = self._cross_check(positions, account)
        if cycle.broker_cross_check.checked and not cycle.broker_cross_check.agrees:
            cycle.notes.append(
                "Broker views disagree, refusing to trade on a stale book: "
                + cycle.broker_cross_check.summary()
            )
            self._record(cycle)
            return cycle

        send = should_execute and bool(cycle.market_open)
        if should_execute and not cycle.market_open:
            cycle.notes.append(
                "Market closed: ticket was not sent (options are day orders only)."
            )
            send = False

        try:
            cycle.execution = submit_proposal(self.client, proposal, dry_run=not send)
        except Exception as exc:  # noqa: BLE001
            cycle.notes.append(f"Broker rejected the ticket: {type(exc).__name__}: {exc}")
            self._record(cycle)
            return cycle

        cycle.notes.append(
            "Order submitted to the paper account."
            if send
            else "Dry run: order was built and validated but not sent."
        )

        if send:
            cycle.reconciliation = self._reconcile(proposal, positions)
            cycle.notes.append(cycle.reconciliation.summary())
            if cycle.reconciliation.checked and not cycle.reconciliation.consistent:
                self.entries_frozen = True
                cycle.notes.append(
                    "Freezing new entries until the SDK and the CLI agree on the book."
                )
            elif cycle.reconciliation.consistent:
                self.entries_frozen = False

        self._record(cycle)
        return cycle

    # --- helpers ----------------------------------------------------------

    def _post_trade_risk(
        self,
        proposal: ProposedTrade,
        *,
        portfolio: PortfolioRisk,
        context: StrategyContext,
        positions: list[Any],
    ) -> PortfolioRisk | None:
        """Rebuild the portfolio with the proposal's legs added.

        Exits and share sales only shrink the book, so there is nothing to pre-check.
        """
        if proposal.action != ACTION_OPEN and proposal.kind != "option":
            return portfolio
        if proposal.is_closing or proposal.kind == "equity":
            return portfolio

        pairs = []
        for leg in proposal.legs:
            candidate = context.quotes.get(leg.symbol.upper())
            if candidate is None:
                return None
            pairs.append((candidate, leg.side))

        extra = prospective_holdings(symbols_sides=pairs, contracts=proposal.qty)
        return build_portfolio_risk(
            equity=context.equity,
            positions=[*positions, *_as_position_like(extra)],
            spots=context.spots,
            betas={s: sig.beta for s, sig in context.signals.items()},
            vols={
                s: sig.realized_vol
                for s, sig in context.signals.items()
                if sig.realized_vol
            },
            greeks=context.quotes,
            bucket_of=self.settings.bucket_of,
        )

    def _high_water_mark(self) -> float | None:
        try:
            return self.journal.high_water_mark()
        except Exception:  # noqa: BLE001 — a missing journal is not a risk event
            return None

    def _cross_check(self, positions: list[Any], account: Any) -> BrokerCrossCheck:
        symbols = {str(getattr(p, "symbol", "")).upper() for p in positions}
        symbols.discard("")
        try:
            return self.cross_checker(
                account_number=str(getattr(account, "account_number", "") or ""),
                position_symbols=symbols,
            )
        except Exception as exc:  # noqa: BLE001 — the CLI is a second opinion, not a gate
            return BrokerCrossCheck(checked=False, notes=[f"{type(exc).__name__}: {exc}"])

    def _reconcile(self, proposal: ProposedTrade, positions: list[Any]) -> FillReconciliation:
        symbols = {str(getattr(p, "symbol", "")).upper() for p in positions}
        symbols.discard("")
        try:
            return self.reconciler(
                sdk_symbols=symbols,
                expected_symbols=[leg.symbol for leg in proposal.legs],
                opening=not proposal.is_closing,
            )
        except Exception as exc:  # noqa: BLE001
            return FillReconciliation(
                checked=False, notes=[f"{type(exc).__name__}: {exc}"]
            )

    def _record(self, cycle: AgentCycle) -> None:
        try:
            self.journal.append("cycle", cycle.model_dump(mode="json", exclude_none=True))
        except Exception as exc:  # noqa: BLE001 — a full disk must not look like a missed fill
            cycle.notes.append(f"Journal write failed: {type(exc).__name__}: {exc}")


class _PositionLike:
    """Minimal duck-typed position so the risk engine can price a hypothetical fill."""

    def __init__(self, holding: Any) -> None:
        self.symbol = holding.symbol
        self.qty = holding.contracts
        self.market_value = holding.market_value
        self.avg_entry_price = holding.avg_entry_price
        self.current_price = holding.current_price
        self.unrealized_pl = 0.0
        self.asset_class = "us_option"


def _as_position_like(holdings: list[Any]) -> list[_PositionLike]:
    return [_PositionLike(holding) for holding in holdings]


def _cycle_summary(cycle: AgentCycle) -> str:
    parts = [
        f"strategy={cycle.strategy}",
        f"market_open={cycle.market_open}",
        f"equity={cycle.equity}",
    ]
    if cycle.portfolio is not None:
        parts.append(f"portfolio={cycle.portfolio}")
    if cycle.proposal is not None:
        proposal = cycle.proposal
        parts.append(f"action={proposal.action}")
        parts.append(f"rationale={proposal.rationale}")
        parts.append(f"qty={proposal.qty}")
        parts.append(f"legs={[leg.model_dump() for leg in proposal.legs]}")
        parts.append(f"limit_price={proposal.limit_price}")
        parts.append(f"max_loss_usd={proposal.max_loss_usd}")
        if proposal.analytics is not None:
            parts.append(f"analytics={proposal.analytics.model_dump(exclude_none=True)}")
    if cycle.risk is not None:
        parts.append(f"risk_checks={cycle.risk.checks}")
    return "\n".join(parts)
