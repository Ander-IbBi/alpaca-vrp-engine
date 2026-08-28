# Reglas y criterios

Actualizado con el **kickoff** (28 ago 2026). Si Discord o la landing lo contradicen,
gana lo escrito ahí.

## Requisitos duros (main challenge — obligatorio)

1. Equipos de **1 a 6**. Registrarse en lablab y **crear o unirse a un equipo**.
2. Agentes **autónomos** (y apps de trading) sobre Alpaca.
3. Usar la **Alpaca Trading API** (obligatorio).
4. Usar **MCP server o CLI** de Alpaca (uno de los dos basta; en el stream el MCP se
   presentó como el núcleo del evento — conviene que se *vea* en demo/vídeo).
5. Toda estrategia **incorpora options trading**.
6. Desarrollar y probar en **paper trading**. Cero capital real.

El main challenge se llama **Options Alpha Agents**: agentes de IA pensados para
**generar P&L** en la plataforma de Alpaca. La solución tiene que mostrar una
estrategia **clara y testeable**, y cómo el agente:

- identifica oportunidades,
- decide en el mercado,
- **gestiona la posición**,
- y rinde **a lo largo de toda la competición** (no un trade único de demo).

Enfoques que citaron: options, trading agents, *portfolio income*, u otros que
Alpaca soporte.

## Extra challenge (opcional): Built in Public

Compartir el progreso en **X y LinkedIn**, etiquetando los perfiles de Alpaca y
lablab (handles en la landing del evento). En el formulario de entrega caben
**hasta 5 enlaces** a posts.

No sustituye al main challenge. Premios aparte: **$500** para cada uno de los dos
equipos ganadores de social, más **un mes de Algo Trader Plus** por miembro del
equipo ganador.

## Fechas

| Momento | Hora oficial | Tu reloj (UTC−4) |
| --- | --- | --- |
| Kickoff | vie 28 ago (stream) | ya ocurrió |
| Q&A Discord | 18:00 CET el día del kickoff | ya ocurrió |
| Build | 28 ago – 4 sep | toda la semana |
| **Cierre de submissions** | vie **4 sep, 17:00 CET** | vie **4 sep ~11:00** (si CET = CEST, UTC+2) |

Cuando el countdown llega a cero, **el formulario se desactiva**. Empezar a
rellenarlo días antes.

## Criterios de los jueces (orden en el stream)

Tony (Alpaca): ideas creativas, **gestión de riesgo**, ejecución técnica, **P&L**.

Joanna, en este orden:

1. **P&L performance** — «first and foremost».
2. **Technology implementation** — API, MCP y CLI.
3. **Creativity & originality**.
4. **Presentation and execution**.

No dieron pesos numéricos.

## Premios (main)

Bolsa **$6,000**. Top 3 overall: **$2,500 / $1,500 / $1,000**.

## Cómo jugarlos con *este* repo

**P&L:** el collar tiene que estar **vivo en paper toda la semana**, no solo en un
screenshot. Loop desatendido (`--loop --execute`) + journal. Una semana es ruido;
no all-in.

**Tech:** el producto opera con `alpaca-py` (Trading API). MCP y/o CLI tienen que
aparecer en README, vídeo y, si puedes, un uso real (inspección de cuenta/cadena).
Sin eso, el criterio 2 se cae aunque el collar sea correcto.

**Creatividad:** overlay de cobertura con riesgo definido (put + call cubierto), no
otro bot de momentum. Encaja con «portfolio income» / gestión de posición.

**Presentación:** vídeo ≤5 min de *lo que construiste*, GitHub con demo, textos del
formulario. Que se vea el ciclo: oportunidad → decisión → riesgo → orden o hold.

## Lo que el stream *no* cerró

- Cómo **vinculan** el número de cuenta paper al P&L (preguntar en Discord
  `#…hackathon` con tag `mentors` si no está en la landing).
- Rúbrica con porcentajes.
- Si el formulario lablab sigue pidiendo slides/cover además del vídeo (la guía
  genérica sí; Joanna citó descripción + vídeo + GitHub/demo).
