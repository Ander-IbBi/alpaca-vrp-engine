from datetime import date, timedelta

from options_agent.alpaca.options import OptionCandidate
from options_agent.strategy.base import StrategyContext
from options_agent.strategy.overlay import (
    ProtectivePutOverlay,
    contracts_for_shares,
    select_protective_put,
)

TODAY = date(2026, 9, 1)


class FakePosition:
    """Stands in for an alpaca-py Position without needing the network."""

    def __init__(self, symbol: str, qty: float, price: float) -> None:
        self.symbol = symbol
        self.qty = qty
        self.current_price = price
        self.asset_class = "us_equity"


def _put(strike: float, days: int, bid: float = 4.0, ask: float = 4.4) -> OptionCandidate:
    return OptionCandidate(
        symbol=f"SPY{days}P{int(strike)}",
        underlying="SPY",
        option_type="put",
        strike=strike,
        expiration=TODAY + timedelta(days=days),
        bid=bid,
        ask=ask,
    )


def test_picks_strike_closest_to_target_moneyness() -> None:
    candidates = [_put(600, 30), _put(570, 30), _put(540, 30)]
    chosen = select_protective_put(candidates, spot=600.0, today=TODAY, moneyness=0.95)
    # 95% of 600 is 570.
    assert chosen is not None
    assert chosen.strike == 570


def test_ignores_expiries_outside_the_window() -> None:
    candidates = [_put(570, 5), _put(570, 200)]
    assert select_protective_put(candidates, spot=600.0, today=TODAY) is None


def test_ignores_calls_and_in_the_money_strikes() -> None:
    itm = _put(700, 30)
    assert select_protective_put([itm], spot=600.0, today=TODAY) is None


def test_contracts_never_over_hedge() -> None:
    assert contracts_for_shares(250) == 2
    assert contracts_for_shares(99) == 0


def test_overlay_skips_without_an_equity_book() -> None:
    strategy = ProtectivePutOverlay(chain_provider=lambda _: [])
    context = StrategyContext(today=TODAY, market_open=True, equity=100_000, cash=100_000)
    proposal = strategy.propose(context)
    assert proposal.skip
    assert "No equity book" in proposal.rationale


def test_overlay_proposes_a_long_put_for_a_share_position() -> None:
    strategy = ProtectivePutOverlay(chain_provider=lambda _: [_put(570, 30), _put(500, 30)])
    context = StrategyContext(
        today=TODAY,
        market_open=True,
        equity=100_000,
        cash=40_000,
        equity_positions=[FakePosition("SPY", 300, 600.0)],
        spot_prices={"SPY": 600.0},
    )
    proposal = strategy.propose(context)

    assert not proposal.skip
    assert proposal.qty == 3  # 300 shares -> 3 contracts
    assert len(proposal.legs) == 1
    assert proposal.legs[0].side == "buy"
    # Premium mid 4.2 x 100 x 3 contracts.
    assert proposal.estimated_cost_usd == 4.2 * 100 * 3
    assert proposal.max_loss_usd == proposal.estimated_cost_usd
