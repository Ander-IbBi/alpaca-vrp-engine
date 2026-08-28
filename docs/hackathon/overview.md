# Overview — Alpaca AI Trading Agents Hackathon

> Mapa de la competición. Reglas en [reglas-y-criterios](reglas-y-criterios.md),
> entrega en [entrega](entrega.md), calendario en [plan-semana](plan-semana.md).

## Qué es

Hackathon **online** de [lablab.ai](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
con Alpaca. **28 agosto – 4 septiembre 2026**. Un único track: **Options Alpha Agents**.
~2600 inscritos.

No es un paper académico: es un **agente de IA que opera opciones en paper trading**
más un **demo** que los jueces puedan abrir y entender en tres minutos.

## Qué hay que construir (una frase)

Un sistema que lee cuenta y mercado → propone un collar (o siembra SPY) → el risk
layer veta o aprueba → el LLM explica → ejecuta en la cuenta **paper** de Alpaca →
se ve en un dashboard.

## Stack exigido

| Pieza | Rol | En este repo |
| --- | --- | --- |
| **Trading API** | Obligatorio | `src/options_agent/alpaca/` con `alpaca-py` |
| **MCP server** | Obligatorio MCP **o** CLI | Documentado en README; útil en Cursor |
| **CLI** | Alternativa al MCP | Documentado; paper por defecto |
| **Paper trading** | Dinero simulado, datos reales | Único modo del código |
| **Opciones** | El track | `strategy/overlay.py` (collar) + `alpaca/orders.py` |

Los jueces **no ven tu IDE**. En el kickoff el mínimo fue: descripción + vídeo ≤5 min +
GitHub con demo. El P&L de la semana cuenta tanto como el repo.

## Cómo se gana

1. Cumplir los requisitos duros (API + MCP/CLI, opciones, cuenta paper nueva).
2. Que se note que es un **agente** con capa de riesgo, no un script que compra un call.
3. No volar la cuenta: mejor curva aburrida y explicable que un all-in.
4. Presentación: README, demo URL, vídeo ≤5 min, slides.

Criterios (kickoff): P&L primero, luego API/MCP/CLI, creatividad, presentación.
Premios main: **$2,500 / $1,500 / $1,000**. Extra social: **$500** × 2 equipos.

## Nuestro ángulo

**Collar agresivo**: el agente siembra 100 SPY y cubre con un put (~delta −0.20)
financiado vendiendo un call (~delta +0.20). Riesgo definido (suelo en el put, techo
en el call). Un playbook, sin auto-replanteo: si ya está collared, hold.

Si el kickoff empuja hacia volatilidad o alpha puro, se cambia `strategy/` y el resto
del sistema (Alpaca, riesgo, journal, UI) sigue igual.

## Enlaces

- Evento: https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon
- Live: https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live
- Discord: https://discord.gg/lablabai · Twitch: https://www.twitch.tv/lablabai
- Alpaca paper: https://app.alpaca.markets/paper/dashboard/overview
- MCP: https://github.com/alpacahq/alpaca-mcp-server · CLI: https://github.com/alpacahq/cli
- Entrega: https://lablab.ai/delivering-your-hackathon-solution
