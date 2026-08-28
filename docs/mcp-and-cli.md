# Alpaca MCP and CLI in this project

> Nota en español al final. This page is in English because judges read it.

The hackathon requires the Alpaca **Trading API** plus the **MCP server and/or the
CLI**. This project uses all three, each where it actually belongs.

## Trading API — the execution path

Every order is built and sent with `alpaca-py`, always `paper=True`
([client.py](../src/options_agent/alpaca/client.py),
[orders.py](../src/options_agent/alpaca/orders.py)). Nothing else places trades.

## CLI — an independent second opinion

The agent must never act on a stale picture of the book: a double-hedged collar or an
accidentally uncovered short call both start with "the SDK said the position was
there". So before any ticket goes out, the cycle reads the **same account through a
different client and a different auth path** — the official Go CLI — and compares:

- the account number, and
- which symbols are actually open.

If the two disagree, the cycle refuses to trade and says so in the journal. If the CLI
is not installed, the check reports `checked: false` and the agent carries on: it is a
safety net, not a dependency.

Code: [cli_bridge.py](../src/options_agent/alpaca/cli_bridge.py), wired in
[loop.py](../src/options_agent/agent/loop.py) as `broker_cross_check`.

### Install (Windows, no Go toolchain needed)

```powershell
gh release download --repo alpacahq/cli --pattern "cli_*_windows_amd64.zip" --dir "$env:USERPROFILE\.alpaca-cli"
Expand-Archive "$env:USERPROFILE\.alpaca-cli\cli_*_windows_amd64.zip" -DestinationPath "$env:USERPROFILE\.alpaca-cli"
& "$env:USERPROFILE\.alpaca-cli\alpaca.exe" profile login --api-key --paper
```

macOS/Linux: `go install github.com/alpacahq/cli/cmd/alpaca@latest`, or the matching
archive from the same release page.

The CLI also reads `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, the same variables the agent
uses, so a logged-in profile is optional for automation.

### See all three views at once

```bash
uv run python scripts/broker_report.py
```

Prints the SDK's account and positions, the CLI's account and positions, and a dry-run
agent cycle.

## MCP — the LLM's window on the account

The [official Alpaca MCP server](https://github.com/alpacahq/alpaca-mcp-server) exposes
the account to an LLM client (Cursor, Claude Desktop, VS Code). It is used for
**research and supervision**, not execution: reading option chains and greeks while
designing the collar, checking snapshots, and inspecting fills after a cycle.

Copy [mcp.example.json](../mcp.example.json) to `.cursor/mcp.json` (gitignored) and
paste **paper** keys.

Why execution does not go through MCP: order submission has to pass
[review_proposal](../src/options_agent/risk/limits.py) every single time. An MCP tool
call is an LLM deciding to place an order directly, which is exactly the code path this
project refuses to have. The model explains and may soft-veto; it never routes an order.

The tool surface the agent exposes to its own LLM is read-only by construction:
[tools.py](../src/options_agent/agent/tools.py).

---

**Resumen (es):** la API de trading ejecuta, la CLI hace de verificación independiente
antes de cada orden (y si no está instalada, el ciclo sigue), y el MCP es la ventana del
LLM a la cuenta para investigar y supervisar, nunca para ejecutar.
