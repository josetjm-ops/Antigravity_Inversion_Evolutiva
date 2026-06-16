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

**Inversión Evolutiva** es un laboratorio de trading algorítmico que aplica algoritmos genéticos al mercado de divisas EUR/USD. El sistema mantiene una población de **15 agentes** de software (5 por cada uno de los 3 arquetipos estratégicos) que compiten entre sí para determinar cuáles estrategias de trading son más rentables ajustadas al riesgo. El sistema **garantiza siempre 15 agentes activos** (Sesión 19): si tras la reproducción normal quedan cupos vacantes, el motor los llena mediante hasta 8 rondas de torneo con umbral OOS y, como último recurso, clona el mejor agente del Hall of Fame. La única excepción es que Yahoo Finance esté completamente caído, en cuyo caso la recuperación se omite y se completa en el siguiente ciclo con datos.

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
│  │  - Trailing stop │       │  - Elimina hasta 9   │            │
│  │  - Abre pos. new │       │  - Crea hasta 9 hijos│            │
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

Cada agente tiene un ID único con formato `YYYY-MM-DD_NN` donde `YYYY-MM-DD` es su fecha de nacimiento y `NN` es su número de orden ese día (01, 02, …). Desde Sesión 25, `NN` es **consecutivo real**: solo los agentes que efectivamente se insertan consumen número (un slot rechazado por el umbral OOS ya no deja un hueco), así que el primer agente nacido un día siempre es `_01`.

Ejemplos: `2026-05-11_01`, `2026-05-11_07`

### Genoma — cuatro bloques de parámetros

Cada agente lleva cuatro diccionarios JSON que constituyen su "ADN". Estos parámetros evolucionan por mutación y crossover entre generaciones.

#### `params_tecnicos` — Sub-agente A

| Gen | Rango | Descripción |
|---|---|---|
| `rsi_periodo` | 5–50 (entero) | Período de cálculo del RSI |
| `rsi_sobrecompra` | 55–90 | Umbral RSI para señal SELL (solo en modo `reversion`) |
| `rsi_sobreventa` | 10–45 | Umbral RSI para señal BUY (solo en modo `reversion`) |
| `rsi_modo` | `"momentum"` / `"reversion"` | Filosofía RSI: `"momentum"` usa cruce del nivel 50 (default desde Sesión 15); `"reversion"` usa sobrecompra/sobreventa legacy. Solo cambia por crossover, no por mutación gaussiana. |
| `rsi_zona_muerta` | 1.0–15.0 | Banda de neutralidad (pips de RSI) alrededor del nivel 50 en modo momentum. RSI dentro de ±zona_muerta → HOLD. Gen mutable gaussianamente. |
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
| `peso_sesgo_tendencia` | 0.20–0.65 | Intensidad del sesgo de tendencia HTF cuando no hay eventos de alto impacto. El sub-agente B devuelve BUY/SELL con esta confianza en lugar de HOLD plano. Gen mutable gaussianamente (desde Sesión 15). |

#### `params_riesgo` — Sub-agente C

| Gen | Rango | Descripción |
|---|---|---|
| `stop_loss_pct` | 0.5%–5% | Fallback de SL cuando no hay ATR (legado) |
| `take_profit_pct` | 1%–10% | Fallback de TP cuando no hay ATR (legado) |
| `max_drawdown_diario_pct` | 3%–20% | Límite de pérdida diaria por agente |
| `capital_por_operacion_pct` | 20%–80% | Porcentaje del capital a utilizar por operación |
| `umbral_confianza_minima` | 0.45–0.85 | Confianza mínima para emitir BUY o SELL |
| `peso_tecnico_vs_macro` | 0.30–0.75 | Peso de la señal técnica sobre la macro (complemento = peso macro) |

#### `params_smc` — Smart Money Concepts + ATR + Régimen + Ruptura

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
| `atr_factor` | 0.8–1.8 | Multiplicador del ATR para calcular el SL dinámico. Tope 3.0 → 1.8 en Sesión 22: con ATR alto, 3.0 producía SL de 60+ pips cuyo TP era inalcanzable intradía (fitness sin señal). |
| `trailing_activation_pips` | 5–40 | Pips de ganancia para activar el trailing stop |
| `trailing_distance_pips` | 5–25 | Distancia del trailing stop al precio extremo favorable |
| `atr_period` | 7–21 (entero) | Período del ATR (Average True Range) de Wilder |
| `htf_filter_enabled` | 0 / 1 | Habilita el filtro de tendencia de temporalidad superior (1h). Activo en S1/S3; siempre 0 en S2 (opera contra-tendencia por diseño). |
| `breakout_lookback_bars` | 10–50 (entero) | Velas 15m para buscar el máximo/mínimo de estructura (S3 ruptura). Ruptura: cierre actual > máximo de `lookback` velas anteriores (o < mínimo). Gen mutable gaussianamente. (desde Sesión 16 — Fase 2) |
| `breakout_min_pips` | 3.0–15.0 | Distancia mínima de la ruptura en pips para considerarla válida (S3). (desde Sesión 16 — Fase 2) |
| `peso_breakout` | 0.20–0.70 | Peso del score de ruptura en el ensamble S3. (desde Sesión 16 — Fase 2) |
| `adx_period` | 14 (fijo) | Período del ADX para el clasificador de régimen. No se muta gaussianamente. |
| `adx_threshold` | 25.0 (fijo) | Umbral ADX para clasificar el régimen: ≥ 25 = TENDENCIA, < 25 = RANGO. No se muta. |
| `be_activation_r` | 0.3–1.0 | Break-even stop: al ganar este múltiplo de R, el SL sube a entrada ± fricción. Gen mutable gaussianamente. (Sesión 22) |
| `exit_on_reversal` | 0 / 1 | Salida por señal contraria fuerte: 1 = cierra la posición si la señal técnica determinista es opuesta con confianza ≥ umbral propio y la ganancia ≥ `min_profit_for_exit_r` × R. Muta por **bit-flip** (prob. 10% por crianza) — la evolución decide si el rasgo aporta. Sembrado 50/50 en la población por la migración 011. (Sesión 22) |
| `min_profit_for_exit_r` | 0.2–1.0 | Piso de ganancia (en R) para permitir la salida por señal contraria. Nunca se cierra en pérdida por señal. Gen mutable gaussianamente. (Sesión 22) |

#### `especie` — Arquetipo estratégico (columna en `agentes`, no en `params_smc`)

| Valor | Filosofía | Cuándo opera | HTF filter |
|---|---|---|---|
| `tendencia` | Momentum multi-timeframe: RSI cruce 50 + EMA + MACD alineados con tendencia 1h | ADX ≥ 25 (mercado con dirección clara) | Activo (veta señales contra-tendencia) |
| `reversion` | Mean-reversion en extremos RSI + OB/FVG estructural en mercado lateral | ADX < 25 (mercado en rango) | Desactivado (opera deliberadamente contra-tendencia) |
| `ruptura` | Breakout de estructura (cierre fuera del rango N velas) confirmado por range_spike | ADX ≥ 25 o NEUTRAL — bloqueada en RANGO desde Sesión 17 (`RUPTURA_SOLO_TENDENCIA=true`) | Activo |

La especie es **inmutable por mutación gaussiana**: el hijo hereda siempre la especie del agente eliminado que reemplaza (reproducción como-por-como), preservando la distribución de arquetipos. Solo cambia si la evolución decide que una especie no merece representación (pero el motor garantiza mínimo 2 agentes por especie activos).

### Capital

- **Capital inicial:** $10 USD por agente en la Generación 1 ($100 USD total). Al expandir la población a 15 agentes (Sesión 16), el pool se redistribuyó: **$6.5757 por agente** (~$98.64 total).
- **Capital actual:** fluctúa con el P&L de cada operación.
- **Redistribución diaria:** al final de cada ciclo evolutivo el Juez suma el pool total y lo divide en partes iguales entre los 15 agentes activos. El mérito individual es lo único que determina la supervivencia, no el capital acumulado.

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

### Sub-agente A — Análisis Técnico (enrutado por especie desde Sesión 16)

`analyze()` recibe la `especie` del agente y construye el ensamble ponderado según el arquetipo:

**S1 — tendencia** (ensamble original):

| Indicador | Señal BUY | Señal SELL | Peso |
|---|---|---|---|
| RSI modo momentum | Cruce al alza nivel 50, o RSI > 50 fuera de zona muerta | Cruce a la baja, o RSI < 50 | `peso_rsi` |
| Cruce EMA | EMA rápida > EMA lenta | EMA rápida < EMA lenta | `peso_ema` |
| MACD histograma | > 0.00005 | < −0.00005 | `peso_macd` |
| FVG activo | Dirección BULL | Dirección BEAR | `peso_fvg` |
| Order Block | Dirección BULL | Dirección BEAR | `peso_ob` |

**S2 — reversion** (pesos ajustados, contra-tendencia):
- RSI modo `reversion` (sobrecompra/sobreventa) × 1.5
- OB y FVG × 1.5 (entradas estructurales en extremos de precio)
- EMA y MACD × 0.4 (contexto reducido — el mercado está en rango, no en tendencia)
- HTF filter desactivado (`htf_filter_enabled=0`): opera deliberadamente en extremos contra la tendencia corta.

**S3 — ruptura** (breakout primario):
- Breakout de estructura × `peso_breakout` (0.20–0.70, default 0.40): cierre fuera del máximo/mínimo de `breakout_lookback_bars` velas anteriores. Confianza 0.65–0.90, amplificada si hay range_spike confirmatorio.
- RSI, EMA × 0.5 (contexto secundario)
- FVG, OB (pesos normales)

**Clasificador de régimen ADX (compartido, calculado una vez por ciclo):**
`calc_regime()` calcula el ADX(14) sobre las velas de 15m y clasifica:
- ADX ≥ 25 → `TENDENCIA` (S1 y S3 habilitados; S2 bloqueado)
- ADX < 25 → `RANGO` (S2 habilitado; S1 bloqueado; S3 también bloqueado desde Sesión 17 cuando `RUPTURA_SOLO_TENDENCIA=true`, el default)
- Sin datos → `NEUTRAL` (todas las especies pueden operar)

Este gate se aplica en `trade_monitor._evaluate_new_positions()` **antes** del pipeline A→B→C. Un agente bloqueado por régimen incompatible cuenta como evaluado pero no genera señal.

**Detección de ruptura `detect_breakout()` (S3):**
- Ruptura alcista: `close[-1] > max(close[-lookback-1:-1])` con diferencia ≥ `breakout_min_pips`.
- Ruptura bajista: `close[-1] < min(close[-lookback-1:-1])` con diferencia ≥ `breakout_min_pips`.
- Usa el cierre (no la mecha) para confirmar que el precio **cerró** fuera del rango, evitando falsos positivos.

**RSI — modo momentum (default desde Sesión 15):**
- Cruce al alza (prev ≤ 50 → actual > 50): BUY fuerte (0.60–0.85)
- Cruce a la baja: SELL fuerte (0.60–0.85)
- Sin cruce, fuera de zona muerta: BUY/SELL débil (0.40–0.60)
- Dentro de zona muerta: HOLD (0.35)

**Range spike condicionado:** amplifica ×1.15 (máx. 0.95) solo si la dirección de la última vela confirma la señal. Spike contrario: confianza intacta.

**Filtro HTF:** activo en S1 y S3. Veta BUY si HTF = BEAR, y SELL si HTF = BULL. Desactivado en S2 por diseño.

La señal ponderada emite BUY/SELL si `score_dominante > 0.40` y la ventaja sobre la dirección opuesta es > 0.15. Si no hay dominancia clara, emite HOLD.

### Sub-agente B — Análisis Macro

1. Descarga del calendario económico (Finnhub) los eventos de las próximas `ventana_noticias_horas` horas.
2. Descarga titulares de noticias forex recientes.
3. Envía todo al LLM DeepSeek y recibe un JSON con `recomendacion`, `confianza` y `sentimiento_score` (-1.0 a +1.0).
4. Aplica los umbrales genéticos del agente: si `sentimiento_score` normalizado ≥ `umbral_sentimiento_compra` → BUY; si ≤ `umbral_sentimiento_venta` → SELL.
5. **Sesgo de tendencia HTF (desde Sesión 15):** si no hay eventos de alto impacto y el LLM devuelve HOLD con baja confianza, en lugar de quedarse en HOLD plano aplica `_sesgo_tendencia()`: consulta la dirección EMA50/EMA200 en 1h y emite BUY (BULL) o SELL (BEAR) con confianza = `min(0.55, peso_sesgo_tendencia)`. Esto evita que el sub-agente B sea siempre neutral en días sin noticias — alinea el sesgo macro con la tendencia de fondo real del mercado.

### Sub-agente C — Riesgo y Decisión Final

**Combinación de señales:**
- Si Técnico y Macro coinciden: promedio ponderado por `peso_tecnico_vs_macro`.
- Si uno emite HOLD: prevalece la señal concreta del otro.
- Si hay conflicto BUY vs SELL: gana la señal más fuerte solo si supera confianza 0.75; de lo contrario HOLD.
- Si la confianza combinada < `umbral_confianza_minima`: forzar HOLD.

**Jerarquía de Stop Loss (en orden de prioridad):**
1. **Order Block estructural** — nivel del OB no mitigado (fuente más fuerte).
2. **Fair Value Gap** — nivel del FVG no rellenado.
3. **ATR dinámico** — `precio ± (ATR × atr_factor)`. Piso: `MIN_SL_PIPS` (default **10 pips**), techo: `MAX_SL_PIPS` (default **35 pips**, Sesión 22). El piso protege el SL del ruido normal de las velas de 1m que usa el monitor intra-vela; el techo garantiza que el TP resultante (R:R ≥ 1.5) sea alcanzable antes del cierre EOD. El techo aplica a TODAS las fuentes de SL — el SL estructural (OB/FVG) que supere `MAX_SL_PIPS` se descarta y cae a la rama ATR. (Fase 0 desde Sesión 16; techo desde Sesión 22)
4. **Porcentaje fijo** — `stop_loss_pct` del precio de entrada (fallback si ATR = 0).

> **Fricción de mercado (Fase 0, desde Sesión 16):** al cerrar cada operación BUY/SELL, `close_operation()` descuenta `TRADE_FRICTION_PIPS` (default 1.4 pips round-trip ≈ 0.8 spread + 0.6 slippage) del P&L, independientemente del resultado. Sin esto el simulador es optimista y la evolución converge hacia estrategias que solo "funcionan" sin costos. Con fricción modelada, el fitness refleja el edge real neto.

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
_MODEL  = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")   # default y producción: deepseek-chat
                                                          # (no hay override DEEPSEEK_MODEL configurado)
_client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)
model       = _MODEL
temperature = 0.1     # respuestas muy deterministas
max_tokens  = 512
timeout     = 30 seg
```

> **Modelo activo en producción:** `deepseek-chat` (default del código). No existe un secret ni variable `DEEPSEEK_MODEL` configurado, ni se mapea en el bloque `env:` de ningún workflow — verificado el 2026-06-15 con `gh secret list` / `gh variable list`. Para usar otro modelo (p. ej. `deepseek-reasoner`), hay que añadir `DEEPSEEK_MODEL` como secret **y** mapearlo en cada workflow.

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
  ├── SÍ + gen exit_on_reversal=1 → Salida por señal contraria (Sesión 22):
  │         Con los datos de mercado ya descargados se recalcula la señal
  │         técnica DETERMINISTA del agente (sin LLM, reason() neutralizado
  │         como en el backtester). La posición se cierra solo si se cumplen
  │         LAS TRES condiciones:
  │           1. Señal OPUESTA a la posición (BUY abierto + señal SELL o viceversa).
  │           2. Confianza >= umbral_confianza_minima del propio agente
  │              (la misma vara que exige para ABRIR en contra).
  │           3. Ganancia >= min_profit_for_exit_r × R. Nunca se cierra en
  │              pérdida por señal — para eso está el SL.
  │         Ambos parámetros son GENES: la evolución decide si el rasgo aporta.
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

### Break-even stop (Sesión 22 — gen `be_activation_r`)

Entre el "no proteger nada" y el trailing (que exige +1R completo) existía un
hueco: una posición con +24 pips de ganancia flotante sobre un SL de 35 pips
podía devolverlo todo (caso op #9284, 2026-06-12). El break-even stop lo cubre:

1. Al acumular `be_activation_r × R` de ganancia flotante (gen mutable,
   rango 0.3–1.0, default 0.6), el SL se mueve a **entrada ± fricción**
   (`TRADE_FRICTION_PIPS`): la operación ya no puede terminar en pérdida.
2. No recorta el potencial: el TP y el trailing siguen intactos por encima.
3. Se aplica vela a vela dentro del verificador intra-bar, DESPUÉS del chequeo
   de SL/TP de la vela (misma convención anti-lookahead del trailing) y
   **nunca empeora** un SL ya mejorado por el trailing.
4. Replicado en `evolution/backtester.py`: el fitness OOS castiga o premia el
   gen con el mismo comportamiento que tendrá en producción.

### Trailing stop dinámico

El trailing stop protege ganancias sin limitar el potencial alcista:

1. Se activa cuando la posición acumula `trailing_activation_pips` de ganancia (gen del agente, default 15 pips).
2. **Fase 0 (desde Sesión 16):** la activación nunca ocurre antes de +1R de ganancia flotante (R = distancia original del SL en pips). Si el gen pedía activar antes de 1R, se eleva a 1R automáticamente. Así un trade ganador jamás puede recortarse por debajo del break-even.
3. La distancia de seguimiento se acota a `min(trailing_distance_pips, 0.7 × activation_pips)` para garantizar que el profit bloqueado al activar sea siempre > 0.
4. Una vez activo, el SL se mueve a `precio_extremo_favorable - trailing_distance_pips`.
5. El SL **nunca retrocede**: solo puede mejorar (moverse a favor).
6. Desde Sesión 13 el trailing se aplica **vela a vela** dentro del verificador intra-bar usando el extremo favorable de cada vela (low para SELL, high para BUY). Resultado: ratcheo preciso, casi idéntico al de un broker tick-by-tick.

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
1. EVALUACIÓN DE FITNESS — Expectancy ajustada por riesgo (Fase 1, desde Sesión 16)
   ────────────────────────────────────────────────────────────────────────────────
   expectancy = win_rate × avg_win − (1 − win_rate) × avg_loss
   (P&L ya es neto de fricción: TRADE_FRICTION_PIPS descontado al cerrar cada op)

   confianza_estadistica = LEAST(1.0, n_trades / MIN_SAMPLE_TRADES)
   (escala 0→1 mientras el agente acumula muestra mínima de 15 trades cerrados)

   fitness = (expectancy / (max_drawdown + 0.01))
             × confianza_estadistica
             − penalidad_overtrading

   Penalidad: −0.5 si avg_ops_dia > 3 Y win_rate < 50%

   Por qué expectancy y no ROI: con ROI, 3 trades de suerte inflan el fitness.
   La expectancy neta por trade es estadísticamente estable y exige que el edge
   sea real, no acumulable por azar. La confianza estadística bloquea la selección
   de agentes con muestra insuficiente.

2. RANKING + FILTRO DE ELEGIBILIDAD (Muestra mínima + Periodo de Gracia)
   ──────────────────────────────────────────────────────────────────────
   Ordenar agentes por fitness DESC.
   Desempate: fecha_nacimiento DESC, id DESC.

   Filtrar agentes INMUNES (no elegibles para eliminación esa tarde):
     A. GRACIA (inviolable): operaciones_total == 0 Y edad < GRACE_PERIOD_DAYS días HÁBILES
     B. MUESTRA MÍNIMA HÍBRIDA (Fase 4, Sesión 17):
        n_trades < MIN_SAMPLE_TRADES  Y  edad < MIN_SAMPLE_DAYS días hábiles.
        Ambas condiciones simultáneas — si el agente lleva ≥ MIN_SAMPLE_DAYS días
        en producción ya es evaluable aunque tenga pocos trades (especie en régimen
        adverso, baja frecuencia de señales).

        Excepción — tope de pérdida (Fase 3, Sesión 17):
        Si B aplica pero roi_total ≤ −IMMUNITY_MAX_LOSS_PCT (default 8 %), la
        inmunidad se revoca: el agente pasa a eligible con flag _immunity_revoked
        y es candidato a eliminación. Documentado como "Inmunidad revocada por
        drawdown" en razon_eliminacion. No afecta la inmunidad A.
   Los inmunes mantienen estado 'activo' automáticamente.

3. CUOTA DINÁMICA + PROTECCIÓN DE ESPECIES
   ─────────────────────────────────────────
   Sobre los agentes ELEGIBLES (no inmunes):
     - Se ordenan por (fitness ASC, fecha_nacimiento ASC, id ASC)
       → primeros candidatos son veteranos rezagados.
     - Solo eliminables los que tienen fitness_score <= 0.
     - n_eliminate = min(N_ELIMINATE=9, len(eliminables))  ← máx. 3 por especie × 3 especies

   Protección de diversidad de especies (Fase 2): nunca se elimina un agente
   si hacerlo bajaría su especie (tendencia/reversion/ruptura) por debajo de
   MIN_AGENTS_PER_ESPECIE (default 2). Se saltea ese candidato y se toma el
   siguiente peor de una especie con ≥ 3 agentes.

4. ¿CICLO SUSPENDIDO?
   ───────────────────
   Si cuota dinámica = 0: ciclo suspendido, sin eliminación/reproducción normal.
   La recuperación de cupos vacantes (paso 7) aún se intenta si hay déficit de población
   y los datos de Yahoo Finance están disponibles.

5. FORZADO DE DIVERSIDAD GENÉTICA
   ───────────────────────────────
   CV < DIVERSITY_VARIANCE_THRESHOLD → sigmas × SIGMA_BOOST_FACTOR (default 2.0).

6. REPRODUCCIÓN CON TORNEO DE CANDIDATOS + UMBRAL DE CALIDAD (Fases 3/1, desde Sesión 16/17)
   ─────────────────────────────────────────────────────────────────────────────────────────────
   Para cada cupo vacante:
     a. Descargar datos históricos: fetch_backtest_data() — 1 request compartido
        para todos los hijos del ciclo: 60d/15m + 3mo/1h de Yahoo Finance.
     b. Generar N_CANDIDATE_CHILDREN candidatos (default 3), cada uno con padres
        distintos (selección fitness-proporcional). PUREZA DE ESPECIE (Sesión 25):
        cada candidato lleva ≥1 padre de la especie del hijo, dominante al 60%
        (p1_weight=0.6), mientras la especie no esté extinta.
     c. Ejecutar run_backtest() en cada candidato: walk-forward OOS 20 días,
        mismo pipeline de producción (señales + régimen ADX + ruptura-en-RANGO
        gate + SL/TP + fricción), sin LLM (heurística pura, determinista).
     d. Umbral de calidad (Fase 1, Sesión 17): el mejor candidato solo se despliega
        si fitness OOS > TOURNAMENT_MIN_OOS_FITNESS (default 0.0, estrictamente mayor)
        Y n_trades OOS >= TOURNAMENT_MIN_OOS_TRADES (default 5).
        - Si no pasa: fallback al Hall of Fame — se generan 3 hijos de padres HoF,
          con la misma garantía de ≥1 padre de la especie (pureza dura, Sesión 25),
          y se aplica el mismo umbral.
        - Si tampoco pasa: el slot queda vacante (registrado en logs_juez como
          "slot_vacante"). El pool continúa con un agente menos hasta el próximo ciclo.
     e. Si Yahoo Finance falla → fallback a crianza directa sin backtest (sin umbral).
     f. El hijo hereda la especie del eliminado (como-por-como).
     g. S2 hijos: rsi_modo=reversion, htf_filter_enabled=0 forzados post-crossover.
     h. INSERT en agentes con columna especie. capital_inicial = cuota del pool.

   Selección de padres: probabilidad proporcional a fitness_score.
     - Caso especial (Fase 2, Sesión 17): si todos los agentes del pool tienen
       fitness_score ≤ 0 (pool completamente negativo), los pesos se recalculan
       usando el fitness OOS del backtest (corrida una vez por agente y cacheada
       por ciclo). Esto evita selección aleatoria uniforme cuando el pool está en
       crisis — se prefieren los que el OOS sugiere que son mejores.
   Agentes con < MIN_SAMPLE_TRADES tienen fitness ≈ 0 → raramente seleccionados.

7. RECUPERACIÓN DE CUPOS VACANTES — GARANTÍA DE 15 (Sesión 18 / 19)
   ────────────────────────────────────────────────────────────────────
   Después de la reproducción (o en ciclo suspendido), el motor calcula el déficit
   por especie: TARGET_AGENTS_PER_ESPECIE − n_activos_especie y llena TODOS los
   cupos faltantes (Sesión 19: sin tope por ciclo). Para cada cupo:
     a. Usar los mismos datos de backtest ya descargados (sin nueva descarga de red).
     b. Hasta REPOPULATION_MAX_ATTEMPTS_PER_SLOT rondas (default 8) de
        (torneo N candidatos → umbral OOS) seguido de (HoF N candidatos → umbral OOS).
        Se detiene en cuanto un candidato supera el umbral. Cada candidato lleva
        ≥1 padre de la especie (pureza dura, Sesión 25).
     c. DEGRADACIÓN 1 (Sesión 21): si tras agotar las rondas nadie pasa el umbral
        estricto, entra el MEJOR CANDIDATO DE CRUCE visto en todas las rondas
        (origen='mejor_candidato_oos') — un hijo de dos padres distintos con
        muestra OOS corta vale más que un clon sin cruce. El cruce 60/40 nunca
        se abandona.
     d. DEGRADACIÓN 2 — último recurso real (Sesión 21): si ningún pool tiene 2
        padres para criar candidatos, se cruzan los DOS MEJORES genomas distintos
        disponibles entre HoF y pool (origen='forzado_cruce'); el de la especie
        correcta es siempre el padre dominante (60% del genoma, p1_weight=0.6
        explícito). Un agente eliminado puede aportar como uno de los dos padres,
        pero NUNCA ser el genoma único. Auto-clon (origen='forzado_clon_unico')
        SOLO si existe literalmente un genoma activo en el sistema.
        ⚠️ El clon forzado de Sesión 19 (origen='forzado_hof'/'forzado_pool',
        padre==madre) queda ELIMINADO: el 2026-06-12 produjo 4 hijos sin cruce,
        3 de ellos del mismo genoma de otra especie y 1 de un agente eliminado
        esa misma noche. Ver Sesión 21 en el historial.
     e. Si Yahoo Finance no está disponible (sin datos de mercado) → omitir
        silenciosamente: no hay base ni para validar ni para criar (fallback sin Yahoo).
   Los agentes recuperados se insertan en la misma transacción DB y reciben capital
   de la redistribución de ese mismo ciclo.
   Trazabilidad: `slots_recuperados` y `deficit_restante` en `logs_juez.datos_json`.
   Ciclo suspendido: si hay recuperados → la redistribución de capital sí se ejecuta.

8. RAZONAMIENTO LLM (solo si el ciclo NO está suspendido, o si hubo recuperados)

9. PERSISTENCIA EN LOGS (logs_juez: evaluacion_diaria, eliminacion, nuevo_agente)

10. REDISTRIBUCIÓN DE CAPITAL
    pool_total / n_agentes_activos → todos inician el día siguiente igualados.
    Incluye agentes recuperados en el paso 7.
```

### Cálculo del Fitness — Expectancy ajustada (Fase 1)

```sql
-- Por agente (n_trades = operaciones cerradas):
expectancy = (n_wins/n_trades) × avg_win − (1 − n_wins/n_trades) × avg_loss

-- confianza estadística (ramp-up hasta 15 trades):
confianza = LEAST(1.0, n_trades / 15)

-- fitness final:
fitness = (expectancy / (max_drawdown + 1)) × confianza − penalidad_overtrading

-- max_drawdown: máxima caída desde pico del capital acumulado
-- avg_win / avg_loss: ya son netos de fricción (1.4 pips descontados al cerrar)
```

### Backtester Walk-Forward (Fase 3)

```
Walk-forward split:
  Train    : primeros BACKTEST_TRAIN_DAYS (40d) — warmup de indicadores
  Validate : últimos  BACKTEST_VALIDATE_DAYS (20d) — período OOS

Dentro del período OOS, la simulación replica producción exactamente:
  - calc_signals() con los mismos genes del candidato
  - Clasificador ADX: S1 bloqueado en RANGO, S2 bloqueado en TENDENCIA,
    S3 bloqueado en RANGO si RUPTURA_SOLO_TENDENCIA=true (Sesión 17)
  - SubAgentTechnical.analyze(especie=...) — ensamble por arquetipo
  - SubAgentRisk._compute_levels() — SL ≥ 10 pips, R:R objetivo
  - check_sl_tp_intrabar() sobre cada vela 15m: SL/TP exactos
  - Fricción TRADE_FRICTION_PIPS descontada por trade

Rendimiento: ~3.5s/candidato, ~53s por ciclo completo (5 slots × 3 candidatos).
Margen: 11 minutos libres del workflow judge_daily (timeout 12 min).
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

### Creación — cinco escenarios

| Escenario | Mecanismo |
|---|---|
| **Inicio del sistema** | Script manual de siembra con parámetros fijos o copiados de agentes previos |
| **Reproducción diaria** | El Juez selecciona 2 padres del pool de supervivientes elegibles (fitness-proporcional; pesos OOS si todos pierden) y los cruza con mutación. Torneo de 3 candidatos backtesteados OOS — solo se despliega el mejor si supera el umbral de calidad (Sesión 17) |
| **Fallback Hall of Fame** | Si ningún candidato del torneo supera el umbral OOS, se crían 3 candidatos con genes del Hall of Fame (misma especie primero) y se aplica el mismo umbral. Si tampoco pasan: slot vacante (Sesión 17) |
| **Recuperación de cupos — garantía de 15 (Sesión 18 / 19 / 21)** | En cada ciclo el motor detecta el déficit por especie (`TARGET_AGENTS_PER_ESPECIE − activos`) y llena TODOS los cupos (sin tope). Por cupo: hasta `REPOPULATION_MAX_ATTEMPTS_PER_SLOT` rondas de torneo→HoF con umbral OOS; si nadie pasa, **el mejor candidato de cruce** (`origen='mejor_candidato_oos'` — siempre dos padres distintos); si ningún pool tiene 2 padres, **cruce forzado de los 2 mejores genomas distintos** (`'forzado_cruce'`). Auto-clon solo con un único genoma activo en el sistema. Solo se omite si Yahoo Finance está caído. |
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

La cuota de eliminación no es rígida. Cada tarde el motor calcula cuántos agentes salen, **con un máximo de N_ELIMINATE (default 9 = 3 por especie × 3 especies) y un mínimo de 0**, aplicando tres salvaguardas:

**Salvaguarda 1 — Periodo de Gracia / Muestra mínima híbrida (Fases 3-4, Sesión 17):**
- **Periodo de Gracia (inviolable):** `operaciones_total == 0` y edad < `GRACE_PERIOD_DAYS` días hábiles.
- **Muestra mínima híbrida:** `n_trades < MIN_SAMPLE_TRADES` (15) **Y** `edad < MIN_SAMPLE_DAYS` (7 días hábiles). Si el agente cumple una sola condición ya es elegible, evitando inmunidad perpetua en especies de baja frecuencia.
- **Tope de pérdida revoca inmunidad por muestra:** si el agente está protegido solo por muestra insuficiente (no por Gracia) y su `roi_total ≤ −IMMUNITY_MAX_LOSS_PCT` (default 8 %), la inmunidad se revoca y pasa a eligible. Documentado en `razon_eliminacion` como "Inmunidad revocada por drawdown".

**Salvaguarda 2 — Protección de fitness positivo:** solo son eliminables los agentes elegibles con `fitness_score <= 0` (negativo o cero). Un veterano rentable nunca se elimina solo para cumplir la cuota.

**Salvaguarda 3 — Diversidad de especies:** nunca se elimina un agente si hacerlo bajaría su especie por debajo de `MIN_AGENTS_PER_ESPECIE` (default 2). Con 5 agentes por especie, el máximo eliminable por especie es 3.

**Orden de eliminación (desempate generalizado):** los candidatos elegibles se ordenan por `(fitness_score ASC, fecha_nacimiento ASC, id ASC)`. Eso significa que ante empate de fitness, los **veteranos rezagados** salen primero y los **agentes jóvenes** se preservan — un complemento simétrico al desempate del ranking de supervivencia.

**Tres escenarios posibles cada tarde:**

| Escenario | Eliminados | Nacimientos | Razón |
|---|---|---|---|
| Día normal con veteranos negativos | 1 a 9 | 0 al mismo número (reproducción) + recuperación de TODO el déficit hasta 15 (mejor candidato de cruce si nadie pasa OOS) | Bottom por fitness, respetando máx 3/especie |
| Día de HOLD generalizado | 0 | recuperación de TODO el déficit hasta 15, si Yahoo disponible | Todos los activos están en inmunidad/gracia |
| Veteranos rentables protegidos | 0 | recuperación de TODO el déficit hasta 15, si Yahoo disponible | Todos los elegibles tienen fitness > 0 |
| Yahoo Finance caído | 0 a 9 | sin recuperación (no hay datos para validar ni clonar) | Población puede quedar < 15 ese día; se recupera al siguiente ciclo con datos |

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
| `params_smc` | JSONB | Genes SMC + ATR + Régimen + Ruptura (incluye `breakout_*`, `adx_*` desde migración 010) |
| `especie` | VARCHAR(20) | Arquetipo estratégico: `tendencia` / `reversion` / `ruptura`. Columna añadida en migración 010. Inmutable por mutación gaussiana — solo cambia entre generaciones por regla de sustitución como-por-como. |
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
| `slots_vacantes` | array | Slots no cubiertos en la reproducción (Fase 1, Sesión 17): `[{id, especie, razon}]`. Vacío si todos los cupos se llenaron. |
| `slots_recuperados` | array | Cupos recuperados en este ciclo (Sesión 18 / 19 / 21): `[{id, especie, fitness_oos, origen}]` donde `origen` ∈ `{"torneo", "hall_of_fame", "mejor_candidato_oos", "forzado_cruce", "forzado_clon_unico"}`. `mejor_candidato_oos` = mejor hijo de cruce cuando nadie superó el umbral; `forzado_cruce` = cruce de los 2 mejores genomas distintos cuando no hay pools para torneo; `forzado_clon_unico` = auto-clon con un solo genoma activo (excepcional). Los antiguos `forzado_hof`/`forzado_pool` (clones padre==madre, Sesión 19) fueron eliminados en Sesión 21. Vacío si no hubo recuperación. |
| `deficit_restante` | object | Déficit por especie no cubierto tras la recuperación (Sesión 18): `{especie: n}`. Vacío si no quedó déficit. |
| `insight_mercado` | string | Comentario del LLM (vacío en días suspendidos sin recuperación) |
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
| **Poblacion** | Tabla de ranking en vivo, KPIs: ROI top, win rate, pool total. Toggle **"Por especie"** para ver los 15 agentes agrupados en 3 secciones (tendencia / reversión / ruptura) con stats de grupo. |
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

Todos los workflows usan **`actions/checkout@v6`**, **`actions/setup-python@v6`** y **`actions/github-script@v9`** (compatibles con **Node.js 24**). Las versiones previas sobre Node.js 20 fueron actualizadas en Sesión 12 (`checkout`/`setup-python`) y Sesión 20 (`github-script@v7 → @v9`, antes del forzado de Node 24 del 16-jun-2026).

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
  AGENTS_ELIMINATE_PER_CYCLE   = 9     (3 por especie × 3 especies)
  MUTATION_SIGMA_WEIGHTS       = 0.05
  MUTATION_SIGMA_PERIODS       = 0.08
  MUTATION_SIGMA_RISK          = 0.10
  MIN_ROI_FOR_HALL_OF_FAME     = 0.05
  GRACE_PERIOD_DAYS            = 2
  DIVERSITY_VARIANCE_THRESHOLD = 0.01
  SIGMA_BOOST_FACTOR           = 2.0
  TOURNAMENT_MIN_OOS_FITNESS   = 0.0   (Sesión 17)
  TOURNAMENT_MIN_OOS_TRADES    = 5     (Sesión 17)
  IMMUNITY_MAX_LOSS_PCT        = 8.0   (Sesión 17)
  MIN_SAMPLE_DAYS              = 7     (Sesión 17)
  RUPTURA_SOLO_TENDENCIA       = true  (Sesión 17)
  TARGET_AGENTS_PER_ESPECIE    = 5     (Sesión 18/19)
  REPOPULATION_MAX_PER_CYCLE   = 3     (Sesión 18, deprecado en Sesión 19)
  REPOPULATION_MAX_ATTEMPTS_PER_SLOT = 8  (Sesión 19)

En caso de fallo: crea GitHub Issue con alerta.

Schedule histórico (comentado): "45 3 * * 2-6"  → 03:45 UTC
```

### `health_check.yml` — Verificación diaria de dependencias

**Disparado por:** cron-job.org a las 8:00:00 Bogotá L-V (job "GH Action - Health Check").

Verifica en orden: secrets configurados → DB (Supabase) → agentes activos → Yahoo Finance → **saldo de DeepSeek** → ping de inferencia DeepSeek → Finnhub (opcional). Si algún check falla, crea un GitHub Issue de alerta (o comenta el existente del día).

**Preflight de saldo DeepSeek (Sesión 23):** antes del ping de inferencia, el paso "Verificar saldo de DeepSeek" consulta `GET https://api.deepseek.com/user/balance`:
- `is_available=false` (saldo agotado) → **falla** con mensaje accionable y link de recarga.
- saldo `< DEEPSEEK_LOW_BALANCE_USD` (default 2.0 USD) → ⚠️ **avisa sin fallar** (recargar pronto, antes de que llegue a cero).
- si el endpoint de saldo no responde → no rompe el check; el ping queda como red de seguridad.

**Clasificación de errores del ping (Sesión 23):** el paso "Verificar DeepSeek API" distingue `HTTP 402 Insufficient Balance` (recargar saldo) de `401/403` (API key inválida) y de errores de red/timeout — cada caso imprime una instrucción distinta.

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
| `DEEPSEEK_MODEL` | Modelo DeepSeek a utilizar (default y producción: `deepseek-chat`; no hay override configurado — ver §6.4) |
| `FINNHUB_API_KEY` | API key de Finnhub (noticias y calendario económico) |
| `ALPHA_VANTAGE_API_KEY` | API key de Alpha Vantage (legado) |
| `GOOGLE_SHEET_ID` | ID del spreadsheet de Google Sheets |
| `GOOGLE_CREDENTIALS_JSON` | JSON completo de la service account de Google (o ruta al archivo en local) |
| `DEEPSEEK_BASE_URL` | Base URL del API (default: `https://api.deepseek.com`) |
| `DEEPSEEK_LOW_BALANCE_USD` | Umbral de aviso preventivo de saldo bajo en DeepSeek (USD). El health check NO falla por saldo bajo; solo emite ⚠️ para recargar antes de que llegue a cero. Default: `2.0`. **Sesión 23.** |
| `JUDGE_TIMEZONE` | Zona horaria del Juez (default: `America/Bogota`) |
| `JUDGE_RUN_TIME` | Hora de ejecución del Juez en zona local (default: `23:00`) |
| `TRADING_START_TIME_UTC` | Hora UTC desde la que se permite abrir posiciones, formato HH:MM (default: `06:30` = 1:30 am Bogotá) |
| `TRADING_CUTOFF_TIME_UTC` | Hora UTC límite para abrir posiciones, formato HH:MM (default: `04:00` = 11:00 pm Bogotá; ventana cruza la medianoche UTC) |
| `AGENTS_ELIMINATE_PER_CYCLE` | Agentes eliminados por ciclo. Default: `9` (3 por especie × 3 especies). Con `MIN_AGENTS_PER_ESPECIE=2` y 5 agentes/especie el cap real por especie es 3. |
| `MUTATION_SIGMA_WEIGHTS` | Sigma de mutación para pesos (default: `0.05`) |
| `MUTATION_SIGMA_PERIODS` | Sigma de mutación para períodos (default: `0.08`) |
| `MUTATION_SIGMA_RISK` | Sigma de mutación para riesgo/SMC (default: `0.10`) |
| `MIN_ROI_FOR_HALL_OF_FAME` | ROI mínimo para entrar al Hall of Fame (default: `0.05`) |
| `GRACE_PERIOD_DAYS` | Días HÁBILES (lun-vie) de inmunidad para agentes recién nacidos sin operaciones (default: `2`) |
| `DIVERSITY_VARIANCE_THRESHOLD` | Coeficiente de variación mínimo del ADN antes de activar el sigma boost. Subir a `0.05` para criterio más estricto (default: `0.01`) |
| `SIGMA_BOOST_FACTOR` | Multiplicador aplicado a las sigmas cuando la diversidad cae bajo el umbral (default: `2.0`) |
| `TRADE_FRICTION_PIPS` | Fricción round-trip (spread + slippage) en pips descontada del P&L de cada operación al cerrar. Default: `1.4` (≈ 0.8 spread retail EUR/USD + 0.6 slippage). **Fase 0, desde Sesión 16.** |
| `MIN_SL_PIPS` | Piso mínimo del Stop Loss en pips. Por debajo de esto el SL muere por el ruido de las velas de 1m. Default: `10.0`. El sizing escala inverso a sl_pips (mismo riesgo porcentual). **Fase 0, desde Sesión 16.** |
| `MAX_SL_PIPS` | Techo del Stop Loss en pips, para TODAS las fuentes de SL (estructura OB/FVG y ATR). Un SL mayor produce un TP inalcanzable intradía: el trade solo puede terminar en EOD o SL completo y el fitness pierde la señal (op #9284: SL estructural de 61 pips). Default: `35.0`. **Sesión 22.** |
| `MIN_SAMPLE_TRADES` | Operaciones cerradas mínimas antes de que un agente sea elegible para eliminación, reproducción o Hall of Fame. Default: `15`. **Fase 1, desde Sesión 16.** |
| `MIN_AGENTS_PER_ESPECIE` | Agentes mínimos por especie (tendencia/reversion/ruptura). La evolución no elimina si bajaría una especie de este umbral. Default: `2`. **Fase 2, desde Sesión 16.** |
| `BACKTEST_TRAIN_DAYS` | Días de warmup del backtester (ignorados en la evaluación). Default: `40`. **Fase 3, desde Sesión 16.** |
| `BACKTEST_VALIDATE_DAYS` | Días OOS del backtester (out-of-sample, define el fitness). Default: `20`. **Fase 3, desde Sesión 16.** |
| `N_CANDIDATE_CHILDREN` | Candidatos generados por slot vacante en el torneo de reproducción. Default: `3`. **Fase 3, desde Sesión 16.** |
| `TOURNAMENT_MIN_OOS_FITNESS` | Fitness OOS mínimo (estrictamente mayor) para desplegar un hijo del torneo. Default: `0.0` (cualquier fitness positivo pasa). **Fase 1, desde Sesión 17.** |
| `TOURNAMENT_MIN_OOS_TRADES` | Trades OOS mínimos para desplegar un hijo del torneo. Default: `5`. **Fase 1, desde Sesión 17.** |
| `IMMUNITY_MAX_LOSS_PCT` | Revoca la inmunidad por muestra insuficiente si `roi_total ≤ −IMMUNITY_MAX_LOSS_PCT` (%). Default: `8.0`. No afecta el Periodo de Gracia. **Fase 3, desde Sesión 17.** |
| `MIN_SAMPLE_DAYS` | Días hábiles mínimos alternativos a `MIN_SAMPLE_TRADES` (condición híbrida OR). Default: `7`. **Fase 4, desde Sesión 17.** |
| `RUPTURA_SOLO_TENDENCIA` | Si `true`, la especie `ruptura` no abre posiciones en régimen `RANGO` (ni en el monitor de producción ni en el backtester OOS). NEUTRAL siempre opera. Default: `true`. **Fase 5, desde Sesión 17.** |
| `TARGET_AGENTS_PER_ESPECIE` | Objetivo de agentes activos por especie. El motor llena TODO el déficit cada ciclo (3 × 5 = 15 garantizados). Default: `5`. **Sesión 18 / 19.** |
| `REPOPULATION_MAX_PER_CYCLE` | **DEPRECADO (Sesión 19).** El tope por ciclo se eliminó para garantizar los 15. Default: `3` (sin efecto). **Sesión 18.** |
| `REPOPULATION_MAX_ATTEMPTS_PER_SLOT` | Rondas de reintento (torneo→HoF) por cupo antes de desplegar el mejor candidato de cruce. Acota el costo de backtests del cron. Default: `8`. **Sesión 19 / 21.** |
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
             1. Evaluación de fitness: Expectancy ajustada para los agentes activos
             2. Filtro de Periodo de Gracia: separar inmunes de elegibles
             3. Cuota dinámica: identificar elegibles con fitness <= 0

             ╔══════════════════════════════════════════════════════╗
             ║  ¿Cuota = 0?                                         ║
             ╠══════════════════════════════════════════════════════╣
             ║  SÍ → CICLO SUSPENDIDO                               ║
             ║      - No se elimina ni se reproduce normalmente.    ║
             ║      - 7. Recuperación de cupos: si hay déficit y   ║
             ║           Yahoo disponible, se intenta rellenar      ║
             ║           (con redistribución si hay recuperados).  ║
             ║      - Log 'evaluacion_diaria' con                   ║
             ║        cycle_suspended=true.                         ║
             ║      - LLM omitido salvo que haya recuperados.       ║
             ║                                                      ║
             ║  NO → CICLO ACTIVO                                   ║
             ║      4. Forzado de diversidad: si CV bajo,           ║
             ║         duplica las sigmas de mutación.              ║
             ║      5. Eliminar N agentes (1 a 9, fitness<=0).      ║
             ║      6. Reproducir hijos: torneo de 3 candidatos     ║
             ║         con backtest OOS + umbral de calidad;        ║
             ║         fallback Hall of Fame; slot vacante si       ║
             ║         ningún candidato pasa (Sesión 17).           ║
             ║      7. Recuperar TODO el déficit hasta los 15       ║
             ║         (Sesión 18/19): torneo→HoF ×8 rondas;        ║
             ║         mejor candidato de cruce si nadie pasa OOS.  ║
             ║      8. Razonamiento LLM: veredicto y expectativas.  ║
             ║      9. Registro detallado en logs_juez.             ║
             ║     10. Redistribución de capital: pool ÷ activos    ║
             ║         (incluye agentes recuperados en paso 7).     ║
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
| `seed_gen1.py` | Crea 10 agentes Generación 1 con parámetros por defecto y $10 c/u (pool inicial $100). | Una sola vez al migrar a una nueva BD vacía. |
| `seed_15_agents.py` | Expande la población de 10 a 15 agentes (5 por especie). Cría 5 nuevos usando `breed_agent()` con padres de la misma especie y redistribuye el pool entre los 15. Sincroniza Google Sheets. Soporta `--dry-run`. | Ejecutado el 2026-06-02 para pasar de 10 a 15 agentes activos. Usar si se hace un reset y hay que volver a 5/especie. |
| `diversify_gen1.py` | Aplica mutación gaussiana individualizada a los agentes activos para darles ADN propio (σ elevada). | Cuando todos los agentes tienen params idénticos y el motor evolutivo queda bloqueado por falta de fitness diferencial. |
| `recompute_pnl.py` | Recalcula `pnl`, `capital_usado` (nocional USD) y `pnl_porcentaje` para todas las ops cerradas que tenían `capital_usado` en lotes (convención anterior al commit `6572c11`). Reconstruye `capital_actual` y `roi_total` de los agentes. | **Ya ejecutado** el 2026-05-20. Solo necesario si se detectan operaciones históricas con `capital_usado` < 1.0 (indicativo de lotes en vez de USD). |
| `backtest_estrategia.py` | Backtest determinista walk-forward sobre datos históricos de Yahoo Finance. Replica el pipeline Técnico → Macro → Riesgo vela por vela con el LLM neutralizado (ruta heurística pura). Calcula PnL, win-rate, drawdown y Calmar Ratio. Soporta genes reales desde DB (`--agent-id`), rango configurable (`--range`) y desglose por día (`--dia`). | Validar cambios de estrategia antes del deploy. Usar `--range 1mo` como referencia estándar. |

Comando para ejecutar:
```bash
python scripts/seed_gen1.py        # nueva BD vacía → 10 agentes Gen1
python scripts/seed_15_agents.py   # expande 10 → 15 agentes (5/especie)
python scripts/diversify_gen1.py   # diversifica ADN de los agentes activos
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
│   ├── indicators.py            (OHLCV + RSI/EMA/MACD/FVG/OB/ATR + HTF series 1h)
│   ├── macro_scraper.py         (Finnhub: noticias + calendario)
│   ├── simulated_broker.py      (Yahoo Finance: snapshot + OHLC 1m intra-vela
│   │                              · check_sl_tp_intrabar / get_intrabar_candles)
│   └── alpha_vantage_client.py  (legado: dataclass TechnicalSignals)
├── db/
│   ├── connection.py            (psycopg2 + Supabase pooler)
│   ├── apply_migrations.py
│   ├── migrations/              (001 – 010: migración 010 añade columna especie)
│   └── seeds/
├── evolution/
│   ├── evolution_engine.py      (fitness expectancy, crossover, especies, torneo backtest)
│   └── backtester.py            (walk-forward OOS: señales→SL/TP→fricción, ~3.5s/candidato)
├── dashboard/                   Streamlit (inversion-evolutiva.streamlit.app)
│   ├── app.py
│   ├── charts.py
│   ├── data.py
│   └── logo.png
├── mobile-app/                  Next.js (app complementaria, Vercel)
├── scripts/                     Utilidades manuales (seed_gen1, seed_15_agents, diversify_gen1, backtest_estrategia)
├── tests/                       pytest (pipeline + evolution + intra-bar SL/TP; conftest.py aísla la suite en la sandbox Neon — NUNCA borrar)
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

*Documento actualizado el 2026-06-12 (Sesión 22 — salidas inteligentes como genes evolutivos: break-even stop `be_activation_r`, salida por señal contraria `exit_on_reversal`/`min_profit_for_exit_r`, techo `MAX_SL_PIPS` y recorte de `atr_factor` a 1.8; replicado en backtester, migración 011 en producción).*

## Historial de cambios mayores

- **2026-06-16 (Sesión 25 — numeración consecutiva real de agentes + pureza de especie) · commits `e16de0b`, `f28ee78` · en producción:**
  - **Contexto:** revisión de coherencia del ciclo del 2026-06-16, que eliminó 1 agente y creó `2026-06-16_02` con herencia de `2026-05-29_04` (ruptura, activo) × `2026-05-19_06` (tendencia, eliminado, Gen1). El usuario detectó dos cosas: (1) un único agente nuevo nombrado `_02` sin que existiera `_01`; (2) un agente eliminado reproduciéndose.
  - **Diagnóstico (confirmado contra producción vía `logs_juez`):** el slot `2026-06-16_01` SÍ se crió pero el backtester lo rechazó (3 trades < umbral OOS) y quedó vacante; la red de repoblación insertó el agente real como `_02` — el índice `_01` se "quemó" con el intento rechazado. El agente eliminado pudo criar porque su genoma sigue archivado en el Hall of Fame (elitismo intencional, confirmado y mantenido).
  - **Cambio A — numeración consecutiva real (`fix`, commit `e16de0b`):** nuevo helper `_renumber_contiguous` renumera los agentes que SÍ se insertan a `_01, _02, …` sin huecos, antes de persistir, en ciclo activo Y suspendido. Un slot rechazado por el umbral OOS ya no quema su índice. El remap se propaga a `slots_recuperados`; los slots vacantes se reetiquetan después para no colisionar con un ID real. 5 tests en `tests/test_numeracion_consecutiva.py`.
  - **Decisión de diseño — se mantiene el elitismo del HoF:** el usuario consideró restringir la reproducción a "solo agentes vivos" y lo revirtió tras confirmar que el HoF es un archivo histórico que preserva genes buenos. Muerte y archivo son juicios independientes: la muerte usa `fitness ≤ 0` (cuota dinámica, recalculado en vivo, castiga drawdown/overtrading); el archivo usa `roi_total ≥ 0.05` (congelado al inscribirse). Por eso `2026-05-19_06` (ROI 6.699 en HoF) seguía criando pese a estar eliminado. Un eliminado puede ser 1 de 2 padres, nunca el genoma único.
  - **Cambio B — pureza de especie (`feat`, commit `f28ee78`):** un hijo etiquetado de una especie podía nacer de padres de OTRAS especies cuando sus candidatos puros fallaban el umbral OOS y el fallback del HoF mezclaba el top global (el HoF de reversion tenía 1 sola entrada vs 13 de tendencia / 4 de ruptura). `breed_agent` forzaba los genes-interruptor (`rsi_modo`, `htf_filter_enabled`) pero el resto del genoma venía de otra especie → erosión de la especie débil hacia los genes de la fuerte, diluyendo la decorrelación que las especies buscan. Solución (**pureza dura**, decisión del usuario): todo hijo lleva **≥1 padre de su especie, dominante al 60%**, mientras la especie tenga algún genoma (activo —incl. jóvenes/inmunes— o en HoF); cross-species total solo si la especie está extinta. Helpers `_species_genome_pool` (pool puro: maduros → activos → HoF de la especie) + `_species_dominant_pair` (garantiza el padre de la especie como dominante) aplicados en los 5 puntos de cruce (torneo principal, fallback HoF, vía sin-backtest, repoblación torneo + HoF). El `forzado_cruce` ya era puro. El caso sano (≥2 padres elegibles de la especie) queda idéntico — la garantía solo actúa cuando el torneo habría cruzado entre especies. 6 tests en `tests/test_pureza_especie.py`.
  - **Verificación:** suite **79/79**, incluido el ciclo evolutivo real en sandbox Neon (confirma que la pureza no rompe la garantía de 15 agentes ni el cruce 60/40).

- **2026-06-15 (Sesión 24 — resiliencia ante timeouts transitorios de Yahoo Finance y del pooler de Supabase) · commit `dc2938b` · en producción:**
  - **Contexto:** dos runs del Trade Monitor fallaron por blips de red — uno con `Read timed out` al bajar OHLCV de Yahoo para escanear NUEVAS entradas (sin posiciones abiertas en riesgo), otro por un cold-start del pooler de Supabase que agotó los reintentos de DB. El run siguiente (15 min después) salió OK solo en ambos casos.
  - **Fix:** `data/yahoo_client.py` (nuevo) — cliente centralizado con 3 intentos y backoff 2/5s ante timeout/conexión/5xx; reemplaza los `requests.get` crudos de `indicators.py` y `simulated_broker.py`. `db/connection.py` — 3→4 reintentos, backoff 5/10/20s, connect_timeout 10→15s. `cron/trade_monitor.py` — `critical_errors` ahora son SOLO los fallos de vigilancia SL/TP de posiciones abiertas; un timeout bajando datos para nuevas entradas ya no es crítico (exit code y alerta solo si `critical_errors > 0`). Mensaje de alerta del workflow reformulado. 5 tests en `tests/test_yahoo_client.py`.
  - **Criterio establecido:** el correo de alerta del Trade Monitor = solo cuando no se pudo verificar/cerrar una posición ABIERTA.

- **2026-06-15 (Sesión 23 — incidente: saldo DeepSeek agotado (HTTP 402) + endurecimiento del health check) · commit `feb3f75` · en producción:**
  - **Síntoma reportado:** a las 8:16 am Bogotá llegó el correo automático "Health Check Diario: All jobs have failed". El run de las 8:00 am (13:00 UTC, #27547965887) falló en el step "Verificar DeepSeek API"; los 5 checks previos (secrets, DB, agentes activos, Yahoo Finance) pasaron.
  - **Causa raíz — saldo agotado, NO es bug de código:** DeepSeek devolvió `Error code: 402 - {'message': 'Insufficient Balance'}`. La API key es válida (autentica bien); lo que se agotó fue el saldo de la cuenta durante el fin de semana. El health check funcionó como debía: detectó el problema y alertó (correo + issue #14). Historial: verde todos los días hasta el 12-jun, primer fallo el 15-jun.
  - **Impacto real (bajo) — el sistema es resiliente al 402:** los tres sub-agentes y el Juez tienen fallback determinista ante fallo del LLM, así que el trading continuó. (1) `SubAgentTechnical` solo consulta el LLM en la zona ambigua (conf 0.45–0.65) y ante excepción cae a la heurística ponderada; (2) `SubAgentMacro` cae a `_fallback_score` (sesgo HTF); (3) `SubAgentRisk` cae a la decisión heurística; (4) `JudgeAgent` cae a `_fallback_verdict`. La protección de SL/TP de posiciones abiertas es 100% determinista (`check_sl_tp` / `check_sl_tp_intrabar`, sin LLM) — nunca estuvo en riesgo. El Monitor de Trades siguió en verde salvo un fallo puntual a las 11:00 (issue #13) por timeout transitorio del pooler de Supabase, que se autorrecuperó en el ciclo siguiente.
  - **Degradación mientras el saldo estuvo en cero:** los agentes operaron con pura heurística (sin el "desempate" del LLM en señales ambiguas) y el Juez perdió su veredicto cualitativo (la evolución genética en sí es determinista y siguió). El health check habría fallado cada mañana hasta recargar.
  - **Fix (`.github/workflows/health_check.yml`):** (1) **Paso nuevo "Verificar saldo de DeepSeek"** (preflight, antes del ping): consulta `GET https://api.deepseek.com/user/balance`; falla con mensaje accionable si `is_available=false` (saldo agotado) y emite ⚠️ sin fallar si el saldo cae por debajo de `DEEPSEEK_LOW_BALANCE_USD` (default 2.0 USD) — aviso preventivo para recargar ANTES de llegar a cero. Si el endpoint de saldo falla, no rompe el check (el ping queda de red de seguridad). (2) **Clasificación de errores en el ping:** distingue `402 Insufficient Balance` (recargar) / `401-403` (key inválida) / error de red o timeout — cada caso da instrucción distinta, en vez del genérico "DeepSeek API error" anterior.
  - **Limpieza de etiquetas Neon → Supabase:** el step de DB del health check y el checklist de su issue automático (`health_check.yml`) más el issue del Trade Monitor (`trade_monitor.yml`) decían "Neon"; corregido a Supabase, que es la DB real (`...pooler.supabase.com:6543`).
  - **Corrección de documentación:** este documento decía "modelo en producción: `deepseek-reasoner`". Verificado con `gh secret list` / `gh variable list`: **no existe** `DEEPSEEK_MODEL` configurado ni se mapea en ningún workflow, por lo que producción usa el default del código `deepseek-chat`. Secciones 6.4 y 15 actualizadas.
  - **Verificación:** YAML válido en ambos workflows; los 2 scripts Python embebidos compilan; árbol de decisión del saldo probado en 5 escenarios (agotado→exit 1, bajo→⚠️, sano→ok, cuenta CNY, respuesta vacía). Tras recargar el saldo, el Health Check corrió completo en verde (run #27550771274, 13:47 UTC). Issues #13 y #14 cerrados con su causa raíz documentada.
  - **Nueva env var:** `DEEPSEEK_LOW_BALANCE_USD=2.0` (umbral de aviso preventivo; el health check NO falla por saldo bajo, solo avisa).
  - **Mejora pendiente sugerida (no implementada):** proveedor LLM de respaldo (un segundo endpoint OpenAI-compatible vía `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`) para que un saldo agotado no degrade silenciosamente a todos los agentes a heurística. Requiere una segunda API key — pendiente de decisión del usuario.

- **2026-06-12 (Sesión 22 — salidas inteligentes como genes evolutivos + coherencia intradía de SL/TP) · en producción:**
  - **Contexto:** la op #9284 (BUY del 2026-06-12, agente `2026-06-02_05`) expuso dos problemas: (1) su SL **estructural** (OB) de 61 pips generó un TP de 122 pips inalcanzable intradía — la rama ATR tenía techo de 50 pips pero la estructural solo validaba distancia mínima; con TP inalcanzable y trailing a +1R (61 pips) inactivable, el trade solo podía terminar en EOD o SL completo → fitness sin señal; (2) la posición llegó a +24 pips y los devolvió sin que ningún mecanismo protegiera la ganancia parcial. El usuario propuso evaluar las señales cada 15 min también para SALIR; se acordó implementarlo como **genes** para que la evolución decida (no como regla impuesta).
  - **Coherencia intradía de SL (`agents/sub_agent_risk.py`):** nuevo techo `MAX_SL_PIPS` (env, default 35) aplicado a TODAS las fuentes de SL: el estructural (OB/FVG) que lo supere se descarta y cae a la rama ATR (antes solo se validaba `too_close`); la rama ATR baja su tope hardcodeado de 50 → `MAX_SL_PIPS`. Bound del gen `atr_factor` 3.0 → **1.8** en `_BOUNDS_SMC`.
  - **Gen `be_activation_r` (0.3–1.0, default 0.6, gaussiano) — break-even stop:** al ganar `be_activation_r × R`, el SL se mueve a entrada ± fricción (`TRADE_FRICTION_PIPS`): la operación ya no puede terminar en pérdida sin recortar su potencial. Implementado en `_apply_trailing_stop` (vela a vela, tras el chequeo SL/TP de la vela, nunca empeora un SL ya mejorado). El SELECT de posiciones abiertas ahora hace JOIN con `agentes` para leer el gen.
  - **Gen `exit_on_reversal` (0/1, bit-flip 10%) + `min_profit_for_exit_r` (0.2–1.0, default 0.4, gaussiano) — salida por señal contraria:** nueva `_check_reversal_exits()` en `trade_monitor` (reusa el OHLCV/HTF ya descargado, sin costo LLM — `reason()` neutralizado como en el backtester). Cierra una posición solo si: señal técnica OPUESTA + confianza ≥ `umbral_confianza_minima` del propio agente + ganancia ≥ `min_profit_for_exit_r × R`. Nunca cierra en pérdida por señal. Nuevo mecanismo de mutación **bit-flip** para genes booleanos (`_BOOLEAN_GENE_FLIP_PROB`) — mantiene el rasgo re-descubrible si se extingue.
  - **Backtester (`evolution/backtester.py`):** BE-stop y salida por reversa replicados con la misma lógica y convenciones (hits primero, BE después; señal en cadencia `_CHECK_EVERY`; trades marcados `hit="REV"`). El fitness OOS premia o castiga los genes nuevos con el comportamiento real de producción.
  - **Migración `011_salidas_inteligentes.sql` (aplicada en prod Supabase y sandbox Neon):** backfill idempotente de los 3 genes; semilla de diversidad 50/50 de `exit_on_reversal` en los activos (8 con el rasgo / 7 sin él) — sin ella el rasgo no existiría en la población; clamp de `atr_factor` > 1.8 (verificado: 0 agentes sobre el tope).
  - **Tests (`tests/test_sesion22_salidas.py`):** 12 nuevos — BE BUY/SELL, umbral no alcanzado, gen en 0, BE nunca empeora, SL estructural a 61 pips descartado (caso #9284), cap ATR, defaults/bounds, bit-flip produce ambos valores, y las 3 condiciones de la salida por reversa (cierra con señal fuerte; no cierra bajo el piso de ganancia; no cierra con señal débil o misma dirección).
  - **Filosofía:** nada se impone — los tres parámetros se heredan, mutan y compiten. Si los linajes con break-even temprano o salida por reversa superan a los que dejan correr, sus hijos dominarán la población; si no, se extinguen. La selección natural responde la pregunta.

- **2026-06-12 (Sesión 21 — cruce 60/40 garantizado: eliminación del clon forzado padre==madre) · en producción:**
  - **Contexto:** el ciclo del 2026-06-12 eliminó correctamente 4 agentes con fitness ≤ 0, pero los 4 hijos nacieron por el "clon forzado" de Sesión 19 con **padre == madre**: los 3 de reversion eran mutaciones del MISMO genoma Gen-1 de especie tendencia (`2026-05-19_10`), y el de ruptura era clon de `2026-06-02_06` — **el agente eliminado esa misma noche** (su entrada HoF era de cuando tuvo ROI positivo). Incoherencia adicional: el umbral OOS rechazó hijos de cruce real con fitness +0.067 (por tener 4 trades en vez de 5) y luego desplegó clones con fitness −0.144 y −0.116. El usuario detectó la violación del principio central del sistema: los hijos deben recombinar el ADN de los dos mejores padres (cruce 60/40).
  - **Rediseño de la jerarquía de recuperación (`evolution/evolution_engine.py`, `_try_repopulate`):** el cruce de dos padres distintos nunca se abandona. Escalera nueva: (1) torneo con umbral estricto (sin cambio); (2) **mejor candidato de cruce** (`origen='mejor_candidato_oos'`): si nadie pasa el umbral tras todas las rondas, entra el hijo de cruce con mayor fitness OOS — muestra corta es preferible a clon sin cruce; (3) **cruce forzado** (`'forzado_cruce'`): si ningún pool tiene 2 padres, se cruzan los dos mejores genomas distintos entre HoF y pool, con el de la especie correcta como dominante (60% vía `p1_weight=0.6` explícito — nuevo parámetro opcional de `breed_agent`); un agente eliminado puede ser uno de los dos padres pero nunca el genoma único; (4) auto-clon (`'forzado_clon_unico'`) SOLO si existe un único genoma activo en el sistema. Los orígenes `forzado_hof`/`forzado_pool` desaparecen.
  - **`_get_hof_parents` con mezcla de especies:** cuando la especie tiene < 2 padres únicos, antes se descartaban por completo y se usaba el top global (por eso los hijos de reversion salieron 100% tendencia); ahora se conservan los de la especie y se completa con el top global — el mejor padre de la especie sigue disponible y domina el cruce. La query también devuelve `a.estado` para que el último recurso evite eliminados como genoma único.
  - **`judge_agent.py`:** la descripción del ciclo reporta por separado los cupos por "mejor candidato de cruce sin umbral" y por "cruce/clon forzado".
  - **Corrección retroactiva en producción (`scripts/fix_genealogia_20260612.py`):** los 4 hijos de anoche fueron re-criados in-place con la lógica nueva (mismos ids, capital, generación y FKs; solo cambian padres y genoma): `_05` (reversion) ← `2026-06-02_05 × 2026-05-28_04`; `_06` (reversion) ← `2026-06-02_05 × 2026-05-28_04` (pasó umbral estricto vía torneo, n=5); `_07` (reversion) ← `2026-05-29_04 × 2026-05-19_04` (pasó vía HoF, n=5); `_08` (ruptura) ← `2026-05-29_04 × 2026-06-05_01` (pasó vía HoF, n=5). Verificado: 0 agentes activos con padre==madre en toda la población. Corrección registrada en `logs_juez` y Sheets re-sincronizado vía backfill.
  - **Tests (`tests/test_sesion18_repopulacion.py`):** los 2 tests de clon forzado reemplazados por 5 nuevos: mejor candidato cuando nadie pasa el umbral (verifica que todas las crianzas usaron padres distintos), cruce forzado con 2 genomas, veto a eliminados como genoma único (cupo queda vacante), auto-clon solo con genoma único activo, y coherencia de especie (p1 dominante = especie del cupo aunque otro genoma global puntúe mejor). **Suite completa: 51/51 verdes.**

- **2026-06-11 (Sesión 20 — auditoría exhaustiva de operación: 4 reparaciones + incidente de tests revertido) · commits `9d7dc38`, `8af973a`, `f70806d` · en producción:**
  - **Contexto:** el Agente Juez no se ejecutó las noches del 10 y 11 de junio (issues #9 y #12). La auditoría posterior revisó todos los flujos del sistema y encontró tres fallos adicionales independientes.
  - **Fallo #1 — Juez caído por IndexError en fallback HoF (`evolution/evolution_engine.py`):** ambas noches crashearon en `random.choice()` sobre lista vacía al buscar segundo padre en el Hall of Fame. Causa raíz: `estrategias_exitosas` admite varias entradas del **mismo agente**, y el HoF de la especie `reversion` tenía 2 entradas de un único agente (`2026-05-28_04`) — `random.choices` elegía el mismo id dos veces (garantizado) y el filtro por id distinto devolvía `[]`. Fix en dos capas: (1) `_get_hof_parents()` deduplica por agente con `DISTINCT ON (agente_origen_id)` quedándose con la mejor entrada de cada uno — "2 padres HoF" ahora significa 2 agentes genéticamente distintos, y si una especie solo tiene 1 agente único cae al fallback multi-especie con diversidad real; (2) guards `if alternatives:` en los 3 puntos del código que seleccionan segundo padre (reproducción, repoblación y fallback HoF) — si no hay alternativa, se procede con self-mutation (`hp2 = hp1`), que `breed_agent` soporta.
  - **Fallo #2 — Backfill semanal de Sheets caído desde el 31-may (`utils/sheets_backfill.py`):** error 429 de la API de Google ("Write requests per minute per user") al crecer la tabla `operaciones` (~8.900 filas): los lotes de 50 filas generaban más de 60 escrituras/minuto. Fix: lotes de 50 → 500 filas, pausa de 1.2s entre requests y reintento con espera de 65s (garantiza el reset de la cuota por minuto) en `clear`, `update` y `append_rows`. `manual_backfill.yml`: `timeout-minutes` 10 → 20. Verificado con 2 backfills exitosos en producción el mismo día.
  - **Fallo #3 — Trade Monitor con caídas intermitentes (`db/connection.py`):** issues #5, #6, #7, #8 y #11 (jun 2-10) causados por timeouts transitorios del pooler de Supabase en la primera conexión del run (`psycopg2.OperationalError: timeout expired`), con un único intento de conexión. Fix: `get_conn()` y `health_check()` reintentan 3 veces con backoff 5s/15s y warning por intento.
  - **Incidente durante la auditoría — tests ejecutaron un ciclo evolutivo contra PRODUCCIÓN (revertido):** al correr `pytest tests/`, el test de integración `test_full_evolution_cycle_on_db` ejecutó `EvolutionEngine.run()` contra la DB de producción: sus fixtures escriben en la sandbox Neon (hardcodeada en el test), pero el motor importa `db.connection`, que lee `DATABASE_URL` del entorno (y `load_dotenv()` no sobreescribe variables presentes) — el `.env` local apunta a Supabase. Resultado a las 14:38 UTC: 4 agentes reales eliminados (`2026-05-29_01`, `2026-05-29_02`, `2026-06-02_04`, `2026-06-02_06`), 4 hijos criados (`2026-06-11_04..07`) y capital redistribuido a mitad de día con datos parciales (el sistema evalúa solo al EOD). **Rollback completo en una transacción:** agentes reactivados, hijos borrados (solo tenían ops `cancelada`), capitales restaurados desde el snapshot pre-redistribución de `ranking_historico`, 19 filas de ranking y 7 entradas HoF espurias borradas, Sheets re-sincronizado vía backfill. Verificado: capitales finales idénticos a los de la mañana (pool $97.44, 5/5/5 por especie).
  - **Barrera permanente — `tests/conftest.py` (nuevo):** pytest carga `conftest.py` antes que cualquier módulo de tests, por lo que asigna `DATABASE_URL` = sandbox Neon y vacía `GOOGLE_SHEET_ID`/`GOOGLE_CREDENTIALS_JSON` antes de que `load_dotenv()` corra. Toda la suite queda aislada de producción **por construcción**.
  - **Tests de integración reparados:** fixtures autosuficientes — `_reset_agents()` ya no asume filas preexistentes: vacía la sandbox (tablas hijas primero, en orden de FKs) y recrea 15 agentes génesis (5 por especie) con el genoma moderno completo; `test_db_operation_persist` crea su agente con `ON CONFLICT DO NOTHING`. Aserción del ciclo alineada con la garantía de Sesión 19: población final = 15 (`eliminados == nuevos` ya no aplica por slots vacantes/repoblación/clon forzado). Migraciones 009-010 aplicadas a la sandbox Neon (le faltaba la columna `especie`). **Suite completa: 48/48 verdes con producción intacta.**
  - **Mantenimiento de workflows:** `actions/github-script` v7 → v9 en `judge_daily.yml`, `trade_monitor.yml` y `health_check.yml` (GitHub fuerza Node.js 24 desde el 16-jun-2026; v7 corría sobre Node 20 deprecado).
  - **Limpieza:** issues #5-#9, #11 y #12 cerrados con su causa raíz documentada. Estado post-sesión: 15/15 agentes (5/especie), pool $97.44, 0 issues abiertos, los 4 workflows verdes. Los 2 ciclos evolutivos perdidos (10 y 11-jun) no dejaron estado inconsistente: ambos crashes ocurrieron antes de cualquier escritura; la evolución se retoma con el ciclo de esta noche.
  - **Contexto:** la recuperación de cupos de Sesión 18 tenía dos límites de diseño que impedían garantizar los 15 agentes: el tope `REPOPULATION_MAX_PER_CYCLE` (máx 3/ciclo) y el umbral OOS que dejaba cupos vacantes si ningún candidato pasaba. El usuario solicitó que **siempre existan 15 agentes**.
  - **Sin tope por ciclo (`evolution/evolution_engine.py`):** `_try_repopulate()` ahora intenta llenar TODOS los cupos faltantes en un solo ciclo (`REPOPULATION_MAX_PER_CYCLE` queda deprecado, sin efecto).
  - **Reintentos por cupo:** hasta `REPOPULATION_MAX_ATTEMPTS_PER_SLOT` rondas (default 8) de (torneo → umbral OOS) seguido de (HoF → umbral OOS), deteniéndose al primer candidato que pasa. Sube fuertemente la probabilidad de cubrir el cupo respetando el filtro de calidad.
  - **Clon forzado como último recurso:** ⚠️ **ELIMINADO en Sesión 21** (el 2026-06-12 produjo 4 hijos con padre==madre, violando el cruce 60/40 — ver entrada de Sesión 21). Diseño original: si tras agotar las rondas nadie supera el OOS, se clonaba el mejor agente del Hall of Fame (`origen='forzado_hof'`; si no hay HoF, el mejor del pool → `'forzado_pool'`). Garantizaba los 15 pero sin recombinación genética.
  - **Fallback sin Yahoo Finance (preservado):** si no hay datos de mercado, la recuperación se omite por completo — no hay base para validar ni para clonar. Ese día la población puede quedar < 15 y se recupera al siguiente ciclo con datos.
  - **Trazabilidad:** `origen` en `slots_recuperados` admite ahora `forzado_hof`/`forzado_pool`; `judge_agent.py` reporta en la descripción cuántos cupos se llenaron por clon forzado.
  - **Tests:** `tests/test_sesion18_repopulacion.py` actualizado — 6 tests: llenado completo sin tope (4 cupos), llenado de los 15, omisión sin backtest, fallback HoF, clon forzado HoF, clon forzado pool. Suite: 45 no-DB verdes (3 fallos de BD esperados sin `DATABASE_URL`).
  - **Nuevas env vars:** `REPOPULATION_MAX_ATTEMPTS_PER_SLOT=8` en `.env.example` y `judge_daily.yml`.

- **2026-06-09 (Sesión 18 — recuperación de cupos vacantes) · commit `b6ef177` · en producción:**
  - **Contexto:** la Fase 1 de Sesión 17 introdujo el umbral OOS, lo que puede dejar slots vacantes cuando ningún candidato pasa el filtro de calidad. Con el tiempo, la población puede quedar por debajo de `TARGET_AGENTS_PER_ESPECIE` sin mecanismo de recuperación automática.
  - **Implementación (`evolution/evolution_engine.py`):** nuevo método `_try_repopulate()` que calcula el déficit por especie (`TARGET_AGENTS_PER_ESPECIE − activos`) y, para cada cupo faltante (hasta `REPOPULATION_MAX_PER_CYCLE` intentos), aplica el mismo pipeline de calidad: torneo de N candidatos → umbral OOS → fallback HoF → slot vacante (nunca forzado). Reutiliza los datos de backtest ya descargados en el ciclo.
  - **Ciclo activo:** la recuperación se ejecuta después de la reproducción normal (paso 7) y antes de la redistribución de capital. Los agentes recuperados se incluyen en la redistribución.
  - **Ciclo suspendido:** la recuperación aún se intenta si hay déficit; descarga datos de Yahoo si no los tiene. Si la recuperación tiene éxito, la redistribución de capital se ejecuta (antes no se redistribuía en ciclos suspendidos).
  - **Invariante de calidad:** el control OOS es innegociable. Un agente que no supera `TOURNAMENT_MIN_OOS_FITNESS` / `TOURNAMENT_MIN_OOS_TRADES` nunca se inserta, ni en recuperación ni en reproducción normal.
  - **Fallback sin Yahoo Finance:** si `fetch_backtest_data()` falla → repopulación omitida silenciosamente (el déficit se reporta en `deficit_restante` pero ningún agente se inserta sin control de calidad).
  - **Trazabilidad:** `slots_recuperados: [{id, especie, fitness_oos, origen}]` y `deficit_restante: {especie: n}` añadidos a `EvolutionResult` y a `logs_juez.datos_json` del evento `evaluacion_diaria`. Los hijos recuperados también generan su evento `nuevo_agente` normal en `judge_agent.py`.
  - **Nuevas env vars:** `TARGET_AGENTS_PER_ESPECIE=5`, `REPOPULATION_MAX_PER_CYCLE=3` en `.env.example` y `judge_daily.yml`.
  - **Tests:** 5 tests unitarios nuevos en `tests/test_sesion18_repopulacion.py` (sin DB ni red): déficit detectado y cubierto, omisión sin backtest, respeto de cap `REPOPULATION_MAX_PER_CYCLE`, fallback HoF, control de calidad estricto (no forzar). Suite completa: 44/44 no-DB + 5 nuevos = 49 tests verdes (3 fallos de BD esperados sin `DATABASE_URL`).

- **2026-06-09 (Sesión 17 — 5 mejoras evolutivas: Fases 1-5) · commits `feat(sesion-17-fase-1..5)` · en producción:**
  - **Contexto:** pool con caída de −0.86 % identificó tres causas raíz: (1) torneo sin umbral de calidad desplegaba hijos con fitness OOS ≤ 0; (2) selección de padres uniforme cuando todo el pool es negativo; (3) inmunidad por muestra insuficiente sin tope de pérdida permitía agentes con drawdown severo sobrevivir indefinidamente.
  - **Fase 1 — Torneo con umbral de calidad (`evolution_engine.py`):** el hijo del torneo se despliega solo si `fitness OOS > TOURNAMENT_MIN_OOS_FITNESS` (0.0, estrictamente) Y `n_trades OOS ≥ TOURNAMENT_MIN_OOS_TRADES` (5). Fallback: se generan 3 hijos de padres del Hall of Fame (misma especie primero) y se aplica el mismo umbral. Si tampoco pasan: slot vacante (registrado en `logs_juez`, campo `slots_vacantes` en `EvolutionResult`).
  - **Fase 2 — Selección de padres por OOS cuando pool negativo (`evolution_engine.py`):** si todos los agentes activos tienen `fitness_score ≤ 0`, los pesos de selección se calculan usando el fitness OOS del backtester (corrido una vez por agente, cacheado por ciclo) en vez de floor 0.0001. Evita selección aleatoria uniforme en crisis.
  - **Fase 3 — Inmunidad revocable por drawdown (`evolution_engine.py`):** un agente protegido solo por muestra insuficiente pierde la inmunidad si `roi_total ≤ −IMMUNITY_MAX_LOSS_PCT` (default 8 %). El Periodo de Gracia Operativa (ops=0, joven) es inviolable. Documentado en `razon_eliminacion`.
  - **Fase 4 — Elegibilidad híbrida por días hábiles (`evolution_engine.py`, `backtester.py`):** la muestra mínima pasa de condición única a condición OR: elegible si `n_trades ≥ MIN_SAMPLE_TRADES` **O** `edad ≥ MIN_SAMPLE_DAYS` (7 días hábiles). Aplica en eliminación, reproducción y Hall of Fame.
  - **Fase 5 — Ruptura bloqueada en régimen RANGO (`trade_monitor.py`, `backtester.py`):** especie `ruptura` no abre posiciones cuando `regime_estado == "RANGO"` y `RUPTURA_SOLO_TENDENCIA=true` (default). Replicado en el backtester para coherencia entre fitness OOS y producción. NEUTRAL sigue operando sin restricción.
  - **Fase 6 — Tests, docs, env vars y workflow:** 6 tests unitarios nuevos en `tests/test_sesion17_fases.py` (umbral torneo, inmunidad revocada, elegibilidad híbrida, gate ruptura x2, constantes). 5 nuevas env vars en `.env.example` y `judge_daily.yml`. `AGENTS_ELIMINATE_PER_CYCLE` corregido de "5" a "9" en el workflow. Secciones 8, 10 y 15 de este documento actualizadas.
  - **Tests:** 13/13 intrabar/trailing verdes · 6/6 nuevos tests de Sesión 17 verdes.

- **2026-06-02 (Sesión 16 continuación — 15 agentes, cuota por especie, vista agrupada en dashboards) · commit `0b15d72` · en producción:**
  - **Expansión de población a 15 agentes (5 por especie):** `scripts/seed_15_agents.py` cría 5 nuevos agentes (1 tendencia + 2 reversion + 2 ruptura) usando `breed_agent()` con los mejores padres de cada especie; redistribuye el pool de $98.64 entre los 15 activos ($6.5757/agente). Incluye `--dry-run` para validar antes de ejecutar. Google Sheets sincronizado. Resultado: distribución exacta 5/5/5.
  - **N_ELIMINATE 5 → 9:** máximo 3 eliminaciones por especie × 3 especies = 9 total. Con `MIN_AGENTS_PER_ESPECIE=2` y 5 agentes/especie, la protección garantiza que nunca se eliminen más de 3 de ninguna especie en un ciclo.
  - **Vista agrupada por especie — Streamlit (`dashboard/app.py`, `dashboard/data.py`):** `fetch_agents()` incluye columna `especie`. Toggle "Por especie" en tab Población: vista agrupada con header de color por especie (emoji, n agentes, fitness medio, ROI medio, capital medio) + tabla y vista plana con columna Especie visible.
  - **Vista agrupada por especie — App móvil (`mobile-app/app/api/dashboard/route.js`, `mobile-app/app/page.js`):** query `activeAgents` incluye `COALESCE(a.especie,'tendencia')`. `RankingList` refactorizado en `AgentRow`, `AgentCard` y `SpeciesGroup` independientes. Botón toggle "Agrupar por especie" (inactivo por defecto). Vista plana con columna Especie con emoji y color. Deploy a Vercel forzado manualmente (`npx vercel --prod`) al no detectarse automáticamente el push del subdirectorio.

- **2026-06-01 (Sesión 16 — auditoría extrema + rediseño evolutivo completo: Fases 0-3) · commits `83f08ef` → `29238c7` · en producción:**
  - **Contexto:** auditoría cuantitativa reveló esperanza matemática estructuralmente negativa (−0.95%/op medida sobre 361 trades en 12 días). El día de la auditoría (01-jun) registró 9/9 operaciones cerradas en pérdida, todas exactamente en el Stop Loss. Causas identificadas: (1) stops de 5 pips barridos por ruido de velas de 1m; (2) trailing/EOD cortaban ganadores, perdedores corrían al SL completo (R:R real 0.67 vs 2.0 objetivo); (3) tres indicadores colineales (RSI-momentum + EMA + MACD) = un solo factor de momentum → selección adversa en rango; (4) 10 agentes 100% correlacionados = 1 apuesta repetida 10 veces; (5) simulador sin fricción sobreestimaba rendimiento.
  - **Fase 0 — Realismo de mercado (commit `83f08ef`):** (1) Piso de Stop Loss 5 → **10 pips** (`MIN_SL_PIPS`, `sub_agent_risk.py`): el stop deja de morir por ruido de velas 1m. (2) **Fricción round-trip** `TRADE_FRICTION_PIPS=1.4` descontada del P&L en `close_operation()`: spread + slippage modelados. (3) **Trailing a +1R**: el trailing nunca activa antes de ganar una R completa; los ganadores no se recortan por debajo de break-even. Verificado: fricción consume 69% del ganador medio histórico — la verdad real que el simulador ocultaba.
  - **Fase 1 — Integridad evolutiva (commit `eb3a0ff`):** (1) **Fitness por expectancy neta**: `fitness = (expectancy/trade / (max_drawdown+1)) × confianza_estadistica`. Expectancy = `win_rate × avg_win − loss_rate × avg_loss` (ya neta de fricción). (2) **Muestra mínima** `MIN_SAMPLE_TRADES=15`: agentes con menos de 15 trades cerrados son inmunes a eliminación, reproducción y Hall of Fame. (3) **Selección de padres por fitness** (no por ROI crudo). La evolución deja de premiar suerte reciente; exige edge estadísticamente validado.
  - **Fase 2 — Diversidad real por especies + filtro de régimen ADX (commit `39e25ff`):** (1) **Migración 010** (`010_especies.sql`): columna `especie` en `agentes` (4 tendencia / 3 reversion / 3 ruptura). S2 configurados con `rsi_modo=reversion`, `htf_filter_enabled=0`. S3 con genes `breakout_lookback_bars=20`, `breakout_min_pips=5.0`. (2) **3 arquetipos decorrelacionados**: tendencia (momentum, ADX≥25), reversion (extremos RSI/OB/FVG, ADX<25), ruptura (breakout de estructura, ambos regímenes). Verificado: con ADX=17.7 (el del día de la auditoría), tendencia bloqueada, reversion habilitada → habrían ganado exactamente los mismos movimientos que perdieron los S1. (3) **Clasificador ADX** `calc_regime()` + `detect_breakout()` en `indicators.py`. `TechnicalSignals` extendido con `adx`, `regime_estado`, `breakout_activo`, `breakout_direccion`, `breakout_pips`. (4) **Gate de régimen** en `trade_monitor._evaluate_new_positions()`: agente bloqueado si su especie es incompatible con el régimen actual. (5) **Protección de diversidad**: evolución garantiza ≥ 2 agentes por especie; hijos heredan especie del eliminado (como-por-como); crossover preferentemente entre padres de la misma especie. (6) `SubAgentTechnical.analyze()` recibe `especie` y enruta el ensamble ponderado según el arquetipo. (7) `InvestorAgent` carga y propaga `especie` desde la DB.
  - **Fase 3 — Backtest walk-forward + torneo de candidatos (commit `29238c7`):** (1) **`evolution/backtester.py`**: simula el pipeline completo (calc_signals → análisis por especie → SL/TP → fricción) sobre 60 días de historia. Walk-forward: 40d warmup / 20d OOS. Sin LLM (heurística determinista). ~3.5s por backtest. (2) **Torneo en `evolution_engine.run()`**: por cada slot vacante, se generan `N_CANDIDATE_CHILDREN=3` candidatos, se backtes-tean en OOS, se inserta el de mayor fitness. Si Yahoo Finance falla → fallback a crianza directa. 1 sola descarga de datos para todo el ciclo. Tiempo total estimado: ~53s (dentro del timeout de 12 min del workflow). (3) El motor evolutivo ahora tiene masa estadística para seleccionar: ~520 OOS candles por candidato vs. ~3 trades/día en vivo.
  - **6 archivos modificados + 2 nuevos** en este conjunto de commits: `agents/sub_agent_risk.py`, `agents/investor_agent.py`, `agents/sub_agent_technical.py`, `cron/trade_monitor.py`, `data/indicators.py`, `data/alpha_vantage_client.py`, `evolution/evolution_engine.py`, `db/migrations/010_especies.sql` (nuevo), `evolution/backtester.py` (nuevo), `.env.example`.
  - **Migración 010 aplicada en producción** (Supabase) durante la sesión, antes del commit. 10 agentes activos con especie asignada.
  - **Tests:** 13/13 intrabar/trailing verdes tras cada fase.

- **2026-05-29 (Sesión 15 — auditoría estratégica completa: filtro HTF + RSI momentum + sesgo macro + backtest) · commit `bea6a9f` · en producción:**
  - **Contexto:** todas las operaciones del 29-may cerraron en pérdida. Análisis de los gráficos (EUR/USD 15m y 1h) reveló tres problemas estructurales: (1) el RSI operaba en modo sobrecompra/sobreventa, comprando en tendencias bajistas; (2) la macro siempre devolvía HOLD plano cuando no había noticias, dejando la decisión solo al técnico con señales débiles; (3) los spikes de volatilidad amplificaban la confianza sin verificar si la vela confirmaba la dirección.
  - **Fase 1 — Filtro HTF (`htf_filter_enabled`, `data/indicators.py`, `agents/sub_agent_technical.py`, `cron/trade_monitor.py`):** se descarga EMA50/EMA200 en velas de 1h y se calcula `htf_direccion` (BULL/BEAR/NEUTRAL). Señales que contradicen la tendencia superior se vetan → forzadas a HOLD. El filtro se aplica después del scoring ponderado y antes del LLM para que el veto sea definitivo. Añadidos tres campos a `TechnicalSignals`: `htf_direccion`, `htf_ema_rapida`, `htf_ema_lenta`.
  - **Fase 2 — RSI Momentum (`agents/sub_agent_technical._score_rsi`):** reescritura completa de la lógica RSI. El modo `"momentum"` (nuevo default) usa el cruce del nivel 50 en lugar de sobrecompra/sobreventa: cruce alcista → BUY fuerte (0.60–0.85), cruce bajista → SELL fuerte (0.60–0.85), sin cruce pero fuera de la `rsi_zona_muerta` → señal débil (0.40–0.60), dentro de la zona muerta → HOLD (0.35). Se necesita el RSI de la vela anterior (`rsi_prev`) para detectar el cruce — añadido como campo opcional en `TechnicalSignals` con default 50.0.
  - **Fase 3 — Sesgo macro por tendencia HTF (`agents/sub_agent_macro.py`, `agents/investor_agent.py`):** nuevo método `_sesgo_tendencia(htf_trend)` en `SubAgentMacro`. Cuando el LLM devuelve HOLD con confianza baja y no hay eventos de alto impacto, en lugar de propagar HOLD plano (0.35) el sub-agente B usa la dirección EMA 1h para emitir BUY/SELL con confianza = `min(0.55, peso_sesgo_tendencia)`. El gen `peso_sesgo_tendencia` (0.20–0.65, default 0.40) controla la intensidad. `InvestorAgent.run_cycle` ahora acepta y propaga `htf_trend` al sub-agente B.
  - **Fase 4 — Range Spike condicionado (`agents/sub_agent_technical.analyze`):** el spike de volatilidad (rango > MA20 × multiplicador) solo amplifica la confianza ×1.15 si la dirección de la última vela (`candle_direccion`) confirma la señal. Si contradice, la confianza queda intacta — ni amplifica ni atenúa. Eliminado el bloque de atenuación que introducía regresión (−1.6pp win-rate vs la versión sin atenuación en backtest).
  - **Fase 5 — Genes nuevos en `evolution_engine.py` + migración 009:** los 4 genes nuevos registrados en `_BOUNDS_*` para que `_mutate_block` los evolucione: `rsi_zona_muerta` (1.0–15.0, en `_BOUNDS_TECNICOS_PERIODS`), `peso_sesgo_tendencia` (0.20–0.65, en `_BOUNDS_MACRO`), `htf_filter_enabled` (no mutado gaussianamente — en `_DEFAULT_SMC_PARAMS` como valor entero 0/1). El gen `rsi_modo` es string → solo cambia por crossover, no tiene entry en `_BOUNDS_*`. Migración `009_htf_rsi_sesgo_genes.sql` hace backfill idempotente de los 4 genes en los 10 agentes existentes usando el patrón `params_col || '{"key": val}'::jsonb WHERE NOT (params_col ? 'key')`. Aplicada en producción en Sesión 15.
  - **Fase 6 — Backtest walk-forward de validación (`scripts/backtest_estrategia.py`):** nuevo script que reproduce el pipeline completo vela por vela (LLM neutralizado → ruta heurística determinista). Descarga OHLCV 15m de Yahoo Finance + serie HTF 1h para el filtro. Replica exactamente `check_sl_tp_intrabar`, `_apply_trailing_stop` y `close_operation`. Resultados sobre 1 mes (2.068 velas, genes semilla):
    - **ANTES (sin las 4 fases):** PnL +$0.20, win-rate 45.5%, drawdown 13.23%, Calmar 0.151
    - **DESPUÉS (con las 4 fases):** PnL +$1.32, win-rate 47.7%, drawdown 8.73%, Calmar 1.508
    - Mejora: PnL ×6.6 (+561%), drawdown −4.5pp, Calmar +901%
  - **9 archivos en el commit:** `data/alpha_vantage_client.py`, `data/indicators.py`, `agents/sub_agent_technical.py`, `agents/sub_agent_macro.py`, `agents/investor_agent.py`, `cron/trade_monitor.py`, `evolution/evolution_engine.py`, `db/migrations/009_htf_rsi_sesgo_genes.sql` (nuevo), `scripts/backtest_estrategia.py` (nuevo).
  - **Estado post-Sesión 15:** migración 009 aplicada en producción (10 agentes activos, todos con los 4 genes nuevos). Push a `origin/master`. El sistema opera con las 4 mejoras activas. Monitoreo recomendado durante 2-3 días hábiles para confirmar la mejora en producción.

- **2026-05-28 (Sesión 14 — hotfix producción: migración 008 faltante + timeout del monitor) · commit `9f910ed` · en producción:**
  - **Síntoma reportado:** desde las 9:30 pm del 27-may no se generaban operaciones nuevas; el dashboard quedó congelado toda la noche.
  - **Causa raíz #1 — migración 008 nunca aplicada a Supabase:** el deploy de Sesión 13 (commit `8be1654`, 27-may 21:34 Bogotá) introdujo en `cron/trade_monitor.sync_once()` una consulta que selecciona `o.timestamp_ultima_verificacion`. Esa columna la crea la migración 008, que **solo se commiteó pero nunca se corrió en producción**. Resultado: cada ciclo del monitor (cada 15 min) moría con `psycopg2.errors.UndefinedColumn: column o.timestamp_ultima_verificacion does not exist` desde las 9:45 pm. ⚠️ La nota de Sesión 13 que afirmaba "el código ya es retrocompatible con la columna ausente — fallback a `timestamp_entrada`" era **incorrecta**: el fallback aplica a la carga de velas OHLC, pero el `SELECT` de la columna en el cursor de operaciones falla en duro si la columna no existe. El force-close del Juez usa otra ruta (`--force-close-all`) y no se vio afectado, por eso las posiciones abiertas sí se cerraron al EOD.
  - **Fix #1:** `python -m db.apply_migrations --only 008` ejecutado contra Supabase (idempotente). Columna `operaciones.timestamp_ultima_verificacion` confirmada presente. Monitor verde de inmediato (run de verificación: `evaluados=10 errores=0`).
  - **Causa raíz #2 — timeout del job demasiado ajustado:** el run de las 11:15 UTC (06:15 Bogotá) se **canceló a los 5 min** (`timeout-minutes: 5`) tras evaluar solo 6 de 10 agentes. Cuando `deepseek-reasoner` responde lento (~30-35s/agente en vez de ~3s), 10 agentes + setup + verificación SL/TP no caben en 5 min y GitHub mata el job a la mitad.
  - **Fix #2 (commit `9f910ed`):** `timeout-minutes` del job `monitor` subido de **5 → 12** en `trade_monitor.yml`. Da holgura para los 10 agentes con DeepSeek lento y sigue por debajo de la cadencia de 15 min (sin riesgo de solape). Validado: runs programados por cron-job.org pasan con `evaluados=10`.
  - **Limpieza de issues de alerta:** cerrados los 4 issues abiertos en GitHub. #4 (28-may) resuelto por el Fix #1. #1 (8-may, `timeout expired` en Neon) y #3 (19-may, `compute time quota exceeded` en Neon) cerrados por obsoletos: eran de la infra Neon previa, ya reemplazada por Supabase. #2 (18-may, `CheckViolation` en `ranking_historico.evento`) ya estaba resuelto el mismo día por el commit `1920d83`; el Juez corre verde desde el 19-may.
  - **Lección registrada (memoria del proyecto):** al desplegar código que use una columna/tabla nueva, **aplicar la migración a Supabase en el deploy** (`python -m db.apply_migrations --only NNN`); los workflows de GitHub Actions NO corren migraciones automáticamente.
  - **Estado post-Sesión 14:** migración 008 en producción, monitor evaluando los 10 agentes sin cortes, timeout 12 min, 0 issues abiertos. 10 agentes activos, 0 posiciones abiertas en el momento de la verificación (todos los agentes en HOLD, conf ~0.32-0.34 — decisión legítima de estrategia).

- **2026-05-27 (Sesión 13 — verificación intra-vela de SL/TP con OHLC 1m) · commit `8be1654` · en producción:**
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
  - **Estado post-Sesión 13:** 6 archivos commiteados y pusheados a `origin/master` (commit `8be1654`). Los workflows de GitHub Actions usarán la nueva lógica intra-vela a partir del próximo disparo de `trade_monitor.yml`. ~~**Pendiente aplicar en producción:** `python -m db.apply_migrations --only 008`~~ → **APLICADA en Sesión 14 (2026-05-28).** ⚠️ La afirmación de "retrocompatibilidad con la columna ausente" resultó **falsa**: el `SELECT o.timestamp_ultima_verificacion` en `sync_once()` falla en duro con `UndefinedColumn` si la columna no existe, lo que dejó el monitor caído ~8h hasta que se aplicó la migración. Ver entrada de Sesión 14 arriba.

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
