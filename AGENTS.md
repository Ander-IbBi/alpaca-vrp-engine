# alpaca-options-agent — Contexto para agentes de IA

> Fuente de verdad del proyecto. Cursor la lee automáticamente. Mantenla corta.

## Qué es
Agente de overlay de cobertura con **opciones** (collar agresivo sobre SPY) para el
[Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(28 ago – 4 sep 2026, track **Options Alpha Agents**). Opera **solo en Alpaca paper**.
El entregable son código público + demo Streamlit + vídeo, no el chat del IDE.

Este repo es independiente del vault de Obsidian `Vault/deep-hedging` (estudio teórico).

## Stack
- Python 3.11+, empaquetado con **uv** (`pyproject.toml`)
- `alpaca-py` (Trading API, paper-only), Streamlit para el demo
- LLM opcional (`uv sync --extra llm`) en `agent/llm.py`
- Alpaca MCP y CLI: exigidos por el evento, documentados en README; no sustituyen `alpaca-py`

## Estructura
- `src/options_agent/`
  - `config.py` — settings + **abort si `ALPACA_LIVE_TRADE=true`**
  - `alpaca/` — `client.py` (paper), `options.py` (cadena con quotes/greeks), `orders.py` (tickets)
  - `strategy/` — `base.py` (vocabulario), `overlay.py` (collar agresivo; intercambiable)
  - `risk/` — `limits.py` (por orden, call cubierto), `account.py` (circuit breaker de cuenta)
  - `agent/` — `loop.py` (ciclo), `tools.py` (tools del LLM), `llm.py` (explica + veto suave)
  - `journal.py` — traza JSONL de decisiones
- `app/streamlit_app.py` — demo para jueces
- `scripts/` — `smoke_paper.py`, `run_agent.py`
- `tests/` — riesgo, config, estrategia y órdenes **sin** tocar la red
- `docs/` — competición y guías (español)

## Reglas duras
- **Nunca live.** `TradingClient(..., paper=True)`; no añadir un flag para live.
- La estrategia propone; el LLM explica (veto suave fail-open); `risk/` decide. No crear caminos que salten el riesgo.
- Short desnudo de opciones: prohibido. Un call corto del collar exige acciones de cobertura.
- `DRY_RUN=true` por defecto; ejecutar requiere intención explícita.

## Convenciones
- Código, comentarios, docstrings, notebooks y README: **inglés**.
- Notas de `docs/`: español.
- Markdown en `kebab-case`. Commits: Conventional Commits.
- Antes de dar algo por bueno: `uv run pytest` y `uv run ruff check .`.
