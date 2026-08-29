# Documentation

The root [README](../README.md) is the entry point. This folder is the detail behind it.

- [**Strategy**](strategy.md) — realised and implied vol, the variance risk premium, the
  expected-value integral, the probability wedge, Kelly sizing, the payoff and stress
  engine
- [**Architecture**](architecture.md) — the three broker planes, the cycle step by step,
  the module map, and where to change what
- [**MCP and CLI**](mcp-and-cli.md) — what each Alpaca surface does, and why only one of
  them can place an order
- [Sources](sources.md) — Alpaca docs, options mechanics, and the theory the engine uses

The previous strategy — a defined-risk collar overlay — is a separate project:
[alpaca-collar-overlay](https://github.com/Ander-IbBi/alpaca-collar-overlay).
