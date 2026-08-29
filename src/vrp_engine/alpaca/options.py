"""Option contract discovery and quotes, normalised into plain candidates.

Keeping a small internal shape (`OptionCandidate`) means the whole strategy can be
unit tested without hitting the network or depending on alpaca-py models. The greeks
travel with the candidate because the risk layer aggregates them into portfolio
exposures, not just to pick a strike.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from alpaca.data.requests import OptionChainRequest, OptionSnapshotRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest
from pydantic import BaseModel

from vrp_engine.alpaca.client import PaperAlpaca

US_EASTERN = ZoneInfo("America/New_York")
CONTRACT_MULTIPLIER = 100

# OCC: ROOT + YYMMDD + C/P + strike*1000 as 8 digits (e.g. SPY260918P00750000).
_OCC_RE = re.compile(
    r"^(?P<root>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])(?P<strike>\d{8})$"
)


class ParsedOcc(NamedTuple):
    """Pieces of an OCC option symbol the engine needs to reason about positions."""

    underlying: str
    expiration: date
    option_type: str  # "call" or "put"
    strike: float


class OptionCandidate(BaseModel):
    """One tradable contract, reduced to what the engine needs."""

    symbol: str
    underlying: str
    option_type: str  # "call" or "put"
    strike: float
    expiration: date
    open_interest: int | None = None
    bid: float | None = None
    ask: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
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

    @property
    def is_call(self) -> bool:
        return self.option_type == "call"

    def dte(self, today: date) -> int:
        return (self.expiration - today).days

    def tradable(self, *, max_spread_fraction: float, min_open_interest: int = 0) -> bool:
        """Can we realistically get filled on this contract?

        A quote is the operative liquidity test: a contract with no bid cannot be sold
        and a wide market eats the whole edge. Open interest is only applied when the
        data is actually present, since the chain snapshot does not carry it.
        """
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0:
            return False
        spread = self.spread_fraction
        if spread is None or spread > max_spread_fraction:
            return False
        if self.open_interest is not None and self.open_interest < min_open_interest:
            return False
        return True


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


def is_option_position(position: Any) -> bool:
    """True when an Alpaca position is an option contract rather than shares."""
    asset_class = str(getattr(position, "asset_class", "") or "").lower()
    if "option" in asset_class:
        return True
    return parse_occ_symbol(str(getattr(position, "symbol", "") or "")) is not None


def market_date(now: datetime | None = None) -> date:
    """Today in US/Eastern. Using the local date would skew DTE for a UTC-evening run."""
    moment = now or datetime.now(US_EASTERN)
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(US_EASTERN).date()


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


def _greeks_and_iv(snapshot: Any) -> tuple[dict[str, float | None], float | None]:
    greeks = getattr(snapshot, "greeks", None)
    iv = getattr(snapshot, "implied_volatility", None)
    if isinstance(snapshot, dict):
        greeks = snapshot.get("greeks") or greeks
        iv = snapshot.get("implied_volatility") or snapshot.get("impliedVolatility") or iv

    def pick(name: str) -> float | None:
        if greeks is None:
            return None
        value = getattr(greeks, name, None)
        if value is None and isinstance(greeks, dict):
            value = greeks.get(name)
        return _as_float(value)

    return (
        {
            "delta": pick("delta"),
            "gamma": pick("gamma"),
            "theta": pick("theta"),
            "vega": pick("vega"),
        },
        _as_float(iv),
    )


def candidates_from_snapshots(
    snapshots: dict[str, Any],
    *,
    underlying: str,
) -> list[OptionCandidate]:
    """Turn a chain snapshot (quotes, greeks, IV) into candidates.

    Pure enough to unit test: pass a fake dict of snapshot-like objects, no network.
    """
    ticker = underlying.upper()
    candidates: list[OptionCandidate] = []
    for symbol, snapshot in snapshots.items():
        parsed = parse_occ_symbol(symbol)
        if parsed is None or parsed.underlying != ticker:
            continue
        bid, ask = _quote_bid_ask(snapshot)
        greeks, iv = _greeks_and_iv(snapshot)
        candidates.append(
            OptionCandidate(
                symbol=symbol,
                underlying=parsed.underlying,
                option_type=parsed.option_type,
                strike=parsed.strike,
                expiration=parsed.expiration,
                bid=bid,
                ask=ask,
                implied_volatility=iv,
                **greeks,
            )
        )
    candidates.sort(key=lambda c: (c.expiration, c.option_type, c.strike))
    return candidates


def expiries_in_window(
    candidates: list[OptionCandidate],
    *,
    today: date,
    min_dte: int,
    max_dte: int,
) -> list[date]:
    """Distinct expiries inside the DTE window, nearest first."""
    found = {
        c.expiration for c in candidates if min_dte <= c.dte(today) <= max_dte
    }
    return sorted(found)


def fetch_contracts(
    client: PaperAlpaca,
    underlying: str,
    *,
    option_type: ContractType | None = None,
    expiration_gte: date | None = None,
    expiration_lte: date | None = None,
    limit: int = 100,
) -> list[OptionCandidate]:
    """List active contracts for an underlying (no prices attached, but open interest)."""
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


def fetch_chain_quotes(client: PaperAlpaca, underlying: str) -> dict[str, Any]:
    """Raw chain snapshot (quotes, greeks, IV) keyed by OCC symbol."""
    request = OptionChainRequest(underlying_symbol=underlying.upper())
    return _as_snapshot_map(client.option_data.get_option_chain(request))


def fetch_snapshots_for(
    client: PaperAlpaca,
    symbols: list[str],
) -> list[OptionCandidate]:
    """Quotes and greeks for an explicit list of contracts.

    Held positions can sit outside the DTE window the chain request covers (an
    inherited leg, or one the engine is about to close), and the manager still needs a
    live mark for them.
    """
    wanted = sorted({s.strip().upper() for s in symbols if s.strip()})
    if not wanted:
        return []
    snapshots = _as_snapshot_map(
        client.option_data.get_option_snapshot(
            OptionSnapshotRequest(symbol_or_symbols=wanted)
        )
    )
    candidates: list[OptionCandidate] = []
    by_underlying: dict[str, dict[str, Any]] = {}
    for symbol, snapshot in snapshots.items():
        parsed = parse_occ_symbol(symbol)
        if parsed is None:
            continue
        by_underlying.setdefault(parsed.underlying, {})[symbol] = snapshot
    for underlying, group in by_underlying.items():
        candidates.extend(candidates_from_snapshots(group, underlying=underlying))
    return candidates


def fetch_quoted_chain(
    client: PaperAlpaca,
    underlying: str,
    *,
    today: date | None = None,
    min_dte: int = 1,
    max_dte: int = 9,
) -> list[OptionCandidate]:
    """Contracts in the DTE window, with bid/ask/greeks/IV from the chain snapshot.

    Without quotes the risk layer would reject every ticket for a missing cost
    estimate, so the engine always works from this snapshot rather than metadata.
    """
    as_of = today or market_date()
    candidates = candidates_from_snapshots(
        _as_snapshot_map(
            client.option_data.get_option_chain(
                OptionChainRequest(
                    underlying_symbol=underlying.upper(),
                    expiration_date_gte=as_of + timedelta(days=min_dte),
                    expiration_date_lte=as_of + timedelta(days=max_dte),
                )
            )
        ),
        underlying=underlying,
    )
    if candidates:
        return candidates
    # Empty window (holiday week, UTC/ET date skew): widen once rather than skip the
    # underlying entirely. The DTE filter downstream still enforces the real window.
    wide = OptionChainRequest(
        underlying_symbol=underlying.upper(),
        expiration_date_gte=as_of,
        expiration_date_lte=as_of + timedelta(days=max_dte + 14),
    )
    return candidates_from_snapshots(
        _as_snapshot_map(client.option_data.get_option_chain(wide)),
        underlying=underlying,
    )


def _as_snapshot_map(chain: Any) -> dict[str, Any]:
    if not chain:
        return {}
    if isinstance(chain, dict):
        return chain
    try:
        return dict(chain)
    except (TypeError, ValueError):
        return {}
