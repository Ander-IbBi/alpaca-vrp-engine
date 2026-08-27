from datetime import date, timedelta

from options_agent.alpaca.options import OptionCandidate
from options_agent.strategy.base import StrategyContext
from options_agent.strategy.overlay import (
    AggressiveCollarOverlay,
    collar_cash_and_max_loss,
    contracts_for_shares,
    net_limit_price,
    select_collar,
    select_protective_put,
)

TODAY = date(2026, 9, 1)
EXPIRY = TODAY + timedelta(days=30)  # 2026-10-01


class FakePosition:
    """Stands in for an alpaca-py Position without needing the network."""

    def __init__(
        self,
        symbol: str,
        qty: float,
        price: float = 770.0,
        *,
        option: bool = False,
    ) -> None:
        self.symbol = symbol
        self.qty = qty
        self.current_price = price
        self.asset_class = "us_option" if option else "us_equity"


def _occ(kind: str, strike: float) -> str:
    flag = "P" if kind == "put" else "C"
    return f"SPY{EXPIRY:%y%m%d}{flag}{int(strike * 1000):08d}"


def _opt(
    kind: str,
    strike: float,
    *,
    days: int = 30,
    delta: float,
    bid: float,
    ask: float,
) -> OptionCandidate:
    expiration = TODAY + timedelta(days=days)
    flag = "P" if kind == "put" else "C"
    return OptionCandidate(
        symbol=f"SPY{expiration:%y%m%d}{flag}{int(strike * 1000):08d}",
        underlying="SPY",
        option_type=kind,
        strike=strike,
        expiration=expiration,
        bid=bid,
        ask=ask,
        delta=delta,
    )


def test_picks_strike_closest_to_target_moneyness() -> None:
    candidates = [
        _opt("put", 600, delta=-0.5, bid=4.0, ask=4.4),
        _opt("put", 570, delta=-0.2, bid=4.0, ask=4.4),
        _opt("put", 540, delta=-0.1, bid=4.0, ask=4.4),
    ]
    chosen = select_protective_put(candidates, spot=600.0, today=TODAY, moneyness=0.95)
    assert chosen is not None
    assert chosen.strike == 570


def test_ignores_expiries_outside_the_window() -> None:
    candidates = [
        _opt("put", 570, days=5, delta=-0.2, bid=4.0, ask=4.4),
        _opt("put", 570, days=200, delta=-0.2, bid=4.0, ask=4.4),
    ]
    assert select_protective_put(candidates, spot=600.0, today=TODAY) is None


def test_ignores_calls_and_in_the_money_strikes() -> None:
    itm = _opt("put", 700, delta=-0.8, bid=4.0, ask=4.4)
    assert select_protective_put([itm], spot=600.0, today=TODAY) is None


def test_contracts_never_over_hedge() -> None:
    assert contracts_for_shares(250) == 2
    assert contracts_for_shares(99) == 0


def test_select_collar_picks_target_deltas() -> None:
    chain = [
        _opt("put", 740, delta=-0.13, bid=2.1, ask=2.2),
        _opt("put", 750, delta=-0.20, bid=3.2, ask=3.3),
        _opt("put", 760, delta=-0.28, bid=4.4, ask=4.5),
        _opt("call", 785, delta=0.26, bid=2.9, ask=3.0),
        _opt("call", 790, delta=0.20, bid=1.8, ask=1.9),
        _opt("call", 800, delta=0.08, bid=0.5, ask=0.6),
        _opt("put", 750, days=5, delta=-0.20, bid=3.2, ask=3.3),
        _opt("call", 790, days=5, delta=0.20, bid=1.8, ask=1.9),
        _opt("put", 751, delta=-0.21, bid=1.0, ask=3.0),  # 67% spread
    ]
    chosen = select_collar(chain, spot=770.0, today=TODAY)
    assert chosen is not None
    assert chosen.put.strike == 750
    assert chosen.call.strike == 790
    assert chosen.put.expiration == chosen.call.expiration


def test_select_collar_needs_quotes_and_delta() -> None:
    blind = OptionCandidate(
        symbol=_occ("put", 750),
        underlying="SPY",
        option_type="put",
        strike=750,
        expiration=EXPIRY,
    )
    assert select_collar([blind], spot=770.0, today=TODAY) is None


def test_collar_max_loss_is_the_gap_plus_net_debit() -> None:
    put = _opt("put", 750, delta=-0.20, bid=3.2, ask=3.2)
    call = _opt("call", 790, delta=0.20, bid=1.8, ask=1.8)
    net, max_loss = collar_cash_and_max_loss(spot=770.0, put=put, call=call, qty=1)
    assert net == 140.0
    assert max_loss == 2000.0 + 140.0


def test_net_limit_price_rounds_to_cents() -> None:
    assert net_limit_price(3.25, 1.81) == 1.44
    assert net_limit_price(2.0, 2.0) == 0.01


def test_overlay_seeds_spy_when_the_book_is_empty() -> None:
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: [])
    context = StrategyContext(
        today=TODAY,
        market_open=True,
        equity=100_000,
        cash=100_000,
        underlyings=["SPY"],
        spot_prices={"SPY": 770.0},
    )
    proposal = strategy.propose(context)
    assert not proposal.skip
    assert proposal.kind == "equity"
    assert proposal.qty == 100
    assert proposal.legs[0].symbol == "SPY"
    assert proposal.estimated_cost_usd == 77_000.0


def test_overlay_skips_seed_when_equity_cap_would_break() -> None:
    strategy = AggressiveCollarOverlay(
        chain_provider=lambda _: [],
        max_equity_notional_usd=80_000,
    )
    context = StrategyContext(
        today=TODAY,
        market_open=True,
        equity=100_000,
        cash=100_000,
        underlyings=["SPY"],
        spot_prices={"SPY": 900.0},
    )
    proposal = strategy.propose(context)
    assert proposal.skip
    assert "equity cap" in proposal.rationale


def test_overlay_proposes_a_collar_for_a_share_position() -> None:
    chain = [
        _opt("put", 750, delta=-0.20, bid=3.2, ask=3.3),
        _opt("call", 790, delta=0.20, bid=1.8, ask=1.9),
    ]
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: chain)
    context = StrategyContext(
        today=TODAY,
        market_open=True,
        equity=100_000,
        cash=23_000,
        underlyings=["SPY"],
        equity_positions=[FakePosition("SPY", 100, 770.0)],
        spot_prices={"SPY": 770.0},
    )
    proposal = strategy.propose(context)

    assert not proposal.skip
    assert proposal.kind == "option"
    assert proposal.qty == 1
    assert len(proposal.legs) == 2
    assert proposal.legs[0].side == "buy"
    assert proposal.legs[1].side == "sell"
    assert proposal.covering_shares == 100
    assert proposal.limit_price == 1.40  # mid 3.25 - 1.85
    assert proposal.estimated_cost_usd == 140.0
    assert proposal.max_loss_usd == 2000.0 + 140.0


def test_overlay_caps_at_one_collar_even_with_more_shares() -> None:
    chain = [
        _opt("put", 750, delta=-0.20, bid=3.2, ask=3.3),
        _opt("call", 790, delta=0.20, bid=1.8, ask=1.9),
    ]
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: chain)
    context = StrategyContext(
        today=TODAY,
        market_open=True,
        equity=100_000,
        cash=40_000,
        underlyings=["SPY"],
        equity_positions=[FakePosition("SPY", 300, 770.0)],
        spot_prices={"SPY": 770.0},
    )
    proposal = strategy.propose(context)
    assert proposal.qty == 1


def test_overlay_skips_when_already_collared() -> None:
    strategy = AggressiveCollarOverlay(chain_provider=lambda _: [])
    context = StrategyContext(
        today=TODAY,
        market_open=True,
        equity=100_000,
        cash=20_000,
        underlyings=["SPY"],
        equity_positions=[FakePosition("SPY", 100, 770.0)],
        option_positions=[
            FakePosition(_occ("put", 750), 1, option=True),
            FakePosition(_occ("call", 790), -1, option=True),
        ],
        spot_prices={"SPY": 770.0},
    )
    proposal = strategy.propose(context)
    assert proposal.skip
    assert "already collared" in proposal.rationale
