# Reglas y criterios

La landing de lablab está casi vacía hasta el kickoff. Esto mezcla **lo publicado** con
**prácticas estándar de lablab**. El 28 ago a las 15:00 UTC llega el brief oficial:
actualiza esta nota ese día.

## Requisitos duros

1. **18+**, online, equipos de **1 a 6** personas.
2. Usar la **Alpaca Trading API**.
3. Usar **MCP server o CLI** de Alpaca.
4. La estrategia **incorpora opciones**.
5. Entrega con **cuenta paper nueva y dedicada**.
6. Todo en **paper**. Cero dinero real.

## Fechas (UTC)

| Momento | Cuándo |
| --- | --- |
| Kickoff + cierre de inscripción | **vie 28 ago 2026, 15:00** |
| Cierre de submissions | **vie 4 sep 2026, 15:00** |

## Criterios (confirmar en kickoff)

- **P&L performance** — equity de la cuenta paper durante el evento.
- **Technology implementation** — API, agente, opciones, MCP/CLI, calidad del repo.
- **Creativity & originality** — el overlay de cobertura frente al enésimo bot de momentum.
- **Presentation & execution** — vídeo, slides, demo comprensible.

## Cómo jugarlos

**P&L:** una semana es ruido. Empieza a operar el 28 con límites estrictos. El repo ya
trae circuit breaker (`MAX_DAILY_LOSS_USD`, `MIN_EQUITY_USD`) y tope por orden.

**Tech:** la estrategia propone, el LLM explica (veto suave), `risk/` decide. Órdenes
de opciones reales, collar multi-leg con quotes y delta. El journal JSONL da trazabilidad.

**Creatividad:** riesgo definido y hedging explicable. Nada de short desnudo.

**Presentación:** vídeo grabado el 3 sep. El 4 es buffer.

## Qué escuchar en el kickoff

- ¿Sigue habiendo un solo track?
- ¿Piden herramientas concretas, posts en redes o informes de sesiones de IA?
- ¿Cómo se vincula la cuenta paper para medir P&L?
- Rúbrica numérica y desglose de premios.
- Hosting permitido para el demo.
