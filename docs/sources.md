# Sources

Material and links for the project.

## Competition

- **Alpaca AI Trading Agents Hackathon** (lablab.ai, 28 Aug – 4 Sep 2026) —
  https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon — event and track.
- **Live dashboard** —
  https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live — milestones and UTC times.
- **Submission guidelines** — https://lablab.ai/delivering-your-hackathon-solution —
  video, PDF, public repo, demo URL.
- **Getting started lablab** — https://lablab.ai/getting-started-guide — teams ≤6, Discord.

## Alpaca

- **Agentic / MCP** — https://alpaca.markets/agentic — MCP as the LLM ↔ Trading API interface.
- **alpaca-mcp-server** (official, v2 FastMCP) — https://github.com/alpacahq/alpaca-mcp-server —
  account, order, options and data tools. V2 breaks V1 names.
- **Alpaca CLI** — https://github.com/alpacahq/cli — paper by default; `alpaca data option chain`.
- **CLI docs** — https://docs.alpaca.markets/us/docs/alpacas-cli
- **Paper dashboard** — https://app.alpaca.markets/paper/dashboard/overview
- **alpaca-py** — https://alpaca.markets/sdks/python/ — SDK used by the product.
- **Webinar “Build Your Own AI-Powered Hedge Fund”** (27 Aug 2026) — https://luma.com/qoym39ry

## Hedging theory

- Protective put and collar: defined-risk hedge on a long book.
  The product uses a **collar** (put ~delta −0.20 financed by a call ~delta +0.20).
- Deep hedging (Bühler et al., 2019) — learned hedging with transaction costs.
  Theoretical context; the in-depth study lives in the `Vault/deep-hedging` vault.
