# Sources

Material the engine is built on.

## Alpaca

- **alpaca-py** — https://alpaca.markets/sdks/python/ — SDK used for execution.
- **alpaca-mcp-server** — https://github.com/alpacahq/alpaca-mcp-server — research plane.
- **Alpaca CLI** — https://github.com/alpacahq/cli — verification plane; paper by default.
- **CLI docs** — https://docs.alpaca.markets/us/docs/alpacas-cli
- **Paper dashboard** — https://app.alpaca.markets/paper/dashboard/overview
- **Agentic / MCP** — https://alpaca.markets/agentic

## Options mechanics that shaped the design

- **Multi-leg (MLeg) orders** — up to 4 legs; accepted only when every leg is covered
  *within the same ticket*, which is why exits are their own all-to-close order and the
  classic single-ticket roll is not used.
- **Options levels** — level 3 is required for credit spreads and iron condors.
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

The previous design of this project — a defined-risk collar overlay — is archived at
https://github.com/Ander-IbBi/alpaca-collar-overlay.
