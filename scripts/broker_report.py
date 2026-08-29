"""Pre-flight report: two independent broker views, plus everything the engine sees.

Run this before letting the agent trade. It is the dry-run proof that the plumbing,
the signals and the risk engine all work against the real paper account:

    uv run python scripts/broker_report.py

Sections, in the order a reader needs them:

  1. `alpaca-py` — the account the agent trades through
  2. the Alpaca CLI — a separate binary and auth path reading the same account
  3. signals — realised vol, implied vol, the variance risk premium, per underlying
  4. the ranked scanner — every candidate structure the engine evaluated
  5. portfolio stress — worst case and the sigma ladder on the book as it stands
  6. the proposal — what the next cycle would do, without sending anything

Nothing here can place an order: the cycle runs with `execute=False`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vrp_engine.agent.loop import AgentCycle, VrpAgent  # noqa: E402
from vrp_engine.alpaca.cli_bridge import (  # noqa: E402
    cli_account,
    cli_market_open,
    cli_positions,
    find_cli,
)
from vrp_engine.alpaca.client import PaperAlpaca  # noqa: E402
from vrp_engine.config import (  # noqa: E402
    LiveTradingForbiddenError,
    MissingCredentialsError,
    Settings,
    assert_paper_only,
    load_settings,
)


def _rule(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 62 - len(title)))


def _fmt(value: Any, spec: str = ".2f", dash: str = "-") -> str:
    """Format a number that may legitimately be missing."""
    if value is None:
        return dash
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return str(value)


def _sdk_section(client: PaperAlpaca) -> tuple[Any, list[Any]]:
    account = client.account()
    positions = client.positions()

    _rule("alpaca-py (SDK)")
    print(
        f"account={account.account_number}  equity={_fmt(account.equity)}  "
        f"cash={_fmt(account.cash)}  options_bp={_fmt(client.options_buying_power())}"
    )
    clock = client.clock()
    print(
        f"market_open={getattr(clock, 'is_open', None)}  "
        f"next_open={getattr(clock, 'next_open', None)}"
    )
    for position in positions:
        print(
            f"  {position.symbol:<24} qty={_fmt(position.qty, '.0f'):>6} "
            f"value={_fmt(getattr(position, 'market_value', None)):>12} "
            f"pl={_fmt(getattr(position, 'unrealized_pl', None)):>10}"
        )
    if not positions:
        print("  (flat)")
    return account, positions


def _cli_section() -> None:
    _rule("Alpaca CLI (independent client)")
    binary = find_cli()
    if binary is None:
        print("  CLI not installed; see docs/mcp-and-cli.md. The agent runs without it.")
        return

    print(f"  binary: {binary}")
    result = cli_account()
    if result.error:
        print(f"  error: {result.error}")
    elif isinstance(result.data, dict):
        print(
            f"  account={result.data.get('account_number')} "
            f"equity={result.data.get('equity')} "
            f"options_level={result.data.get('options_approved_level')}"
        )
    print(f"  market_open={cli_market_open()}")

    listed = cli_positions()
    if isinstance(listed.data, list):
        for item in listed.data:
            print(f"  {str(item.get('symbol', '')):<24} qty={item.get('qty')}")
        if not listed.data:
            print("  (flat)")


def _signals_section(cycle: AgentCycle) -> None:
    _rule("Signals (variance risk premium per underlying)")
    if not cycle.signals:
        print("  no signals: the bars request returned nothing usable")
        return

    header = (
        f"  {'symbol':<7}{'spot':>9}{'RV':>8}{'IV':>8}{'VRP_z':>8}"
        f"{'slope':>8}{'trend':>7}{'beta':>7}  stance"
    )
    print(header)
    for symbol, digest in sorted(cycle.signals.items()):
        stance = str(digest.get("stance") or "")
        if digest.get("event_blackout"):
            stance += " (event blackout)"
        print(
            f"  {symbol:<7}"
            f"{_fmt(digest.get('spot'), '.2f'):>9}"
            f"{_fmt(digest.get('realized_vol'), '.1%'):>8}"
            f"{_fmt(digest.get('implied_vol'), '.1%'):>8}"
            f"{_fmt(digest.get('vrp_z'), '+.2f'):>8}"
            f"{_fmt(digest.get('term_slope'), '+.1%'):>8}"
            f"{str(digest.get('trend') or '-'):>7}"
            f"{_fmt(digest.get('beta'), '.2f'):>7}  {stance}"
        )


def _scanner_section(scan: dict[str, Any] | None) -> None:
    _rule("Ranked scanner (best expected value per dollar-day of risk)")
    if not scan:
        print("  the scanner produced nothing this run")
        return

    print(
        f"  {scan.get('n_candidates', 0)} candidate(s) evaluated across "
        f"{len(scan.get('considered') or [])} underlying(s), "
        f"{scan.get('n_accepted', 0)} cleared every gate"
    )
    rows = scan.get("top") or []
    if not rows:
        print("  nothing to show")
    else:
        print(
            f"  {'sym':<6}{'structure':<20}{'dte':>4}{'credit':>9}{'maxloss':>9}"
            f"{'p_mdl':>7}{'p_imp':>7}{'wedge':>8}{'edge':>8}{'score':>8}  verdict"
        )
        for row in rows:
            verdict = "accepted" if row.get("accepted") else "; ".join(row.get("rejects") or [])
            print(
                f"  {str(row.get('underlying', '')):<6}"
                f"{str(row.get('structure', '')):<20}"
                f"{_fmt(row.get('dte'), '.0f'):>4}"
                f"{_fmt(row.get('credit_usd'), '.0f'):>9}"
                f"{_fmt(row.get('max_loss_usd'), '.0f'):>9}"
                f"{_fmt(row.get('p_win_model'), '.0%'):>7}"
                f"{_fmt(row.get('p_win_implied'), '.0%'):>7}"
                f"{_fmt(row.get('wedge'), '+.3f'):>8}"
                f"{_fmt(row.get('edge'), '+.3f'):>8}"
                f"{_fmt(row.get('score'), '+.4f'):>8}  {verdict}"
            )

    skipped = scan.get("skipped") or {}
    if skipped:
        print("\n  stood down:")
        for symbol, reason in sorted(skipped.items()):
            print(f"    {symbol:<7} {reason}")


def _stress_section(cycle: AgentCycle, settings: Settings) -> None:
    _rule("Portfolio risk (the book as it stands, priced to expiry)")
    portfolio = cycle.portfolio
    if not portfolio:
        print("  no portfolio digest this cycle")
        return

    equity = float(portfolio.get("equity") or 0.0) or 1.0
    budgets = [
        (
            "aggregate worst case",
            portfolio.get("worst_case_loss_usd"),
            settings.risk_budget_pct,
        ),
        (
            "worst stress scenario",
            portfolio.get("worst_stress_loss_usd"),
            settings.max_stress_loss_pct,
        ),
    ]
    for label, used, cap in budgets:
        used_usd = float(used or 0.0)
        print(
            f"  {label:<24}{used_usd:>12,.0f} USD  "
            f"{used_usd / equity:>7.1%} of a {cap:.0%} budget"
        )

    delta_usd = float(portfolio.get("beta_weighted_delta_usd") or 0.0)
    print(
        f"  {'beta-weighted delta':<24}{delta_usd:>12,.0f} USD  "
        f"{delta_usd / equity:>+7.1%} against +/-{settings.max_net_delta_pct:.0%}"
    )
    print(
        f"  {'net vega / theta':<24}"
        f"{_fmt(portfolio.get('net_vega'), '+,.0f'):>12} / "
        f"{_fmt(portfolio.get('net_theta'), '+,.0f')} per day"
    )

    stress = portfolio.get("stress") or {}
    if stress:
        print("\n  one-week shock ladder (P&L at expiry, negative is a loss):")
        for label in ("-2sigma", "-1sigma", "+1sigma", "+2sigma"):
            if label in stress:
                print(f"    {label:<9}{float(stress[label]):>+12,.0f} USD")

    by_underlying = portfolio.get("risk_by_underlying") or {}
    if by_underlying:
        print("\n  risk by underlying (worst case, vs a "
              f"{settings.max_underlying_loss_pct:.0%} cap each):")
        for symbol, used in sorted(by_underlying.items(), key=lambda kv: -float(kv[1])):
            print(f"    {symbol:<7}{float(used):>12,.0f} USD  {float(used) / equity:>7.1%}")

    by_bucket = portfolio.get("risk_by_bucket") or {}
    if by_bucket:
        print(f"\n  risk by bucket (vs a {settings.max_bucket_loss_pct:.0%} cap each):")
        for bucket, used in sorted(by_bucket.items(), key=lambda kv: -float(kv[1])):
            print(f"    {bucket:<7}{float(used):>12,.0f} USD  {float(used) / equity:>7.1%}")


def _proposal_section(cycle: AgentCycle) -> None:
    _rule("What the next cycle would do (nothing was sent)")
    guard = cycle.account_guard
    if guard is not None:
        print(f"  account guard: {guard.summary()}")

    proposal = cycle.proposal
    if proposal is None:
        print("  no proposal: an earlier step ended the cycle")
    elif proposal.skip or not proposal.legs:
        print(f"  stand down: {proposal.rationale}")
    else:
        print(f"  action={proposal.action}  qty={proposal.qty}  limit={_fmt(proposal.limit_price)}")
        for leg in proposal.legs:
            print(f"    {leg.side:<5}{leg.symbol:<24}{leg.position_intent or ''}")
        print(f"  max loss: {_fmt(proposal.max_loss_usd, ',.0f')} USD")
        print(f"  rationale: {proposal.rationale}")

    if cycle.risk is not None:
        verdict = "allowed" if cycle.risk.allowed else "BLOCKED"
        print(f"  risk layer: {verdict} — {cycle.risk.summary()}")
    if cycle.quote_cross_check is not None:
        print(f"  quote cross-check: {cycle.quote_cross_check.summary()}")
    if cycle.broker_cross_check is not None:
        print(f"  broker cross-check: {cycle.broker_cross_check.summary()}")

    if cycle.notes:
        print("\n  notes:")
        for note in cycle.notes:
            print(f"    - {note}")


def main() -> int:
    try:
        settings = assert_paper_only(load_settings())
        client = PaperAlpaca(settings)
    except (LiveTradingForbiddenError, MissingCredentialsError) as exc:
        print(f"ERROR: {exc}")
        return 1

    _sdk_section(client)
    _cli_section()

    agent = VrpAgent(client)
    # The ranking runs even out of hours, so this report is useful on a weekend.
    scan_cycle, _ = agent.dry_scan()
    _signals_section(scan_cycle)
    _scanner_section(scan_cycle.scan)

    # Then one real cycle, execute=False: the same code path Monday will run, no tickets.
    cycle = agent.run_once(execute=False)
    _stress_section(cycle, settings)
    _proposal_section(cycle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
