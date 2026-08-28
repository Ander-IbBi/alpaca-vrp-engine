# Qué tienes que hacer tú

Instrucciones para **operar el hackathon**, no para programar. El código ya está:
collar agresivo sobre SPY, solo paper, dry-run por defecto.

Kickoff ya ocurrió (28 ago). Brief en [reglas-y-criterios](reglas-y-criterios.md).
Corte de entrega: **4 sep 17:00 CET** (≈11:00 tu hora).

Los jueces **no ven Cursor**. Ven: cuenta paper (P&L de la semana) + repo + demo + vídeo ≤5 min.

---

## Estado ahora (viernes 28, noche)

| Cosa | Estado |
| --- | --- |
| Código / tests | Listos. 91 tests y ruff en verde, sin red ni claves. |
| Estrategia | Collar financiado + **gestión activa** (rolar call ITM, rolar por vencimiento, monetizar put). |
| Cuenta de entrega | `PA3GMY396XY9`, $100k, **opciones nivel 3**, libro vacío. Keys ya en `.env`. |
| GitHub | Público: https://github.com/Ander-IbBi/alpaca-options-agent — CI en verde. |
| CLI de Alpaca | Instalada en `~/.alpaca-cli` y con sesión paper iniciada. |
| Agente en bucle | **No está corriendo**, y no puede: el mercado abre el **lunes 31 a las 9:30**. |

Lo único que falta para generar P&L es arrancar el lunes.

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

## Este fin de semana (mercado cerrado)

Nada de trading: la bolsa abre el lunes. Lo que sí puedes cerrar ya:

### 1. lablab

- [ ] Equipo creado en el dashboard **aunque vayas solo** (1 a 6 personas).
- [ ] Discord de lablab, canal del hackathon.
- [ ] Empezar a rellenar el formulario de entrega (se guarda; no esperes al día 4).

### 2. Built in Public (opcional, premio aparte de $500)

Posts en **X y LinkedIn** etiquetando a Alpaca y lablab. Caben **5 enlaces** en el
formulario. Ideas que ya puedes contar: el repo público, por qué un collar en vez de
un bot direccional, y la verificación cruzada SDK/CLI.

### 3. Comprobación rápida (opcional)

```powershell
uv run python scripts/broker_report.py
```

Enseña la cuenta vista por el SDK y por la CLI, y un ciclo en seco.

---

## Lunes 31 — arranca el P&L

El mercado abre a las **9:30** (tu hora). El agente ya sabe qué hacer.

### A. Sembrar y collarizar

```powershell
# Ciclo 1: compra 100 SPY
uv run python scripts/run_agent.py --execute

# Espera al fill (mira el dashboard). Ciclo 2: abre el collar
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

## Lunes 31 – miércoles 2: a qué estar atento

No rediseñes la estrategia a mitad de semana. El agente ya gestiona solo.

Mira, no toques:

| Señal | Qué significa | Qué haces |
| --- | --- | --- |
| «overlay already on … Hold: short 790c safe; …» | Correcto: ha mirado y decide esperar | Nada |
| «Roll the short call up» | SPY subió por encima del call; recupera recorrido | Nada, es lo que debe hacer |
| «Roll the collar out» | Se acercaba el vencimiento | Nada |
| «Harvest the hedge» | El put dobló en una caída; realiza el beneficio | Nada |
| «Broker views disagree… stale book» | SDK y CLI no coinciden | Mira el dashboard; suele ser un fill a medias. Se resuelve solo |
| «Open overlay orders… waiting» | Hay un limit sin fill | Nada; el limit es day, el siguiente ciclo reintenta |
| «Risk layer blocked» | El código te salvó | No fuerces la orden a mano |
| Equity < ~80k o pérdida diaria > 1500 | Circuit breaker | El agente deja de mandar. No «recuperes» a mano con más riesgo |
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
uv run python scripts/broker_report.py
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
| ¿Cómo grabo el vídeo? | [video-guion](video-guion.md) |
| ¿Calendario corto? | [plan-semana](plan-semana.md) |
| ¿Qué carpeta toca? | [Guía del repo](../guia-del-repo.md) |
| ¿Qué pinta el MCP y qué la CLI? | [mcp-and-cli](../mcp-and-cli.md) |
