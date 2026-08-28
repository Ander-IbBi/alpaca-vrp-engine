"""The agent cycle: observe, propose, check risk, record, and only then execute."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from options_agent.agent.llm import Advisor, AdvisorReview, build_advisor, safe_review
from options_agent.alpaca.cli_bridge import BrokerCrossCheck, cross_check_account
from options_agent.alpaca.client import PaperAlpaca
from options_agent.alpaca.options import OptionCandidate, fetch_quoted_chain
from options_agent.alpaca.orders import submit_proposal
from options_agent.config import Settings
from options_agent.journal import Journal
from options_agent.risk.account import AccountGuardResult, check_account_guardrails
from options_agent.risk.limits import RiskDecision, limits_from_settings, review_proposal
from options_agent.strategy.base import ProposedTrade, Strategy, StrategyContext
from options_agent.strategy.overlay import (
    AggressiveCollarOverlay,
    is_option_position,
    market_date,
    order_touches_watchlist,
)


class AgentCycle(BaseModel):
    """Everything one iteration did. This is what the demo and the journal show."""

    strategy: str
    market_open: bool | None = None
    equity: float | None = None
    cash: float | None = None
    n_equity_positions: int = 0
    n_option_positions: int = 0
    account_guard: AccountGuardResult | None = None
    broker_cross_check: BrokerCrossCheck | None = None
    proposal: ProposedTrade | None = None
    risk: RiskDecision | None = None
    llm: AdvisorReview | None = None
    execution: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)


class OverlayAgent:
    def __init__(
        self,
        client: PaperAlpaca,
        *,
        strategy: Strategy | None = None,
        journal: Journal | None = None,
        advisor: Advisor | None = None,
        cross_checker: Callable[..., BrokerCrossCheck] | None = None,
    ) -> None:
        self.client = client
        self.settings: Settings = client.settings
        self.strategy = strategy or AggressiveCollarOverlay(
            chain_provider=self._chain_provider,
            seed_shares=self.settings.seed_shares,
            max_equity_notional_usd=self.settings.max_equity_notional_usd,
            max_order_notional_usd=self.settings.max_order_notional_usd,
        )
        self.journal = journal or Journal(self.settings.journal_path)
        self.advisor = advisor if advisor is not None else build_advisor(self.settings)
        # Injected so tests exercise the cycle without shelling out to the CLI.
        self.cross_checker = cross_checker or cross_check_account

    def _chain_provider(self, underlying: str) -> list[OptionCandidate]:
        return fetch_quoted_chain(self.client, underlying, today=market_date())

    def build_context(self) -> StrategyContext:
        clock = self.client.clock()
        account = self.client.account()
        positions = self.client.positions()
        equity_positions = [p for p in positions if not is_option_position(p)]
        option_positions = [p for p in positions if is_option_position(p)]

        spots: dict[str, float] = {}
        for position in equity_positions:
            symbol = str(getattr(position, "symbol", "")).upper()
            price = getattr(position, "current_price", None)
            if price:
                spots[symbol] = float(price)
            else:
                spots[symbol] = self._safe_last_price(symbol)
        for symbol in self.settings.underlying_list():
            if symbol not in spots:
                spots[symbol] = self._safe_last_price(symbol)

        return StrategyContext(
            today=market_date(),
            market_open=bool(clock.is_open),
            equity=float(account.equity),
            cash=float(account.cash),
            underlyings=self.settings.underlying_list(),
            equity_positions=equity_positions,
            option_positions=option_positions,
            spot_prices=spots,
        )

    def _safe_last_price(self, symbol: str) -> float:
        try:
            return self.client.last_price(symbol) or 0.0
        except Exception:  # noqa: BLE001 — a quote blip must not kill the cycle
            return 0.0

    def _cross_check(self, context: StrategyContext, account: Any) -> BrokerCrossCheck:
        symbols = {
            str(getattr(p, "symbol", "")).upper()
            for p in [*context.equity_positions, *context.option_positions]
        }
        symbols.discard("")
        try:
            return self.cross_checker(
                account_number=str(getattr(account, "account_number", "") or ""),
                position_symbols=symbols,
            )
        except Exception as exc:  # noqa: BLE001 — the CLI is a second opinion, not a gate
            return BrokerCrossCheck(checked=False, notes=[f"{type(exc).__name__}: {exc}"])

    def _blocking_open_orders(self) -> bool:
        try:
            open_orders = self.client.open_orders()
        except Exception:  # noqa: BLE001
            # Fail closed: do not stack tickets if we cannot see working orders.
            return True
        watch = self.settings.underlying_list()
        return any(order_touches_watchlist(order, watch) for order in open_orders)

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

    def _run_once_inner(self, *, execute: bool | None = None) -> AgentCycle:
        """One full pass. Defaults to the DRY_RUN setting; pass execute=True to trade."""
        should_execute = (not self.settings.dry_run) if execute is None else execute

        context = self.build_context()
        account = self.client.account()
        guard = check_account_guardrails(
            equity=context.equity,
            last_equity=float(getattr(account, "last_equity", context.equity) or context.equity),
            settings=self.settings,
        )

        cycle = AgentCycle(
            strategy=getattr(self.strategy, "name", type(self.strategy).__name__),
            market_open=context.market_open,
            equity=context.equity,
            cash=context.cash,
            n_equity_positions=len(context.equity_positions),
            n_option_positions=len(context.option_positions),
            account_guard=guard,
        )

        if not guard.trading_allowed:
            cycle.notes.append("Account guardrail hit: " + "; ".join(guard.reasons))
            self._record(cycle)
            return cycle

        if self._blocking_open_orders():
            cycle.notes.append(
                "Open overlay orders already working (or the order list was unavailable); waiting."
            )
            self._record(cycle)
            return cycle

        # Second opinion from Alpaca's CLI: a different client reading the same
        # account. Only a real disagreement stops the cycle; a missing binary does not.
        cycle.broker_cross_check = self._cross_check(context, account)
        if cycle.broker_cross_check.checked and not cycle.broker_cross_check.agrees:
            cycle.notes.append(
                "Broker views disagree, refusing to trade on a stale book: "
                + cycle.broker_cross_check.summary()
            )
            self._record(cycle)
            return cycle

        proposal = self.strategy.propose(context)
        cycle.proposal = proposal
        if proposal.skip:
            cycle.notes.append(proposal.rationale or "Strategy proposed nothing.")
            self._record(cycle)
            return cycle

        decision = review_proposal(proposal, limits_from_settings(self.settings))
        cycle.risk = decision
        if not decision.allowed:
            cycle.notes.append("Risk layer blocked the trade: " + decision.summary())
            self._record(cycle)
            return cycle

        cycle.llm = safe_review(self.advisor, _cycle_summary(cycle))
        if not cycle.llm.approved:
            cycle.notes.append(
                "LLM soft veto ("
                + (cycle.llm.reject_reason or "unspecified")
                + "): "
                + cycle.llm.explanation
            )
            self._record(cycle)
            return cycle
        cycle.notes.append("LLM: " + cycle.llm.explanation)

        send = should_execute and bool(context.market_open)
        if should_execute and not context.market_open:
            cycle.notes.append(
                "Market closed: ticket was not sent (options day orders would fail)."
            )
            send = False

        try:
            cycle.execution = submit_proposal(self.client, proposal, dry_run=not send)
        except Exception as exc:  # noqa: BLE001
            cycle.notes.append(f"Broker rejected the ticket: {type(exc).__name__}: {exc}")
            self._record(cycle)
            return cycle
        cycle.notes.append(
            "Order submitted to paper account."
            if send
            else "Dry run: order was built and validated but not sent."
        )
        self._record(cycle)
        return cycle

    def _record(self, cycle: AgentCycle) -> None:
        try:
            self.journal.append("cycle", cycle.model_dump(mode="json", exclude_none=True))
        except Exception as exc:  # noqa: BLE001 — a full disk must not look like a missed fill
            cycle.notes.append(f"Journal write failed: {type(exc).__name__}: {exc}")


def _cycle_summary(cycle: AgentCycle) -> str:
    parts = [
        f"strategy={cycle.strategy}",
        f"market_open={cycle.market_open}",
        f"equity={cycle.equity}",
        f"cash={cycle.cash}",
    ]
    if cycle.proposal is not None:
        parts.append(f"kind={cycle.proposal.kind}")
        parts.append(f"rationale={cycle.proposal.rationale}")
        parts.append(f"qty={cycle.proposal.qty}")
        parts.append(f"legs={[leg.model_dump() for leg in cycle.proposal.legs]}")
        parts.append(f"estimated_cost_usd={cycle.proposal.estimated_cost_usd}")
        parts.append(f"max_loss_usd={cycle.proposal.max_loss_usd}")
        parts.append(f"limit_price={cycle.proposal.limit_price}")
    if cycle.risk is not None:
        parts.append(f"risk={cycle.risk.summary()}")
    return "\n".join(parts)
