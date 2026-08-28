"""The CLI cross-check is a safety net, so its failure modes matter more than its
happy path: a missing binary must never stop the agent from trading."""

from options_agent.alpaca import cli_bridge
from options_agent.alpaca.cli_bridge import CliResult, cross_check_account


def _stub(monkeypatch, *, account: CliResult, positions: CliResult | None = None) -> None:
    monkeypatch.setattr(cli_bridge, "cli_account", lambda: account)
    monkeypatch.setattr(
        cli_bridge,
        "cli_positions",
        lambda: positions or CliResult(available=True, data=[]),
    )


def test_missing_binary_is_not_a_disagreement(monkeypatch) -> None:
    _stub(monkeypatch, account=CliResult(available=False, error="alpaca CLI not installed"))
    result = cross_check_account(account_number="PA1", position_symbols=set())
    assert result.checked is False
    assert result.agrees is True  # nothing was compared, so nothing conflicts


def test_matching_views_agree(monkeypatch) -> None:
    _stub(
        monkeypatch,
        account=CliResult(available=True, data={"account_number": "PA1"}),
        positions=CliResult(available=True, data=[{"symbol": "SPY", "qty": "100"}]),
    )
    result = cross_check_account(account_number="PA1", position_symbols={"SPY"})
    assert result.checked is True
    assert result.agrees is True


def test_a_different_account_is_flagged(monkeypatch) -> None:
    _stub(monkeypatch, account=CliResult(available=True, data={"account_number": "PA_OTHER"}))
    result = cross_check_account(account_number="PA1", position_symbols=set())
    assert not result.agrees
    assert "PA_OTHER" in result.summary()


def test_a_position_the_cli_cannot_see_is_flagged(monkeypatch) -> None:
    _stub(
        monkeypatch,
        account=CliResult(available=True, data={"account_number": "PA1"}),
        positions=CliResult(available=True, data=[]),
    )
    result = cross_check_account(account_number="PA1", position_symbols={"SPY"})
    assert not result.agrees
    assert "SPY" in result.summary()


def test_a_position_only_the_cli_sees_is_flagged(monkeypatch) -> None:
    _stub(
        monkeypatch,
        account=CliResult(available=True, data={"account_number": "PA1"}),
        positions=CliResult(available=True, data=[{"symbol": "QQQ", "qty": "10"}]),
    )
    result = cross_check_account(account_number="PA1", position_symbols=set())
    assert not result.agrees
    assert "QQQ" in result.summary()


def test_run_cli_reports_a_missing_binary(monkeypatch) -> None:
    monkeypatch.setattr(cli_bridge, "find_cli", lambda: None)
    result = cli_bridge.run_cli("account", "get")
    assert result.available is False
    assert result.error is not None
    assert "not installed" in result.error
