"""Option contract discovery and quotes, normalised into plain candidates.

Keeping a small internal shape (`OptionCandidate`) means the strategy can be unit
tested without hitting the network or depending on alpaca-py models.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, NamedTuple

from alpaca.data.requests import OptionChainRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest
from pydantic import BaseModel

from options_agent.alpaca.client import PaperAlpaca

# OCC: ROOT + YYMMDD + C/P + strike*1000 as 8 digits (e.g. SPY260918P00750000).
_OCC_RE = re.compile(
    r"^(?P<root>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])(?P<strike>\d{8})$"
)


class ParsedOcc(NamedTuple):
    """Pieces of an OCC option symbol the overlay needs to reason about positions."""

    underlying: str
    expiration: date
    option_type: str  # "call" or "put"
    strike: float


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
    delta: float | None = None
    implied_volatility: float | None = None

    @property
    def mid_price(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    @property
    def spread_fraction(self) -> float | None:
        """(ask - bid) / mid. None when the quote is missing or mid is zero."""
        mid = self.mid_price
        if mid is None or mid <= 0 or self.bid is None or self.ask is None:
            return None
        return (self.ask - self.bid) / mid

    def dte(self, today: date) -> int:
        return (self.expiration - today).days


def parse_occ_symbol(symbol: str) -> ParsedOcc | None:
    """Decode an OCC symbol. Returns None when the string is not a standard option id."""
    match = _OCC_RE.match(symbol.strip().upper())
    if match is None:
        return None
    return ParsedOcc(
        underlying=match.group("root"),
        expiration=date(
            2000 + int(match.group("yy")),
            int(match.group("mm")),
            int(match.group("dd")),
        ),
        option_type="call" if match.group("cp") == "C" else "put",
        strike=int(match.group("strike")) / 1000.0,
    )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_bid_ask(snapshot: Any) -> tuple[float | None, float | None]:
    quote = getattr(snapshot, "latest_quote", None)
    if quote is None and isinstance(snapshot, dict):
        quote = snapshot.get("latest_quote") or snapshot.get("latestQuote")
    if quote is None:
        return None, None
    bid = getattr(quote, "bid_price", None)
    ask = getattr(quote, "ask_price", None)
    if bid is None and isinstance(quote, dict):
        bid = quote.get("bid_price", quote.get("bp"))
        ask = quote.get("ask_price", quote.get("ap"))
    return _as_float(bid), _as_float(ask)


def _greeks_delta_iv(snapshot: Any) -> tuple[float | None, float | None]:
    greeks = getattr(snapshot, "greeks", None)
    iv = getattr(snapshot, "implied_volatility", None)
    if isinstance(snapshot, dict):
        greeks = snapshot.get("greeks") or greeks
        iv = snapshot.get("implied_volatility") or snapshot.get("impliedVolatility") or iv
    delta = getattr(greeks, "delta", None) if greeks is not None else None
    if delta is None and isinstance(greeks, dict):
        delta = greeks.get("delta")
    return _as_float(delta), _as_float(iv)


def candidates_from_snapshots(
    snapshots: dict[str, Any],
    *,
    underlying: str,
) -> list[OptionCandidate]:
    """Turn a chain snapshot (quotes, greeks, IV) into overlay candidates.

    Pure enough to unit test: pass a fake dict of snapshot-like objects, no network.
    """
    ticker = underlying.upper()
    candidates: list[OptionCandidate] = []
    for symbol, snapshot in snapshots.items():
        parsed = parse_occ_symbol(symbol)
        if parsed is None or parsed.underlying != ticker:
            continue
        bid, ask = _quote_bid_ask(snapshot)
        delta, iv = _greeks_delta_iv(snapshot)
        candidates.append(
            OptionCandidate(
                symbol=symbol,
                underlying=parsed.underlying,
                option_type=parsed.option_type,
                strike=parsed.strike,
                expiration=parsed.expiration,
                bid=bid,
                ask=ask,
                delta=delta,
                implied_volatility=iv,
            )
        )
    return candidates


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


def fetch_quoted_chain(
    client: PaperAlpaca,
    underlying: str,
    *,
    today: date | None = None,
    min_dte: int = 21,
    max_dte: int = 45,
) -> list[OptionCandidate]:
    """Contracts in the DTE window, with bid/ask/delta/IV from the chain snapshot.

    This is what the live overlay uses: without quotes the risk layer would reject
    every ticket for a missing cost estimate.
    """
    as_of = today or date.today()
    request = OptionChainRequest(
        underlying_symbol=underlying.upper(),
        expiration_date_gte=as_of + timedelta(days=min_dte),
        expiration_date_lte=as_of + timedelta(days=max_dte),
    )
    chain = client.option_data.get_option_chain(request)
    return candidates_from_snapshots(dict(chain), underlying=underlying)
