# Inversión Evolutiva — Documentación Completa

## Tabla de contenidos

1. [Objetivo del sistema](#1-objetivo-del-sistema)
2. [Stack tecnológico](#2-stack-tecnológico)
3. [Arquitectura general](#3-arquitectura-general)
4. [Estructura de los agentes](#4-estructura-de-los-agentes)
5. [Pipeline de decisión A → B → C](#5-pipeline-de-decisión-a--b--c)
6. [Cuándo y cómo se consulta el LLM](#6-cuándo-y-cómo-se-consulta-el-llm)
7. [Monitor de trades — SL/TP y trailing stop](#7-monitor-de-trades--sltp-y-trailing-stop)
8. [Agente Juez — ciclo evolutivo diario](#8-agente-juez--ciclo-evolutivo-diario)
9. [Mutación genética](#9-mutación-genética)
10. [Creación y eliminación de agentes](#10-creación-y-eliminación-de-agentes)
11. [Base de datos — PostgreSQL](#11-base-de-datos--postgresql)
12. [Google Sheets — registro y trazabilidad](#12-google-sheets--registro-y-trazabilidad)
13. [Dashboard Streamlit](#13-dashboard-streamlit)
14. [Automatización — GitHub Actions](#14-automatización--github-actions)
15. [Variables de configuración](#15-variables-de-configuración)
16. [Flujo completo día a día](#16-flujo-completo-día-a-día)
17. [Scripts utilitarios](#17-scripts-utilitarios-scripts)
18. [Estructura del repositorio](#18-estructura-del-repositorio)

---

## 1. Objetivo del sistema

**Inversión Evolutiva** es un laboratorio de trading algorítmico que aplica algoritmos genéticos al mercado de divisas EUR/USD. El sistema mantiene una población de 10 agentes de software que compiten entre sí para determinar cuáles estrategias de trading son más rentables ajustadas al riesgo.

Los agentes **no son configurados manualmente**: nacen con parámetros aleatorios o heredados, operan de forma autónoma durante el día y cada tarde son evaluados. Los peores son eliminados y los mejores reproducen descendencia con mutaciones estocásticas. Con el tiempo, la población converge hacia estrategias más eficientes sin intervención humana.

**Principios fundamentales:**

- Cada agente opera con capital virtual real en condiciones de mercado reales (precios EUR/USD de Yahoo Finance).
- La competencia es justa: todos arrancan con el mismo capital cada ciclo evolutivo.
- El broker es simulado: no se ejecutan órdenes en ningún bróker externo. El P&L se calcula sobre variaciones reales del tipo de cambio.
- El sistema es completamente autónomo: corre sin intervención humana de lunes a viernes.

---

## 2. Stack tecnológico

### Backend

| Herramienta | Rol |
|---|---|
| **Python 3.11** | Lenguaje principal de todos los agentes, monitor y motor evolutivo |
| **PostgreSQL (Supabase)** | Base de datos principal — estado de agentes, operaciones, logs del Juez. Transaction Pooler (PgBouncer, puerto 6543) para compatibilidad IPv4 con GitHub Actions |
| **DeepSeek API** (`deepseek-reasoner`) | LLM para razonamiento táctico (señales ambiguas) y veredictos del Juez. Modelo configurado en env var `DEEPSEEK_MODEL` |
| **Yahoo Finance** | Precios OHLCV de EUR/USD en tiempo real — sin API key, sin límite |
| **Finnhub API** | Noticias forex y calendario económico |
| **Alpha Vantage** | Indicadores técnicos (legado, usado puntualmente) |
| **gspread + Google Sheets API** | Registro de operaciones y árbol genealógico de agentes |

### Frontend / Observabilidad

| Herramienta | Rol |
|---|---|
| **Streamlit** | Dashboard web en tiempo real con 6 pestañas |
| **Plotly** | Gráficas interactivas (precio, ROI, evolución) |
| **Next.js** | App móvil complementaria (directorio `mobile-app/`) |

### Infraestructura

| Herramienta | Rol |
|---|---|
| **GitHub Actions** | Runtime de ejecución de los 4 workflows (monitor, juez, health check, backfill). Lógica intacta — solo el disparo se delega a cron-job.org desde Sesión 12 |
| **cron-job.org** | Scheduler externo gratuito que dispara los workflows vía `workflow_dispatch` API con precisión ±5 seg, reemplazando el cron interno de GH (poco fiable, retrasos crónicos de horas) |
| **Streamlit Community Cloud** | Hosting del dashboard en `inversion-evolutiva.streamlit.app` |
| **Vercel** | Hosting de la app móvil Next.js en `https://mobile-app-smoky-phi.vercel.app` |
| **GitHub Secrets** | Almacenamiento seguro de todas las credenciales |

---

## 3. Arquitectura general

```
┌─────────────────────────────────────────────────────────────────┐
│  cron-job.org (scheduler externo · America/Bogota · ±5 seg)     │
│  4 cronjobs → HTTPS POST → GitHub workflow_dispatch API         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  GITHUB ACTIONS (runtime)                       │
│                                                                 │
│  trade_monitor.yml          judge_daily.yml                     │
│  Cada 15 min, L-V           10:45 pm Bogotá, L-V                │
│  ┌──────────────────┐       ┌──────────────────────┐            │
│  │  TradeMonitor    │       │  Agente Juez         │            │
│  │  - Verifica SL/TP│       │  - Evalúa fitness    │            │
│  │  - Trailing stop │       │  - Elimina bottom 5  │            │
│  │  - Abre pos. new │       │  - Crea 5 hijos      │            │
│  └────────┬─────────┘       │  - Redistribuye cap. │            │
│           │                 │  - Razona con LLM    │            │
│           │                 └──────────┬───────────┘            │
└───────────┼──────────────────────────┼────────────────────────-┘
            │                          │
            ▼                          ▼
┌───────────────────────────────────────────────────────────────┐
│                  POSTGRESQL (Supabase)                         │
│   agentes | operaciones | logs_juez | ranking_historico |     │
│   estrategias_exitosas                                        │
└────────────────────────┬──────────────────────────────────────┘
                         │
            ┌────────────┴─────────────┐
            ▼                          ▼
   ┌─────────────────┐       ┌──────────────────────┐
   │  STREAMLIT      │       │  GOOGLE SHEETS        │
   │  Dashboard      │       │  Agentes | Operaciones│
   │  Tiempo real    │       │  (registro histórico) │
   └─────────────────┘       └──────────────────────┘
```

### Fuentes de datos externas

```
Yahoo Finance ──► OHLCV EUR/USD (precios reales, sin key)
Finnhub      ──► Noticias forex + calendario económico
DeepSeek     ──► LLM para señales ambiguas y veredictos evolutivos
```

---

## 4. Estructura de los agentes

### Identificación

Cada agente tiene un ID único con formato `YYYY-MM-DD_NN` donde `YYYY-MM-DD` es su fecha de nacimiento y `NN` es su número de orden ese día (01, 02, ..., 10).

Ejemplos: `2026-05-11_01`, `2026-05-11_07`

### Genoma — cuatro bloques de parámetros

Cada agente lleva cuatro diccionarios JSON que constituyen su "ADN". Estos parámetros evolucionan por mutación y crossover entre generaciones.

#### `params_tecnicos` — Sub-agente A

| Gen | Rango | Descripción |
|---|---|---|
| `rsi_periodo` | 5–50 (entero) | Período de cálculo del RSI |
| `rsi_sobrecompra` | 55–90 | Umbral RSI para señal SELL |
| `rsi_sobreventa` | 10–45 | Umbral RSI para señal BUY |
| `ema_rapida` | 3–29 (entero) | Período de la EMA rápida |
| `ema_lenta` | 10–50 (entero) | Período de la EMA lenta (siempre > EMA rápida) |
| `macd_rapida` | 5–20 (entero) | Período rápido del MACD |
| `macd_lenta` | 15–40 (entero) | Período lento del MACD |
| `macd_senal` | 3–15 (entero) | Período de la línea de señal MACD |
| `peso_rsi` | 0.1–0.7 | Peso del RSI en la señal ponderada (∑pesos = 1) |
| `peso_ema` | 0.1–0.7 | Peso del cruce de EMAs |
| `peso_macd` | 0.1–0.7 | Peso del histograma MACD |

#### `params_macro` — Sub-agente B

| Gen | Rango | Descripción |
|---|---|---|
| `peso_noticias_alto` | 0.3–0.9 | Peso de eventos de alto impacto (NFP, CPI, FOMC) |
| `peso_noticias_medio` | 0.05–0.4 | Peso de eventos de impacto medio |
| `peso_noticias_bajo` | 0.01–0.2 | Peso de eventos de bajo impacto |
| `umbral_sentimiento_compra` | 0.55–0.85 | Sentimiento mínimo para recomendar BUY |
| `umbral_sentimiento_venta` | 0.15–0.45 | Sentimiento máximo para recomendar SELL |
| `ventana_noticias_horas` | 1–8 (entero) | Horizonte temporal del análisis macro |
| `peso_total_macro` | 0.2–0.7 | Peso global de la señal macro en la decisión final |

#### `params_riesgo` — Sub-agente C

| Gen | Rango | Descripción |
|---|---|---|
| `stop_loss_pct` | 0.5%–5% | Fallback de SL cuando no hay ATR (legado) |
| `take_profit_pct` | 1%–10% | Fallback de TP cuando no hay ATR (legado) |
| `max_drawdown_diario_pct` | 3%–20% | Límite de pérdida diaria por agente |
| `capital_por_operacion_pct` | 20%–80% | Porcentaje del capital a utilizar por operación |
| `umbral_confianza_minima` | 0.45–0.85 | Confianza mínima para emitir BUY o SELL |
| `peso_tecnico_vs_macro` | 0.30–0.75 | Peso de la señal técnica sobre la macro (complemento = peso macro) |

#### `params_smc` — Smart Money Concepts + ATR

| Gen | Rango | Descripción |
|---|---|---|
| `fvg_min_pips` | 2–15 | Tamaño mínimo de Fair Value Gap para considerarlo válido |
| `ob_impulse_pips` | 5–20 | Tamaño mínimo del impulso para detectar un Order Block |
| `range_spike_multiplier` | 1.2–3.0 | Multiplicador de rango para detectar spike de volatilidad |
| `risk_reward_target` | 1.5–4.0 | Relación riesgo:beneficio objetivo (TP = SL × R:R) |
| `macro_quarantine_minutes` | 30–120 | Minutos de cuarentena antes de eventos críticos (NFP, CPI) |
| `risk_pct_per_trade` | 1%–2% | Porcentaje del equity en riesgo por operación |
| `peso_fvg` | 0.05–0.50 | Peso del FVG en la señal ponderada |
| `peso_ob` | 0.05–0.50 | Peso del Order Block en la señal ponderada |
| `atr_factor` | 0.8–3.0 | Multiplicador del ATR para calcular el SL dinámico |
| `trailing_activation_pips` | 5–40 | Pips de ganancia para activar el trailing stop |
| `trailing_distance_pips` | 5–25 | Distancia del trailing stop al precio extremo favorable |
| `atr_period` | 7–21 (entero) | Período del ATR (Average True Range) de Wilder |

### Capital

- **Capital inicial:** $10 USD por agente ($100 USD total en el pool).
- **Capital actual:** fluctúa con el P&L de cada operación.
- **Redistribución diaria:** al final de cada ciclo evolutivo el Juez suma el pool total y lo divide en partes iguales entre los 10 agentes activos. El mérito individual es lo único que determina la supervivencia, no el capital acumulado.

---

## 5. Pipeline de decisión A → B → C

Cada vez que el monitor de trades evalúa un agente libre, ejecuta este pipeline:

```
Yahoo Finance
    │
    ▼
[OHLCV 15 min / 1h]
    │
    ├──► Sub-agente A (Técnico)
    │         RSI + EMA + MACD + FVG + OB + Range Spike
    │         → señal_tecnico {recomendacion, confianza, indicadores}
    │
    ├──► Sub-agente B (Macro)            ◄── Finnhub (noticias + calendario)
    │         LLM analiza eventos macro
    │         → señal_macro {recomendacion, confianza, sentimiento_score}
    │
    └──► Sub-agente C (Riesgo/Decisión)
              Combina A + B con pesos genéticos
              Calcula SL (OB → FVG → ATR → % fallback)
              Calcula TP (SL × R:R target)
              Position sizing dinámico → nocional en USD
              → RiskDecision {accion_final, stop_loss, take_profit, nocional_usd}
                    │
                    ▼
              PostgreSQL → INSERT operaciones
              Google Sheets → log_operation()
```

### Sub-agente A — Análisis Técnico

Calcula scores individuales para 5 indicadores y los combina con ponderación genética:

| Indicador | Señal BUY | Señal SELL | Peso |
|---|---|---|---|
| RSI | < `rsi_sobreventa` | > `rsi_sobrecompra` | `peso_rsi` |
| Cruce EMA | EMA rápida > EMA lenta | EMA rápida < EMA lenta | `peso_ema` |
| MACD histograma | > 0.00005 | < -0.00005 | `peso_macd` |
| FVG activo | Dirección BULL | Dirección BEAR | `peso_fvg` |
| Order Block | Dirección BULL | Dirección BEAR | `peso_ob` |

Si hay un **range spike** (rango actual > MA20 del rango × `range_spike_multiplier`), la confianza de la señal dominante se amplifica ×1.15 (máx. 0.95).

La señal ponderada emite BUY si `score_buy > score_sell` y `score_buy > 0.45`. Igual para SELL. Si ninguno supera el umbral, emite HOLD.

### Sub-agente B — Análisis Macro

1. Descarga del calendario económico (Finnhub) los eventos de las próximas `ventana_noticias_horas` horas.
2. Descarga titulares de noticias forex recientes.
3. Envía todo al LLM DeepSeek y recibe un JSON con `recomendacion`, `confianza` y `sentimiento_score` (-1.0 a +1.0).
4. Aplica los umbrales genéticos del agente: si `sentimiento_score` normalizado ≥ `umbral_sentimiento_compra` → BUY; si ≤ `umbral_sentimiento_venta` → SELL.

### Sub-agente C — Riesgo y Decisión Final

**Combinación de señales:**
- Si Técnico y Macro coinciden: promedio ponderado por `peso_tecnico_vs_macro`.
- Si uno emite HOLD: prevalece la señal concreta del otro.
- Si hay conflicto BUY vs SELL: gana la señal más fuerte solo si supera confianza 0.75; de lo contrario HOLD.
- Si la confianza combinada < `umbral_confianza_minima`: forzar HOLD.

**Jerarquía de Stop Loss (en orden de prioridad):**
1. **Order Block estructural** — nivel del OB no mitigado (fuente más fuerte).
2. **Fair Value Gap** — nivel del FVG no rellenado.
3. **ATR dinámico** — `precio ± (ATR × atr_factor)`. Rango: mín. 5 pips, máx. 50 pips.
4. **Porcentaje fijo** — `stop_loss_pct` del precio de entrada (fallback si ATR = 0).

**Take Profit:** siempre `TP = distancia_SL × risk_reward_target`.

**Position sizing dinámico — nocional en USD:**
```
lotes        = (equity × risk_pct_per_trade) / (sl_pips × $0.10/pip)
nocional_usd = lotes × 1000 × precio_entrada
nocional_usd = min(nocional_usd, equity × 50)   # techo de apalancamiento 50:1
risk_pct forzado al rango [1%, 2%]               # límites inmutables

Ejemplo: equity=$10, risk=2%, SL=36 pips, precio=1.16225
  lotes        = (10 × 0.02) / (36 × 0.10) = 0.0556
  nocional_usd = 0.0556 × 1000 × 1.16225 = $64.60
  Si SL tocado: pérdida = $64.60 × (36 pips × 0.0001 / 1.16225) ≈ $0.20 = 2% equity ✓
```

El campo `capital_usado` en la BD almacena el **nocional en USD** (no lotes). El P&L se calcula como `(precio_salida - precio_entrada) / precio_entrada × nocional_usd`, lo que produce dólares reales proporcionales al movimiento del par. El LLM no puede sobrescribir este valor; el sizer lo calcula siempre por heurística.

---

## 6. Cuándo y cómo se consulta el LLM

El LLM **DeepSeek** (`deepseek-chat`) se consulta en tres momentos distintos del sistema:

### 6.1 Sub-agente A — validación por zona ambigua

**Cuándo:** Únicamente cuando la confianza de la señal ponderada cae en el rango `[0.45, 0.65]`.

**Qué se envía:** Todos los valores de indicadores (RSI, EMA, MACD, FVG, OB, range spike) con sus scores individuales y la señal ponderada provisional.

**Qué se espera:** JSON con `{"recomendacion": "BUY"|"SELL"|"HOLD", "confianza": 0.0-1.0, "razon": "..."}`.

**Fallback:** Si el LLM no está disponible, se usa directamente la señal heurística ponderada.

### 6.2 Sub-agente B — análisis macro (siempre)

**Cuándo:** En cada ciclo de trading, siempre que haya eventos o titulares disponibles.

**Qué se envía:** Eventos del calendario económico con impacto alto (NFP, CPI, FOMC, PIB, PMI) y titulares recientes de forex.

**Qué se espera:** JSON con `{"recomendacion", "confianza", "sentimiento_score", "eventos_clave", "razon"}`.

**Fallback:** Heurística local: si hay muchos eventos de alto impacto → HOLD con confianza baja.

### 6.3 Sub-agente C — confirmación final

**Cuándo:** Cuando la decisión preliminar es BUY o SELL (no para HOLD).

**Qué se envía:** Señales de A y B completas, precio actual, SL/TP calculados, nocional USD pre-calculado y la acción preliminar con su confianza.

**Qué se espera:** JSON con `{accion_final, confianza_final, stop_loss, take_profit, razonamiento}`. El LLM puede confirmar o ajustar la acción, confianza y niveles de SL/TP, pero **no el tamaño de posición** (el sizer ya lo calculó correctamente y el LLM no lo incluye en su respuesta).

**Fallback:** Se usa la decisión heurística calculada localmente.

### 6.4 Agente Juez — veredicto evolutivo

**Cuándo:** Una vez al día, después de que el motor genético completa la selección/eliminación/reproducción.

**Qué se envía:** Tabla completa de supervivientes y eliminados con su fitness, ROI, win rate y parámetros clave; lista de nuevos agentes con sus padres.

**Qué se espera:** JSON con `veredicto_general`, análisis de por qué fallaron los eliminados, expectativas para los nuevos y recomendaciones de parámetros.

**Fallback:** El Juez genera un veredicto automático sin LLM con la información de fitness y ROI disponible.

### Configuración técnica del cliente LLM

```python
# base_agent.py
_MODEL  = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")   # default: deepseek-chat
                                                          # en producción: deepseek-reasoner
_client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)
model       = _MODEL
temperature = 0.1     # respuestas muy deterministas
max_tokens  = 512
timeout     = 30 seg
```

> **Modelo activo en producción:** `deepseek-reasoner` (configurado en `.env` y en GitHub Secrets como `DEEPSEEK_MODEL`). El API de DeepSeek puede servir este modelo bajo el alias `deepseek-v4-flash` en algunas respuestas — es el mismo endpoint.

---

## 7. Monitor de trades — SL/TP y trailing stop

**Archivo:** `cron/trade_monitor.py`
**Ejecución:** Cada 15 minutos, lunes a viernes. El primer ciclo corre a las 1:30 am Bogotá (06:30 UTC) y el último a las 10:30 pm Bogotá (03:30 UTC del día siguiente UTC). La ventana de apertura de nuevas posiciones cubre todo ese rango (1:30 am – 11:00 pm Bogotá nominal). El monitor sigue vigilando SL/TP de posiciones abiertas durante todo el horario y se apaga 15 minutos antes del cierre forzoso del Juez.

### Ciclo de cada ejecución

```
Para cada agente:
  ┌─ ¿Tiene posición abierta?
  │
  ├── SÍ → Verificación intra-vela de SL/TP (desde Sesión 13):
  │         1. Cargar timestamp_ultima_verificacion de la operación
  │            (o timestamp_entrada si nunca se verificó).
  │         2. Descargar OHLC 1-minuto de Yahoo Finance desde ese
  │            timestamp hasta ahora (≈15 velas por ciclo).
  │         3. Iterar velas en orden cronológico. Por cada vela:
  │              a) Chequear si high/low tocó SL o TP usando el SL
  │                 ANTES de aplicar trailing en esa vela.
  │                 - HIT_SL: cerrar exactamente al precio del SL.
  │                 - HIT_TP: cerrar exactamente al precio del TP.
  │                 - Ambos en la misma vela → SL gana (conservador).
  │                 timestamp_salida = timestamp real de la vela.
  │              b) Sin hit → aplicar trailing usando el extremo
  │                 favorable de la vela (low SELL / high BUY).
  │         4. Si la operación cierra:
  │              - UPDATE operaciones (estado=cerrada, pnl, precio_salida,
  │                timestamp_salida del momento exacto de la mecha)
  │              - UPDATE agentes (capital_actual, roi_total, ops_ganadoras)
  │              - Actualizar Google Sheets
  │         5. Si no cierra: persistir sl_dinamico, precio_extremo_favorable
  │            y timestamp_ultima_verificacion = última vela procesada.
  │         6. Fallback automático: si Yahoo no devuelve velas (mercado
  │            cerrado, error API), cae al check legacy con snapshot único
  │            para no bloquear el ciclo. Log warning en ese caso.
  │
  └── NO → Evaluar si abrir nueva posición:
            1. ¿Estamos en horario de trading? (1:30 am – 11:00 pm Bogotá)
            2. ¿Cuarentena macro? (eventos críticos próximos)
            3. Ejecutar pipeline A → B → C
            4. Si decisión = BUY/SELL → INSERT en operaciones
```

### Por qué la verificación intra-vela importa para la evolución

Antes de Sesión 13 el monitor comparaba un **único snapshot** del precio cada 15 min contra SL/TP. Cualquier mecha entre dos ciclos era invisible: un TP tocado y rebotado nunca cerraba el trade. Eso introducía un sesgo sistemático en el fitness:

- TPs ambiciosos parecían malos (nunca "llegaban") → ADN sesgado hacia R:R bajo.
- SLs ajustados parecían buenos (nunca "saltaban") → ADN frágil en producción real.
- Los genes `risk_reward_target`, `atr_factor` y `trailing_*` se calibraban contra un mercado ficticio.

Con OHLC 1-minuto, cada ciclo de 15 min cubre las 15 velas anteriores con precisión real. El precio de cierre cuando hay hit es **exactamente** el nivel SL/TP (lo que devolvería un broker real con una orden stop/limit), y el `timestamp_salida` refleja la mecha exacta. El fitness ahora se calcula sobre cierres honestos, y la evolución selecciona ADN portable a un broker real.

### Convención conservadora ante ambigüedad intra-vela

Si una sola vela toca SL y TP simultáneamente (gap o vela muy amplia), `check_sl_tp_intrabar` devuelve **HIT_SL**. Es la convención estándar de backtesters serios (Backtrader, vectorbt) — ante ambigüedad sobre el orden intra-vela, asumir el peor caso para el trader, evitando inflar el fitness con casos que en producción real podrían haber cerrado en SL.

Por la misma razón, dentro de cada vela se chequea SL/TP **antes** de aplicar el trailing. Aplicar trailing primero (con el extremo favorable) y después chequear SL con el extremo adverso de la misma vela equivaldría a "ver el futuro" para apretar el SL — un sesgo a favor del trader.

### Trailing stop dinámico

El trailing stop protege ganancias sin limitar el potencial alcista:

1. Se activa cuando la posición acumula `trailing_activation_pips` de ganancia (gen del agente, default 15 pips).
2. Una vez activo, el SL se mueve a `precio_extremo_favorable - trailing_distance_pips` (default 10 pips).
3. El SL **nunca retrocede**: solo puede mejorar (moverse a favor).
4. Desde Sesión 13 el trailing se aplica **vela a vela** dentro del verificador intra-bar usando el extremo favorable de cada vela (low para SELL, high para BUY) en vez del snapshot único cada 15 min. Resultado: ratcheo más preciso, casi idéntico al de un broker tick-by-tick.

### Cierre al final del día (EOD)

El workflow `judge_daily.yml` ejecuta `trade_monitor --force-close-all` a las **10:45 pm Bogotá (03:45 UTC)**, exactamente 15 minutos antes del Juez. Esto cierra todas las posiciones abiertas al precio de mercado actual para que el Juez evalúe con P&L definitivo del día. El gap de 15 minutos es un buffer de seguridad por si el cierre se reintenta o tarda.

### Guardia EOD defensiva (red de seguridad ante retrasos)

Históricamente los crons programados de GitHub Actions **no garantizaban disparo puntual**: podían retrasarse horas durante picos de carga del runner. Desde Sesión 12 (27-may-2026) el disparo se hace vía **cron-job.org** (precisión ±5 seg), eliminando ese problema en condiciones normales. La guardia EOD se mantiene **como red de seguridad** por si alguna vez el scheduler externo se cae o si se ejecuta un rollback temporal al cron interno de GH.

`trade_monitor.py` incluye una función `_eod_guard()` que corre al inicio de **cada** ciclo de 15 minutos:

```
Para cada ciclo del monitor:
  1. _eod_guard():
       - Calcula el inicio del día de trading UTC actual (TRADING_START_TIME_UTC).
       - Si existen posiciones BUY/SELL abiertas con
         timestamp_entrada < ese inicio → son del día anterior.
       - Llama internamente a force_close_all() para cerrarlas.
  2. (continúa el ciclo normal: SL/TP, trailing, nuevas posiciones)
```

**Consecuencia:** aunque `judge_daily.yml` se retrase X horas, el primer monitor que despierte después del cierre del día anterior cierra las posiciones huérfanas automáticamente. La ventana máxima de exposición se reduce a 15 minutos (el intervalo entre monitores).

Para que esto funcione en la "ventana ciega" de 03:30 – 06:30 UTC (11pm – 1:30am Bogotá, antes sin monitor), `trade_monitor.yml` incluye un cron adicional `*/15 4-6 * * 2-6` que despierta el monitor cada 15 minutos en ese rango.

---

## 8. Agente Juez — ciclo evolutivo diario

**Archivo:** `agents/judge_agent.py` + `evolution/evolution_engine.py`
**Ejecución:** 11:00 pm hora Bogotá (04:00 UTC del día siguiente), lunes a viernes. El cierre forzoso de posiciones se ejecuta a las 10:45 pm Bogotá (03:45 UTC) dentro del mismo workflow `judge_daily.yml`.

### Secuencia del ciclo evolutivo

```
1. EVALUACIÓN DE FITNESS (Calmar Ratio Proxy)
   ─────────────────────────────────────────
   fitness = ROI_total / (max_drawdown + 1)
   Penalidad: -0.5 si avg_ops_dia > 3 Y win_rate < 50%

2. RANKING + FILTRO DE ELEGIBILIDAD (Periodo de Gracia Operativa)
   ─────────────────────────────────────────────────────────────
   Ordenar 10 agentes por fitness DESC.
   Desempate por juventud: fecha_nacimiento DESC, id DESC.

   Filtrar agentes INMUNES (no elegibles para eliminación esa tarde):
     - operaciones_total == 0   (nunca ha cerrado una operación)
     - AND edad < GRACE_PERIOD_DAYS días HÁBILES (lun-vie)
   Los inmunes mantienen estado 'activo' automáticamente.

3. CUOTA DINÁMICA DE ELIMINACIÓN
   ──────────────────────────────
   Sobre los agentes ELEGIBLES (no inmunes):
     - Se ordenan por (fitness ASC, fecha_nacimiento ASC, id ASC)
       → los primeros candidatos son veteranos rezagados.
     - Solo se eliminan agentes con fitness_score <= 0 (negativo o cero).
     - n_eliminate = min(N_ELIMINATE=5, len(eliminables))

   La cuota deja de ser rígida: NUNCA se elimina a un agente veterano con
   fitness > 0 solo para cumplir con la cifra de 5 eliminaciones.

4. ¿CICLO SUSPENDIDO?
   ───────────────────
   Si la cuota dinámica resulta = 0 (todos los elegibles son rentables o
   todos los activos están en Periodo de Gracia):
     - NO se elimina ningún agente
     - NO se crean nuevos agentes
     - NO se redistribuye capital (la población queda intacta)
     - Se registra UN único log 'evaluacion_diaria' con
       cycle_suspended=true y suspension_reason explícito
     - NO se invoca al LLM (se ahorra gasto de tokens en días HOLD)
     - El día siguiente la población joven puede acumular más datos

   Si la cuota > 0, continúa con los pasos 5–9.

5. FORZADO DE DIVERSIDAD GENÉTICA
   ───────────────────────────────
   Se calcula el coeficiente de variación (CV) promedio del ADN de los
   supervivientes elegibles sobre claves técnicas, macro y SMC.
   Si CV < DIVERSITY_VARIANCE_THRESHOLD (default 0.01 = 1%):
     - sigma_weights, sigma_periods, sigma_risk se multiplican por
       SIGMA_BOOST_FACTOR (default 2.0)
     - Esto fuerza exploración agresiva y evita el estancamiento por clones.

6. REPRODUCCIÓN (un hijo por cada eliminado)
   ──────────────────────────────────────────
   Para cada cupo vacante:
     a. Seleccionar 2 padres del pool de supervivientes ELEGIBLES
        (probabilidad proporcional al ROI de cada padre)
     b. Crossover de los 4 bloques de genes
     c. Mutación gaussiana sobre cada gen (sigmas posiblemente boosteadas)
     d. Normalizar pesos y aplicar constraints
     e. INSERT en agentes (generacion = max_generacion_activa + 1)

7. RAZONAMIENTO LLM (solo si el ciclo NO está suspendido)
   ──────────────────────────────────────────────────────
   DeepSeek analiza los resultados y produce:
   - Por qué fallaron los eliminados (parámetros problemáticos)
   - Qué se espera de los nuevos agentes (herencia + mutación)
   - Insight sobre condiciones de mercado del día
   - Recomendaciones de parámetros para próximas generaciones

8. PERSISTENCIA EN LOGS
   ──────────────────────
   logs_juez ← evaluacion_diaria (veredicto global con métricas nuevas)
   logs_juez ← eliminacion (uno por agente eliminado)
   logs_juez ← seleccion_padres (uno por agente nuevo)
   logs_juez ← nuevo_agente (uno por agente nuevo)

9. REDISTRIBUCIÓN DE CAPITAL
   ──────────────────────────
   pool_total = SUM(capital_actual) de todos los activos antes del ciclo
   capital_por_agente = pool_total / n_agentes_activos
   UPDATE agentes SET capital_actual = capital_por_agente WHERE estado = 'activo'
   → Todos arrancan el día siguiente con el mismo capital
   (En ciclos suspendidos este paso se omite: el capital se preserva tal cual.)
```

### Cálculo del Calmar Ratio Proxy

```sql
-- Por agente:
fitness = ROI_total / (max_drawdown + 1)
        - PENALIDAD

-- max_drawdown: mayor caída desde un pico del capital acumulado:
drawdown = (peak - capital_acumulado) / peak

-- Penalidad por overtrading sin resultados:
IF avg_ops_dia > 3 AND win_rate < 50% THEN penalidad = 0.5
```

### Periodo de Gracia Operativa (inmunidad)

El Periodo de Gracia evita el "infanticidio algorítmico": en días con HOLD
generalizado (cuarentena macro o condiciones de mercado adversas) los agentes
recién nacidos pueden no operar y arrancar con fitness = 0. Sin protección,
serían eliminados antes de tener oportunidad de demostrar su ADN.

Un agente es **inmune** a la eliminación esa tarde si cumple ambas condiciones:
1. `operaciones_total == 0` (no ha cerrado ninguna operación).
2. Edad en **días hábiles** (lunes a viernes) < `GRACE_PERIOD_DAYS` (default 2).

Como el mercado Forex institucional no opera sábado ni domingo, esos días no
acumulan edad: un agente nacido un viernes evaluado el lunes tiene 1 día
hábil de edad, no 3.

### Suspensión del ciclo (cuota = 0)

El sistema reconoce dos escenarios en los que la mejor decisión es **no hacer
nada** esa tarde:

- **Día de HOLD generalizado:** toda la población está bajo Periodo de Gracia
  (no hay veteranos elegibles porque todos son novatos sin operaciones).
- **Veteranos rentables protegidos:** los elegibles tienen todos fitness > 0,
  no hay candidatos sanos a eliminar.

En ambos casos el ciclo queda explícitamente registrado como suspendido en
`logs_juez` con `cycle_suspended=true`, `cuota_aplicada=0` y un campo
`suspension_reason` que documenta la causa para auditoría posterior.

---

## 9. Mutación genética

**Archivo:** `evolution/evolution_engine.py`

### Crossover

Cada bloque de genes del hijo se construye gen por gen. Para cada gen:
- Con probabilidad `p1_weight` (60% si padre1 tiene mejor ROI, 40% si no) se hereda del padre 1.
- Con probabilidad complementaria se hereda del padre 2.

```python
for gen in all_keys:
    hijo[gen] = padre1[gen] if random() < p1_weight else padre2[gen]
```

### Mutación gaussiana

Sobre cada gen del hijo se aplica ruido gaussiano multiplicativo:

```
gen_mutado = gen_original × (1 + N(0, sigma))
```

Los valores de sigma por tipo de gen:

| Tipo | Variable | Sigma estándar |
|---|---|---|
| Pesos técnicos | `MUTATION_SIGMA_WEIGHTS` | 5% |
| Períodos técnicos | `MUTATION_SIGMA_PERIODS` | 8% |
| Riesgo y SMC | `MUTATION_SIGMA_RISK` | 10% |

> Para la siembra de la Generación 1 (mayo 2026) se usó sigma reducido a la mitad (2.5%, 4%, 5%) para preservar mejor el ADN de los agentes semilla.

### Clamping post-mutación

Cada gen se fuerza a permanecer dentro de su rango de seguridad definido en `_BOUNDS_*`. Los genes enteros se redondean.

### Constraints de integridad

Tras la mutación se aplican dos reglas de negocio:
- **EMA:** `ema_rapida` siempre < `ema_lenta`. Si colisionan, `ema_lenta` se ajusta a `ema_rapida + random(3, 8)`.
- **SL/TP:** `take_profit_pct` siempre ≥ `stop_loss_pct × 1.5`. Si no, TP se recalcula.

### Normalización de pesos técnicos

Los tres pesos del sub-agente A (`peso_rsi`, `peso_ema`, `peso_macd`) se normalizan para que sumen exactamente 1.0 tras la mutación.

### Forzado de diversidad genética (sigma boost)

Para evitar que la población converja a un clon único y el algoritmo se estanque, antes de generar los hijos el motor mide la diversidad del pool de padres.

**Métrica:** coeficiente de variación (CV = desviación estándar / |media|) promediado sobre las claves numéricas representativas del ADN:

- **Técnicas:** `rsi_periodo`, `ema_rapida`, `ema_lenta`, `peso_rsi`, `peso_ema`, `peso_macd`.
- **Macro:** `peso_noticias_alto`, `umbral_sentimiento_compra`, `ventana_noticias_horas`, `peso_total_macro`.
- **SMC:** `fvg_min_pips`, `risk_reward_target`, `macro_quarantine_minutes`, `peso_fvg`, `peso_ob`, `atr_factor`.

**Comportamiento:**

```
cv = _compute_genetic_variance(supervivientes_elegibles)
si cv < DIVERSITY_VARIANCE_THRESHOLD (default 0.01 = 1%):
    sigma_weights *= SIGMA_BOOST_FACTOR (default 2.0)
    sigma_periods *= SIGMA_BOOST_FACTOR
    sigma_risk    *= SIGMA_BOOST_FACTOR
    result.sigma_boost_applied = True
```

Esto significa que en ciclos donde la población se parece demasiado, los hijos nacen con mutaciones del orden del 10% en pesos y 16% en períodos (en vez del 5% y 8% habitual), forzando una exploración agresiva de nuevas fronteras estratégicas.

El valor de `genetic_variance_cv` y el flag `sigma_boost_applied` quedan registrados en `logs_juez.datos_json` para cada ciclo, junto con las sigmas efectivamente utilizadas (`sigma_used`).

---

## 10. Creación y eliminación de agentes

### Creación — cuatro escenarios

| Escenario | Mecanismo |
|---|---|
| **Inicio del sistema** | Script manual de siembra con parámetros fijos o copiados de agentes previos |
| **Reproducción diaria** | El Juez selecciona 2 padres del pool de supervivientes y los cruza con mutación |
| **Reset manual** | Limpieza total de la DB e inserción de nueva generación semilla |
| **Registro en Hall of Fame** | `estrategias_exitosas` captura los genes de agentes con ROI > 0.05% para herencia futura |

### Formato del INSERT de un nuevo agente

```sql
INSERT INTO agentes (
    id,                -- 'YYYY-MM-DD_NN'
    fecha_nacimiento,
    generacion,        -- max(generacion activa) + 1
    padre_1_id,        -- NULL en generación inicial
    padre_2_id,        -- NULL en generación inicial
    params_tecnicos,   -- JSONB
    params_macro,      -- JSONB
    params_riesgo,     -- JSONB
    params_smc,        -- JSONB
    capital_inicial,   -- 10.00 al nacer
    capital_actual,    -- asignado por redistribución
    estado             -- 'activo'
)
```

### Eliminación con cuota dinámica

La cuota de eliminación deja de ser rígida (5 agentes fijos por día). Cada tarde el motor calcula cuántos agentes salen, **con un máximo de N_ELIMINATE (default 5) y un mínimo de 0**, aplicando dos salvaguardas:

**Salvaguarda 1 — Periodo de Gracia Operativa:** los agentes con `operaciones_total == 0` y edad < `GRACE_PERIOD_DAYS` días hábiles quedan inmunes esa tarde.

**Salvaguarda 2 — Protección de fitness positivo:** solo son eliminables los agentes elegibles con `fitness_score <= 0` (negativo o cero). Un veterano rentable nunca se elimina solo para cumplir la cuota.

**Orden de eliminación (desempate generalizado):** los candidatos elegibles se ordenan por `(fitness_score ASC, fecha_nacimiento ASC, id ASC)`. Eso significa que ante empate de fitness, los **veteranos rezagados** salen primero y los **agentes jóvenes** se preservan — un complemento simétrico al desempate del ranking de supervivencia.

**Tres escenarios posibles cada tarde:**

| Escenario | Eliminados | Nacimientos | Razón |
|---|---|---|---|
| Día normal con veteranos negativos | 1 a 5 | igual número | Bottom por fitness, hasta el tope dinámico |
| Día de HOLD generalizado | 0 | 0 | Todos los activos están en Periodo de Gracia |
| Veteranos rentables protegidos | 0 | 0 | Todos los elegibles tienen fitness > 0 |

**SQL de la eliminación:**

```sql
UPDATE agentes SET
    estado             = 'eliminado',
    fecha_eliminacion  = fecha_del_ciclo,
    razon_eliminacion  = 'Selección natural YYYY-MM-DD: cuota dinámica = N
                          (fitness <= 0). Desempate por veteranía
                          (fecha_nacimiento ASC, id ASC). Inmunes en gracia: M.'
WHERE id = ANY(eliminados)
```

Sus registros permanecen en la tabla `agentes` para trazabilidad histórica; solo cambia `estado`. Cuando el ciclo queda suspendido (cuota = 0) no se ejecuta este UPDATE: la población completa continúa intacta al día siguiente.

---

## 11. Base de datos — PostgreSQL

Alojada en **Supabase** (PostgreSQL gestionado, plan Free). La conexión usa el **Transaction Pooler de PgBouncer** (puerto 6543) para compatibilidad con GitHub Actions (IPv4). 5 tablas principales + 2 vistas + 1 trigger de auditoría:

**Cadena de conexión activa:**
```
postgresql://postgres.<project-id>:<password>@aws-1-us-west-2.pooler.supabase.com:6543/postgres
```

**Índice de integridad para operaciones:**
```sql
-- Garantiza que un agente nunca tenga más de una posición BUY/SELL abierta simultáneamente.
-- Previene race conditions TOCTOU cuando dos runs de GitHub Actions se solapan.
CREATE UNIQUE INDEX idx_one_open_buysell_per_agent
  ON operaciones(agente_id)
  WHERE estado = 'abierta' AND accion IN ('BUY', 'SELL');
```

### `agentes`

Registro central de cada agente. Campos clave:

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | VARCHAR(20) PK | Identificador único YYYY-MM-DD_NN |
| `fecha_nacimiento` | DATE | Fecha de creación del agente |
| `generacion` | INTEGER | Número de generación evolutiva |
| `padre_1_id` / `padre_2_id` | VARCHAR(20) FK | Árbol genealógico (NULL en Gen 1) |
| `estado` | VARCHAR | `activo` / `eliminado` / `retirado` |
| `params_tecnicos` | JSONB | Genes del sub-agente A |
| `params_macro` | JSONB | Genes del sub-agente B |
| `params_riesgo` | JSONB | Genes del sub-agente C |
| `params_smc` | JSONB | Genes SMC + ATR (incluye `atr_period` desde migración 006) |
| `capital_inicial` / `capital_actual` | DECIMAL | Capital en dólares |
| `roi_total` | DECIMAL | ROI acumulado en porcentaje |
| `operaciones_total` / `operaciones_ganadoras` | INTEGER | Estadísticas |
| `fecha_eliminacion` | DATE | Fecha en que el Juez eliminó al agente (NULL si activo) |
| `razon_eliminacion` | TEXT | Justificación de la eliminación (incluye fitness/cuota dinámica) |
| `created_at` / `updated_at` | TIMESTAMPTZ | Auditoría temporal |

### `operaciones`

Log de cada señal BUY/SELL/HOLD. Una fila por ciclo de decisión.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | SERIAL PK | Identificador numérico auto-incremental |
| `agente_id` | FK → agentes | Agente que generó la señal |
| `timestamp_entrada` / `timestamp_salida` | TIMESTAMPTZ | Marcas de tiempo de apertura y cierre |
| `accion` | VARCHAR | `BUY` / `SELL` / `HOLD` |
| `precio_entrada` / `precio_salida` | DECIMAL | Precios reales de mercado |
| `capital_usado` | DECIMAL | **Nocional en USD** de la posición (exposición total, no capital "gastado"). Ejemplo: $51 significa que el agente controla $51 de EUR/USD. Con equity=$10 eso es ~5:1 de apalancamiento real, topado en 50:1. |
| `pips_sl` | DECIMAL(8,2) | Distancia del SL al precio de entrada en pips (poblada en INSERT desde Sesión 7) |
| `pnl` | DECIMAL | Ganancia/pérdida en USD. Fórmula: `(precio_salida - precio_entrada) / precio_entrada × capital_usado`. Positivo = ganancia, negativo = pérdida. |
| `pnl_porcentaje` | DECIMAL | ROI del trade sobre el **capital del agente** (`pnl / capital_disponible × 100`), no sobre el nocional. Indica cuánto movió la cuenta ese trade. |
| `estado` | VARCHAR | `abierta` / `cerrada` / `cancelada` |
| `senal_tecnico` | JSONB | Output completo del sub-agente A |
| `senal_macro` | JSONB | Output completo del sub-agente B |
| `decision_riesgo` | JSONB | Output completo del sub-agente C (SL, TP, razonamiento) |
| `sl_dinamico` | DECIMAL | SL actual (actualizado por trailing stop) |
| `precio_extremo_favorable` | DECIMAL | Precio más favorable alcanzado (para trailing) |
| `timestamp_ultima_verificacion` | TIMESTAMPTZ | Hasta qué momento (UTC) ya se examinó OHLC 1m para esta operación. El monitor descarga velas posteriores a este valor en cada ciclo. Inicialmente = `timestamp_entrada`; avanza con la última vela procesada. Migración 008. |
| `created_at` | TIMESTAMPTZ | Marca de creación del registro |

### `logs_juez`

Audit trail completo del Agente Juez.

| Tipo de evento | Descripción |
|---|---|
| `evaluacion_diaria` | Veredicto global del ciclo: supervivientes, eliminados, inmunes, suspensión, sigmas usadas, capital |
| `eliminacion` | Un registro por agente eliminado con fitness y razonamiento LLM |
| `seleccion_padres` | Qué padres se eligieron para cada nuevo agente |
| `nuevo_agente` | Genes del nuevo agente y expectativas del LLM |

**Campos del `datos_json` en `evaluacion_diaria`** (estructura JSONB):

| Campo | Tipo | Descripción |
|---|---|---|
| `survivors` | array | IDs de agentes que sobrevivieron (incluye inmunes y supervivientes elegibles) |
| `eliminated` | array | IDs eliminados esta tarde (vacío si el ciclo está suspendido) |
| `new_agents` | array | IDs de los hijos creados (vacío si el ciclo está suspendido) |
| `immune_agents` | array | IDs protegidos por Periodo de Gracia |
| `eligible_veterans` | array | IDs evaluables esta tarde (no inmunes) |
| `cycle_suspended` | bool | `true` cuando la cuota dinámica = 0 |
| `suspension_reason` | string | Justificación técnica si está suspendido |
| `cuota_aplicada` | int | Número final de eliminados (0 si suspendido) |
| `genetic_variance_cv` | float | Coeficiente de variación del ADN de los supervivientes |
| `sigma_boost_applied` | bool | `true` si se duplicaron las sigmas por baja diversidad |
| `sigma_used` | object | `{weights, periods, risk}` efectivamente aplicadas |
| `capital_pool_total` | float | Pool total en USD |
| `capital_por_agente` | float | Cuota individual tras redistribución |
| `insight_mercado` | string | Comentario del LLM (vacío en días suspendidos) |
| `recomendacion_parametros` | string | Sugerencias del LLM para próximas generaciones |

Estos campos permiten al Dashboard Streamlit (y a cualquier consulta SQL ad-hoc en Supabase) reconstruir con total trazabilidad qué pasó cada día y por qué.

### `ranking_historico`

Snapshot diario de posición, ROI y capital de cada agente al final del día. Alimenta las gráficas de evolución del dashboard.

### `estrategias_exitosas`

Hall of Fame: parámetros de agentes que superaron el umbral `MIN_ROI_FOR_HALL_OF_FAME` (default 0.05%). Reserva de "genes buenos" para herencia futura.

---

## 12. Google Sheets — registro y trazabilidad

**Hoja:** `Agentes` — árbol genealógico completo de todos los agentes (vivos y eliminados).

| Columna | Contenido |
|---|---|
| ID | Identificador del agente |
| Generación | Número de generación |
| Tipo Origen | `Génesis` (sin padres) o `Mutante Gen-N` |
| Fecha Nacimiento | Fecha de creación |
| Padre 1 / Padre 2 | IDs de los padres (vacío en Gen 1) |
| Estado | `activo` / `eliminado` |
| ROI Total (%) | Acumulado |
| Fitness (Calmar) | Calmar Ratio Proxy |
| Win Rate (%) | Porcentaje de operaciones ganadoras |
| Ops Total | Número total de operaciones |
| Capital Inicial / Final | En USD |
| Genes SMC | FVG min pips, OB impulso pips, R:R target, etc. |

**Hoja:** `Operaciones` — registro en tiempo real de cada trade.

| Columna | Contenido |
|---|---|
| ID / Agente ID | Identificadores |
| Timestamp Entrada (Bogotá) | Hora de apertura en zona Colombia |
| Acción | BUY / SELL / HOLD |
| Precio Entrada / SL / TP | Niveles de la operación |
| Pips SL / R:R | Distancias calculadas |
| Capital Usado ($) | Monto en USD |
| Estado | abierta / cerrada / cancelada |
| P&G ($) | Fórmula `GOOGLEFINANCE` para posiciones abiertas, valor real al cierre |
| Confianza Técnica / Macro | Señales individuales |
| RSI / FVG / OB / Range Spike | Indicadores en el momento del trade |
| Razonamiento LLM | Texto de DeepSeek explicando la decisión |

### Eventos que actualizan Sheets automáticamente

| Evento | Método llamado | Trigger | Pestaña afectada |
|---|---|---|---|
| Operación abre (BUY/SELL/HOLD) | `log_operation()` | `investor_agent._persist_operation` | Operaciones — nueva fila con generación |
| Operación cierra (SL/TP/EOD) | `update_operation()` + `update_agent_live()` | `investor_agent.close_operation` | Operaciones (precio/P&G) + Agentes (capital, ROI, Ops, Win Rate) |
| Nuevo agente (evolución) | `log_agent()` | `evolution_engine._insert_new_agent` | Agentes — nueva fila |
| Agente eliminado | `update_agent_status()` | `evolution_engine._eliminate_agents` | Agentes (Estado, ROI final, capital final) |
| Redistribución de capital | `update_agent_live()` × N | `evolution_engine._redistribute_capital` | Agentes — capital nuevo de todos los activos |
| Backfill semanal | `sheets_backfill.run_backfill` | `manual_backfill.yml` (cron domingo 06:00 UTC) | Ambas pestañas reescritas desde DB |

**Diseño asíncrono:** el logger (`utils/sheets_logger.py`) está envuelto en `try/except`. Si Sheets no está disponible o la API rate-limitea, el trading no se interrumpe. El backfill semanal corrige cualquier desincronización acumulada.

**`update_agent_live()`** (lightweight): actualiza solo Capital Final, ROI Total, Ops Total y Win Rate. No toca Estado, Fecha Eliminación ni Razón Eliminación (esas las maneja `update_agent_status` cuando ocurre eliminación). Usa `batch_update` con 4 celdas para minimizar API calls.

---

## 13. Dashboard Streamlit

**URL:** `https://inversion-evolutiva.streamlit.app`
**Archivo:** `dashboard/app.py`
**Tema:** Dark luxury — oro (#d4af37) y esmeralda (#00c878) sobre fondo negro (#07070f).

### 6 pestañas

| Pestaña | Contenido |
|---|---|
| **Poblacion** | Tabla de ranking en vivo, KPIs: ROI top, win rate, pool total |
| **Evolucion** | Curvas de supervivencia por generación, árbol genealógico, heatmap de fitness |
| **Agente Juez** | Log diario del Juez: veredictos coloreados (eliminacion/nacimiento/supervivencia) |
| **Operaciones** | Historial de trades: filtros por agente, distribución de P&L, win/loss |
| **Precio** | Gráfica candlestick EUR/USD con overlay de las operaciones de un agente seleccionado |
| **Instrucciones** | Documentación interna del sistema completa |

El dashboard es **solo lectura**: no tiene capacidad de enviar órdenes ni modificar la DB.

---

## 14. Automatización — GitHub Actions + cron-job.org

### Estrategia de scheduling (desde Sesión 12, 2026-05-27)

El **disparo programado** de los 4 workflows ya **no depende del cron interno de GitHub Actions**. GitHub no garantiza puntualidad en sus crons (`schedule:` es "best effort" según su documentación oficial) y en producción se observaron retrasos crónicos de **2–8 horas** que afectaban especialmente al `judge_daily.yml` (caso límite documentado: ejecución a las 7 AM Bogotá cuando debía correr a las 10:45 PM).

A partir de Sesión 12, la disparada se delega a **[cron-job.org](https://cron-job.org)** — un servicio externo gratuito que invoca el endpoint `workflow_dispatch` de la GitHub REST API con precisión **±5 segundos**:

```
cron-job.org (Bogotá TZ, ±5 seg)
        │
        │  HTTPS POST con Bearer PAT
        ▼
GitHub API /repos/.../workflows/{file}/dispatches
        │
        ▼
GitHub Actions ejecuta el workflow (lógica intacta)
```

Los bloques `schedule:` en los 4 workflows están **comentados** (no eliminados) para permitir rollback de 1 commit. El `workflow_dispatch:` queda como única vía de disparo activa.

**Configuración de los 4 cronjobs en cron-job.org:**

| Cronjob | Workflow target | Crontab (Bogotá TZ) | Frecuencia efectiva |
|---|---|---|---|
| GH Action - Trade Monitor | `trade_monitor.yml` | `*/15 * * * 1-5` | Cada 15 min, L-V |
| GH Action - Judge Daily | `judge_daily.yml` | `45 22 * * 1-5` | 10:45 pm L-V |
| GH Action - Health Check | `health_check.yml` | `0 8 * * 1-5` | 8:00 am L-V |
| GH Action - Backfill Weekly | `manual_backfill.yml` | `0 1 * * 0` | Domingo 1:00 am |

**Headers HTTP comunes a los 4 cronjobs:**
```
Authorization: Bearer ghp_xxx (GitHub Personal Access Token, scope Actions: R/W)
Accept:        application/vnd.github.v3+json
Content-Type:  application/json
```
**Method:** POST · **Body:** `{"ref":"master"}`

**Validación en producción (27-may):** primer ciclo del Juez con el nuevo scheduler disparó a las **22:45:03 Bogotá** (programado 22:45:00 → desfase +3 seg) y completó el ciclo evolutivo en 15m 57s. Comparativa con los 5 ciclos anteriores bajo GH cron interno: retrasos entre +2h 39m y +3h 22m. La migración resolvió un problema crónico, no aislado.

### Versiones de las GitHub Actions

Todos los workflows usan **`actions/checkout@v6`** y **`actions/setup-python@v6`** (ambas estables desde enero 2026, compatibles con **Node.js 24**). Las versiones previas (`@v4` / `@v5`, basadas en Node.js 20) fueron actualizadas en Sesión 12 antes de la deprecación forzosa de GitHub del 2-jun-2026.

### `trade_monitor.yml` — Monitor intraday

**Disparado por:** cron-job.org cada 15 min L-V Bogotá (job "GH Action - Trade Monitor").
**Cobertura:** las 24 horas del día Bogotá L-V — 96 disparos/día × 5 días = 480/semana. Los ~10 disparos fuera del horario de trading (00:00–01:29 am Lun; 23:00–23:45 Vie) son no-op: `_within_trading_hours()` retorna `False`, el EOD guard verifica posiciones huérfanas (típicamente ninguna) y el monitor sale en <1s.

```
Pasos:
  1. Checkout (actions/checkout@v6)
  2. Setup Python 3.11 (actions/setup-python@v6)
  3. pip install -r requirements.txt
  4. python -m cron.trade_monitor --run-once

En caso de fallo: crea un GitHub Issue con alerta (evita duplicados)

Schedule histórico (comentado, sin uso desde Sesión 12):
  - "30,45 6 * * 1-5"     → 1:30 am y 1:45 am Bogotá
  - "*/15 7-23 * * 1-5"   → 2:00 am – 6:45 pm Bogotá
  - "*/15 0-2 * * 2-6"    → 7:00 pm – 9:45 pm Bogotá
  - "0,15,30 3 * * 2-6"   → 10:00 pm – 10:30 pm Bogotá (último ciclo)
  - "*/15 4-6 * * 2-6"    → 11:00 pm – 1:45 am Bogotá (ventana ciega EOD)
```

**Secrets requeridos:** `DATABASE_URL` (Supabase Transaction Pooler), `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS_JSON`

**Env vars de horario:** `TRADING_START_TIME_UTC=06:30`, `TRADING_CUTOFF_TIME_UTC=04:00` (la ventana cruza la medianoche UTC; el código lo gestiona explícitamente).

### `judge_daily.yml` — Ciclo evolutivo diario

**Disparado por:** cron-job.org a las 22:45:00 Bogotá L-V (job "GH Action - Judge Daily").

```
Pasos (todos en un solo job):
  1. Guard de ventana segura (NUEVO en Sesión 12)
     Solo activo cuando event=schedule. Aborta el run si la hora UTC
     actual está fuera de 02:00–06:00 UTC (9 pm–1 am Bogotá). Protección
     residual por si algún día se reactiva el cron de GH y se dispara
     durante trading hours. workflow_dispatch (cron-job.org y manual)
     omite el guard.
  2. Checkout + Python 3.11 (@v6)
  3. Cierre EOD a las 10:45 pm Bogotá:
        python -m cron.trade_monitor --force-close-all
  4. sleep 900 (15 minutos) hasta las 11:00 pm Bogotá.
  5. Health check de la DB
  6. Ciclo evolutivo a las 11:00 pm Bogotá:
        python -m cron.judge_scheduler --run-now

Disparo manual: workflow_dispatch con opción dry_run=true (solo health check).
Recuperación de ciclos perdidos: workflow_dispatch sin dry_run (la guardia
de ventana segura se omite, permitiendo correr el Juez en cualquier hora).

Parámetros de evolución (env vars):
  AGENTS_ELIMINATE_PER_CYCLE   = 5
  MUTATION_SIGMA_WEIGHTS       = 0.05
  MUTATION_SIGMA_PERIODS       = 0.08
  MUTATION_SIGMA_RISK          = 0.10
  MIN_ROI_FOR_HALL_OF_FAME     = 0.05
  GRACE_PERIOD_DAYS            = 2
  DIVERSITY_VARIANCE_THRESHOLD = 0.01
  SIGMA_BOOST_FACTOR           = 2.0

En caso de fallo: crea GitHub Issue con alerta.

Schedule histórico (comentado): "45 3 * * 2-6"  → 03:45 UTC
```

### `health_check.yml` — Verificación diaria de dependencias

**Disparado por:** cron-job.org a las 8:00:00 Bogotá L-V (job "GH Action - Health Check").

Verifica que DB, DeepSeek API, Finnhub API, Yahoo Finance y todos los secrets estén disponibles. Si algún check falla, crea un GitHub Issue de alerta.

```
Schedule histórico (comentado): "0 13 * * 1-5"  → 13:00 UTC = 8:00 am Bogotá
```

### `manual_backfill.yml` — Sincronización Sheets ↔ DB

**Disparado por:** cron-job.org los domingos a las 1:00 am Bogotá (job "GH Action - Backfill Weekly") + workflow_dispatch manual.

```
Pasos:
  1. python utils/diagnose_backfill.py  (verifica DB + creds Sheets)
  2. python -m utils.sheets_backfill    (limpia y reescribe Agentes + Operaciones)

Corrige cualquier desincronización acumulada durante la semana.

Schedule histórico (comentado): "0 6 * * 0"  → 06:00 UTC dominical
```

### Plan de rollback al cron interno de GH

Si cron-job.org sufriera una caída prolongada:
1. Descomentar el bloque `schedule:` en los 4 workflows (1 commit).
2. Deshabilitar los 4 cronjobs en cron-job.org (toggle en el dashboard).
3. GitHub Actions vuelve a su scheduler interno con los crons originales.

El sistema sigue operando — solo pierde puntualidad. Las guardias defensivas (`_eod_guard` en trade_monitor y ventana segura en judge_daily) mitigan el impacto.

---

## 15. Variables de configuración

Todas las variables se definen en `.env` local (desarrollo) o en **GitHub Secrets** (producción).

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Cadena de conexión a Supabase PostgreSQL (Transaction Pooler, puerto 6543) |
| `DEEPSEEK_API_KEY` | API key de DeepSeek LLM |
| `DEEPSEEK_MODEL` | Modelo DeepSeek a utilizar (producción: `deepseek-reasoner`) |
| `FINNHUB_API_KEY` | API key de Finnhub (noticias y calendario económico) |
| `ALPHA_VANTAGE_API_KEY` | API key de Alpha Vantage (legado) |
| `GOOGLE_SHEET_ID` | ID del spreadsheet de Google Sheets |
| `GOOGLE_CREDENTIALS_JSON` | JSON completo de la service account de Google (o ruta al archivo en local) |
| `DEEPSEEK_BASE_URL` | Base URL del API (default: `https://api.deepseek.com`) |
| `JUDGE_TIMEZONE` | Zona horaria del Juez (default: `America/Bogota`) |
| `JUDGE_RUN_TIME` | Hora de ejecución del Juez en zona local (default: `23:00`) |
| `TRADING_START_TIME_UTC` | Hora UTC desde la que se permite abrir posiciones, formato HH:MM (default: `06:30` = 1:30 am Bogotá) |
| `TRADING_CUTOFF_TIME_UTC` | Hora UTC límite para abrir posiciones, formato HH:MM (default: `04:00` = 11:00 pm Bogotá; ventana cruza la medianoche UTC) |
| `AGENTS_ELIMINATE_PER_CYCLE` | Agentes eliminados por ciclo (default: `5`) |
| `MUTATION_SIGMA_WEIGHTS` | Sigma de mutación para pesos (default: `0.05`) |
| `MUTATION_SIGMA_PERIODS` | Sigma de mutación para períodos (default: `0.08`) |
| `MUTATION_SIGMA_RISK` | Sigma de mutación para riesgo/SMC (default: `0.10`) |
| `MIN_ROI_FOR_HALL_OF_FAME` | ROI mínimo para entrar al Hall of Fame (default: `0.05`) |
| `GRACE_PERIOD_DAYS` | Días HÁBILES (lun-vie) de inmunidad para agentes recién nacidos sin operaciones (default: `2`) |
| `DIVERSITY_VARIANCE_THRESHOLD` | Coeficiente de variación mínimo del ADN antes de activar el sigma boost. Subir a `0.05` para criterio más estricto (default: `0.01`) |
| `SIGMA_BOOST_FACTOR` | Multiplicador aplicado a las sigmas cuando la diversidad cae bajo el umbral (default: `2.0`) |
| `LOG_LEVEL` | Nivel de logs (default: `INFO`) |
| `ENVIRONMENT` | Ambiente (`production` / `development`) |

---

## 16. Flujo completo día a día

### Lunes a viernes — horario Bogotá

```
1:30am   ─── GitHub Actions despierta trade_monitor (primer ciclo del día)
             ├── Verifica SL/TP de posiciones abiertas
             └── Evalúa nuevos trades para agentes libres

Cada 15 min de 1:30am a 10:30pm:
             ├── Ciclo SL/TP + trailing stop por agente
             ├── Si agente libre y dentro de la ventana de apertura
             │   (1:30am – 11:00pm) → pipeline A→B→C → posible nuevo trade
             └── Registro en DB y Google Sheets

10:30pm  ─── Último monitor antes del cierre

10:45pm  ─── GitHub Actions despierta judge_daily (paso 1):
             Cierre forzado de TODAS las posiciones abiertas al precio de
             mercado (force-close-all). Buffer de 15 min hasta el Juez.

11:00pm  ─── judge_daily continúa (paso 2):
             1. Evaluación de fitness: Calmar Ratio Proxy para 10 agentes
             2. Filtro de Periodo de Gracia: separar inmunes de elegibles
             3. Cuota dinámica: identificar elegibles con fitness <= 0

             ╔══════════════════════════════════════════════════════╗
             ║  ¿Cuota = 0?                                         ║
             ╠══════════════════════════════════════════════════════╣
             ║  SÍ → CICLO SUSPENDIDO                               ║
             ║      - No se elimina, no se reproduce, no se         ║
             ║        redistribuye capital.                         ║
             ║      - Log único 'evaluacion_diaria' con             ║
             ║        cycle_suspended=true.                         ║
             ║      - No se invoca al LLM (ahorro de tokens).       ║
             ║                                                      ║
             ║  NO → CICLO ACTIVO                                   ║
             ║      4. Forzado de diversidad: si CV bajo,           ║
             ║         duplica las sigmas de mutación.              ║
             ║      5. Eliminar N agentes (1 a 5, fitness<=0).      ║
             ║      6. Reproducir N hijos (crossover + mutación).   ║
             ║      7. Razonamiento LLM: veredicto y expectativas.  ║
             ║      8. Registro detallado en logs_juez.             ║
             ║      9. Redistribución de capital: pool ÷ activos.   ║
             ╚══════════════════════════════════════════════════════╝

11:05pm  ─── Sistema queda en reposo hasta el día siguiente (1:30am)
```

### Fines de semana

El sistema no opera: GitHub Actions no tiene schedule para sábado/domingo. Los agentes permanecen en sus estados finales del viernes hasta el lunes.

### Intervención manual

El usuario puede disparar manualmente cualquier workflow desde GitHub Actions:
- **trade_monitor** → `workflow_dispatch` para forzar un ciclo inmediato.
- **judge_daily** → `workflow_dispatch` con `dry_run=true` para verificar estado sin evolucionar.

---

## 17. Scripts utilitarios (`scripts/`)

Carpeta con utilidades que se ejecutan de forma puntual (no automática). No se invocan desde GitHub Actions; el usuario las corre manualmente cuando se necesita una operación específica sobre la BD.

| Script | Propósito | Cuándo usarlo |
|---|---|---|
| `seed_gen1.py` | Crea 10 agentes Generación 1 con parámetros por defecto y $10 c/u (pool inicial $100). | Una sola vez al migrar a una nueva BD vacía. Editar la constante `HOY` antes de ejecutar. |
| `diversify_gen1.py` | Aplica mutación gaussiana individualizada a los 10 agentes activos para darles ADN propio (σ elevada). | Cuando los 10 agentes tienen params idénticos (típicamente justo después de un seed) y el motor evolutivo queda bloqueado por falta de fitness diferencial. |
| `recompute_pnl.py` | Recalcula `pnl`, `capital_usado` (nocional USD) y `pnl_porcentaje` para todas las ops cerradas que tenían `capital_usado` en lotes (convención anterior al commit `6572c11`). Reconstruye `capital_actual` y `roi_total` de los agentes. | **Ya ejecutado** el 2026-05-20. Solo necesario si se detectan operaciones históricas con `capital_usado` < 1.0 (indicativo de lotes en vez de USD). |

Comando para ejecutar:
```bash
python scripts/seed_gen1.py        # nueva BD vacía → 10 agentes Gen1
python scripts/diversify_gen1.py   # diversifica ADN de los 10 activos
```

Ambos requieren `.env` cargado con `DATABASE_URL` válido (Supabase Transaction Pooler).

---

## 18. Estructura del repositorio

```
Antigravity_Inversion_Evolutiva/
├── agents/                      Pipeline de sub-agentes + Inversor + Juez
│   ├── base_agent.py
│   ├── investor_agent.py
│   ├── judge_agent.py
│   ├── sub_agent_technical.py
│   ├── sub_agent_macro.py
│   └── sub_agent_risk.py
├── cron/                        Entry points para GitHub Actions
│   ├── trade_monitor.py         (cada 15 min: SL/TP + nuevas + guardia EOD)
│   └── judge_scheduler.py       (lanza el ciclo evolutivo del Juez)
├── data/                        Fuentes de mercado y indicadores
│   ├── indicators.py            (OHLCV + RSI/EMA/MACD/FVG/OB/ATR)
│   ├── macro_scraper.py         (Finnhub: noticias + calendario)
│   ├── simulated_broker.py      (Yahoo Finance: snapshot + OHLC 1m intra-vela
│   │                              · check_sl_tp_intrabar / get_intrabar_candles)
│   └── alpha_vantage_client.py  (legado: dataclass TechnicalSignals)
├── db/
│   ├── connection.py            (psycopg2 + Supabase pooler)
│   ├── apply_migrations.py
│   ├── migrations/              (001 – 008)
│   └── seeds/
├── evolution/
│   └── evolution_engine.py      (fitness, crossover, mutación, redistribución)
├── dashboard/                   Streamlit (inversion-evolutiva.streamlit.app)
│   ├── app.py
│   ├── charts.py
│   ├── data.py
│   └── logo.png
├── mobile-app/                  Next.js (app complementaria, Vercel)
├── scripts/                     Utilidades manuales (seed_gen1, diversify_gen1)
├── tests/                       pytest (pipeline + evolution)
├── utils/
│   ├── sheets_logger.py         (gspread: trazabilidad en Google Sheets)
│   ├── sheets_backfill.py       (sincronización completa DB → Sheets)
│   └── diagnose_backfill.py     (verificación previa al backfill)
├── .github/workflows/
│   ├── trade_monitor.yml        (cada 15 min L-V)
│   ├── judge_daily.yml          (10:45 pm Bogotá L-V)
│   ├── manual_backfill.yml      (domingo + dispatch manual)
│   └── health_check.yml         (08:00 am Bogotá L-V)
├── streamlit_app.py             (entrypoint Streamlit Cloud)
├── Procfile                     (Streamlit Cloud config)
├── runtime.txt                  (Python 3.11)
├── requirements.txt
└── Inversion_Evolutiva.md       (este documento)
```

---

*Documento actualizado el 2026-05-27 (Sesión 13 — verificación intra-vela de SL/TP con OHLC 1m: elimina el sesgo evolutivo del check por snapshot, fitness ahora honesto).*

## Historial de cambios mayores

- **2026-05-27 (Sesión 13 — verificación intra-vela de SL/TP con OHLC 1m):**
  - **Problema raíz detectado:** la operación #2803 (SELL @ 1.16496, TP=1.16263, abierta 02:46 am) cerró en SL trailing 1.16387 a las 18:30 aunque el precio claramente tocó el TP durante el día (mecha hasta ~1.16143). Causa: `cron/trade_monitor.py` chequeaba SL/TP contra un **único snapshot** del precio (`get_current_price()` → `regularMarketPrice` de Yahoo) cada 15 min. Las mechas entre dos ciclos eran sistemáticamente invisibles.
  - **Por qué es crítico para la evolución:** el fitness de cada agente se calcula sobre los resultados de sus operaciones. Si el simulador miente sistemáticamente, los genes `risk_reward_target`, `atr_factor`, `trailing_*` se calibran contra un mercado ficticio. Mutaciones hacia TP ambicioso parecen malas (nunca "llegan"), mutaciones hacia SL ajustado parecen buenas (nunca "saltan"). Se estaba criando ADN frágil que colapsaría en un broker real.
  - **Nueva migración 008 (`db/migrations/008_intrabar_verification.sql`):** añade columna `timestamp_ultima_verificacion TIMESTAMPTZ` a `operaciones` + backfill idempotente con `timestamp_entrada` para los registros existentes. Marca hasta qué momento el monitor ya examinó OHLC para cada operación.
  - **Nuevas funciones en `data/simulated_broker.py`:**
    - `check_sl_tp_intrabar(action, stop_loss, take_profit, candle) -> PositionResult`: chequea si una vela OHLC tocó SL o TP. BUY → SL si low ≤ SL, TP si high ≥ TP. SELL → invertido. Si la vela toca ambos → devuelve `HIT_SL` (convención conservadora: ante ambigüedad intra-vela, asumir el peor caso para el trader).
    - `get_intrabar_candles(since: datetime) -> list[dict]`: wrapper sobre `get_price_history(interval="1m", range_str="1d")` que filtra velas posteriores a `since`. Devuelve `[]` si Yahoo no responde (fin de semana, fallo API).
    - Las funciones legacy (`get_current_price`, `check_sl_tp`, `exit_price_for`, `get_price_history`) quedan intactas para retro-compatibilidad y como fallback.
  - **`agents/investor_agent.close_operation()`:** nuevo parámetro opcional `timestamp_salida: datetime | None = None`. Cuando el cierre proviene del verificador intra-vela, recibe el timestamp real de la mecha (no `datetime.now()`). Si se omite, comportamiento legacy (EOD / fallback).
  - **Reescritura del loop SL/TP en `cron/trade_monitor.sync_once()`:** tres funciones privadas nuevas:
    - `_verify_position_intrabar(op, fallback_price)`: orquesta la verificación. Carga velas desde `timestamp_ultima_verificacion`, itera cronológicamente, chequea SL/TP **primero** con el SL pre-trailing de cada vela, y solo si no hay hit aplica trailing usando el extremo favorable. Devuelve `{closed, candles_checked, fallback}`.
    - `_persist_trailing(op, sl, extremo, since_ts)`: persiste `sl_dinamico`, `precio_extremo_favorable` y opcionalmente `timestamp_ultima_verificacion`.
    - `_close_op(op, precio_salida, ts_salida, resultado)`: cierra la operación reusando `InvestorAgent.close_operation` con el timestamp real propagado.
  - **Fallback automático:** si Yahoo no devuelve velas (mercado cerrado, error de red), el verificador cae al check snapshot legacy con `get_current_price()` para no bloquear el ciclo. Log warning en ese caso.
  - **Sin cambio en el cron:** la frecuencia actual (cada 15 min vía cron-job.org) sigue igual porque cada ciclo ahora cubre los 15 min anteriores con resolución de 1 min — mejor precisión, mismo costo.
  - **Tests nuevos (`tests/test_sltp_intrabar.py`):** 13 tests pasan — 9 unitarios de `check_sl_tp_intrabar` (BUY/SELL × {solo TP, solo SL, ambos→SL, ninguno} + acción inválida) + 4 de integración del loop con velas sintéticas (caso #2803 reproducido cierra en TP, trailing intra-vela que aprieta SL, fallback sin velas, cursor de verificación avanza correctamente).
  - **`agents/investor_agent.close_operation`** propaga el `ts_salida` también al `SheetsLogger.update_operation` para que Sheets muestre el timestamp real de la mecha cuando el cierre es intra-vela.
  - **Lo que NO cambia:** `_apply_trailing_stop()`, `_eod_guard()`, `_evaluate_new_positions()`, schema `decision_riesgo` JSONB (SL/TP originales preservados como audit trail), cron schedule (cron-job.org `*/15 * * * 1-5`), lógica de evolución, judge_daily, pool de capital, sincronización Sheets.

- **2026-05-27 (Sesión 12 — scheduling externo confiable + actions Node 24 + timezones dashboard):**
  - **Migración de scheduling de GH Actions cron a cron-job.org** (commits `0d3b028` y posteriores): el cron interno de GitHub Actions, observado retrasando el `judge_daily.yml` entre 2h 39m y 3h 22m durante 5 días consecutivos (caso límite documentado: ciclo del 26-may corrió a las 07:00 am en lugar de 22:45 del 25-may; force-close-all interrumpió posiciones activas durante trading hours). Reemplazado por [cron-job.org](https://cron-job.org), un servicio externo gratuito con precisión ±5 seg. 4 cronjobs creados en zona America/Bogota: Trade Monitor (`*/15 * * * 1-5`), Judge Daily (`45 22 * * 1-5`), Health Check (`0 8 * * 1-5`), Backfill Weekly (`0 1 * * 0`). Disparan vía HTTPS POST al endpoint `workflow_dispatch` de la GitHub REST API con un Personal Access Token (scope: Actions R/W). Los bloques `schedule:` de los 4 workflows fueron comentados (no eliminados — rollback de 1 commit disponible). Validación en producción: primer Juez bajo el nuevo régimen disparó a las 22:45:03 Bogotá (+3 seg de margen) y completó 15m 57s sin warnings.
  - **Guardia de ventana segura en `judge_daily.yml`** (commit `cf769c6`): nuevo paso al inicio del workflow que verifica `now_utc` y aborta con `exit 1` si está fuera de 02:00–06:00 UTC (9 pm–1 am Bogotá). Aplica solo a `event=schedule`, no a `workflow_dispatch` (esto permite recuperación manual de ciclos perdidos). Defensa residual por si algún día se reactiva el cron de GH y se dispara durante trading hours.
  - **Actions actualizadas a v6 (Node.js 24)** (commit `e6960ed`): GitHub anunció el 2-jun-2026 como fecha de forzado de Node.js 24 y el 16-sep-2026 como eliminación total de Node.js 20 en los runners. Las 4 referencias a `actions/checkout@v4` y las 4 a `actions/setup-python@v5` (ambas sobre Node 20) actualizadas a `@v6` (Node 24). Validado en run real: el warning amarillo de deprecation desapareció y los runs son ~10s más rápidos.
  - **Timezones del dashboard normalizadas a hora Bogotá** (commit `1e2defa`): dos lugares mostraban hora UTC raw — sidebar "Último ciclo" y Tab Agente Juez "Log de Actividad". `data.py` ahora marca `created_at` de `logs_juez` y `last_judge` de `fetch_system_status()` como tz-aware UTC. `app.py` añade helper `_fmt_bogota(dt, fmt)` que acepta naive/aware, datetime/Timestamp, None/NaT y devuelve string formateado en America/Bogota. Convención documentada: DB almacena UTC, data layer parsea como tz-aware UTC, capa de display convierte a Bogotá. El resto del dashboard (Operaciones, Precio, eje X de candlestick) ya convertía correctamente — sin cambios.
  - **Detección del problema sistémico:** análisis del histórico de runs reveló que el Juez venía retrasándose **crónicamente** todos los días, no solo en el incidente del 26-may. La migración resolvió un problema de fondo, no aislado.

- **2026-05-24 (Sesión 11 — auditoría sistemática + sincronización Sheets completa + limpieza legacy):**
  - **Auditoría profunda del aplicativo:** verificación independiente de los 4 workflows, motor evolutivo, motor de riesgo, agente Juez y monitor de trades. Resultado: sistema conforme a los parámetros establecidos. Primer agente de Generación 2 (`2026-05-23_01`) creado correctamente el 23-may con padres `2026-05-19_05 × 2026-05-19_02` (crossover fitness-proporcional). El agente eliminado (`2026-05-19_08`, fitness=-0.20) fue el único candidato elegible bajo la regla de cuota dinámica; durante 3 días seguidos (20, 21, 22-may) el ciclo se suspendió correctamente porque todos los Gen1 tenían fitness > 0.
  - **Eliminación de código legacy** (commit `d3115de`): `cron/trading_runner.py` y `.github/workflows/trading_cycle.yml` removidos. `trading_runner` era un ciclo único de "calentamiento" a las 2 am Bogotá sin EOD guard, sin trailing stop, sin cuarentena macro y sin verificación de horario. Su funcionalidad (abrir nuevas posiciones) ya está cubierta con creces por `trade_monitor.yml` (cada 15 min desde 1:30 am). Eliminar redundancia previene doble ejecución y consolida un único punto de mantenimiento para el trading.
  - **Reparación de workflow Sheets backfill** (commit `c3b9c6b`): el `manual_backfill.yml` referenciaba `utils/diagnose_backfill.py` pero el archivo nunca había sido creado, causando exit code 2 en el paso "Diagnostico previo". Creado el script que verifica conexión a DB (cuenta agentes activos, totales, ops, max_gen) y validez de credenciales Sheets (`GOOGLE_SHEET_ID` + `GOOGLE_CREDENTIALS_JSON` con `client_email` extraído del JSON).
  - **Sincronización Sheets completamente automática** (commit `f86ad75`): cerrados 4 huecos críticos en las actualizaciones en tiempo real.
    - **Generación en operaciones:** `log_operation()` ahora recibe el parámetro `generacion` (antes hardcodeado a `""`). `InvestorAgent` almacena `self.generacion` en `__init__`, lo recibe vía `params["generacion"]` desde `trade_monitor._evaluate_new_positions` (cuya query ahora incluye `a.generacion`) y desde `from_db` (cuya SELECT también lo incluye).
    - **Capital/ROI del agente tras cierre de operación:** `close_operation()` consulta `roi_total`, `operaciones_total` y `operaciones_ganadoras` actualizados en DB y llama al nuevo método `update_agent_live()` para reflejar el capital, ROI y win rate en la pestaña Agentes inmediatamente al cierre de cada SL/TP/EOD. Antes esto solo se actualizaba en eliminación.
    - **Redistribución de capital en Sheets:** `evolution_engine._redistribute_capital()` ahora itera sobre todos los agentes activos tras la UPDATE en DB y llama a `update_agent_live()` para cada uno. El nuevo capital igualado por el ciclo evolutivo aparece en Sheets al instante, sin esperar al próximo backfill.
    - **Safety-net semanal:** `manual_backfill.yml` añade `schedule: 0 6 * * 0` (domingos 06:00 UTC = 1:00 am Bogotá) que reescribe ambas pestañas desde la BD para corregir cualquier desincronización acumulada durante la semana.
  - **Nuevo método `SheetsLogger.update_agent_live()`:** actualización lightweight de Capital Final, ROI Total, Ops Total y Win Rate (4 celdas vía `batch_update`). No modifica Estado, Fecha Eliminación ni Razón Eliminación (esas las maneja `update_agent_status` cuando ocurre eliminación). Wrap en `try/except` con warning en `CellNotFound` y error log para excepciones generales — no interrumpe el trading si Sheets falla.
  - **Estado post-Sesión 11:** Pool $103.18 / 10 agentes = $10.32 c/u (pool +3.18% en 4 días de trading). Win rate promedio 62% (94 ops positivas vs 57 negativas, excluyendo 41 ops con P&G exacto $0 por precios entrada=salida en condiciones planas). 9 estrategias en Hall of Fame. Primer Gen2 en periodo de gracia (inmune por 2 días hábiles).

- **2026-05-20 (Sesión 10 — corrección modelo P&L y position sizing):**
  - **Problema raíz identificado:** el sizer (`sub_agent_risk._dynamic_position_size`) calculaba internamente lotes (~0.044) pero los almacenaba en `capital_usado` y `close_operation` los trataba como dólares de nocional. Resultado: P&L comprimido ~1162× (se mostraba $0.0000 en trades con ganancia real de ~$0.02). Esto impedía que el motor evolutivo diferenciara el fitness entre agentes.
  - **Fix de position sizing** (`agents/sub_agent_risk.py`): `_dynamic_position_size` ahora recibe `precio` y devuelve el **nocional en USD** = `lotes × 1000 × precio`. El techo de apalancamiento pasa de `equity × 20%` (dimensionalmente incorrecto, comparaba lotes con dólares) a `equity × 50` (50:1 de apalancamiento máximo, estándar forex minorista). Nuevas constantes: `_MAX_LEVERAGE = 50.0`, `_UNITS_PER_LOT = 1000.0`.
  - **Fix de pnl_porcentaje** (`agents/investor_agent.py`): `pnl_porcentaje` pasa a ser el ROI sobre el capital del agente (`pnl / capital_disponible × 100`) en lugar del retorno sobre el nocional. Indica de forma intuitiva cuánto movió la cuenta ese trade.
  - **Eliminación del override LLM de capital** (`agents/sub_agent_risk.py`): el LLM ya no puede sobrescribir `capital_a_usar`; el sizer siempre calcula el nocional heurísticamente. El system prompt del sub-agente C se actualiza para eliminar `capital_a_usar` del JSON esperado.
  - **Migración one-off del historial** (`scripts/recompute_pnl.py`): script ejecutado en producción que reconvirtió las 18 operaciones cerradas de Gen1 — actualizó `capital_usado` (ahora en USD: ~$33–$295), `pnl` (ahora real en USD: ej. op 373 BUY +5.5pips = +$0.0243) y `pnl_porcentaje` — y reconstruyó `capital_actual` y `roi_total` de los 10 agentes.
  - **Estado post-corrección:** todos los agentes muestran ROI ~+2% real. La próxima noche el juez evolutivo tendrá por primera vez señal clara de P&L en dólares para diferenciar fitness y tomar decisiones de selección/reproducción.

- **2026-05-20 (Sesión 9 — auditoría operativa post-migración):**
  - **Detección del problema:** las 10 operaciones SELL del 19-may se cerraron a las 02:02 am Bogotá del 20-may (no a las 10:45 pm del 19-may) al precio de mercado, no por SL/TP. Causa raíz: el cron `judge_daily.yml` se disparó con ~3.5 h de retraso (limitación conocida de GitHub Actions Cron). La auditoría también reveló que los 10 agentes Gen1 fueron sembrados con ADN idéntico (sin mutación) y que existía una ventana ciega operativa de 3 h sin monitoreo.
  - **Guardia EOD defensiva** (`cron/trade_monitor.py`): nueva función `_eod_guard()` que se ejecuta al inicio de cada `sync_once()`. Si detecta posiciones BUY/SELL con `timestamp_entrada < inicio_trading_utc_hoy`, llama a `force_close_all()` automáticamente. Elimina la dependencia exclusiva del scheduler de GitHub Actions para el cierre EOD.
  - **Ventana ciega cerrada** (`.github/workflows/trade_monitor.yml`): añadida quinta entrada de cron `*/15 4-6 * * 2-6` que cubre 11pm – 1:45am Bogotá (antes sin ningún monitor). Combinada con `_eod_guard`, la ventana máxima de exposición de posiciones huérfanas se reduce a 15 minutos.
  - **Manejo explícito de `UniqueViolation`** (`agents/investor_agent.py`): captura específica del error de violación de índice único parcial; en caso de concurrencia entre dos workflows, el segundo INSERT se ignora silenciosamente con log informativo (en vez de propagar como error genérico).
  - **Diversidad genética restaurada:** los 10 agentes Gen1 (`2026-05-19_01..10`) sembrados con `scripts/seed_gen1.py` tenían params idénticos, lo que impedía la evolución (todos con fitness ≥ 0, ninguno eliminable). Se aplicó mutación gaussiana con sigmas elevadas (σ_w=0.08, σ_p=0.12, σ_r=0.15) vía `scripts/diversify_gen1.py`. Diversidad confirmada: `atr_factor` 1.11–2.09, `risk_reward` 1.50–2.61, EMAs y RSI distintos por agente.
  - **Migración 007 (no-op):** el índice único parcial documentado como tal (Sesión 8: `idx_one_open_buysell_per_agent`) ya existía. La migración 007 se conserva como NO-OP para mantener continuidad en el numerado.
  - **Limpieza del repositorio:** eliminados archivos one-off ya cumplidos su propósito — `scratch/` (debug scripts), `*.bat` (despliegues manuales puntuales), `utils/diagnose_backfill.py`, `api/` (carpeta vacía) y `CALCULO_VARIABLES_Y_MOTOR_DECISION.md` (contenido consolidado en este documento).

- **2026-05-18 (Sesión 7 — reestructuración nocturna):**
  - **Horarios reasignados:**
    - Ventana de apertura de nuevas posiciones: 1:30 am – 11:00 pm Bogotá (06:30 UTC – 04:00 UTC del día siguiente). La ventana ahora cruza la medianoche UTC; `_within_trading_hours()` lo maneja explícitamente.
    - Monitor SL/TP / trailing: cada 15 minutos de 1:30 am a 10:30 pm Bogotá (último ciclo). `trade_monitor.yml` ahora declara 4 entradas de cron coordinadas para cubrir el rango sin choques con el Juez.
    - Cierre forzoso EOD: 10:45 pm Bogotá (03:45 UTC).
    - Ciclo evolutivo del Juez: 11:00 pm Bogotá (04:00 UTC del día siguiente).
    - `judge_daily.yml` se dispara a las 03:45 UTC y ejecuta `force-close-all` → `sleep 900` → ciclo evolutivo en un único job para garantizar la separación de 15 minutos.
  - **Nuevas env vars de horario:** `TRADING_START_TIME_UTC` y `TRADING_CUTOFF_TIME_UTC` reemplazan a las antiguas `TRADING_START_UTC` / `TRADING_CUTOFF_UTC` (que solo aceptaban horas enteras). Formato HH:MM. Defaults: `06:30` y `04:00`.
  - **Limpieza de OANDA:** la migración `002_oanda_integration.sql` queda deprecada (no-op) y la nueva migración `005_cleanup_oanda_columns.sql` elimina las columnas `oanda_trade_id`, `oanda_units`, `oanda_realized_pl` y su índice. El aplicativo nunca usó esa integración: el broker siempre fue simulado.
  - **`atr_period` retroactivo:** la migración `006_atr_period_backfill.sql` aplica `atr_period: 14` a TODOS los agentes existentes que no lo tenían en `params_smc`. La migración `003_smc_schema.sql` también incluye `atr_period: 14` en el default JSONB para nuevas instancias limpias.
  - **`pips_sl` poblado:** `agents/investor_agent._persist_operation` ahora calcula e inserta `pips_sl = abs(precio_entrada - stop_loss) * 10_000` para operaciones BUY/SELL. Las HOLD lo dejan NULL.
  - **Documentación de tabla `agentes` y `operaciones`** completada: ahora incluye `fecha_eliminacion`, `razon_eliminacion`, `pips_sl`, `pnl_porcentaje`, `created_at` y `updated_at`.
- **2026-05-18 (Sesión 7 — hotfix vespertino):** Corregido bug introducido en el snapshot de `ranking_historico`: el motor escribía `evento = 'supervivencia_gracia'`, valor que violaba el CHECK constraint del schema (solo admite `'supervivencia', 'eliminacion', 'nacimiento', 'evaluacion'`). Se uniformó a `'supervivencia'` para todos los supervivientes (tanto los protegidos por fitness > 0 como los inmunes en Periodo de Gracia). La distinción entre supervivencia normal e inmunidad por gracia se conserva en `logs_juez.datos_json.immune_agents` y `cycle_suspended`, así que no se pierde trazabilidad.
- **2026-05-18 (Sesión 7):** Implementación de Periodo de Gracia Operativa, Cuota Dinámica de Eliminación, Regla de Desempate Generalizado y Forzado de Diversidad Genética. Suspensión automática del ciclo en días de HOLD generalizado para preservar agentes jóvenes y veteranos rentables. Nuevas env vars: `GRACE_PERIOD_DAYS`, `DIVERSITY_VARIANCE_THRESHOLD`, `SIGMA_BOOST_FACTOR`. Logging extendido en `logs_juez.datos_json` con `cycle_suspended`, `immune_agents`, `eligible_veterans`, `cuota_aplicada`, `genetic_variance_cv`, `sigma_boost_applied`, `sigma_used`. Omisión de invocación al LLM en ciclos suspendidos.
- **2026-05-19 (Sesión 8 — migración BD + fix race condition):**
  - **Migración Neon → Supabase:** la BD fue migrada de Neon PostgreSQL (cuota gratuita agotada) a Supabase Free Tier. Se aplica el schema completo (5 tablas, 2 vistas, 1 trigger), se crea `scripts/seed_gen1.py` para sembrar la Generación 1 (`2026-05-19_01` a `_10`, $10 c/u, parámetros por defecto), y se actualiza `DATABASE_URL` en GitHub Actions, Vercel (app móvil: `https://mobile-app-smoky-phi.vercel.app`) y Streamlit Cloud.
  - **Conexión via Transaction Pooler:** Supabase usa PgBouncer en modo transacción (puerto 6543, IPv4) para compatibilidad con runners de GitHub Actions. Variables de entorno ahora incluyen `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` además de `DATABASE_URL`.
  - **Fix NameError en `close_operation`** (commit `3092565`): `precio_entrada` ahora se asigna antes del bloque if/else en `agents/investor_agent.py`, eliminando el `NameError` que crasheaba el monitor cuando `precio_entrada` era NULL en la BD.
  - **Fix race condition TOCTOU — posiciones duplicadas** (commit `4c15fe4`): `_persist_operation` reemplaza el `INSERT VALUES` simple por `INSERT ... SELECT ... WHERE NOT EXISTS (...)` para BUY/SELL. La comprobación de posición abierta y la inserción son ahora **atómicas** en la BD; si dos runs del workflow se solapan, el segundo obtiene `fetchone() = None` y aborta sin crear duplicado.
  - **Índice único parcial:** `CREATE UNIQUE INDEX idx_one_open_buysell_per_agent ON operaciones(agente_id) WHERE estado='abierta' AND accion IN ('BUY','SELL')` — segunda línea de defensa a nivel de BD para garantizar la invariante.
  - **Limpieza de duplicados:** 4 operaciones duplicadas (ids 8, 10, 12, 14) abiertas por la race condition en el primer run post-migración fueron marcadas como `cancelada`; se decrementó `operaciones_total` en los 4 agentes afectados.
  - **Modelo LLM actualizado:** `DEEPSEEK_MODEL=deepseek-reasoner` en `.env` y GitHub Secrets. `base_agent.py` lee el modelo desde env var con fallback a `deepseek-chat`. Todas las conexiones verificadas: DB OK, LLM OK, Yahoo Finance OK (EUR/USD 1.16117), Google Sheets OK.

- **2026-05-10:** Documento inicial. Refleja el estado del sistema en Generación 1 del nuevo run iniciado el 2026-05-11.
