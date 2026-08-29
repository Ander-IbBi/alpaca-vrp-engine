# Documentation

Everything is in English. The root [README](../README.md) is the entry point for judges;
this folder is the detail behind it.

## The product
- [**Strategy**](strategy.md) — the maths: realised and implied vol, the variance risk
  premium, the expected-value integral, the probability wedge, Kelly sizing, the payoff
  and stress engine
- [**Architecture**](architecture.md) — the three broker planes, the cycle step by step,
  the module map, and where to change what
- [MCP and CLI](mcp-and-cli.md) — what each Alpaca surface does, and why only one of them
  can place an order

## Competition
- [**What you need to do**](hackathon/operator-instructions.md) — the runbook: Monday's
  open, the loop, what to watch, what to submit
- [Overview](hackathon/overview.md) — what you are walking into
- [Rules and criteria](hackathon/rules-and-criteria.md) — hard requirements and scoring
- [Submission](hackathon/submission.md) — submission checklist
- [Video / NotebookLM](hackathon/video-script.md) — sources + prompt to generate the pitch
- [Week plan](hackathon/week-plan.md) — 26 Aug – 4 Sep, and the cutoff hour resolved

## Background
- [Sources](sources.md) — links and material
- [Explanations](explanations/hackathon-alpaca-overview.md) — study notes on the event and
  the broker

The previous strategy — a defined-risk collar overlay — lives on as its own project at
[alpaca-collar-overlay](https://github.com/Ander-IbBi/alpaca-collar-overlay). The Obsidian
vault `C:\Users\User\Vault\deep-hedging` remains for deep-hedging theory; this repo is
only the hackathon product.
