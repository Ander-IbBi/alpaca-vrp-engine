from datetime import date, timedelta
from types import SimpleNamespace

from options_agent.agent.llm import RuleBasedAdvisor
from options_agent.agent.loop import OverlayAgent
from options_agent.alpaca.cli_bridge import BrokerCrossCheck
from options_agent.alpaca.options import OptionCandidate
from options_agent.journal import Journal
from options_agent.strategy.overlay import AggressiveCollarOverlay


def _no_cli(**_kwargs) -> BrokerCrossCheck:
    """Unit tests never shell out; the CLI bridge has its own test module."""
    return BrokerCrossCheck(checked=False, notes=["stubbed"])

TODAY = date(2026, 9, 1)
EXPIRY = TODAY + timedelta(days=30)


class FakePosition:
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


def _opt(kind: str, strike: float, *, delta: float, bid: float, ask: float) -> OptionCandidate:
    flag = "P" if kind == "put" else "C"
    return OptionCandidate(
        symbol=f"SPY{EXPIRY:%y%m%d}{flag}{int(strike * 1000):08d}",
        underlying="SPY",
        option_type=kind,
        strike=strike,
        expiration=EXPIRY,
        bid=bid,
        ask=ask,
        delta=delta,
    )


class _Settings:
    dry_run = True
    seed_shares = 100
    max_equity_notional_usd = 80_000.0
    max_order_notional_usd = 2_500.0
    max_contracts_per_order = 5
    max_daily_loss_usd = 1_500.0
    min_equity_usd = 80_000.0
    openai_api_key = ""

    def underlying_list(self) -> list[str]:
        return ["SPY"]


class FakeClient:
    def __init__(
        self,
        *,
        positions=None,
        open_orders=None,
        market_open=True,
        clock_error: Exception | None = None,
        order_list_error: Exception | None = None,
        last_price: float | None = 770.0,
    ) -> None:
        self.settings = _Settings()
        self._positions = list(positions or [])
        self._open_orders = list(open_orders or [])
        self._market_open = market_open
        self._clock_error = clock_error
        self._order_list_error = order_list_error
        self._last_price = last_price
        self.submitted: list = []
        self.trading = SimpleNamespace(submit_order=self._submit)

    def _submit(self, request):
        self.submitted.append(request)
        return SimpleNamespace(id="ord-1", status="accepted")

    def clock(self):
        if self._clock_error is not None:
            raise self._clock_error
        return SimpleNamespace(is_open=self._market_open)

    def account(self):
        return SimpleNamespace(equity=100_000, cash=25_000, last_equity=100_000)

    def positions(self):
        return list(self._positions)

    def open_orders(self):
        if self._order_list_error is not None:
            raise self._order_list_error
        return list(self._open_orders)

    def last_price(self, symbol: str) -> float:
        if self._last_price is None:
            raise RuntimeError("quote down")
        return self._last_price


def _collar_chain() -> list[OptionCandidate]:
    return [
        _opt("put", 750, delta=-0.20, bid=3.2, ask=3.3),
        _opt("call", 790, delta=0.20, bid=1.8, ask=1.9),
    ]


def _agent(
    client: FakeClient,
    tmp_path,
    chain: list[OptionCandidate] | None = None,
) -> OverlayAgent:
    return OverlayAgent(
        client,  # type: ignore[arg-type]
        strategy=AggressiveCollarOverlay(chain_provider=lambda _: chain or []),
        journal=Journal(tmp_path / "agent.jsonl"),
        advisor=RuleBasedAdvisor(),
        cross_checker=_no_cli,
    )


def test_cycle_holds_when_the_collar_is_already_on(tmp_path) -> None:
    client = FakeClient(
        positions=[
            FakePosition("SPY", 100, 770.0),
            FakePosition(_occ("put", 750), 1, option=True),
            FakePosition(_occ("call", 790), -1, option=True),
        ]
    )
    cycle = _agent(client, tmp_path).run_once(execute=False)
    assert cycle.proposal is not None
    assert cycle.proposal.skip
    assert "overlay already on" in (cycle.proposal.rationale or "")
    assert client.submitted == []


def test_open_watchlist_orders_block_a_new_ticket(tmp_path) -> None:
    client = FakeClient(
        positions=[FakePosition("SPY", 100, 770.0)],
        open_orders=[SimpleNamespace(symbol="SPY", legs=None)],
    )
    cycle = _agent(client, tmp_path, chain=_collar_chain()).run_once(execute=True)
    assert cycle.proposal is None
    assert any("Open overlay orders" in note for note in cycle.notes)
    assert client.submitted == []


def test_unavailable_order_list_fails_closed(tmp_path) -> None:
    client = FakeClient(
        positions=[FakePosition("SPY", 100, 770.0)],
        order_list_error=RuntimeError("orders endpoint down"),
    )
    cycle = _agent(client, tmp_path).run_once(execute=True)
    assert any("waiting" in note.lower() for note in cycle.notes)
    assert client.submitted == []


def test_execute_does_not_send_when_the_market_is_closed(tmp_path) -> None:
    client = FakeClient(
        positions=[FakePosition("SPY", 100, 770.0)],
        market_open=False,
    )
    cycle = _agent(client, tmp_path, chain=_collar_chain()).run_once(execute=True)
    assert cycle.execution is not None
    assert cycle.execution["dry_run"] is True
    assert client.submitted == []
    assert any("Market closed" in note for note in cycle.notes)


def test_execute_sends_when_the_market_is_open(tmp_path) -> None:
    client = FakeClient(positions=[FakePosition("SPY", 100, 770.0)])
    cycle = _agent(client, tmp_path, chain=_collar_chain()).run_once(execute=True)
    assert cycle.execution is not None
    assert cycle.execution["submitted"] is True
    assert len(client.submitted) == 1


def test_a_broker_disagreement_stops_the_cycle(tmp_path) -> None:
    def disagrees(**_kwargs) -> BrokerCrossCheck:
        return BrokerCrossCheck(
            checked=True,
            agrees=False,
            notes=["CLI sees account PA_OTHER, SDK sees PA1"],
        )

    client = FakeClient(positions=[FakePosition("SPY", 100, 770.0)])
    agent = OverlayAgent(
        client,  # type: ignore[arg-type]
        strategy=AggressiveCollarOverlay(chain_provider=lambda _: _collar_chain()),
        journal=Journal(tmp_path / "agent.jsonl"),
        advisor=RuleBasedAdvisor(),
        cross_checker=disagrees,
    )
    cycle = agent.run_once(execute=True)

    assert cycle.proposal is None
    assert client.submitted == []
    assert any("stale book" in note for note in cycle.notes)


def test_api_fault_does_not_kill_the_loop(tmp_path) -> None:
    client = FakeClient(clock_error=RuntimeError("clock timeout"))
    cycle = _agent(client, tmp_path).run_once(execute=False)
    assert cycle.proposal is None
    assert any("Cycle failed" in note for note in cycle.notes)


def test_journal_failure_is_noted_not_raised(tmp_path) -> None:
    class BoomJournal:
        def append(self, kind: str, payload: dict) -> dict:
            raise OSError("disk full")

    client = FakeClient(
        positions=[
            FakePosition("SPY", 100, 770.0),
            FakePosition(_occ("put", 750), 1, option=True),
        ]
    )
    agent = OverlayAgent(
        client,  # type: ignore[arg-type]
        strategy=AggressiveCollarOverlay(chain_provider=lambda _: []),
        journal=BoomJournal(),  # type: ignore[arg-type]
        advisor=RuleBasedAdvisor(),
        cross_checker=_no_cli,
    )
    cycle = agent.run_once(execute=False)
    assert any("Journal write failed" in note for note in cycle.notes)
