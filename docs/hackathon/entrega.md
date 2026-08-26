# Entrega — checklist

Plantilla estándar de lablab ([guía](https://lablab.ai/delivering-your-hackathon-solution)).
Se envía desde el **dashboard del equipo**.

## Antes de enviar

- [ ] Equipo creado en lablab (1–6) y Discord conectado
- [ ] Track: **Options Alpha Agents**
- [ ] Cuenta Alpaca **paper nueva** usada durante el evento
- [ ] Repo **público** en GitHub (privado = los jueces no puntúan el código)
- [ ] README en inglés que permita clonar y correr el demo sin preguntarte nada
- [ ] Demo desplegado con URL viva
- [ ] El demo enseña opciones + agente + riesgo + P&L, no solo texto
- [ ] `.env` fuera del repo; `.env.example` dentro
- [ ] `uv run pytest` y `uv run ruff check .` en verde

## Campos del formulario

| Campo | Notas |
| --- | --- |
| Title | Corto (~50 caracteres) |
| Short description | ≤255 caracteres |
| Long description | ≥100 palabras: problema, enfoque, opciones, stack Alpaca |
| Cover image | PNG/JPG 16:9 |
| Video | **MP4 subido**, ≤5 min, ≤300 MB |
| Slides | PDF |
| GitHub | URL pública |
| Application URL | Demo interactivo |
| Technologies | Alpaca API, MCP, CLI, Python, Streamlit, opciones, LLM |

## Guion del vídeo (5 min)

1. Quién eres y el problema (20 s)
2. Qué hace el agente y por qué opciones (40 s)
3. Demo: propuesta → riesgo → orden paper (2 min)
4. Journal y P&L; por qué no es un script (1 min)
5. Cierre: API + MCP/CLI, y qué harías con más tiempo (20 s)

Grabar el **3 sep**.
