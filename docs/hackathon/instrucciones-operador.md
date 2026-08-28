# Qué tienes que hacer tú

Instrucciones para **operar el hackathon**, no para programar. El código ya está:
collar agresivo sobre SPY, solo paper, dry-run por defecto.

Kickoff ya ocurrió (28 ago). Brief en [reglas-y-criterios](reglas-y-criterios.md).
Corte de entrega: **4 sep 17:00 CET** (≈11:00 tu hora).

Los jueces **no ven Cursor**. Ven: cuenta paper (P&L de la semana) + repo + demo + vídeo ≤5 min.

---

## Estado ahora (jueves 27)

| Cosa | Estado |
| --- | --- |
| Código / tests | Listos. El playbook es: sembrar 100 SPY → 1 collar → **hold**. |
| Agente en bucle | **No está corriendo.** Hace falta una ventana de terminal abierta. |
| Cuenta Alpaca actual | De **desarrollo**. Ya tiene 100 SPY + collar. Sirve para probar, **no** para puntuar. |
| Reloj de P&L del evento | Empieza el viernes, en una **cuenta paper nueva y vacía**. |

No dejes `--loop --execute` contra las keys de desarrollo: esas operaciones no cuentan
y ensucian el libro.

---

## Horarios (apunta los dos)

Estás en UTC−4 (hora Nueva York en verano).

| Qué | UTC | Tu reloj |
| --- | --- | --- |
| Kickoff + cierra inscripción | vie **28 ago 15:00** | vie **28 ago 11:00** |
| Semana de trading paper | 28 ago – 4 sep | ídem |
| Día de vídeo / slides / demo | jue **3 sep** | todo el día |
| Corte de submissions | vie **4 sep 17:00 CET** | vie **4 sep ~11:00** |

Envía el **3 sep por la noche** o el **4 por la mañana**. El 4 no se inventa estrategia.

---

## Enlaces que debes tener abiertos

Quédate con estas pestañas (o fíjalas):

1. **Evento** — https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon
2. **Live / hitos** — https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live
3. **Kickoff (Twitch)** — https://www.twitch.tv/lablabai
4. **Discord lablab** — https://discord.gg/lablabai
5. **Dashboard del equipo** (entrega) — el de tu equipo en lablab, no el de Alpaca
6. **Alpaca paper** — https://app.alpaca.markets/paper/dashboard/overview
7. **Cómo entregar** — https://lablab.ai/delivering-your-hackathon-solution
8. **Guía de equipos** — https://lablab.ai/getting-started-guide

Track a elegir: **Options Alpha Agents**.

---

## Hoy (antes del kickoff)

Haz esto aunque el código ya funcione.

### 1. lablab

- [ ] Equipo creado en el dashboard **aunque vayas solo** (1 a 6 personas).
- [ ] Discord de lablab abierto y cuenta vinculada si el formulario lo pide.
- [ ] Track: Options Alpha Agents.

### 2. Comprobar que no se te ha escapado nada de Alpaca (cuenta de desarrollo)

- [ ] En el dashboard paper: **opciones habilitadas** (nivel de trading; aquí era 3).
- [ ] `uv run python scripts/smoke_paper.py` imprime `Smoke test OK (paper)`.

No hace falta operar más en esta cuenta.

### 3. Qué escuchar en el kickoff (apunta literal)

No improvises el viernes: lleva estas preguntas y escríbelas en
[reglas-y-criterios](reglas-y-criterios.md) en cuanto acabe el stream.

- ¿Sigue habiendo **un solo track** y se llama igual?
- ¿Cómo **vinculan** la cuenta paper para medir P&L? (número de cuenta, screenshot, form…)
- ¿La cuenta de entrega tiene que ser **nueva / reseteada / otro login**?
- ¿Exigen **MCP y CLI**, o basta con uno de los dos? (el repo ya documenta los dos)
- ¿Piden posts en redes, logs de sesiones de IA, o un informe extra?
- ¿Rúbrica numérica (cuánto vale P&L vs demo vs código)?
- ¿El demo tiene que estar **desplegado** (URL pública) o vale localhost + vídeo?
- Hosting permitido (Streamlit Community Cloud, Hugging Face, etc.).
- Premios y desempates.

---

## Viernes 28 — día 1 del evento

Orden fijo. No adelantes el `--execute` de entrega **antes** del kickoff.

### A. Ver el kickoff (11:00 tu hora)

Twitch. Toma notas. Actualiza `docs/hackathon/reglas-y-criterios.md`.

### B. Cuenta paper de **entrega**

Objetivo: libro **vacío** y keys **solo** de esa cuenta en `.env`.

1. Sigue lo que diga el brief (nueva cuenta, reset paper, u otro usuario).
2. En Alpaca: confirma otra vez **paper** (no live) y **opciones ON**.
3. Crea API keys **paper**. Pégalas en `.env` (sustituyen las de desarrollo).
4. Comprueba que **no** has puesto `ALPACA_LIVE_TRADE=true`.
5. Deja `DRY_RUN=true` hasta el smoke.

En PowerShell, desde la carpeta del repo:

```powershell
uv run python scripts/smoke_paper.py
```

Tiene que salir `paper=True` y el **número de cuenta nuevo**. Si ves el de desarrollo,
las keys no se han cambiado.

### C. Primeras órdenes (empieza el P&L)

Cuando el smoke esté bien y el mercado de USA esté **abierto** (aprox. 9:30–16:00
Nueva York):

```powershell
# Un ciclo: debería comprar 100 SPY
uv run python scripts/run_agent.py --execute

# Espera a que SPY esté en posiciones (dashboard). Luego el collar:
uv run python scripts/run_agent.py --execute
```

Si el mercado está cerrado, `--execute` **no envía** opciones (son day orders). Espera
a la apertura.

Cuando veas 100 SPY + put largo + call corto, el playbook ya está. A partir de ahí:

```powershell
uv run python scripts/run_agent.py --loop --execute --interval 900
```

Esa ventana **tiene que quedarse abierta** (cada 15 min: hold, journal, circuit breaker).
Si cierras el PC o la terminal, el agente se para. No pasa nada grave (el collar sigue
en Alpaca); al volver, lanza el mismo comando.

Opcional, en otra terminal, para verte a ti mismo lo que verán los jueces:

```powershell
uv run streamlit run app/streamlit_app.py
```

El toggle «Send order to paper» déjalo **apagado** salvo que quieras un ciclo a mano.
El bucle de la otra ventana ya ejecuta.

### D. LLM (opcional, recomendable para el demo)

Si quieres explicaciones en el journal:

```powershell
uv sync --extra llm
```

Descomenta `OPENAI_API_KEY` en `.env`. Sin clave, el asesor rule-based aprueba y el
ciclo sigue igual.

---

## Sábado 29 – miércoles 2: a qué estar atento

No rediseñes la estrategia a mitad de semana. El agente, si el collar está on, **hold**.

Mira, no toques:

| Señal | Qué significa | Qué haces |
| --- | --- | --- |
| Journal: «overlay already on» | Correcto | Nada |
| Journal: «Open overlay orders… waiting» | Hay un limit sin fill | Nada; si al cierre se cancela (day), el siguiente ciclo reintenta |
| Journal: «Risk layer blocked» | El código te salvó | No fuerces la orden a mano |
| Equity por debajo de ~80k o pérdida diaria > 1500 | Circuit breaker | El agente deja de mandar. No «recuperes» a mano con más riesgo |
| Posición a medias (solo put o solo call) | El agente espera a propósito | No vendas otro call sobre las mismas 100 acciones |
| Smoke o ciclo hablan de **live** | Stop inmediato | Keys mal; vuelve a paper |
| `.env` en un commit | Stop | No lo subas nunca. Solo `.env.example` vacío |

Una vez al día:

1. Dashboard Alpaca: equity, posiciones, que sigue siendo **paper**.
2. Últimas líneas de `data/journal/agent.jsonl` (o la tabla del Streamlit).
3. Discord / live de lablab por si cambian reglas.

El P&L de una semana es ruido. Una curva aburrida y explicable gana a un all-in.

---

## Jueves 3 — lo que hay que **entregar**

Se envía desde el **dashboard del equipo en lablab**, no por email. Detalle de campos
en [entrega](entrega.md).

Prepara, en este orden:

1. **Repo GitHub público.** Privado = los jueces no puntúan código. README en inglés
   (el que ya hay). Cero secrets.
2. **Demo con URL** que un juez abra sin instalar nada. Streamlit del repo:
   cuenta, posiciones, un ciclo, journal. Si el kickoff acepta solo vídeo + repo,
   igual conviene URL: reduce fricción.
3. **Vídeo MP4** ≤ 5 min, ≤ 300 MB, **subido** al formulario (no solo un link de
   YouTube si lablab pide el archivo).
4. **Slides PDF.**
5. **Cover** PNG/JPG 16:9.
6. Textos: título corto, descripción corta (≤255), larga (≥100 palabras): problema,
   collar, opciones, Alpaca API + MCP/CLI, paper only.

### Guion del vídeo (5 min)

1. Quién eres y el problema (20 s).
2. Qué hace el agente y por qué opciones / collar (40 s).
3. Demo en vivo: propuesta → riesgo → orden o hold (2 min).
4. Journal + P&L; por qué no es un script (1 min).
5. Cierre: Trading API + MCP o CLI; qué harías con más tiempo (20 s).

En el vídeo tiene que verse **paper**, **opciones**, y que el riesgo es código, no el LLM.

---

## Viernes 4 — corte 11:00 tu hora

- [ ] Formulario enviado **con margen** (ideal: ya enviado el 3).
- [ ] Repo público, demo URL viva, vídeo reproduce, PDF abre.
- [ ] Track correcto.
- [ ] Número de cuenta paper de entrega el que ellos pidan (si lo piden).

Ese día: no nuevas estrategias, no refactors grandes.

---

## Qué no hagas nunca

- Trading **live** o `ALPACA_LIVE_TRADE=true`.
- Subir `.env` o pegar keys en el README / issues / Discord público.
- Short de opciones **desnudo** a mano «para mejorar el P&L».
- Cambiar de collar a otra idea el jueves 3 porque «no va».
- Dejar el único `--loop` en un PC que se duerme sin haber comprobado que el collar
  ya está en la cuenta (las posiciones viven en Alpaca; el loop solo decide).

---

## Comandos de cabecera (copia y pega)

Desde `C:\Users\User\Projects\alpaca-options-agent`:

```powershell
uv run python scripts/smoke_paper.py
uv run python scripts/run_agent.py
uv run python scripts/run_agent.py --execute
uv run python scripts/run_agent.py --loop --execute --interval 900
uv run streamlit run app/streamlit_app.py
uv run pytest
uv run ruff check .
```

Sin `--execute`, **no se manda nada** al broker.

---

## Si te pierdes

| Pregunta | Dónde |
| --- | --- |
| ¿Qué es el evento? | [overview](overview.md) |
| ¿Qué puntúan? | [reglas-y-criterios](reglas-y-criterios.md) |
| ¿Campos del formulario? | [entrega](entrega.md) |
| ¿Calendario corto? | [plan-semana](plan-semana.md) |
| ¿Qué carpeta toca? | [Guía del repo](../guia-del-repo.md) |
