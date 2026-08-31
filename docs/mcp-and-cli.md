# Alpaca MCP and CLI in this project

The agent uses the Alpaca **Trading API**, the **MCP server**, and the **CLI**.
Each one has a job the other two cannot do. None of them is decoration, and only one of
them can move money.

| Plane | Surface | Job | Fails how? |
| --- | --- | --- | --- |
| Execution | `alpaca-py` | Places every order, `paper=True` | Hard: no order, journalled |
| Verification | Alpaca CLI | Confirms the book before and after each ticket | Open: reports `checked: false` |
| Research | Alpaca MCP server | Market context and a second quote source | Open: cycle continues without it |

## Trading API — the execution path

Every order is built and sent with `alpaca-py`, always `paper=True`
([client.py](../src/vrp_engine/alpaca/client.py),
[orders.py](../src/vrp_engine/alpaca/orders.py)). Nothing else places trades, and nothing
reaches `submit_order` without passing
[review_proposal](../src/vrp_engine/risk/limits.py) first.

One broker constraint shaped the whole design: **a multi-leg order is accepted only if
every leg is covered inside that same ticket**. That rules out the classic single-ticket
roll of a short leg, so exits are always their own all-`*_to_close` ticket and entries are
separate. `orders.py` raises locally on a mixed ticket rather than let the broker discover
it.

## CLI — an independent second opinion, twice per ticket

The agent must never act on a stale picture of the book. A doubled position or an
accidentally uncovered short both start the same way: "the SDK said the position was
there."

So the CLI — a different binary, a different language, a different auth path — reads the
*same* account and the cycle compares two things that would actually change a decision:
the account identity, and which symbols are open.

**Before a ticket** (`cross_check_account`): if the two views disagree, the cycle refuses
to trade and says so in the journal.

**After a submission** (`reconcile_after_submit`): the book is read again, including a
fresh SDK position list so the comparison is not against the pre-trade snapshot.

- Expected legs missing from the CLI's list are reported as **pending**, not as a fault —
  a limit order resting at the net mid may simply not have filled yet.
- Expected legs that one client has already picked up and the other has not are the
  same in-flight fill, not a split book.
- A symbol that *neither* side expected from this ticket is the case that matters:
  `consistent=False` **freezes new entries** until they agree again.

Exits stay allowed while frozen. A later cycle that sees the two views match thaws
the freeze even if it has nothing to send; requiring another ticket to lift it would
trap the book on a hold. A safety check that blocked closing would trap the book it
fired over, which is worse than the mismatch it was reacting to.

The CLI also answers the market-hours question independently (`cli_market_open`), so a
wrong `is_open` from one source cannot on its own decide whether the agent trades.

If the binary is not installed, every check reports `checked: false` and the cycle
continues. It is a safety net, not a dependency — CI and a fresh clone both work without
it.

Code: [cli_bridge.py](../src/vrp_engine/alpaca/cli_bridge.py), wired in
[loop.py](../src/vrp_engine/agent/loop.py).

### Install (Windows, no Go toolchain needed)

```powershell
gh release download --repo alpacahq/cli --pattern "cli_*_windows_amd64.zip" --dir "$env:USERPROFILE\.alpaca-cli"
Expand-Archive "$env:USERPROFILE\.alpaca-cli\cli_*_windows_amd64.zip" -DestinationPath "$env:USERPROFILE\.alpaca-cli"
& "$env:USERPROFILE\.alpaca-cli\alpaca.exe" profile login --api-key --paper
```

macOS/Linux: `go install github.com/alpacahq/cli/cmd/alpaca@latest`, or the matching
archive from the same release page.

The bridge finds the binary on `PATH` first, then falls back to `~/.alpaca-cli`,
`~/go/bin` and `/usr/local/bin`. The CLI also reads `ALPACA_API_KEY` /
`ALPACA_SECRET_KEY`, the same variables the agent uses, so a logged-in profile is
optional for automation.

### See both views at once

```bash
uv run python scripts/broker_report.py
```

Prints the SDK's account and positions, the CLI's account and positions, the current
signals, the ranked scanner and the portfolio stress table — the dry-run proof before any
capital moves.

## MCP — the research plane, in code

Most projects use the [Alpaca MCP server](https://github.com/alpacahq/alpaca-mcp-server)
as an IDE convenience: a human asks a chat client about the account. That is useful for
development and it is configured here too (below), but it is not part of the product.

The agent itself is an MCP **client**.
[mcp_bridge.py](../src/vrp_engine/alpaca/mcp_bridge.py) launches the server over stdio,
initialises a session, and calls read-only tools. Two concrete uses:

1. **The daily regime briefing.** `get_market_movers`, `get_most_active_stocks` and
   `get_news` across the universe, summarised once per day by the LLM analyst and carried
   into every cycle that day. Prices are already in the bars; *why* the tape looks like
   this is not, and that is the one thing a language model is genuinely better at than the
   code around it.
2. **A second source for option quotes.** Before the engine sizes anything,
   `get_option_snapshot` re-quotes the exact legs of the proposed structure and
   `cross_check_option_quotes` compares mids against the SDK's. A stale or crossed quote
   is the most likely way a fake edge enters the model, and the cheapest way to catch it
   is to ask a second source the same question. A divergence above 15% kills the ticket.

### Why MCP cannot place an order

Order submission must pass `review_proposal` every single time. An MCP tool call is a
model deciding to place an order directly, which is precisely the code path this project
refuses to have.

That is enforced structurally rather than by instruction. `READ_ONLY_TOOLS` is a
`frozenset` in `mcp_bridge.py`, and a tool not on it is never invoked — the check happens
after the call is constructed and before the transport sees it, so no prompt, model output
or server-side tool listing can turn the research plane into an execution plane. The
server is also spawned with `ALPACA_PAPER_TRADE=True` and paper credentials, with no
configuration that could point it elsewhere.

Everything is bounded: `MCP_ENABLED` turns the plane off, `MCP_TIMEOUT_SECONDS` caps the
whole batch, one failing tool does not sink the others, and every failure mode — missing
package, missing keys, timeout, crash — returns an unavailable `McpResearch` with a note
that lands in the journal. The engine's edge is computed from the SDK's own data; MCP
enriches the narrative and cross-checks the quotes.

```bash
uv sync --extra mcp    # installs the mcp client library
```

Config: `MCP_COMMAND` (default `uvx`) and `MCP_ARGS` (default `alpaca-mcp-server`).

### MCP for development

Copy [mcp.example.json](../mcp.example.json) to `.cursor/mcp.json` (gitignored) and paste
**paper** keys. That gives an IDE chat client the same read-only window on the account —
handy while designing structures, and entirely separate from the agent's own client.

## The agent's own tool surface

[tools.py](../src/vrp_engine/agent/tools.py) is what the LLM analyst can call, and it is
read-only by construction. The model receives an already-built, already-risk-approved
ticket and may raise a soft veto from a fixed list of five reasons. It cannot change a
strike, resize a position, approve something risk rejected, or invent a new reason: an
unrecognised veto is discarded and the trade proceeds.
