"""The agent cycle: observe, propose, check risk, record, and only then execute."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from options_agent.agent.llm import Advisor, AdvisorReview, build_advisor, safe_review
from options_agent.alpaca.client import PaperAlpaca
from options_agent.alpaca.options import OptionCandidate, fetch_quoted_chain
from options_agent.alpaca.orders import submit_proposal
from options_agent.config import Settings
from options_agent.journal import Journal
from options_agent.risk.account import AccountGuardResult, check_account_guardrails
from options_agent.risk.limits import RiskDecision, limits_from_settings, review_proposal
from options_agent.strategy.base import ProposedTrade, Strategy, StrategyContext
from options_agent.strategy.overlay import AggressiveCollarOverlay


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
    ) -> None:
        self.client = client
        self.settings: Settings = client.settings
        self.strategy = strategy or AggressiveCollarOverlay(
            chain_provider=self._chain_provider,
            seed_shares=self.settings.seed_shares,
            max_equity_notional_usd=self.settings.max_equity_notional_usd,
        )
        self.journal = journal or Journal(self.settings.journal_path)
        self.advisor = advisor if advisor is not None else build_advisor(self.settings)

    def _chain_provider(self, underlying: str) -> list[OptionCandidate]:
        return fetch_quoted_chain(self.client, underlying, today=datetime.now(UTC).date())

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

        # Spots for held names and the watchlist (needed to seed SPY from a flat book).
        spots: dict[str, float] = {}
        for position in equity_positions:
            symbol = str(getattr(position, "symbol", "")).upper()
            price = getattr(position, "current_price", None)
            spots[symbol] = float(price) if price else (self.client.last_price(symbol) or 0.0)
        for symbol in self.settings.underlying_list():
            if symbol not in spots:
                spots[symbol] = self.client.last_price(symbol) or 0.0

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

        if self.client.open_orders():
            cycle.notes.append("Open orders already working; waiting for fill.")
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
            cycle.execution = submit_proposal(self.client, proposal, dry_run=True)
            self._record(cycle)
            return cycle

        cycle.execution = submit_proposal(self.client, proposal, dry_run=not send)
        cycle.notes.append(
            "Order submitted to paper account."
            if send
            else "Dry run: order was built and validated but not sent."
        )
        self._record(cycle)
        return cycle

    def _record(self, cycle: AgentCycle) -> None:
        self.journal.append("cycle", cycle.model_dump(mode="json", exclude_none=True))


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
