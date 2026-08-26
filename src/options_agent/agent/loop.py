"""The agent cycle: observe, propose, check risk, record, and only then execute."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from options_agent.alpaca.client import PaperAlpaca
from options_agent.alpaca.options import OptionCandidate, fetch_contracts
from options_agent.alpaca.orders import submit_proposal
from options_agent.config import Settings
from options_agent.journal import Journal
from options_agent.risk.account import AccountGuardResult, check_account_guardrails
from options_agent.risk.limits import RiskDecision, limits_from_settings, review_proposal
from options_agent.strategy.base import ProposedTrade, Strategy, StrategyContext
from options_agent.strategy.overlay import ProtectivePutOverlay


class AgentCycle(BaseModel):
    """Everything one iteration did. This is what the demo and the journal show."""

    strategy: str
    market_open: bool | None = None
    equity: float | None = None
    cash: float | None = None
    n_equity_positions: int = 0
    n_option_positions: int = 0
    account_guard: AccountGuardResult | None = None
    proposal: ProposedTrade | None = None
    risk: RiskDecision | None = None
    execution: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)


class OverlayAgent:
    def __init__(
        self,
        client: PaperAlpaca,
        *,
        strategy: Strategy | None = None,
        journal: Journal | None = None,
    ) -> None:
        self.client = client
        self.settings: Settings = client.settings
        self.strategy = strategy or ProtectivePutOverlay(chain_provider=self._chain_provider)
        self.journal = journal or Journal(self.settings.journal_path)

    def _chain_provider(self, underlying: str) -> list[OptionCandidate]:
        return fetch_contracts(self.client, underlying, limit=200)

    def build_context(self) -> StrategyContext:
        clock = self.client.clock()
        account = self.client.account()
        positions = self.client.positions()
        equity_positions = [
            p for p in positions if str(getattr(p, "asset_class", "")) != "us_option"
        ]
        option_positions = [
            p for p in positions if str(getattr(p, "asset_class", "")) == "us_option"
        ]

        # Spot prices only for what we might hedge, to keep the call count low.
        spots: dict[str, float] = {}
        for position in equity_positions:
            symbol = str(getattr(position, "symbol", "")).upper()
            price = getattr(position, "current_price", None)
            spots[symbol] = float(price) if price else (self.client.last_price(symbol) or 0.0)

        return StrategyContext(
            today=datetime.now(UTC).date(),
            market_open=bool(clock.is_open),
            equity=float(account.equity),
            cash=float(account.cash),
            underlyings=self.settings.underlying_list(),
            equity_positions=equity_positions,
            option_positions=option_positions,
            spot_prices=spots,
        )

    def run_once(self, *, execute: bool | None = None) -> AgentCycle:
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

        cycle.execution = submit_proposal(self.client, proposal, dry_run=not should_execute)
        cycle.notes.append(
            "Order submitted to paper account."
            if should_execute
            else "Dry run: order was built and validated but not sent."
        )
        self._record(cycle)
        return cycle

    def _record(self, cycle: AgentCycle) -> None:
        self.journal.append("cycle", cycle.model_dump(mode="json", exclude_none=True))
