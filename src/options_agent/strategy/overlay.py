"""Aggressive collar overlay: long shares, long put, short call, same expiry.

Intuition: a protective put caps the drawdown but its premium is a P&L drag. Selling
a call against the same book finances most of that put. The trade-off is a ceiling
on the rally.

Technical: seed 100 shares of the watchlist name if the book is empty, then pick a
put near delta -0.20 and a call near delta +0.20 in the 21-45 DTE window. One
contract covers 100 shares; never over-hedge, and this playbook sizes to one collar.

Math: long stock at $S$, long put $K_p$, short call $K_c$, net debit $D = P_p - P_c$.
P&L per share is $\\mathrm{clip}(S_T, K_p, K_c) - S - D$. Max loss is
$(S - K_p + D) \\times 100$ per contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from options_agent.alpaca.options import OptionCandidate, parse_occ_symbol
from options_agent.strategy.base import ProposedLeg, ProposedTrade, StrategyContext

CONTRACT_MULTIPLIER = 100
TARGET_PUT_DELTA = -0.20
TARGET_CALL_DELTA = 0.20
MAX_QUOTE_SPREAD = 0.35
MAX_COLLAR_CONTRACTS = 1
US_EASTERN = ZoneInfo("America/New_York")

DELTA_FINANCING = "delta"
ZERO_COST_FINANCING = "zero_cost"
# Alpaca's multi-leg ceiling; the risk layer enforces it too.
MAX_TICKET_LEGS = 4
# A financed call must still leave room to rally before the ceiling bites.
MIN_CALL_OTM_FRACTION = 0.01
# Management thresholds. Defaults are conservative: they fire on real events,
# not on noise, so an unattended week does not churn the book.
DEFAULT_ROLL_DTE = 10
DEFAULT_PUT_PROFIT_MULTIPLE = 2.0
DEFAULT_MAX_ROLL_DEBIT_USD = 1_500.0


class CollarSelection(NamedTuple):
    put: OptionCandidate
    call: OptionCandidate


def select_protective_put(
    candidates: list[OptionCandidate],
    *,
    spot: float,
    today: date,
    moneyness: float = 0.95,
    min_dte: int = 21,
    max_dte: int = 60,
) -> OptionCandidate | None:
    """Closest put to the target strike within the accepted expiry window.

    Kept as a pure helper (the live overlay uses `select_collar`).
    """
    target_strike = spot * moneyness
    viable = [
        c
        for c in candidates
        if c.option_type == "put"
        and c.strike <= spot  # a protective put is bought out of the money
        and min_dte <= c.dte(today) <= max_dte
    ]
    if not viable:
        return None
    # Prefer the strike nearest the target; break ties with the shorter expiry.
    return min(viable, key=lambda c: (abs(c.strike - target_strike), c.dte(today)))


def _quoted_in_window(
    candidate: OptionCandidate,
    *,
    today: date,
    min_dte: int,
    max_dte: int,
) -> bool:
    if candidate.mid_price is None or candidate.delta is None:
        return False
    spread = candidate.spread_fraction
    if spread is None or spread > MAX_QUOTE_SPREAD:
        return False
    return min_dte <= candidate.dte(today) <= max_dte


def pick_financing_call(
    calls: list[OptionCandidate],
    *,
    spot: float,
    target_call_delta: float,
    financing: str,
    put_mid: float,
) -> OptionCandidate | None:
    """Choose the short call: either by delta, or by what pays for the put.

    `zero_cost` removes the standing net debit of a delta-picked collar, which is
    a guaranteed drag whenever the underlying goes nowhere. The trade-off is a
    nearer ceiling, so the strike still has to sit a minimum distance above spot.
    """
    if not calls:
        return None
    if financing != ZERO_COST_FINANCING:
        return min(calls, key=lambda c: abs((c.delta or 0.0) - target_call_delta))

    floor = spot * (1 + MIN_CALL_OTM_FRACTION)
    affordable = [c for c in calls if c.strike >= floor] or calls
    # Closest premium to the put's cost; on ties keep the higher (roomier) strike.
    return min(affordable, key=lambda c: (abs((c.mid_price or 0.0) - put_mid), -c.strike))


def select_collar(
    candidates: list[OptionCandidate],
    *,
    spot: float,
    today: date,
    target_put_delta: float = TARGET_PUT_DELTA,
    target_call_delta: float = TARGET_CALL_DELTA,
    min_dte: int = 21,
    max_dte: int = 45,
    call_financing: str = DELTA_FINANCING,
) -> CollarSelection | None:
    """OTM put near target delta, then a same-expiry OTM call to finance it.

    Pure function: no network. Delta is the live selection rule; moneyness is a
    fallback only inside `select_protective_put`.
    """
    puts = [
        c
        for c in candidates
        if c.option_type == "put"
        and c.strike < spot
        and _quoted_in_window(c, today=today, min_dte=min_dte, max_dte=max_dte)
    ]
    # Closest delta first; if that expiry has no quoted OTM call, try the next put.
    puts.sort(key=lambda c: (abs((c.delta or 0.0) - target_put_delta), c.dte(today)))
    for put in puts:
        calls = [
            c
            for c in candidates
            if c.option_type == "call"
            and c.strike > spot
            and c.strike > put.strike
            and c.expiration == put.expiration
            and _quoted_in_window(c, today=today, min_dte=min_dte, max_dte=max_dte)
        ]
        call = pick_financing_call(
            calls,
            spot=spot,
            target_call_delta=target_call_delta,
            financing=call_financing,
            put_mid=put.mid_price or 0.0,
        )
        if call is None:
            continue
        return CollarSelection(put=put, call=call)
    return None


def money(value: float) -> float:
    """Cents, not float dust — risk caps compare these numbers."""
    return round(value, 2)


def market_date(now: datetime | None = None) -> date:
    """US equity calendar date, not UTC (which rolls over at 20:00 ET)."""
    current = now or datetime.now(US_EASTERN)
    if current.tzinfo is None:
        current = current.replace(tzinfo=US_EASTERN)
    return current.astimezone(US_EASTERN).date()


def contracts_for_shares(shares: float) -> int:
    """One option contract covers 100 shares; never over-hedge."""
    return int(shares // CONTRACT_MULTIPLIER)


def as_limit_tick(net: float) -> float:
    """Round to cents, never to zero: Alpaca rejects a zero-priced limit."""
    ticked = round(net, 2)
    return 0.01 if ticked == 0 else ticked


def net_limit_price(put_mid: float, call_mid: float) -> float:
    """Net debit (positive) or credit (negative), rounded to a valid options tick."""
    return as_limit_tick(put_mid - call_mid)


def collar_cash_and_max_loss(
    *,
    spot: float,
    put: OptionCandidate,
    call: OptionCandidate,
    qty: int,
) -> tuple[float, float]:
    """Net debit (may be negative) and defined max loss of the collared stock."""
    put_mid = put.mid_price
    call_mid = call.mid_price
    if put_mid is None or call_mid is None:
        raise ValueError("collar legs need a mid price")
    net_debit = (put_mid - call_mid) * CONTRACT_MULTIPLIER * qty
    gap = (spot - put.strike) * CONTRACT_MULTIPLIER * qty
    return money(net_debit), money(gap + net_debit)


def _qty(position: Any) -> float:
    return float(getattr(position, "qty", 0) or 0)


def _symbol(position: Any) -> str:
    return str(getattr(position, "symbol", "")).upper()


def is_option_position(position: Any) -> bool:
    """True for option holdings. alpaca-py may expose an enum, not the string 'us_option'."""
    raw = getattr(position, "asset_class", "")
    value = str(getattr(raw, "value", raw) or "").lower()
    if "us_option" in value:
        return True
    return parse_occ_symbol(_symbol(position)) is not None


def long_shares(equity_positions: list[Any], symbol: str) -> float:
    total = 0.0
    for position in equity_positions:
        if _symbol(position) == symbol.upper() and _qty(position) > 0:
            total += _qty(position)
    return total


def covering_option_contracts(option_positions: list[Any], symbol: str) -> tuple[int, int]:
    """Net long puts and short calls on `symbol`, in contracts."""
    ticker = symbol.upper()
    long_puts = 0
    short_calls = 0
    for position in option_positions:
        parsed = parse_occ_symbol(_symbol(position))
        if parsed is None or parsed.underlying != ticker:
            continue
        qty = _qty(position)
        if parsed.option_type == "put" and qty > 0:
            long_puts += int(qty)
        elif parsed.option_type == "call" and qty < 0:
            short_calls += int(-qty)
    return long_puts, short_calls


class OpenOption(NamedTuple):
    """An option we already hold, with what the broker says it is worth."""

    symbol: str
    option_type: str
    strike: float
    expiration: date
    contracts: int  # signed: positive long, negative short
    avg_entry: float | None  # per share, like a quote
    current: float | None

    def dte(self, today: date) -> int:
        return (self.expiration - today).days

    @property
    def profit_multiple(self) -> float | None:
        """Current value over entry cost. 2.0 means the leg doubled."""
        if self.avg_entry is None or self.current is None or self.avg_entry <= 0:
            return None
        return self.current / self.avg_entry


def _price(position: Any, field: str) -> float | None:
    raw = getattr(position, field, None)
    if raw is None:
        return None
    try:
        return abs(float(raw))
    except (TypeError, ValueError):
        return None


def open_options(option_positions: list[Any], symbol: str) -> list[OpenOption]:
    """Parse broker positions into something the management rules can reason about."""
    ticker = symbol.upper()
    parsed_legs: list[OpenOption] = []
    for position in option_positions:
        parsed = parse_occ_symbol(_symbol(position))
        if parsed is None or parsed.underlying != ticker:
            continue
        contracts = int(_qty(position))
        if contracts == 0:
            continue
        parsed_legs.append(
            OpenOption(
                symbol=_symbol(position),
                option_type=parsed.option_type,
                strike=parsed.strike,
                expiration=parsed.expiration,
                contracts=contracts,
                avg_entry=_price(position, "avg_entry_price"),
                current=_price(position, "current_price"),
            )
        )
    return parsed_legs


def overlay_in_place(
    *,
    shares: float,
    option_positions: list[Any],
    symbol: str,
) -> bool:
    """True when a put, a short call, or both already cover the playbook size.

    A half-filled overlay must not open a second collar: another short call on the
    same 100 shares would be naked.
    """
    needed = min(contracts_for_shares(shares), MAX_COLLAR_CONTRACTS)
    if needed < 1:
        return False
    long_puts, short_calls = covering_option_contracts(option_positions, symbol)
    return long_puts >= needed or short_calls >= needed


def already_collared(
    *,
    shares: float,
    option_positions: list[Any],
    symbol: str,
) -> bool:
    """True when both collar legs already cover the playbook size."""
    needed = min(contracts_for_shares(shares), MAX_COLLAR_CONTRACTS)
    if needed < 1:
        return False
    long_puts, short_calls = covering_option_contracts(option_positions, symbol)
    return long_puts >= needed and short_calls >= needed


def free_covering_shares(
    shares: float,
    option_positions: list[Any],
    symbol: str,
    *,
    closing_symbols: Iterable[str] = (),
) -> float:
    """Shares not already pledged to a short call.

    `closing_symbols` are contracts the same ticket buys back. Their shares are
    free again the moment the ticket fills, so a roll is not mistaken for a
    second, uncovered short call.
    """
    closing = {s.strip().upper() for s in closing_symbols}
    still_open = [p for p in option_positions if _symbol(p) not in closing]
    _, short_calls = covering_option_contracts(still_open, symbol)
    return max(shares - short_calls * CONTRACT_MULTIPLIER, 0.0)


def order_touches_watchlist(order: Any, underlyings: list[str]) -> bool:
    """True if an open ticket is for the overlay's stock or its options."""
    names = {u.upper() for u in underlyings}
    symbols: list[str] = [str(getattr(order, "symbol", "") or "").upper()]
    for leg in getattr(order, "legs", None) or []:
        symbols.append(str(getattr(leg, "symbol", "") or "").upper())
    for symbol in symbols:
        if not symbol:
            continue
        if symbol in names:
            return True
        parsed = parse_occ_symbol(symbol)
        if parsed is not None and parsed.underlying in names:
            return True
    return False


def _target_symbol(context: StrategyContext) -> str:
    return context.underlyings[0] if context.underlyings else "SPY"


class AggressiveCollarOverlay:
    """Hackathon playbook: seed the stock, collar it, then actively manage the collar."""

    name = "aggressive-collar-overlay"

    def __init__(
        self,
        chain_provider=None,
        *,
        target_put_delta: float = TARGET_PUT_DELTA,
        target_call_delta: float = TARGET_CALL_DELTA,
        min_dte: int = 21,
        max_dte: int = 45,
        seed_shares: int = 100,
        max_equity_notional_usd: float = 80_000.0,
        max_order_notional_usd: float = 2_500.0,
        call_financing: str = ZERO_COST_FINANCING,
        roll_dte: int = DEFAULT_ROLL_DTE,
        put_profit_multiple: float = DEFAULT_PUT_PROFIT_MULTIPLE,
        max_roll_debit_usd: float = DEFAULT_MAX_ROLL_DEBIT_USD,
    ) -> None:
        # chain_provider(underlying) -> list[OptionCandidate]; injected so tests
        # and the live agent share the same code path.
        self.chain_provider = chain_provider
        self.target_put_delta = target_put_delta
        self.target_call_delta = target_call_delta
        self.min_dte = min_dte
        self.max_dte = max_dte
        self.seed_shares = seed_shares
        self.max_equity_notional_usd = max_equity_notional_usd
        self.max_order_notional_usd = max_order_notional_usd
        self.call_financing = call_financing
        self.roll_dte = roll_dte
        self.put_profit_multiple = put_profit_multiple
        self.max_roll_debit_usd = max_roll_debit_usd

    def propose(self, context: StrategyContext) -> ProposedTrade:
        symbol = _target_symbol(context)
        shares = long_shares(context.equity_positions, symbol)
        if shares < CONTRACT_MULTIPLIER:
            return self._propose_seed(context, symbol, shares)

        if overlay_in_place(
            shares=shares,
            option_positions=context.option_positions,
            symbol=symbol,
        ):
            return self._manage_overlay(context, symbol, shares)

        if self.chain_provider is None:
            return ProposedTrade(
                skip=True,
                rationale="No option chain provider wired; running in observation mode.",
            )

        spot = context.spot_prices.get(symbol)
        if not spot:
            return ProposedTrade(skip=True, rationale=f"No spot price available for {symbol}.")

        qty = min(contracts_for_shares(shares), MAX_COLLAR_CONTRACTS)
        if qty < 1:
            return ProposedTrade(
                skip=True,
                rationale=f"{symbol}: {shares:g} shares is below one contract (100).",
            )

        try:
            remaining = list(self.chain_provider(symbol))
        except Exception as exc:  # noqa: BLE001 — empty book is better than a crashed loop
            return ProposedTrade(skip=True, rationale=f"Option chain fetch failed: {exc}")
        chosen: CollarSelection | None = None
        net_debit = 0.0
        max_loss = 0.0
        while remaining:
            pick = select_collar(
                remaining,
                spot=spot,
                today=context.today,
                target_put_delta=self.target_put_delta,
                target_call_delta=self.target_call_delta,
                min_dte=self.min_dte,
                max_dte=self.max_dte,
                call_financing=self.call_financing,
            )
            if pick is None:
                break
            net_debit, max_loss = collar_cash_and_max_loss(
                spot=spot, put=pick.put, call=pick.call, qty=qty
            )
            if max_loss <= self.max_order_notional_usd:
                chosen = pick
                break
            # Floor too far from spot: drop this put and try a closer strike.
            remaining = [c for c in remaining if c.symbol != pick.put.symbol]

        if chosen is None:
            return ProposedTrade(
                skip=True,
                rationale=(
                    f"No quoted collar for {symbol} inside the "
                    f"{self.min_dte}-{self.max_dte} DTE window "
                    f"with max loss <= ${self.max_order_notional_usd:.0f}."
                ),
            )

        put, call = chosen.put, chosen.call
        put_mid, call_mid = put.mid_price, call.mid_price
        if put_mid is None or call_mid is None:
            return ProposedTrade(skip=True, rationale="Collar legs are missing a mid price.")

        free_shares = free_covering_shares(shares, context.option_positions, symbol)
        return ProposedTrade(
            qty=qty,
            kind="option",
            covering_shares=free_shares,
            limit_price=net_limit_price(put_mid, call_mid),
            legs=[
                ProposedLeg(symbol=put.symbol, side="buy", position_intent="buy_to_open"),
                ProposedLeg(symbol=call.symbol, side="sell", position_intent="sell_to_open"),
            ],
            rationale=(
                f"Collar {shares:g} {symbol} with {qty}x "
                f"{put.strike:g}p/{call.strike:g}c expiring {put.expiration} "
                f"({put.dte(context.today)} DTE); net "
                f"{'debit' if net_debit >= 0 else 'credit'} ${abs(net_debit):.0f}."
            ),
            estimated_cost_usd=money(net_debit),
            max_loss_usd=money(max_loss),
        )

    def _manage_overlay(
        self,
        context: StrategyContext,
        symbol: str,
        shares: float,
    ) -> ProposedTrade:
        """Decide what to do with a collar that is already on.

        Checked in order of urgency: assignment risk first, then expiry, then
        harvesting a hedge that has already paid off. Every branch reports what it
        saw, so a 'hold' cycle still shows the reasoning in the journal.
        """
        legs = open_options(context.option_positions, symbol)
        short_calls = [leg for leg in legs if leg.option_type == "call" and leg.contracts < 0]
        long_puts = [leg for leg in legs if leg.option_type == "put" and leg.contracts > 0]
        spot = context.spot_prices.get(symbol)
        checks: list[str] = []

        breached = [leg for leg in short_calls if spot and spot >= leg.strike]
        if breached:
            call = min(breached, key=lambda leg: leg.strike)
            checks.append(f"short {call.strike:g}c is in the money (spot {spot:.2f})")
            rolled = self._roll_short_call(context, symbol, shares, call, spot or 0.0)
            if rolled is not None:
                return rolled
            checks.append("no acceptable replacement call")
        elif short_calls and spot:
            nearest = min(short_calls, key=lambda leg: leg.strike)
            checks.append(f"short {nearest.strike:g}c safe (spot {spot:.2f})")

        expiring = [leg for leg in legs if leg.dte(context.today) <= self.roll_dte]
        if expiring:
            soonest = min(expiring, key=lambda leg: leg.expiration)
            checks.append(f"{soonest.symbol} at {soonest.dte(context.today)} DTE")
            rolled = self._roll_expiring_collar(context, symbol, shares, legs, spot or 0.0)
            if rolled is not None:
                return rolled
            checks.append("no acceptable replacement expiry")
        elif legs:
            furthest = min(leg.dte(context.today) for leg in legs)
            checks.append(f"nearest expiry {furthest} DTE > {self.roll_dte}")

        for put in long_puts:
            multiple = put.profit_multiple
            if multiple is None:
                continue
            if multiple >= self.put_profit_multiple:
                checks.append(f"put {put.strike:g}p worth {multiple:.1f}x its cost")
                harvested = self._harvest_put(context, symbol, put, spot or 0.0)
                if harvested is not None:
                    return harvested
                checks.append("no cheaper put to re-arm the floor")
            else:
                checks.append(
                    f"put {put.strike:g}p at {multiple:.1f}x "
                    f"(<{self.put_profit_multiple:g}x)"
                )

        puts_held, calls_held = covering_option_contracts(context.option_positions, symbol)
        detail = "; ".join(checks) if checks else "no management signal"
        return ProposedTrade(
            skip=True,
            rationale=(
                f"{symbol} overlay already on "
                f"({shares:g} shares, {puts_held} long put(s), {calls_held} short call(s)). "
                f"Hold: {detail}."
            ),
        )

    def _chain(self, symbol: str) -> list[OptionCandidate] | None:
        if self.chain_provider is None:
            return None
        try:
            return list(self.chain_provider(symbol))
        except Exception:  # noqa: BLE001 — a hold beats a crashed loop
            return None

    def _roll_short_call(
        self,
        context: StrategyContext,
        symbol: str,
        shares: float,
        call: OpenOption,
        spot: float,
    ) -> ProposedTrade | None:
        """Buy back an in-the-money short call and sell a higher one.

        Without this the collar stops earning the moment spot clears the strike,
        and the shares are exposed to early assignment. The replacement must sit
        above both spot and the old strike, and the net debit is capped.
        """
        chain = self._chain(symbol)
        if not chain or call.current is None:
            return None
        qty = min(abs(call.contracts), MAX_COLLAR_CONTRACTS)
        buyback = call.current
        candidates = [
            c
            for c in chain
            if c.option_type == "call"
            and c.strike > max(spot, call.strike)
            and c.expiration >= call.expiration
            and _quoted_in_window(
                c, today=context.today, min_dte=self.min_dte, max_dte=self.max_dte
            )
        ]
        if not candidates:
            return None
        replacement = min(
            candidates,
            key=lambda c: abs((c.delta or 0.0) - self.target_call_delta),
        )
        new_mid = replacement.mid_price
        if new_mid is None:
            return None

        net_debit = money((buyback - new_mid) * CONTRACT_MULTIPLIER * qty)
        if net_debit > min(self.max_roll_debit_usd, self.max_order_notional_usd):
            return None

        covering = free_covering_shares(
            shares, context.option_positions, symbol, closing_symbols={call.symbol}
        )
        return ProposedTrade(
            qty=qty,
            kind="option",
            covering_shares=covering,
            limit_price=net_limit_price(buyback, new_mid),
            legs=[
                ProposedLeg(symbol=call.symbol, side="buy", position_intent="buy_to_close"),
                ProposedLeg(
                    symbol=replacement.symbol, side="sell", position_intent="sell_to_open"
                ),
            ],
            rationale=(
                f"Roll the short call up: {call.strike:g}c is in the money at "
                f"{spot:.2f}, so buy it back and sell {replacement.strike:g}c "
                f"expiring {replacement.expiration}. Restores upside and removes "
                f"assignment risk for a net "
                f"{'debit' if net_debit >= 0 else 'credit'} ${abs(net_debit):.0f}."
            ),
            estimated_cost_usd=net_debit,
            # Both legs stay covered by the shares, so the ticket risks only its debit.
            max_loss_usd=money(max(net_debit, 0.0)),
        )

    def _roll_expiring_collar(
        self,
        context: StrategyContext,
        symbol: str,
        shares: float,
        legs: list[OpenOption],
        spot: float,
    ) -> ProposedTrade | None:
        """Close a collar that is about to expire and open the next one."""
        chain = self._chain(symbol)
        if not chain or not spot:
            return None
        qty = min(contracts_for_shares(shares), MAX_COLLAR_CONTRACTS)
        if qty < 1:
            return None
        pick = select_collar(
            chain,
            spot=spot,
            today=context.today,
            target_put_delta=self.target_put_delta,
            target_call_delta=self.target_call_delta,
            min_dte=self.min_dte,
            max_dte=self.max_dte,
            call_financing=self.call_financing,
        )
        if pick is None:
            return None
        put_mid, call_mid = pick.put.mid_price, pick.call.mid_price
        if put_mid is None or call_mid is None:
            return None

        closing: list[ProposedLeg] = []
        closing_cash = 0.0
        for leg in legs:
            if leg.current is None:
                return None
            if leg.contracts > 0:
                closing.append(
                    ProposedLeg(symbol=leg.symbol, side="sell", position_intent="sell_to_close")
                )
                closing_cash -= leg.current
            else:
                closing.append(
                    ProposedLeg(symbol=leg.symbol, side="buy", position_intent="buy_to_close")
                )
                closing_cash += leg.current
        if len(closing) + 2 > MAX_TICKET_LEGS:
            return None

        net_debit = money((closing_cash + put_mid - call_mid) * CONTRACT_MULTIPLIER * qty)
        if net_debit > min(self.max_roll_debit_usd, self.max_order_notional_usd):
            return None

        covering = free_covering_shares(
            shares,
            context.option_positions,
            symbol,
            closing_symbols={leg.symbol for leg in legs if leg.contracts < 0},
        )
        soonest = min(leg.dte(context.today) for leg in legs)
        return ProposedTrade(
            qty=qty,
            kind="option",
            covering_shares=covering,
            limit_price=as_limit_tick(closing_cash + put_mid - call_mid),
            legs=[
                *closing,
                ProposedLeg(symbol=pick.put.symbol, side="buy", position_intent="buy_to_open"),
                ProposedLeg(symbol=pick.call.symbol, side="sell", position_intent="sell_to_open"),
            ],
            rationale=(
                f"Roll the collar out: nearest leg is {soonest} DTE (<= {self.roll_dte}). "
                f"Close it and open {pick.put.strike:g}p/{pick.call.strike:g}c "
                f"expiring {pick.put.expiration} for a net "
                f"{'debit' if net_debit >= 0 else 'credit'} ${abs(net_debit):.0f}."
            ),
            estimated_cost_usd=net_debit,
            max_loss_usd=money(max(net_debit, 0.0)),
        )

    def _harvest_put(
        self,
        context: StrategyContext,
        symbol: str,
        put: OpenOption,
        spot: float,
    ) -> ProposedTrade | None:
        """Sell a put that has already paid off and re-arm a cheaper floor.

        A hedge that doubled is unrealised profit sitting in a wasting asset. Taking
        it and re-striking lower banks the gain while keeping the book protected.
        """
        chain = self._chain(symbol)
        if not chain or put.current is None or not spot:
            return None
        qty = min(put.contracts, MAX_COLLAR_CONTRACTS)
        if qty < 1:
            return None
        candidates = [
            c
            for c in chain
            if c.option_type == "put"
            and c.strike < min(spot, put.strike)
            and _quoted_in_window(
                c, today=context.today, min_dte=self.min_dte, max_dte=self.max_dte
            )
        ]
        if not candidates:
            return None
        replacement = min(
            candidates,
            key=lambda c: abs((c.delta or 0.0) - self.target_put_delta),
        )
        new_mid = replacement.mid_price
        if new_mid is None:
            return None

        # Selling the rich put and buying a cheaper one should bring cash in.
        net = money((new_mid - put.current) * CONTRACT_MULTIPLIER * qty)
        if net >= 0:
            return None
        # What matters is not how far the floor drops, but how much the book can
        # still lose from here once the harvested cash is counted.
        gap = (spot - replacement.strike) * CONTRACT_MULTIPLIER * qty
        residual_risk = money(gap + net)
        if residual_risk > self.max_order_notional_usd:
            return None
        floor_given_up = money((put.strike - replacement.strike) * CONTRACT_MULTIPLIER * qty)

        return ProposedTrade(
            qty=qty,
            kind="option",
            covering_shares=free_covering_shares(
                long_shares(context.equity_positions, symbol),
                context.option_positions,
                symbol,
            ),
            limit_price=as_limit_tick(new_mid - put.current),
            legs=[
                ProposedLeg(symbol=put.symbol, side="sell", position_intent="sell_to_close"),
                ProposedLeg(
                    symbol=replacement.symbol, side="buy", position_intent="buy_to_open"
                ),
            ],
            rationale=(
                f"Harvest the hedge: {put.strike:g}p is worth "
                f"{put.profit_multiple:.1f}x its cost. Sell it and re-arm at "
                f"{replacement.strike:g}p, banking ${abs(net):.0f} and keeping a floor "
                f"${floor_given_up:.0f} lower."
            ),
            estimated_cost_usd=net,
            max_loss_usd=residual_risk,
        )

    def _propose_seed(
        self,
        context: StrategyContext,
        symbol: str,
        shares_held: float,
    ) -> ProposedTrade:
        needed = max(self.seed_shares - int(shares_held), 0)
        if needed < 1:
            return ProposedTrade(
                skip=True,
                rationale=f"{symbol}: {shares_held:g} shares is below one contract (100).",
            )
        spot = context.spot_prices.get(symbol)
        if not spot:
            return ProposedTrade(
                skip=True,
                rationale=f"No spot price available to seed {needed} {symbol} shares.",
            )
        cost = money(needed * spot)
        if cost > self.max_equity_notional_usd:
            return ProposedTrade(
                skip=True,
                rationale=(
                    f"Seed {needed} {symbol} at ${spot:.2f} costs ${cost:.0f}, "
                    f"above the ${self.max_equity_notional_usd:.0f} equity cap."
                ),
            )
        if context.cash < cost:
            return ProposedTrade(
                skip=True,
                rationale=(
                    f"Need ${cost:.0f} cash to seed {needed} {symbol}; have ${context.cash:.0f}."
                ),
            )
        return ProposedTrade(
            qty=needed,
            kind="equity",
            legs=[ProposedLeg(symbol=symbol, side="buy")],
            rationale=(
                f"No overlay yet: buy {needed} {symbol} shares to seed a 1-contract collar."
            ),
            estimated_cost_usd=cost,
            max_loss_usd=cost,
        )
