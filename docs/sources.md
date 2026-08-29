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

## Alpaca options mechanics that shaped the design

- **Multi-leg (MLeg) orders** — up to 4 legs; accepted only when every leg is covered
  *within the same ticket*, which is why exits are their own all-to-close order and the
  classic single-ticket roll is not used.
- **Options levels** — level 3 is required for credit spreads and iron condors; the paper
  account reports `options_approved_level: 3`.
- **Universal spread margin rule** — the broker values a spread as a piecewise-linear
  payoff, the same model `risk/portfolio.py` uses, so the engine's worst case and the
  broker's collateral requirement agree.

## Strategy theory

- **Variance risk premium** — implied volatility exceeds subsequent realised volatility on
  average, as compensation for gap risk. The engine measures it per underlying rather than
  assuming it, and is willing to trade the negative case.
- **Realised volatility estimators** — close-to-close standard deviation, and Parkinson
  (1980), the high–low range estimator that is far less noisy per observation.
- **Kelly (1956) criterion** — optimal fractional staking on a known edge; used at a 0.35
  haircut because the edge here is modelled, not known.
- Deep hedging (Bühler et al., 2019) — learned hedging with transaction costs.
  Theoretical context only; the in-depth study lives in the `Vault/deep-hedging` vault.
- The previous design of this project — a defined-risk collar overlay — is archived at
  https://github.com/Ander-IbBi/alpaca-collar-overlay.
