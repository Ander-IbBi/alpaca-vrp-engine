"""Option contract discovery and quotes, normalised into plain candidates.

Keeping a small internal shape (`OptionCandidate`) means the strategy can be unit
tested without hitting the network or depending on alpaca-py models.
"""

from __future__ import annotations

from datetime import date

from alpaca.data.requests import OptionChainRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest
from pydantic import BaseModel

from options_agent.alpaca.client import PaperAlpaca


class OptionCandidate(BaseModel):
    """One tradable contract, reduced to what the overlay logic needs."""

    symbol: str
    underlying: str
    option_type: str  # "call" or "put"
    strike: float
    expiration: date
    open_interest: int | None = None
    bid: float | None = None
    ask: float | None = None

    @property
    def mid_price(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    def dte(self, today: date) -> int:
        return (self.expiration - today).days


def fetch_contracts(
    client: PaperAlpaca,
    underlying: str,
    *,
    option_type: ContractType | None = None,
    expiration_gte: date | None = None,
    expiration_lte: date | None = None,
    limit: int = 100,
) -> list[OptionCandidate]:
    """List active contracts for an underlying (no prices attached)."""
    request = GetOptionContractsRequest(
        underlying_symbols=[underlying.upper()],
        status=AssetStatus.ACTIVE,
        type=option_type,
        expiration_date_gte=expiration_gte,
        expiration_date_lte=expiration_lte,
        limit=limit,
    )
    response = client.trading.get_option_contracts(request)
    candidates: list[OptionCandidate] = []
    for contract in response.option_contracts or []:
        candidates.append(
            OptionCandidate(
                symbol=contract.symbol,
                underlying=contract.underlying_symbol,
                option_type=str(getattr(contract.type, "value", contract.type)),
                strike=float(contract.strike_price),
                expiration=contract.expiration_date,
                open_interest=int(contract.open_interest) if contract.open_interest else None,
            )
        )
    return candidates


def fetch_chain_quotes(client: PaperAlpaca, underlying: str) -> dict[str, dict]:
    """Raw chain snapshot (quotes, greeks, IV) keyed by OCC symbol."""
    request = OptionChainRequest(underlying_symbol=underlying.upper())
    chain = client.option_data.get_option_chain(request)
    return dict(chain)
