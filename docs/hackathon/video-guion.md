# Vídeo de 5 minutos — guion y prompt

Entregable obligatorio: **vídeo ≤ 5 min** enseñando lo construido. Grabar el **3 sep**,
cuando ya haya P&L de varias sesiones.

Repo público: https://github.com/Ander-IbBi/alpaca-options-agent

---

## Antes de grabar

- [ ] Cuenta paper con posiciones reales y varios ciclos en el journal.
- [ ] `uv run streamlit run app/streamlit_app.py` abierto, con curva de equity visible.
- [ ] Una terminal con `uv run python scripts/broker_report.py` ya ejecutado.
- [ ] Cursor abierto con el MCP de Alpaca, para enseñarlo 10 segundos.
- [ ] Cerrar notificaciones, Discord, correo. Pantalla limpia.
- [ ] **Las keys no pueden salir en pantalla.** Cuidado con `.env` y con el terminal.

Duración objetivo: **4:30**, nunca 5:01. El formulario corta.

---

## Guion cronometrado

### 0:00 – 0:25 · Quién eres y el problema

> "Soy Ander. Casi todos los agentes de trading que vas a ver esta semana intentan
> adivinar la dirección del mercado. El mío hace lo contrario: da por hecho que no
> sabe hacia dónde va SPY, y se dedica a **dar forma al riesgo** de una cartera larga
> usando opciones."

**Pantalla:** título del proyecto o el README en GitHub.

### 0:25 – 1:10 · Qué hace y por qué opciones

> "La estructura es un **collar**: 100 acciones de SPY, un put comprado que pone un
> suelo, y un call vendido que paga ese put. El riesgo está definido por los dos lados.
> El put es el suelo; las acciones cubren el call, así que nunca hay un short desnudo.
>
> Lo importante no es abrir el collar, es **gestionarlo**. Un collar que abres y dejas
> quieto toma una sola decisión en toda la semana."

**Pantalla:** la tabla de gestión del README, o el diagrama del ciclo.

### 1:10 – 2:40 · Demo: el ciclo en vivo (el bloque que más pesa)

Ejecuta un ciclo delante de la cámara desde Streamlit.

> "Cada ciclo hace esto. Primero lee la cuenta con la Trading API. Después la vuelve a
> leer con la **CLI de Alpaca** — otro cliente, otra autenticación — y compara. Si los
> dos no coinciden en la cuenta o en las posiciones, el agente **no opera**: prefiere
> no hacer nada a operar sobre una foto vieja del libro.
>
> Luego decide. Aquí el collar ya está puesto, así que evalúa la escalera de gestión:
> ¿el call corto se ha metido en dinero? ¿queda poco para el vencimiento? ¿el put ya
> ha doblado? Fíjate en que **incluso cuando decide esperar, dice qué ha mirado**.
>
> Si hubiera propuesta, pasa por la capa de riesgo, que es código, no el modelo. El LLM
> explica y como mucho aplica un veto suave; no puede aprobar lo que el riesgo rechaza,
> y si el LLM se cae el ciclo continúa."

**Pantalla:** Streamlit → botón "Run one cycle" → notas, cross-check, riesgo, JSON.

Si tienes un roll real en el journal, **enséñalo**: es la mejor prueba de gestión.

### 2:40 – 3:40 · Rendimiento y trazabilidad

> "Esta es la curva de equity que da la propia API de Alpaca, no un número que me
> invento yo. Y este es el journal: cada ciclo de la semana, en JSON, con la propuesta,
> la decisión de riesgo y lo que se envió. Se puede auditar entero.
>
> Los límites son duros: tope por orden, suelo de equity y un cortacircuitos de pérdida
> diaria que apaga el agente. Con esto ha operado desatendido toda la semana."

**Pantalla:** curva de equity → tabla del journal → `.env.example` con los límites.

### 3:40 – 4:15 · El stack de Alpaca

> "Trading API para ejecutar, siempre en paper. CLI como verificación independiente
> antes de cada orden. Y el MCP, que es la ventana del LLM a la cuenta: lo uso para
> investigar cadenas y greeks y para supervisar los fills.
>
> Lo que **no** hago es ejecutar por MCP, a propósito: toda orden tiene que pasar por la
> capa de riesgo, y una tool call es el modelo mandando una orden directa. Ese camino no
> existe en este repo."

**Pantalla:** `scripts/broker_report.py` con SDK y CLI en paralelo → MCP en Cursor.

### 4:15 – 4:30 · Cierre

> "Código público, tests que corren sin claves ni red, y cero rutas hacia live trading.
> Con más tiempo llevaría la misma máquina a más subyacentes y a cubrir por delta de
> cartera en vez de por estructura fija. Gracias."

---

## Errores que hunden un vídeo de hackathon

- Pasar 2 minutos explicando qué es un collar. Los jueces lo saben.
- Enseñar solo código. Tiene que verse **la cuenta operando**.
- Decir "el agente decide" sin enseñar una decisión.
- Pasarse de 5 minutos.
- Que se vea una API key.

---

## Prompt para NotebookLM

Sube primero estas fuentes al notebook:

1. `README.md` del repo
2. Este archivo (`video-guion.md`)
3. `docs/mcp-and-cli.md`
4. Un export reciente del journal (`data/journal/agent.jsonl`)

Y usa este prompt:

```text
Eres el guionista de un vídeo de presentación de 5 minutos para el Alpaca AI Trading
Agents Hackathon (track Options Alpha Agents). Trabaja SOLO con las fuentes subidas.

PROYECTO
Un agente autónomo que opera exclusivamente en Alpaca paper trading. Mantiene un
collar de riesgo definido sobre SPY: 100 acciones, un put largo cerca de delta -0.20
como suelo, y un call corto cuya prima paga ese put. El call siempre está cubierto por
las acciones; los shorts desnudos están bloqueados por código.
Lo diferencial es la GESTIÓN: en cada ciclo el agente rola el call corto hacia arriba
si se mete en dinero, rola el collar si se acerca el vencimiento, y monetiza el put si
ya ha doblado su valor. Cuando no hace nada, deja escrito qué comprobó.
Antes de cada orden verifica la cuenta con un segundo cliente (la CLI de Alpaca) y se
niega a operar si los dos clientes no coinciden.

AUDIENCIA
Jueces técnicos de Alpaca y lablab. Saben qué es un collar, un delta y un roll. No hay
que explicar conceptos básicos de opciones.

CRITERIOS QUE PUNTÚAN, por orden
1. Rendimiento de P&L en la cuenta paper
2. Implementación técnica (Trading API, MCP, CLI)
3. Creatividad y originalidad
4. Presentación y ejecución

QUÉ QUIERO
Un guion narrado de 4 minutos y 30 segundos como máximo, en español neutro, dividido en
bloques con marca de tiempo. Para cada bloque:
- el texto exacto a narrar, en frases cortas y decibles en voz alta
- entre corchetes, qué se ve en pantalla en ese momento

REGLAS
- Máximo 4:30 de narración. Prioriza la demo del ciclo en vivo: dale el bloque más largo.
- Tono técnico, directo y sobrio. Sin hype, sin "revolucionario", sin emojis.
- Menciona explícitamente que todo es PAPER TRADING y que no hay ruta a live.
- No es consejo financiero y no se promete rentabilidad.
- No inventes cifras de P&L, nombres de archivos ni funciones: si un dato no está en las
  fuentes, deja un marcador como [CIFRA DE P&L].
- Deja claro que la capa de riesgo es código y que el LLM solo explica y puede vetar de
  forma suave, nunca aprobar lo que el riesgo rechaza.
- Termina con una frase de cierre de 15 segundos.

Devuelve solo el guion, sin preámbulo.
```

### Si NotebookLM genera el audio o el vídeo directamente

Añade al final del prompt:

```text
Genera un único narrador, ritmo pausado y sin conversación entre dos voces. Es una
demo de producto, no un pódcast.
```
