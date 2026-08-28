"""Position management: what the agent does once the collar is already on.

These branches rarely fire during a short contest week, so they are pinned down
here rather than left to the market to demonstrate.
"""

from datetime import date, timedelta

from options_agent.alpaca.options import OptionCandidate
from options_agent.risk.limits import RiskLimits, review_proposal
from options_agent.strategy.base import StrategyContext
from options_agent.strategy.overlay import (
    AggressiveCollarOverlay,
    free_covering_shares,
    open_options,
    pick_financing_call,
    select_collar,
)

TODAY = date(2026, 9, 1)
NEAR = TODAY + timedelta(days=30)
FAR = TODAY + timedelta(days=40)
# Far enough out to still be inside the 21-45 DTE window once NEAR is expiring.
NEXT = TODAY + timedelta(days=55)

LIMITS = RiskLimits(
    max_contracts_per_order=5,
    max_order_notional_usd=2_500,
    max_equity_notional_usd=80_000,
)


class FakePosition:
    def __init__(
        self,
        symbol: str,
        qty: float,
        price: float = 770.0,
        *,
        option: bool = False,
        avg_entry: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.qty = qty
        self.current_price = price
        self.avg_entry_price = avg_entry
        self.asset_class = "us_option" if option else "us_equity"


def _occ(kind: str, strike: float, expiry: date = NEAR) -> str:
    flag = "P" if kind == "put" else "C"
    return f"SPY{expiry:%y%m%d}{flag}{int(strike * 1000):08d}"


def _opt(
    kind: str,
    strike: float,
    *,
    delta: float,
    bid: float,
    ask: float,
    expiry: date = NEAR,
) -> OptionCandidate:
    return OptionCandidate(
        symbol=_occ(kind, strike, expiry),
        underlying="SPY",
        option_type=kind,
        strike=strike,
        expiration=expiry,
        bid=bid,
        ask=ask,
        delta=delta,
    )


def _context(
    *,
    spot: float,
    options: list[FakePosition],
    today: date = TODAY,
    shares: float = 100,
) -> StrategyContext:
    return StrategyContext(
        today=today,
        market_open=True,
        equity=100_000,
        cash=20_000,
        underlyings=["SPY"],
        equity_positions=[FakePosition("SPY", shares, spot)],
        option_positions=options,
        spot_prices={"SPY": spot},
    )


# --- covering shares during a roll -------------------------------------------------


def test_closing_a_short_call_frees_its_shares() -> None:
    options = [FakePosition(_occ("call", 790), -1, option=True)]
    assert free_covering_shares(100, options, "SPY") == 0
    freed = free_covering_shares(
        100, options, "SPY", closing_symbols={_occ("call", 790)}
    )
    assert freed == 100


# --- zero-cost financing -----------------------------------------------------------


def test_zero_cost_picks_the_call_that_pays_for_the_put() -> None:
    calls = [
        _opt("call", 780, delta=0.34, bid=5.0, ask=5.2),
        _opt("call", 785, delta=0.26, bid=3.1, ask=3.3),
        _opt("call", 795, delta=0.14, bid=1.0, ask=1.1),
    ]
    chosen = pick_financing_call(
        calls, spot=770.0, target_call_delta=0.20, financing="zero_cost", put_mid=3.25
    )
    assert chosen is not None
    assert chosen.strike == 785


def test_zero_cost_refuses_to_cap_the_rally_too_close() -> None:
    calls = [
        _opt("call", 772, delta=0.45, bid=3.2, ask=3.3),  # matches the put, but 0.3% OTM
        _opt("call", 790, delta=0.20, bid=1.8, ask=1.9),
    ]
    chosen = pick_financing_call(
        calls, spot=770.0, target_call_delta=0.20, financing="zero_cost", put_mid=3.25
    )
    assert chosen is not None
    assert chosen.strike == 790


def test_delta_financing_is_unchanged() -> None:
    chain = [
        _opt("put", 750, delta=-0.20, bid=3.2, ask=3.3),
        _opt("call", 785, delta=0.26, bid=3.1, ask=3.3),
        _opt("call", 790, delta=0.20, bid=1.8, ask=1.9),
    ]
    picked = select_collar(chain, spot=770.0, today=TODAY, call_financing="delta")
    assert picked is not None
    assert picked.call.strike == 790


# --- rolling an in-the-money short call --------------------------------------------


def _rally_chain() -> list[OptionCandidate]:
    return [
        _opt("call", 800, delta=0.34, bid=6.0, ask=6.2, expiry=FAR),
        _opt("call", 810, delta=0.20, bid=3.0, ask=3.2, expiry=FAR),
        _opt("put", 770, delta=-0.20, bid=4.0, ask=4.2, expiry=FAR),
    ]


def test_short_call_in_the_money_is_rolled_up() -> None:
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: _rally_chain())
    context = _context(
        spot=795.0,
        options=[
            FakePosition(_occ("put", 750), 1, 1.2, option=True, avg_entry=3.25),
            FakePosition(_occ("call", 789), -1, 8.0, option=True, avg_entry=1.85),
        ],
    )
    proposal = strategy.propose(context)

    assert not proposal.skip
    assert [leg.position_intent for leg in proposal.legs] == ["buy_to_close", "sell_to_open"]
    assert proposal.legs[0].symbol == _occ("call", 789)
    assert proposal.legs[1].symbol.endswith("C00810000")
    assert "Roll the short call up" in proposal.rationale
    # The ticket only risks the debit it pays, and the shares still cover it.
    assert proposal.covering_shares == 100
    # Buy back at 8.00, sell the 810 at mid 3.10: a 4.90 net debit per contract.
    assert proposal.estimated_cost_usd == 490.0
    assert review_proposal(proposal, LIMITS).allowed


def test_a_roll_that_costs_too_much_is_not_taken() -> None:
    strategy = AggressiveCollarOverlay(
        chain_provider=lambda _: _rally_chain(),
        max_roll_debit_usd=100.0,
    )
    context = _context(
        spot=795.0,
        options=[
            FakePosition(_occ("put", 750), 1, 1.2, option=True, avg_entry=3.25),
            FakePosition(_occ("call", 789), -1, 8.0, option=True, avg_entry=1.85),
        ],
    )
    proposal = strategy.propose(context)
    assert proposal.skip
    assert "no acceptable replacement call" in proposal.rationale


def test_replacement_call_must_sit_above_spot_and_the_old_strike() -> None:
    # Only a lower strike is quoted, so there is nothing worth rolling into.
    chain = [_opt("call", 780, delta=0.40, bid=6.0, ask=6.2, expiry=FAR)]
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: chain)
    context = _context(
        spot=795.0,
        options=[
            FakePosition(_occ("call", 789), -1, 8.0, option=True, avg_entry=1.85),
        ],
    )
    assert strategy.propose(context).skip


# --- rolling the collar before expiry ----------------------------------------------


def test_collar_near_expiry_is_rolled_out() -> None:
    chain = [
        _opt("put", 750, delta=-0.20, bid=3.2, ask=3.3, expiry=NEXT),
        _opt("call", 790, delta=0.20, bid=3.1, ask=3.3, expiry=NEXT),
    ]
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: chain)
    soon = NEAR - timedelta(days=5)  # the open collar is 5 DTE
    context = _context(
        spot=770.0,
        today=soon,
        options=[
            FakePosition(_occ("put", 750), 1, 0.5, option=True, avg_entry=3.25),
            FakePosition(_occ("call", 790), -1, 0.4, option=True, avg_entry=1.85),
        ],
    )
    proposal = strategy.propose(context)

    assert not proposal.skip
    intents = [leg.position_intent for leg in proposal.legs]
    assert intents == ["sell_to_close", "buy_to_close", "buy_to_open", "sell_to_open"]
    assert "Roll the collar out" in proposal.rationale
    assert proposal.covering_shares == 100
    assert review_proposal(proposal, LIMITS).allowed


# --- harvesting a put that already paid off ----------------------------------------


def test_a_doubled_put_is_harvested_and_re_armed() -> None:
    chain = [
        _opt("put", 725, delta=-0.20, bid=2.0, ask=2.2),
        _opt("put", 740, delta=-0.40, bid=5.0, ask=5.2),
    ]
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: chain)
    # SPY fell to 745, so the 750 put bought for 3.25 is now worth 8.00.
    context = _context(
        spot=745.0,
        options=[FakePosition(_occ("put", 750), 1, 8.0, option=True, avg_entry=3.25)],
    )
    proposal = strategy.propose(context)

    assert not proposal.skip
    assert [leg.position_intent for leg in proposal.legs] == ["sell_to_close", "buy_to_open"]
    assert proposal.legs[1].symbol.endswith("P00725000")
    assert proposal.estimated_cost_usd == -590.0  # cash banked
    assert "Harvest the hedge" in proposal.rationale
    assert review_proposal(proposal, LIMITS).allowed


def test_harvest_is_refused_when_the_new_floor_leaves_too_much_risk() -> None:
    # Re-arming this far below spot would leave more downside than the order cap.
    chain = [_opt("put", 690, delta=-0.20, bid=2.0, ask=2.2)]
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: chain)
    context = _context(
        spot=745.0,
        options=[FakePosition(_occ("put", 750), 1, 8.0, option=True, avg_entry=3.25)],
    )
    proposal = strategy.propose(context)
    assert proposal.skip
    assert "no cheaper put to re-arm the floor" in proposal.rationale


def test_a_put_that_has_not_paid_off_is_left_alone() -> None:
    chain = [_opt("put", 700, delta=-0.20, bid=2.0, ask=2.2)]
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: chain)
    context = _context(
        spot=768.0,
        options=[
            FakePosition(_occ("put", 750), 1, 3.6, option=True, avg_entry=3.25),
            FakePosition(_occ("call", 790), -1, 1.5, option=True, avg_entry=1.85),
        ],
    )
    proposal = strategy.propose(context)
    assert proposal.skip
    assert "1.1x" in proposal.rationale


# --- the quiet case ----------------------------------------------------------------


def test_hold_explains_every_check_it_ran() -> None:
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: [])
    context = _context(
        spot=770.0,
        options=[
            FakePosition(_occ("put", 750), 1, 3.0, option=True, avg_entry=3.25),
            FakePosition(_occ("call", 790), -1, 1.6, option=True, avg_entry=1.85),
        ],
    )
    proposal = strategy.propose(context)

    assert proposal.skip
    assert "overlay already on" in proposal.rationale
    assert "safe" in proposal.rationale  # the short call was checked
    assert "DTE" in proposal.rationale  # so was the expiry
    assert "0.9x" in proposal.rationale  # and so was the hedge


def test_open_options_reads_entry_and_current_prices() -> None:
    legs = open_options(
        [
            FakePosition(_occ("call", 790), -1, 8.0, option=True, avg_entry=1.85),
            FakePosition("SPY", 100, 770.0),
        ],
        "SPY",
    )
    assert len(legs) == 1
    leg = legs[0]
    assert leg.contracts == -1
    assert leg.option_type == "call"
    assert leg.profit_multiple is not None
    assert round(leg.profit_multiple, 2) == 4.32
