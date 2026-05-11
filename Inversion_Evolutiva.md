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
| **PostgreSQL (Neon)** | Base de datos principal — estado de agentes, operaciones, logs del Juez |
| **DeepSeek API** (`deepseek-chat`) | LLM para razonamiento táctico (señales ambiguas) y veredictos del Juez |
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
| **GitHub Actions** | Ejecución automática del monitor (cada 15 min) y del Juez (5pm Bogotá L-V) |
| **Streamlit Community Cloud** | Hosting del dashboard en `inversion-evolutiva.streamlit.app` |
| **GitHub Secrets** | Almacenamiento seguro de todas las credenciales |

---

## 3. Arquitectura general

```
┌─────────────────────────────────────────────────────────────────┐
│                     GITHUB ACTIONS                              │
│                                                                 │
│  trade_monitor.yml          judge_daily.yml                     │
│  Cada 15 min, L-V           5pm Bogotá (22:00 UTC), L-V        │
│  2am–4pm Bogotá             ┌──────────────────────┐            │
│  ┌──────────────────┐       │  Agente Juez         │            │
│  │  TradeMonitor    │       │  - Evalúa fitness    │            │
│  │  - Verifica SL/TP│       │  - Elimina bottom 5  │            │
│  │  - Trailing stop │       │  - Crea 5 hijos      │            │
│  │  - Abre pos. new │       │  - Redistribuye cap. │            │
│  └────────┬─────────┘       │  - Razona con LLM    │            │
│           │                 └──────────┬───────────┘            │
└───────────┼──────────────────────────┼────────────────────────-┘
            │                          │
            ▼                          ▼
┌───────────────────────────────────────────────────────────────┐
│                    POSTGRESQL (Neon)                           │
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
              Position sizing dinámico
              → RiskDecision {accion_final, stop_loss, take_profit, capital_a_usar}
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

**Position sizing dinámico:**
```
capital_uso = (equity × risk_pct_per_trade) / (sl_pips × $0.10/pip)
capital_uso = min(capital_uso, equity × 20%)   # hard cap 20%
risk_pct forzado al rango [1%, 2%]             # límites inmutables
```

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

**Qué se envía:** Señales de A y B completas, precio actual, SL/TP calculados, capital disponible y la acción preliminar con su confianza.

**Qué se espera:** JSON con la decisión final, que puede confirmar o ajustar los niveles de SL/TP y el capital a usar.

**Fallback:** Se usa la decisión heurística calculada localmente.

### 6.4 Agente Juez — veredicto evolutivo

**Cuándo:** Una vez al día, después de que el motor genético completa la selección/eliminación/reproducción.

**Qué se envía:** Tabla completa de supervivientes y eliminados con su fitness, ROI, win rate y parámetros clave; lista de nuevos agentes con sus padres.

**Qué se espera:** JSON con `veredicto_general`, análisis de por qué fallaron los eliminados, expectativas para los nuevos y recomendaciones de parámetros.

**Fallback:** El Juez genera un veredicto automático sin LLM con la información de fitness y ROI disponible.

### Configuración técnica del cliente LLM

```python
# base_agent.py
_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",   # protocolo compatible OpenAI
)
model       = "deepseek-chat"
temperature = 0.1     # respuestas muy deterministas
max_tokens  = 512
timeout     = 30 seg
```

---

## 7. Monitor de trades — SL/TP y trailing stop

**Archivo:** `cron/trade_monitor.py`
**Ejecución:** Cada 15 minutos, lunes a viernes, de 7:00am a 9:00pm UTC (2:00am – 4:00pm Bogotá).

### Ciclo de cada ejecución

```
Para cada agente:
  ┌─ ¿Tiene posición abierta?
  │
  ├── SÍ → Verificar SL/TP:
  │         1. Obtener precio actual (Yahoo Finance)
  │         2. Aplicar trailing stop (si corresponde)
  │         3. Verificar si precio tocó SL o TP
  │         4. Si SÍ → cerrar posición:
  │                     - Calcular P&L real
  │                     - UPDATE operaciones (estado=cerrada, pnl, precio_salida)
  │                     - UPDATE agentes (capital_actual, roi_total, ops_ganadoras)
  │                     - Actualizar Google Sheets
  │
  └── NO → Evaluar si abrir nueva posición:
            1. ¿Estamos en horario de trading? (2am–3pm Bogotá)
            2. ¿Cuarentena macro? (eventos críticos próximos)
            3. Ejecutar pipeline A → B → C
            4. Si decisión = BUY/SELL → INSERT en operaciones
```

### Trailing stop dinámico

El trailing stop protege ganancias sin limitar el potencial alcista:

1. Se activa cuando la posición acumula `trailing_activation_pips` de ganancia (gen del agente, default 15 pips).
2. Una vez activo, el SL se mueve a `precio_extremo_favorable - trailing_distance_pips` (default 10 pips).
3. El SL **nunca retrocede**: solo puede mejorar (moverse a favor).
4. El precio extremo favorable se actualiza en cada ciclo de 15 minutos.

### Cierre al final del día (EOD)

El workflow `judge_daily.yml` ejecuta `trade_monitor --force-close-all` antes de que el Juez evalúe. Esto cierra todas las posiciones abiertas al precio de mercado actual para que el Juez evalúe con P&L definitivo del día.

---

## 8. Agente Juez — ciclo evolutivo diario

**Archivo:** `agents/judge_agent.py` + `evolution/evolution_engine.py`
**Ejecución:** 5:00pm hora Bogotá (22:00 UTC), lunes a viernes.

### Secuencia del ciclo evolutivo

```
1. EVALUACIÓN DE FITNESS (Calmar Ratio Proxy)
   ─────────────────────────────────────────
   fitness = ROI_total / (max_drawdown + 1)
   Penalidad: -0.5 si avg_ops_dia > 3 Y win_rate < 50%

2. RANKING
   ────────
   Ordenar 10 agentes por fitness DESC.
   Desempate: fecha_nacimiento DESC, id DESC
   (agentes más jóvenes sobreviven ante empate de fitness)

3. ELIMINACIÓN
   ────────────
   Los 5 agentes con menor fitness → estado = 'eliminado'

4. REPRODUCCIÓN (5 nuevos agentes)
   ─────────────────────────────────
   Para cada cupo vacante:
     a. Seleccionar 2 padres del pool de supervivientes
        (probabilidad proporcional al ROI de cada padre)
     b. Crossover de los 4 bloques de genes
     c. Mutación gaussiana sobre cada gen
     d. Normalizar pesos y aplicar constraints
     e. INSERT en agentes (generacion = max_generacion_activa + 1)

5. RAZONAMIENTO LLM
   ──────────────────
   DeepSeek analiza los resultados y produce:
   - Por qué fallaron los eliminados (parámetros problemáticos)
   - Qué se espera de los nuevos agentes (herencia + mutación)
   - Insight sobre condiciones de mercado del día
   - Recomendaciones de parámetros para próximas generaciones

6. PERSISTENCIA EN LOGS
   ──────────────────────
   logs_juez ← evaluacion_diaria (veredicto global)
   logs_juez ← eliminacion (uno por agente eliminado)
   logs_juez ← seleccion_padres (uno por agente nuevo)
   logs_juez ← nuevo_agente (uno por agente nuevo)

7. REDISTRIBUCIÓN DE CAPITAL
   ──────────────────────────
   pool_total = SUM(capital_actual) de todos los activos
   capital_por_agente = pool_total / 10
   UPDATE agentes SET capital_actual = capital_por_agente WHERE estado = 'activo'
   → Todos arrancan el día siguiente con el mismo capital
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

### Eliminación

Los 5 agentes con menor Calmar Ratio Proxy son eliminados cada tarde:

```sql
UPDATE agentes SET
    estado             = 'eliminado',
    fecha_eliminacion  = fecha_del_ciclo,
    razon_eliminacion  = 'Selección natural: bottom 5 por fitness'
WHERE id = ANY(eliminados)
```

Sus registros permanecen en la tabla `agentes` para trazabilidad histórica; solo cambia `estado`.

---

## 11. Base de datos — PostgreSQL

Alojada en **Neon** (PostgreSQL serverless). 5 tablas principales:

### `agentes`

Registro central de cada agente. Campos clave:

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | VARCHAR(20) PK | Identificador único YYYY-MM-DD_NN |
| `generacion` | INTEGER | Número de generación evolutiva |
| `padre_1_id` / `padre_2_id` | VARCHAR(20) FK | Árbol genealógico (NULL en Gen 1) |
| `estado` | VARCHAR | `activo` / `eliminado` / `retirado` |
| `params_tecnicos` | JSONB | Genes del sub-agente A |
| `params_macro` | JSONB | Genes del sub-agente B |
| `params_riesgo` | JSONB | Genes del sub-agente C |
| `params_smc` | JSONB | Genes SMC + ATR |
| `capital_inicial` / `capital_actual` | DECIMAL | Capital en dólares |
| `roi_total` | DECIMAL | ROI acumulado en porcentaje |
| `operaciones_total` / `operaciones_ganadoras` | INTEGER | Estadísticas |

### `operaciones`

Log de cada señal BUY/SELL/HOLD. Una fila por ciclo de decisión.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | SERIAL PK | Identificador numérico auto-incremental |
| `agente_id` | FK → agentes | Agente que generó la señal |
| `accion` | VARCHAR | `BUY` / `SELL` / `HOLD` |
| `precio_entrada` / `precio_salida` | DECIMAL | Precios reales de mercado |
| `pnl` | DECIMAL | Ganancia/pérdida en USD |
| `estado` | VARCHAR | `abierta` / `cerrada` / `cancelada` |
| `senal_tecnico` | JSONB | Output completo del sub-agente A |
| `senal_macro` | JSONB | Output completo del sub-agente B |
| `decision_riesgo` | JSONB | Output completo del sub-agente C (SL, TP, razonamiento) |
| `sl_dinamico` | DECIMAL | SL actual (actualizado por trailing stop) |
| `precio_extremo_favorable` | DECIMAL | Precio más favorable alcanzado (para trailing) |

### `logs_juez`

Audit trail completo del Agente Juez.

| Tipo de evento | Descripción |
|---|---|
| `evaluacion_diaria` | Veredicto global del ciclo: supervivientes, eliminados, capital |
| `eliminacion` | Un registro por agente eliminado con fitness y razonamiento LLM |
| `seleccion_padres` | Qué padres se eligieron para cada nuevo agente |
| `nuevo_agente` | Genes del nuevo agente y expectativas del LLM |

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

El logger (`utils/sheets_logger.py`) escribe de forma asíncrona: si Sheets no está disponible, el trading no se interrumpe.

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

## 14. Automatización — GitHub Actions

### `trade_monitor.yml` — Monitor intraday

```
Schedule: */15 7-21 * * 1-5
          Cada 15 minutos, L-V, 7am–9pm UTC (2am–4pm Bogotá)

Pasos:
  1. Checkout del repo
  2. Setup Python 3.11
  3. pip install -r requirements.txt
  4. python -m cron.trade_monitor --run-once

En caso de fallo: crea un GitHub Issue con alerta (evita duplicados)
```

**Secrets requeridos:** `DATABASE_URL`, `DEEPSEEK_API_KEY`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS_JSON`

### `judge_daily.yml` — Ciclo evolutivo diario

```
Schedule: 0 22 * * 1-5
          5:00pm Bogotá (22:00 UTC), lunes a viernes

Pasos:
  1. Checkout + Python 3.11
  2. Health check de la DB
  3. Cierre EOD: python -m cron.trade_monitor --force-close-all
  4. Ciclo evolutivo: python -m cron.judge_scheduler --run-now

Disparo manual: workflow_dispatch con opción dry_run=true (solo health check)

Parámetros de evolución (env vars):
  AGENTS_ELIMINATE_PER_CYCLE = 5
  MUTATION_SIGMA_WEIGHTS     = 0.05
  MUTATION_SIGMA_PERIODS     = 0.08
  MUTATION_SIGMA_RISK        = 0.10
  MIN_ROI_FOR_HALL_OF_FAME   = 0.05

En caso de fallo: crea GitHub Issue con alerta
```

### `health_check.yml` y `trading_cycle.yml`

Workflows adicionales para verificación de estado y ciclos de trading manual ad-hoc.

---

## 15. Variables de configuración

Todas las variables se definen en `.env` local (desarrollo) o en **GitHub Secrets** (producción).

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Cadena de conexión a Neon PostgreSQL |
| `DEEPSEEK_API_KEY` | API key de DeepSeek LLM |
| `FINNHUB_API_KEY` | API key de Finnhub (noticias y calendario económico) |
| `ALPHA_VANTAGE_API_KEY` | API key de Alpha Vantage (legado) |
| `GOOGLE_SHEET_ID` | ID del spreadsheet de Google Sheets |
| `GOOGLE_CREDENTIALS_JSON` | JSON completo de la service account de Google (o ruta al archivo en local) |
| `DEEPSEEK_BASE_URL` | Base URL del API (default: `https://api.deepseek.com`) |
| `DEEPSEEK_MODEL` | Modelo a usar (default: `deepseek-chat`) |
| `JUDGE_TIMEZONE` | Zona horaria del Juez (default: `America/Bogota`) |
| `JUDGE_RUN_TIME` | Hora de ejecución del Juez (default: `17:00`) |
| `AGENTS_ELIMINATE_PER_CYCLE` | Agentes eliminados por ciclo (default: `5`) |
| `MUTATION_SIGMA_WEIGHTS` | Sigma de mutación para pesos (default: `0.05`) |
| `MUTATION_SIGMA_PERIODS` | Sigma de mutación para períodos (default: `0.08`) |
| `MUTATION_SIGMA_RISK` | Sigma de mutación para riesgo/SMC (default: `0.10`) |
| `MIN_ROI_FOR_HALL_OF_FAME` | ROI mínimo para entrar al Hall of Fame (default: `0.05`) |
| `LOG_LEVEL` | Nivel de logs (default: `INFO`) |
| `ENVIRONMENT` | Ambiente (`production` / `development`) |

---

## 16. Flujo completo día a día

### Lunes a viernes — horario Bogotá

```
2:00am  ─── GitHub Actions despierta trade_monitor
            ├── Verifica SL/TP de posiciones abiertas
            └── Evalúa nuevos trades para agentes libres

Cada 15 min hasta las 3:00pm:
            ├── Ciclo SL/TP + trailing stop por agente
            ├── Si agente libre → pipeline A→B→C → posible nuevo trade
            └── Registro en DB y Google Sheets

4:55pm  ─── Último monitor antes del cierre

5:00pm  ─── GitHub Actions despierta judge_daily:
            1. Cierre forzado de todas las posiciones abiertas (EOD)
            2. Evaluación de fitness: Calmar Ratio Proxy para 10 agentes
            3. Eliminación de los 5 con menor fitness
            4. Reproducción: 5 nuevos agentes (crossover + mutación)
            5. Razonamiento LLM: veredicto y expectativas
            6. Registro en logs_juez
            7. Redistribución de capital: pool ÷ 10

5:10pm  ─── Sistema queda en reposo hasta el día siguiente
```

### Fines de semana

El sistema no opera: GitHub Actions no tiene schedule para sábado/domingo. Los agentes permanecen en sus estados finales del viernes hasta el lunes.

### Intervención manual

El usuario puede disparar manualmente cualquier workflow desde GitHub Actions:
- **trade_monitor** → `workflow_dispatch` para forzar un ciclo inmediato.
- **judge_daily** → `workflow_dispatch` con `dry_run=true` para verificar estado sin evolucionar.

---

*Documento generado el 2026-05-10. Refleja el estado del sistema en Generación 1 del nuevo run iniciado el 2026-05-11.*
