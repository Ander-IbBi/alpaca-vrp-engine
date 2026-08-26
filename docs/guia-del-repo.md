# Guía del repo — qué es cada cosa y por qué

Si no sabes dónde va un cambio, empieza aquí.

## Vista rápida

```
alpaca-options-agent/
  README.md            ← lo que ven jueces y GitHub (inglés)
  AGENTS.md            ← contexto para Cursor y otras IAs
  pyproject.toml       ← dependencias y scripts (uv)
  .env.example         ← plantilla de variables; copiar a .env
  src/options_agent/   ← el producto
  app/                 ← demo Streamlit
  scripts/             ← smoke test y ejecución de un ciclo
  tests/               ← tests sin red ni keys
  notebooks/           ← investigación, no el entregable
  docs/                ← competición y guías (español)
  .github/workflows/   ← CI: tests + lint en cada push
```

## El flujo, en una frase

`agent/loop.py` construye un **contexto** → `strategy/` propone un **ProposedTrade** →
`risk/` lo aprueba o lo veta → `alpaca/orders.py` lo convierte en un ticket →
`journal.py` lo registra → Streamlit lo enseña.

## `src/options_agent/`

| Módulo | Sirve para | No es |
| --- | --- | --- |
| `config.py` | Cargar `.env` y **abortar si live** | Un sitio para keys |
| `journal.py` | Traza JSONL de cada decisión | Un logger de debug |
| `alpaca/client.py` | Cliente paper, cuenta, posiciones, precios | Un cliente configurable a live |
| `alpaca/options.py` | Contratos y cadena, normalizados a `OptionCandidate` | La estrategia |
| `alpaca/orders.py` | Construir y enviar el ticket (single o multi-leg) | Quien decide si se opera |
| `risk/limits.py` | Límites por orden (tamaño, coste, short desnudo) | Negociable por el LLM |
| `risk/account.py` | Circuit breaker (suelo de equity, pérdida diaria) | Un stop loss por posición |
| `strategy/base.py` | Vocabulario común (`ProposedTrade`, `StrategyContext`) | Lógica de mercado |
| `strategy/overlay.py` | Protective put: elegir strike y tamaño | Definitivo (se cambia tras kickoff) |
| `agent/loop.py` | El ciclo completo | La UI |
| `agent/tools.py` | Tools que el LLM puede llamar (solo lectura) | Una puerta trasera al broker |
| `agent/llm.py` | Advisor opcional; sin key funciona igual | Requisito para operar |

### Por qué riesgo y estrategia están separados

Un LLM puede alucinar un trade absurdo. La estrategia propone; `review_proposal`
decide. Como el riesgo es código puro, se testea sin tocar Alpaca y no hay forma de
que el modelo lo desactive.

### La parte testeable de verdad

`select_protective_put()` es una función pura: le pasas contratos, spot y fecha, y
devuelve el strike elegido. Por eso `tests/test_strategy.py` puede comprobar la
lógica de cobertura sin mercado abierto.

## `app/`

`streamlit_app.py` es lo que despliegas y lo que abren los jueces: métricas de cuenta,
posiciones, botón para lanzar un ciclo y el journal. Si algo no se ve aquí, para el
jurado no existe.

## `scripts/`

- `smoke_paper.py` — ¿funcionan las keys? Clock + cuenta. Correr esto primero.
- `run_agent.py` — un ciclo; `--execute` para mandar la orden de verdad.

## `tests/`

Cuatro archivos: config (paper-only), risk (vetos), strategy (selección de strike),
orders + journal (tickets y traza). Ninguno usa red, así que corren en CI sin secrets.

## `docs/`

Notas en español. `hackathon/` es la competición; esta guía es el mapa técnico.
El vault de Obsidian `Vault/deep-hedging` queda aparte, para la teoría.

## Comandos que vas a repetir

```bash
uv run pytest                                 # tests
uv run ruff check .                           # lint
uv run python scripts/run_agent.py            # ciclo en dry run
uv run streamlit run app/streamlit_app.py     # demo
```
