# Entrega — qué enviar

Fuente: kickoff 28 ago 2026 + [guía genérica de lablab](https://lablab.ai/delivering-your-hackathon-solution).
Se envía desde el **dashboard del equipo** → **Submit your project** (3 páginas).
El botón aparece junto al countdown. A las **17:00 CET del 4 sep** el form se cierra.

## Lo que dijeron en el kickoff (mínimo)

Tres piezas. Sin esto no hay submission:

1. **Descripción del producto** — título, short description, long description en el form.
2. **Vídeo (presentation)** — tú mostrando lo construido, **máximo 5 minutos**.
3. **GitHub con el demo** — repo (público) y la demo que los jueces puedan ver.

Campos extra del extra challenge: **hasta 5 URLs** de posts en X / LinkedIn.

## Lo que suele pedir el formulario lablab (rellénalo si aparece)

La plantilla estándar a menudo añade cover 16:9, PDF de slides y URL de la app.
Si el form los tiene, no los dejes vacíos. Si no están, no los inventes.

| Campo | Qué poner |
| --- | --- |
| Title | Corto (~50 caracteres) |
| Short description | ≤255 caracteres |
| Long description | ≥100 palabras: problema, collar/opciones, agente autónomo, paper, stack |
| Video | MP4 ≤5 min (límite típico ≤300 MB si piden upload) |
| GitHub | Repo **público** |
| Application URL / demo | Streamlit desplegado o README tan claro que clonar baste |
| Social posts | Hasta 5 links (opcional, Built in Public) |
| Technologies | Alpaca Trading API, MCP, CLI, Python, Streamlit, options, LLM |

## Antes de pulsar send

- [ ] Equipo creado en lablab (1–6) aunque vayas solo
- [ ] Discord; canal del hackathon; mentores con el tag que indiquen
- [ ] Main challenge: agente autónomo + API + (MCP **o** CLI) + **opciones** + **paper**
- [ ] El agente ha operado **durante la semana** (P&L / journal), no solo un dry-run
- [ ] Repo público; `.env` fuera; `.env.example` dentro
- [ ] El vídeo enseña: identificar → decidir → gestionar posición → paper P&L
- [ ] Se ve MCP o CLI (aunque el loop use `alpaca-py`)
- [ ] `uv run pytest` y `uv run ruff check .` en verde

## Guion del vídeo (5 min)

1. Quién eres y el problema (20 s).
2. Estrategia testeable: collar SPY, por qué opciones, riesgo definido (40 s).
3. Demo: ciclo en vivo (propuesta → risk → orden o hold) en **paper** (2 min).
4. Gestión en el tiempo: journal, overlay already on, circuit breaker, curva (1 min).
5. Stack: Trading API + MCP (y/o CLI). Extra: Built in Public si posteaste (20 s).

Grabar el **3 sep**. Enviar el 3 por la noche o el 4 muy temprano.

## Premios (para no confundir el form)

Main (top 3): $2,500 / $1,500 / $1,000. Social (dos equipos): $500 + Algo Trader Plus
un mes por miembro.
