# Plan de la semana

El esqueleto ya está. La estrategia ganadora se afina **después** del kickoff.

## Punto de restauración

Tag git **`restore-collar-playbook`**: el collar agresivo ya construido, **antes** de
probar órdenes en paper. Volver a este estado de código:

```bash
git switch --detach restore-collar-playbook
# o, si quieres mover main aquí (destructivo para commits posteriores):
# git reset --hard restore-collar-playbook
```

Eso no deshace posiciones en Alpaca. Si una prueba paper deja SPY/opciones,
ciérralas en el dashboard o con `close_all_positions`. La cuenta de **entrega**
sigue siendo otra, el viernes.

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
- Comprar el book base (el agente siembra 100 SPY) y lanzar el primer ciclo
- Primeras órdenes pequeñas: el reloj del P&L empieza aquí

## Sáb 29 – lun 31 ago

- [x] Estrategia real en `strategy/` (collar por delta, semilla SPY, skip si ya cubierto)
- [x] LLM en el loop (`agent/llm.py`) para explicar; veto suave fail-open
- [x] Automatizar el ciclo: `run_agent.py --loop --interval 900`
- Streamlit: curva de equity, greeks y journal

## Mar 1 – mié 2 sep

- Pulir el demo hasta que un ciclo completo se vea en vivo
- README final y capturas
- Dejar al agente operando con reglas, no a mano

## Jue 3 sep — día creativo

- Vídeo MP4, slides PDF, desplegar Streamlit, repo público

## Vie 4 sep — corte 15:00 UTC

- Enviar con margen. Nada de estrategias nuevas este día.
