# Plan de Rediseño — Rentabilidad del Motor Evolutivo (Sesión 2026-07-02)

> Resultado de la evaluación exhaustiva de arquitectura y código solicitada vía `/goal`.
> Cubre: (1) ejecución intradía, (2) función de fitness, (3) selección/descarte/evolución.
> Complementa y extiende `PLAN_DE_MEJORA.md` (Fases 1–3 ya implementadas, flags apagados)
> y la auditoría 2026-07-01 (788 trades: WR 39%, payoff 1.12, expectancy negativa).

## Veredicto

El sistema cumple el objetivo **mecánicamente** (agentes compiten, se califican, se eliminan
y se recrían) pero **no económicamente**: la expectancy por operación es negativa y la
selección natural no está filtrando perdedores. Las causas no son bugs puntuales sino tres
decisiones de diseño que anulan la presión selectiva:

1. **El fitness vive en una escala de ruido** (expectancy en dólares sobre ~$6 de capital,
   comprimida en ±0.02) y solo mata cuando es ≤ 0 → un agente con ROI −354% sobrevive con
   fitness −0.005 apenas distinguible de 0.
2. **La redistribución equitativa de capital** (`_redistribute_capital`) borra cada noche la
   consecuencia económica de ganar o perder → la única fuerza selectiva que queda es el
   fitness ruidoso.
3. **La garantía incondicional de 15 agentes** (cascada `forzado_cruce`/`forzado_clon_unico`)
   fuerza el llenado de nichos perdedores (ruptura = 68% de la pérdida) aunque ningún
   candidato demuestre edge → cantidad sobre calidad.

A esto se suman divergencias simulador/producción que invalidan parcialmente el fitness OOS
como predictor: cadencia 1h en backtest vs 15min en prod, y LLM (DeepSeek) que puede
sobreescribir dirección/SL/TP en producción pero no existe en el backtest.

## Hallazgos por área

### 1. Ejecución intradía (`cron/trade_monitor.py`, `agents/*`)

**Sólido:** verificación intra-vela 1m de SL/TP, INSERT atómico anti-carrera, fricción
modelada (1.4 pips), gate de régimen ADX por especie, cuarentena macro, SL acotado 10–35
pips, sizing por riesgo 1–2% con techo de apalancamiento.

**Problemas:**
- **P1. Divergencia de cadencia**: `backtester._CHECK_EVERY=4` (1h) dice "= cron real", pero
  cron-job.org dispara cada 15 min → prod tiene 4× más oportunidades de entrada que el
  simulador que decide qué genomas nacen.
- **P2. LLM en el camino de ejecución** (`sub_agent_technical.analyze` banda 0.45–0.65;
  `sub_agent_risk.analyze` puede reescribir acción/SL/TP): no determinista, no backtesteable,
  costo recurrente; el fitness OOS mide una política distinta a la desplegada.
- **P3. Estructura de salidas corta ganadores**: BE a 0.6R + trailing desde 1R + salida por
  reversal + cierre forzoso EOD 22:45 → TP de 2R casi nunca se alcanza; firma en datos:
  avg_win≈avg_loss (payoff 1.12) pese a R:R objetivo 2.0.
- **P4. Sin filtro de sesión**: ventana 1:30am–11pm Bogotá incluye sesión asiática de baja
  liquidez para EUR/USD; el edge intradía se concentra en Londres/NY.

### 2. Función de fitness (`evolution_engine._build_fitness_sql`, `backtester._calc_metrics`)

- **F1. No es escala-invariante**: expectancy en USD sobre capital ~$6 → todo el ranking vive
  en ±0.02. Solución: expectancy en **R** (múltiplos del riesgo por trade) o en pips netos.
- **F2. `roi_total` roto contamina decisiones**: es suma aritmética de `pnl_pct` con base que
  se reinicia en cada redistribución (`investor_agent.py:410`). Se usa en: elegibilidad HoF
  (`MIN_ROI_HALL_OF_FAME=0.05` = 0.05%, umbral trivial), dominancia de cruce (`p1_weight`),
  revocación de inmunidad, desempates y pesos de padres HoF.
- **F3. Drawdown SQL mal definido**: la serie de capital arranca en 0 (`SUM(pnl)`), sin base;
  picos negativos producen DD sin sentido. El backtester usa base 10.0 → **dos definiciones
  distintas de la misma métrica** entre vivo y OOS.
- **F4. Penalización por overtrading binaria** (−0.5 si ops/día>3 y WR<50%): un acantilado 25×
  mayor que la escala de la señal.
- **F5. Doble fuente de verdad**: la fórmula vive duplicada en SQL (2 sitios) y en Python.

### 3. Selección, descarte y evolución (`evolution_engine.py`)

- **S1. Presión selectiva casi nula**: solo se elimina fitness ≤ 0 (cuota dinámica); con la
  escala comprimida, perdedores crónicos orbitan el 0 y sobreviven; el piso
  `_MIN_AGENTS_PER_ESPECIE=2` + repoblación a 5 blinda a la especie ruptura (WR 24.8%).
- **S2. Capital no selecciona**: redistribución equitativa nocturna (auditoría P0-2: el cambio
  más importante).
- **S3. Cascada de degradación anula el gate OOS**: `mejor_candidato_oos` → `forzado_cruce` →
  `forzado_clon_unico` despliegan candidatos que NO pasaron el umbral para garantizar 15.
- **S4. Intensidad de selección de cría baja**: 3 candidatos/slot sobre 1 split de 20 días;
  población total 15 — minúscula para ~40 dimensiones de genoma.
- **S5. Evaluación en vivo como evaluador primario**: acumular 15 trades reales toma ~1 semana
  por agente → el "tiempo generacional" del GA es de días y la señal por generación es ruido.
  Un GA con este presupuesto de evaluación no converge.
- **Bien diseñado (mantener)**: especies por régimen, inmunidad por muestra con tope de
  pérdida, sigma boost por diversidad, bounds+constraints post-mutación, gate bootstrap y
  multi-fold ya implementados (apagados), instrumentación `fitness_oos_prometido` lista.

## Enfoques superiores propuestos

1. **Invertir la arquitectura de evaluación**: evolución **offline** sobre historia profunda
   (población 100+, multi-fold walk-forward, CMA-ES o GA generacional real) y producción como
   **validación** de campeones — no al revés. Yahoo 60d es insuficiente: incorporar fuente
   histórica de años (Dukascopy/HistData M1/M15).
2. **Fitness escala-invariante y único**: expectancy media en R − λ·varianza, profit factor y
   DD sobre base de capital real; una sola implementación (Python) consumida por SQL vía
   tabla materializada o cálculo en el Juez.
3. **Capital ∝ fitness** con suelo mínimo: el capital se convierte en memoria de desempeño y
   segunda fuerza selectiva.
4. **Selección por ranking relativo** (bottom-k de los elegibles, k adaptativo) + regla de
   bleeder crónico independiente de la escala del fitness + permitir extinción temporal de
   una especie estructuralmente perdedora (o rediseñarla: fade del breakout fallido).
5. **Determinismo en ejecución**: LLM fuera del camino por-trade (queda en el Juez para
   narrativa); mismo código de señal en backtest y prod, misma cadencia.
6. **Payoff primero**: TP parcial +1R + runner, revisar BE/trailing/EOD para dejar correr
   ganadores; sesión de trading como gen.

## Plan de implementación por fases

### Fase 0 — Activar lo ya construido (días; riesgo bajo)
1. Aplicar `db/migrations/012_fitness_oos_prometido.sql` en Supabase prod (manual).
2. Desplegar código de Fase 1 (instrumentación decaimiento OOS→prod) — ya listo.
3. Tras 1 ciclo en shadow: `TOURNAMENT_GATE_MODE=bootstrap`.
4. Unificar cadencia sim/prod: `_CHECK_EVERY=1` en backtester (o documentar y bajar el cron a 1h).

### Fase 1 — Fitness honesto (1 semana; prerequisito de todo)
1. Expectancy en R por trade (pnl / riesgo_planificado) en vivo y OOS; misma fórmula única.
2. Arreglar DD (base de capital real) y retirar la penalización binaria (usar penalización
   continua por frecuencia si hace falta).
3. Retirar `roi_total` de TODAS las decisiones (HoF, p1_weight, inmunidad, desempates);
   sustituir por la métrica nueva. `roi_total` queda solo informativo o se recalcula
   geométricamente.

### Fase 2 — Presión selectiva real (1–2 semanas)
1. Capital ∝ fitness (suelo del 50% de la cuota equitativa; techo 2×).
2. Eliminación bottom-k por ranking relativo entre elegibles + regla de bleeder crónico
   (p.ej. expectancy R < −0.1 con n≥20 → eliminación directa).
3. Gate OOS sin bypass: si ningún candidato pasa el bootstrap, el cupo queda vacante hasta
   el día siguiente (eliminar `forzado_cruce`/`forzado_clon_unico`; población flotante 9–15).
4. Reevaluar `TARGET_AGENTS_PER_ESPECIE` por especie según expectancy histórica (ruptura: 2
   o rediseño).

### Fase 3 — Payoff intradía (2 semanas)
1. Salida parcial 50% a +1R, runner a 2–3R con trailing; BE después del parcial.
2. Sesión de trading como gen (bloques Londres / NY / overlap).
3. LLM fuera del camino de ejecución (flag `LLM_EXECUTION_MODE=off` default off).
4. Meta medible: payoff realizado > 1.6 con WR ≥ 40%.

### Fase 4 — Evolución offline (3–4 semanas; go/no-go tras Fase 1–3)
1. Ingesta histórica multi-año EUR/USD M15 (Dukascopy/HistData) a tabla local/parquet.
2. Motor de evolución offline: población 100+, `BACKTEST_MODE=multifold` (ya listo) sobre
   ventanas rodantes de 6–12 meses, elitismo real.
3. Producción pasa a ser la fase de validación: solo campeones offline entran al pool vivo.

### Fase 5 — Medición continua
- `v_decaimiento_oos`: correlación prometido↔realizado, tasa de falsos positivos del torneo.
- KPIs semanales: expectancy R, profit factor, payoff, DD, por especie.
- Criterio de éxito global: expectancy R > 0 sostenida 4 semanas con PF > 1.1.

**Regla operativa transversal:** cada fase detrás de feature-flag con default = comportamiento
actual; migraciones aditivas; tests solo contra la sandbox Neon (`tests/conftest.py`), nunca
contra prod.
