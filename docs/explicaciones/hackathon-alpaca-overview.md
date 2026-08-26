# Dónde te estás metiendo: hackathon Alpaca × lablab

Texto para releer en frío. Lo operativo está en [`docs/hackathon/`](../hackathon/overview.md).

## El evento

lablab organiza hackathons de IA de una semana, online, con entrega por plataforma
(repo, vídeo, demo, slides). Alpaca patrocina este porque quiere ver **agentes que
mandan órdenes de verdad** a su brokerage, en entorno paper: dinero simulado, mercado real.

El track es **Options Alpha Agents**. Eso acota el terreno: no ganas con un bot que
compra SPY. Tienes que usar **opciones**. “Alpha” aquí significa extraer o proteger
valor de forma sistemática, no adivinar el precio.

## Tres interfaces, un mismo bróker

Con tus keys de Alpaca puedes operar de tres formas:

1. **Trading API** (`alpaca-py`) — lo que usa el producto. Reproducible: un juez clona
   el repo y lo corre.
2. **MCP server** — traduce tools de un LLM a llamadas HTTP de Alpaca. Es lo que hace que
   Cursor o Claude puedan consultar tu cuenta hablando.
3. **CLI** — lo mismo desde la terminal, con paper por defecto.

Las tres apuntan a **la misma cuenta**. Por eso el repo fuerza `paper=True` y aborta si
alguien pone `ALPACA_LIVE_TRADE=true`.

## Paper trading

Una cuenta paralela con ~100k simulados y cotizaciones reales. Las ejecuciones no son
idénticas a live (relleno optimista, opciones poco líquidas), así que sirve para
demostrar el sistema, no para estimar un Sharpe creíble.

El evento pide una cuenta paper **nueva** para que el P&L que ven los jueces sea el de
la semana.

## Qué hace nuestro agente

Un **overlay de cobertura**: sobre un book de acciones, compra puts para limitar la caída.

- **Intuición:** si tienes acciones y el mercado cae, pierdes. Un put paga justo ahí.
- **Riesgo definido:** comprando un put, lo máximo que pierdes es la prima. Por eso el
  risk layer prohíbe vender opciones desnudas, donde la pérdida no tiene techo.
- **Tamaño:** un contrato cubre 100 acciones. Con 300 acciones, 3 contratos; nunca más.
- **Strike y vencimiento:** un strike ~5% por debajo del spot y 21–60 días hasta el
  vencimiento. Más cerca del dinero cubre mejor pero cuesta más.

Matemáticamente, con $N$ acciones y strike $K$, por debajo de $K$ tu resultado deja de
seguir a $S_T$ y queda anclado en $N \cdot K$ menos la prima pagada. Cambias una cola
de pérdidas por un coste conocido.

## Qué es un “agente” para los jueces

No es un chat en el IDE. Es un programa que:

1. **Observa** — cuenta, reloj de mercado, cadena de opciones.
2. **Razona** — reglas y, opcionalmente, un LLM.
3. **Actúa** — manda órdenes, siempre a través de una capa de riesgo que no puede desactivar.
4. **Se explica** — journal JSONL y dashboard.

Ese cuarto punto es el que suele faltar a los demás y el que se ve en un vídeo de 5 minutos.

## Errores tontos que cuestan la entrega

- No crear equipo en lablab (aunque vayas solo).
- Repo privado.
- Vídeo como enlace cuando piden archivo MP4.
- Demo caído el día del corte.
- Cuenta paper sin **opciones habilitadas**: revísalo antes del viernes.
- Dejar `.env` en el repo.

Sigue por [guía del repo](../guia-del-repo.md) para ver cómo se traduce todo esto en código.
