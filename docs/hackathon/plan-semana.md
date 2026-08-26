# Plan de la semana

El esqueleto ya está. La estrategia ganadora se afina **después** del kickoff.

## Mié 26 ago — setup

- [x] Inscripción en lablab
- [x] Repo independiente con código, tests y demo
- [ ] Equipo creado en el dashboard de lablab (aunque vayas solo)
- [ ] Discord de lablab
- [ ] Cuenta Alpaca **paper** de desarrollo + `.env`
- [ ] `uv run python scripts/smoke_paper.py` en verde

## Jue 27 ago

- Webinar de Alpaca (10:00 PDT): https://luma.com/qoym39ry
- MCP de Alpaca funcionando en Cursor; opcional, probar la CLI
- Comprobar que la cuenta paper tiene **opciones habilitadas** (nivel de trading)

## Vie 28 ago — kickoff 15:00 UTC

- Twitch: https://www.twitch.tv/lablabai
- Actualizar [reglas-y-criterios](reglas-y-criterios.md) con el brief real
- Abrir la **cuenta paper de entrega** y poner sus keys en `.env`
- Comprar el book base (p. ej. SPY) y lanzar el primer ciclo del agente
- Primeras órdenes pequeñas: el reloj del P&L empieza aquí

## Sáb 29 – lun 31 ago

- Estrategia real en `strategy/` (strikes por delta/IV, rolling, collar)
- LLM en el loop (`agent/llm.py`, `agent/tools.py`) para razonar y explicar
- Automatizar el ciclo durante la sesión de mercado
- Streamlit: curva de equity, greeks y journal

## Mar 1 – mié 2 sep

- Pulir el demo hasta que un ciclo completo se vea en vivo
- README final y capturas
- Dejar al agente operando con reglas, no a mano

## Jue 3 sep — día creativo

- Vídeo MP4, slides PDF, desplegar Streamlit, repo público

## Vie 4 sep — corte 15:00 UTC

- Enviar con margen. Nada de estrategias nuevas este día.
