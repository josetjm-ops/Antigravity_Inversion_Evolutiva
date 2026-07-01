# Plan de Mejora — Motor Evolutivo de Inversión Evolutiva

> Análisis original realizado en modo **solo lectura**. Este documento fue posteriormente
> **implementado** (código + tests + migración) contra la **sandbox Neon aislada**
> (`tests/conftest.py`), nunca contra Supabase de producción. Ver "Estado de implementación"
> al final de cada fase para el detalle exacto de qué se hizo y qué falta por decidir/aplicar.

## Objetivo

Que los agentes cierren operaciones en positivo con **mayor consistencia**, atacando la
raíz del problema por el lado de la **selección**: hoy no sabemos si el torneo walk-forward
selecciona edge real o regala pasajes por azar, el umbral de despliegue es estadísticamente
débil, y el fitness OOS se mide sobre un único split sensible al régimen de esas 3 semanas.

## Baseline documentado (referencia)

- Estrategia de referencia (Fase 0): **Calmar Ratio 1.508, win-rate 47.7%** sobre 1 mes.
- Estado real hoy (auditoría 2026-07-01, 788 trades cerrados): win-rate global 39%,
  payoff 1.12, expectancy/op negativa. La selección **no** está filtrando a los perdedores.

## Hallazgos de la investigación (evidencia que fundamenta las fases)

1. **El fitness OOS del hijo ganador NO se persiste por agente.** En
   `evolution_engine.py` el torneo calcula `best_bt["fitness"]` (mejor candidato OOS) pero
   solo lo *loguea*; `agentes` no tiene columna para él. El log `nuevo_agente` de
   `judge_agent.py:313` guarda `params_*`, **no** el fitness OOS. → No es reconstruible a
   posteriori para el camino principal del torneo (sí parcialmente para repoblación:
   `EvolutionResult.slots_recuperados` lleva `fitness_oos` y va en `logs_juez.datos_json`).
   Esto **justifica la Mejora 1**: sin columna dedicada, el decaimiento OOS→prod es ciego.

2. **`run_backtest` ya devuelve `oos_trades`** (lista con el `pnl` de cada trade OOS,
   `backtester.py:327`). → El insumo del bootstrap (Mejora 2) **ya existe**; no requiere
   cambiar el motor de backtest, solo consumir su salida.

3. **Costo real medido del backtest (prototipo solo-lectura, EUR/USD hoy):**
   - `fetch_backtest_data()`: **~9 s** (una vez por ciclo; 5 636 velas 15m + 1 542 velas 1h).
   - `run_backtest()`: **~5.7 s de media** por candidato (min 4.6 / max 9.3), split único de
     `VALIDATE_DAYS=20`. → El backtest es **el costo dominante** del ciclo. Esto condiciona la
     viabilidad de la Mejora 3 (multi-fold multiplica este costo).

4. **Timeout del workflow: `judge_daily.yml` → `timeout-minutes: 25`** (no 12 como se asumió
   en el contexto). El presupuesto real es mayor, pero el job incluye force-close + install +
   ciclo, y la repoblación tiene explosión combinatoria (`REPOPULATION_MAX_ATTEMPTS_PER_SLOT=8`
   × (3 torneo + 3 HoF) por cupo). → Multi-fold es viable pero **exige topes de cómputo**.

5. **Simulación del criterio bootstrap sobre los 15 agentes activos reales** (prototipo
   solo-lectura, 1000 resamples, IC 80%):
   - Criterio **actual** (`fitness>0 & n_trades≥5`): **2/15** pasan.
   - Criterio **bootstrap** (`límite inf IC80 > 0 & n≥5`): **1/15** pasa.
   - Caso revelador: `2026-06-27_01` **pasa el actual** (fitness 0.0105, n=5) pero **falla el
     bootstrap** (IC80 inf = −0.062). Es exactamente el falso positivo que buscamos frenar:
     expectancy apenas positiva sobre 5 trades cuyo intervalo de confianza cruza el cero
     → indistinguible de suerte. **Evidencia directa de que el umbral actual deja pasar ruido.**

---

## Orden de fases (por dependencia y prioridad)

El orden **no** es por dificultad sino por dependencia lógica de medición:

```
Fase 1 (Mejora 1)  →  Fase 2 (Mejora 2)  →  Fase 3 (Mejora 3)
instrumentar          endurecer el            reducir la varianza
el decaimiento        criterio (barato)       del fitness (caro)
```

- **Fase 1 primero** porque es el **instrumento de medición** de las otras dos: sin comparar
  OOS-prometido vs realizado no se puede demostrar que Mejora 2 o 3 mejoran la selección.
  Además es la de menor riesgo (columna aditiva + vista).
- **Fase 2 después**: endurece el gate de despliegue; cómputo despreciable; su efecto se
  **mide** con la instrumentación de la Fase 1.
- **Fase 3 al final**: es la más cara (riesgo de timeout) y la que más conviene decidir
  *después* de que la Fase 1 muestre cuánto decaimiento OOS→prod existe realmente. Si el
  decaimiento es bajo, quizá no valga su costo (se documenta como criterio de go/no-go).

**Garantía de operatividad entre fases:** cada fase entra detrás de un **feature-flag con
default = comportamiento actual** y sus migraciones son **aditivas** (`ADD COLUMN NULL` /
`CREATE VIEW`). Si el trabajo se detiene tras la Fase 1 o 2, las fases siguientes quedan
apagadas y la app corre exactamente como hoy.

---

# FASE 1 — Instrumentación del decaimiento OOS → producción (Mejora 1)

### Descripción
Persistir, al nacer cada agente, el **fitness OOS prometido** por el torneo, y proveer una
**vista** que lo compare contra el **fitness real** una vez el agente alcanza
`MIN_SAMPLE_TRADES`. Es el instrumento que hoy no existe para saber si la selección funciona.

### Justificación (basada en la investigación)
- El motor ya calcula `best_bt["fitness"]` del candidato ganador, pero se pierde (hallazgo 1).
- `agentes` carece de columna para almacenarlo (esquema verificado).
- Sin este par (prometido, realizado) es imposible cuantificar el decaimiento ni validar las
  Fases 2 y 3. Es prerequisito de medición.

### Cambios especificados

**a) Migración de BD** — nueva `db/migrations/012_fitness_oos_prometido.sql` (aditiva):
```sql
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS fitness_oos_prometido NUMERIC;
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS n_trades_oos_prometido INTEGER;
COMMENT ON COLUMN agentes.fitness_oos_prometido IS
  'Fitness OOS del torneo walk-forward al nacer el agente (best_bt.fitness). NULL para agentes previos a la migración.';
```
Idempotente (`IF NOT EXISTS`), sin backfill destructivo. Agentes anteriores quedan `NULL`
(la vista los excluye del cálculo de decaimiento). Correr manualmente en prod y sandbox.

**b) Poblado al insertar el hijo** — `evolution/evolution_engine.py` + `_insert_new_agent`:
adjuntar `fitness_oos` y `n_trades_oos` al dict `child` en los puntos donde ya se conoce
`best_bt` (torneo principal ~línea 1837; HoF fallback; repoblación `slots_rec_log` ya lo
tiene). `_insert_new_agent` escribe las dos columnas nuevas. Los caminos de degradación sin
backtest (`forzado_cruce`, `forzado_clon_unico`) escriben `NULL`.

**c) Vista de comparación** — incluida en la migración 012:
```sql
CREATE OR REPLACE VIEW v_decaimiento_oos AS
WITH real AS ( /* reusa el CTE de fitness real de _get_active_agents_ranked */ )
SELECT a.id, a.especie, a.generacion, a.fecha_nacimiento,
       a.fitness_oos_prometido            AS prometido,
       r.fitness_score                    AS realizado,
       r.n_trades,
       (r.fitness_score - a.fitness_oos_prometido) AS decaimiento
FROM agentes a JOIN real r ON r.id = a.id
WHERE a.fitness_oos_prometido IS NOT NULL
  AND r.n_trades >= 15;   -- solo agentes con muestra madura
```
Consulta de auditoría: `AVG(decaimiento)`, correlación prometido↔realizado, % de agentes
cuyo realizado cae por debajo de 0 pese a prometido > 0 (tasa de falsos positivos del torneo).

### Archivos a tocar (en implementación futura)
- `db/migrations/012_fitness_oos_prometido.sql` (nuevo)
- `evolution/evolution_engine.py` (adjuntar métricas OOS al `child`; ~5 puntos de cría)
- `agents/judge_agent.py` (opcional: incluir `fitness_oos_prometido` en el log `nuevo_agente`)
- Dashboard `mobile-app` (opcional: columna "decaimiento" en la pestaña Agentes/Árbol)

### Riesgo estimado — **BAJO**
Columna aditiva `NULL`-able + vista de lectura. No cambia ninguna ruta de decisión ni de
trading. El único punto sensible es threading de `best_bt` hasta `_insert_new_agent`; se
cubre con un test unitario que verifique que un hijo del torneo persiste `fitness_oos_prometido`.

### Feature-flag / operatividad
No requiere flag: es puramente aditivo. Con la migración aplicada y el poblado activo, el
comportamiento evolutivo es idéntico; solo se **registra** un dato extra.

### Esfuerzo estimado
~0.5 día (migración + threading + 2 tests + vista). Es la base de todo lo demás.

### Estado de implementación — ✅ CÓDIGO Y TESTS LISTOS, ⚠️ MIGRACIÓN PENDIENTE EN PROD
- `db/migrations/012_fitness_oos_prometido.sql` creado (columnas + vista `v_decaimiento_oos`).
- `evolution/evolution_engine.py`: `breed_agent()` inicializa ambos campos en `None`;
  los 3 puntos de cría (torneo principal, camino sin backtest, repoblación) adjuntan
  la promesa OOS real; `_insert_new_agent()` persiste ambas columnas.
- Tests nuevos: `tests/test_fitness_oos_prometido.py` (5/5 verdes).
- **Migración 012 aplicada en la SANDBOX Neon** (para poder correr los tests) — **NO en
  Supabase producción**. Acción pendiente del usuario: correr `012_fitness_oos_prometido.sql`
  manualmente en Supabase (patrón establecido: GH Actions no aplica migraciones).
- **Orden de deploy obligatorio**: la migración debe aplicarse en prod **antes** de desplegar
  este código — el `INSERT` de `_insert_new_agent` ya referencia las columnas nuevas sin
  condicional; si el código se despliega sin la migración, **toda inserción de agente nuevo
  fallaría** (rompería el ciclo evolutivo completo, no solo la instrumentación).

---

# FASE 2 — Validación estadística real en el torneo (Mejora 2)

### Descripción
Reemplazar el umbral de despliegue débil (`fitness OOS > 0` con `n_trades ≥ 5`) por un
**bootstrap** sobre los trades OOS del candidato: resample con reemplazo (~1000 iteraciones),
y exigir que el **límite inferior del intervalo de confianza al 80%** de la expectancy sea
**> 0**. Así solo se despliegan candidatos cuyo edge es estadísticamente distinto de azar.

### Justificación (basada en la investigación)
- `run_backtest` ya expone `oos_trades` con el `pnl` por trade (hallazgo 2): insumo directo.
- La simulación sobre los 15 agentes reales (hallazgo 5) muestra que el criterio actual deja
  pasar `2026-06-27_01` (fitness 0.0105 sobre 5 trades) cuyo IC80 inferior es **−0.062**:
  con tan pocos trades una expectancy ligeramente positiva es indistinguible de suerte. El
  bootstrap lo frena. Esta es la causa mecánica de que sobrevivan agentes que luego pierden.
- Costo despreciable: 1000 resamples de una lista de 5–50 floats es <10 ms; irrelevante frente
  a los ~5.7 s del backtest que ya se ejecuta.

### Cambios especificados

**a) Función de gate bootstrap** (nuevo helper en `evolution/backtester.py` o módulo aparte):
```python
def bootstrap_edge_ok(oos_trades, iters=1000, ci=0.80, min_trades=8, seed=None):
    pnls = [t["pnl"] for t in oos_trades]
    if len(pnls) < min_trades:
        return False, None
    rng = random.Random(seed)
    n = len(pnls)
    exps = sorted(sum(pnls[rng.randrange(n)] for _ in range(n)) / n
                  for _ in range(iters))
    lower = exps[int((1 - ci) / 2 * iters)]   # percentil 10 para IC 80%
    return lower > 0, lower
```

**b) Integración en la cascada existente** (sin romperla). El punto de decisión actual es:
```python
passes = best_bt["fitness"] > TOURNAMENT_MIN_OOS_FITNESS and best_bt["n_trades"] >= TOURNAMENT_MIN_OOS_TRADES
```
Se sustituye por `passes, lb = bootstrap_edge_ok(best_bt["oos_trades"])`, en los **tres**
puntos donde hoy vive ese umbral:
- Torneo principal (`evolution_engine.py:1777`),
- Fallback Hall of Fame (`~1823`),
- Repoblación (`_try_repopulate._passes_oos`, `~1293`).

**Clave para no romper la operatividad / garantía de 15 agentes:** el bootstrap solo endurece
el umbral **estricto**; la cascada de degradación ya existente permanece intacta
(`mejor_candidato_oos` → `forzado_cruce` → `forzado_clon_unico`). Si ningún candidato pasa el
bootstrap, se despliega igual el **mejor por límite inferior IC** (no el de mayor fitness
puntual), preservando la población objetivo. Es decir: el sistema nunca queda sin llenar un
cupo; solo elige mejor entre los que hay.

**c) Parámetros como configuración** (`.env`, con defaults = comportamiento nuevo apagado):
```
TOURNAMENT_GATE_MODE=legacy   # legacy | bootstrap
BOOTSTRAP_ITERS=1000
BOOTSTRAP_CI=0.80
BOOTSTRAP_MIN_TRADES=8
```
`BOOTSTRAP_MIN_TRADES=8` (no 5): la simulación muestra que con n=5 el IC80 casi siempre cruza
cero; subir el piso a 8 evita que el gate rechace todo y fuerce degradaciones constantes.

### Simulación con datos históricos — resultado y **limitación documentada**
- **Hecho:** se simuló el criterio sobre los 15 agentes activos actuales (hallazgo 5). El
  bootstrap reduce los pasajes de 2/15 → 1/15 y **captura un falso positivo real**.
- **Limitación (honesta):** **no es posible** reconstruir con exactitud los ciclos evolutivos
  *pasados* desde `logs_juez`, porque (i) `logs_juez` no guarda los `oos_trades` de los
  candidatos, solo los `params` del ganador, y (ii) `fetch_ohlcv` de Yahoo solo entrega los
  ~60 días finales, así que no se puede recrear la ventana OOS que existía en la fecha de
  nacimiento de cada agente histórico. La simulación *prospectiva* sobre los agentes vivos es
  el proxy viable y ya evidencia el impacto. **No se descarta la mejora**: es viable y de alto
  valor; solo su *validación retroactiva exacta* es infeasible por falta de datos persistidos.
- Recomendación derivada: la Fase 1 (persistir `fitness_oos_prometido` + idealmente los
  `oos_trades` en un JSONB) habilita la validación retroactiva **hacia adelante**.

### Archivos a tocar (en implementación futura)
- `evolution/backtester.py` (helper `bootstrap_edge_ok`) o nuevo `evolution/stats_gate.py`
- `evolution/evolution_engine.py` (3 puntos de umbral)
- `tests/test_bootstrap_gate.py` (nuevo)

### Riesgo estimado — **MEDIO**
No hay riesgo de cómputo. El riesgo es **de comportamiento**: un gate más estricto empuja más
cupos a las ramas de degradación (más `forzado_cruce`). Mitigación: (i) flag `legacy` por
defecto; (ii) `BOOTSTRAP_MIN_TRADES=8`; (iii) medir con la vista de la Fase 1 antes de flipear;
(iv) la cascada ya garantiza que la población de 15 nunca queda incompleta.

### Feature-flag / operatividad
`TOURNAMENT_GATE_MODE=legacy` reproduce exactamente el umbral actual. Se flipea a `bootstrap`
tras un ciclo en shadow. Rollback = volver a `legacy`.

### Esfuerzo estimado
~1 día (helper + 3 integraciones + tests + validación en sandbox).

### Estado de implementación — ✅ CÓDIGO Y TESTS LISTOS, ⚠️ FLAG EN `legacy` (apagado)
- `bootstrap_edge_ok()` implementado en `evolution/backtester.py` (config `TOURNAMENT_GATE_MODE`,
  `BOOTSTRAP_ITERS`, `BOOTSTRAP_CI`, `BOOTSTRAP_MIN_TRADES`).
- Nuevo helper único `_passes_oos_gate(bt)` en `evolution_engine.py` que centraliza el switch
  legacy/bootstrap; sustituye la lógica duplicada en los **3** puntos previstos (torneo
  principal, fallback Hall of Fame, `_try_repopulate._passes_oos`) sin duplicar código.
- Tests nuevos: `tests/test_bootstrap_gate.py` (5/5 verdes) — incluye el caso documentado
  `2026-06-27_01` (fitness=0.0105, n=5): pasa en `legacy`, rechazado en `bootstrap`.
- **`TOURNAMENT_GATE_MODE` no está seteado en `.env`** → por defecto usa `legacy` (código
  fuente `os.getenv(..., "legacy")`). El comportamiento en producción **no cambia** hasta que
  el usuario decida setear `TOURNAMENT_GATE_MODE=bootstrap` en el `.env`/Secrets, tras observar
  con la vista de la Fase 1 cuánto decaimiento hay realmente.

---

# FASE 3 — Walk-forward multi-fold (Mejora 3)

### Descripción
Reemplazar el split único (`TRAIN=40 / VALIDATE=20`) por **3 folds deslizantes**
(30d train / 10d validate, avanzando 10d entre folds, con **purge gap de 1 día** entre train y
validate) y **agregar** el fitness entre folds con una media **penalizada por varianza**. Un
candidato robusto debe rendir en varios regímenes, no solo en las 3 semanas del split fijo.

### Justificación (basada en la investigación)
- El OOS actual son los últimos 20 días: si ese tramo fue tendencial, premia genomas de
  tendencia aunque fallen en rango (y viceversa). El multi-fold reduce esa varianza de régimen.
- Es la mejora de **mayor costo** (hallazgo 3): el backtest es el cuello de botella (~5.7 s).
  Por eso va al final y con topes explícitos.

### Cambios especificados

**a) Diseño de folds** (nueva lógica en `evolution/backtester.py`, detrás de flag):
```
Dataset 15m: 60 días disponibles.
Fold k (k=0,1,2), avanzando 10 días:
  train_k    = [inicio+10k , inicio+10k+30)      # warmup de indicadores
  purge      = 1 día descartado (evita fuga train→validate)
  validate_k = [inicio+10k+31 , inicio+10k+41)   # 10 días OOS
```
Ventana total usada ≈ 51 días (cabe en los 60d que ya se descargan; `fetch_backtest_data`
**no cambia**).

**b) Agregación penalizada por varianza:**
```
fitness_multifold = mean(fitness_folds) - LAMBDA * stdev(fitness_folds)
```
con `LAMBDA=0.5` configurable. Penaliza candidatos inestables entre regímenes. `n_trades`
agregado = suma de trades de los 3 folds (mejora la base muestral, y **sinergiza con el
bootstrap de la Fase 2**: 3 folds ⇒ más trades OOS ⇒ IC más informativo).

**c) Costo computacional (medido empíricamente, solo lectura):**
- Backtest actual (1 fold, validate 20d) = **~5.7 s** (media de 5 corridas).
- Fold de validate 10d **medido = ~3.19 s**; 3 folds ⇒ **~9.6 s por candidato** = **~1.68×** el
  costo actual (algo más que proporcional por overhead fijo por fold: HTF, warmup del slice).
- Proyección por ciclo: `N_CANDIDATE_CHILDREN=3` × cupos. Con la repoblación
  (`REPOPULATION_MAX_ATTEMPTS_PER_SLOT=8` × (3+3) candidatos/cupo) el peor caso ya es de
  decenas–cientos de backtests hoy. A 1.68× puede acercarse al **timeout de 25 min**.
- **Topes obligatorios** para caber en 25 min:
  - Reducir `REPOPULATION_MAX_ATTEMPTS_PER_SLOT` (p.ej. 8→4) cuando el flag multi-fold esté on.
  - Cachear el resultado multi-fold por genoma dentro del ciclo (ya existe `parent_bt_cache`).
  - Presupuesto de tiempo global: abortar rondas extra de repoblación si se superan ~18 min y
    caer a la degradación (`forzado_cruce`), preservando la población.

### Archivos a tocar (en implementación futura)
- `evolution/backtester.py` (folds + agregación; `run_backtest` gana un modo multi-fold)
- `evolution/evolution_engine.py` (consumir el fitness agregado; topes de repoblación)
- `.github/workflows/judge_daily.yml` (revisar `timeout-minutes` si hiciera falta margen)
- `tests/test_multifold.py` (nuevo)

### Riesgo estimado — **ALTO** (por cómputo, no por corrección)
El riesgo es agotar el **timeout de 25 min** en ciclos con mucha repoblación, dejando el ciclo
a medias. Mitigación: flag off por defecto, topes de intentos, caché, presupuesto de tiempo con
degradación segura. Riesgo de corrección bajo (la lógica de trade por fold es la ya probada).

### Feature-flag / operatividad
```
BACKTEST_MODE=single   # single | multifold
MULTIFOLD_N_FOLDS=3
MULTIFOLD_TRAIN_DAYS=30
MULTIFOLD_VALIDATE_DAYS=10
MULTIFOLD_PURGE_DAYS=1
MULTIFOLD_LAMBDA=0.5
```
`BACKTEST_MODE=single` = comportamiento actual exacto. Se flipea a `multifold` solo tras medir,
con la instrumentación de la Fase 1, que reduce el decaimiento OOS→prod lo suficiente para
justificar el costo.

### Criterio go/no-go
Implementar Fase 3 **solo si** la vista de la Fase 1 muestra decaimiento OOS→prod material
(p.ej. correlación prometido↔realizado baja, o tasa alta de falsos positivos del torneo). Si el
decaimiento ya es bajo tras la Fase 2, el costo de la Fase 3 puede no compensar; se documentaría
como "no priorizada" en vez de descartada.

### Esfuerzo estimado
~2 días (folds + agregación + topes de cómputo + tests + medición de tiempos en CI).

### Estado de implementación — ✅ CÓDIGO Y TESTS LISTOS, ⚠️ FLAG EN `single` (apagado por diseño)
- `evolution/backtester.py` refactorizado: el núcleo walk-forward se extrajo a
  `_walk_forward_trades()` (reutilizado por ambos modos, sin duplicar la lógica de trading);
  `_run_backtest_single()` reproduce el comportamiento exacto de antes; `_run_backtest_multifold()`
  implementa los 3 folds (30d train / 10d validate / 1d purge / 10d step) con `_compute_fold_bounds()`
  y `_lookup_htf_at()` (HTF por timestamp del fold, sin fuga hacia adelante entre folds — folds
  antiguos NO ven la tendencia HTF "actual"); agregación `mean(fitness_folds) - LAMBDA*stdev(fitness_folds)`.
  `run_backtest()` es ahora un dispatcher según `BACKTEST_MODE`.
- **Bug encontrado y corregido durante la extracción**: el cierre EOD original usaba
  `df_15m["close"].iloc[-1]` (última vela del **dataset completo**). En modo single esto era
  correcto por coincidencia (`n_end == n_total`), pero habría sido una fuga hacia adelante grave
  en multi-fold (folds tempranos cerrando posiciones al precio del *final del dataset*, muchos
  días en el futuro del fold). Corregido a `df_15m["close"].iloc[n_end - 1]` (borde del fold);
  cubierto por test de regresión dedicado.
- Presupuesto de tiempo de repoblación (`REPOPULATION_TIME_BUDGET_SECONDS`, default 900s) añadido
  en `evolution_engine._try_repopulate`: **solo se activa si `BACKTEST_MODE=multifold`** — en
  modo `single` (default) el guard no se evalúa nunca, cero riesgo sobre el comportamiento legacy.
  Si el presupuesto se agota, los cupos restantes saltan directo a la cascada de degradación
  (`forzado_cruce`) sin gastar backtests en rondas de torneo/HoF — la garantía de llenar los 15
  cupos se mantiene intacta.
- Tests nuevos: `tests/test_multifold.py` (8/8) + `tests/test_repopulation_time_budget.py` (2/2).
- Costo multi-fold **medido con el código real** (no solo estimado): ~11.7s/candidato en una
  corrida de humo (3 folds), consistente con la banda ~9.6-11.7s proyectada en el hallazgo 3.
- **`BACKTEST_MODE` no está seteado en `.env`** → por defecto `single`, comportamiento 100%
  legacy. **Recomendación explícita, sin cambios**: el criterio go/no-go original de este plan
  sigue vigente — activar `multifold` solo después de que la Fase 1 (con datos reales
  acumulados en producción) muestre decaimiento OOS→prod material. Hoy no hay agentes con
  `fitness_oos_prometido` poblado en producción todavía, así que ese dato aún no existe.

---

## Resumen ejecutivo

| Fase | Mejora | Riesgo | Cómputo | Prerequisito | Valor | Estado |
|---|---|---|---|---|---|---|
| **1** | Instrumentar decaimiento OOS→prod | Bajo | Nulo | — | Habilita medir todo lo demás | Código+tests listos · migración solo en sandbox |
| **2** | Bootstrap en el torneo | Medio | Despreciable | Fase 1 (para medir efecto) | Frena falsos positivos (evidenciado 1/15 vs 2/15) | Código+tests listos · flag en `legacy` |
| **3** | Walk-forward multi-fold | Alto (timeout) | ~1.68× backtest | Fase 1+2 (go/no-go) | Robustez entre regímenes | Código+tests listos · flag en `single` |

**Puntos de parada seguros:** cada fase deja la app 100% operativa (flag default = actual,
migración aditiva). Parar tras Fase 1 = solo se registra un dato extra. Parar tras Fase 2 =
selección más estricta, con degradación que garantiza los 15 agentes. La Fase 3 es opcional y
condicionada a que la Fase 1 demuestre que su costo se justifica.

**Sinergia:** las tres se refuerzan — Fase 1 mide, Fase 2 endurece el gate, Fase 3 le da a ese
gate más trades OOS (3 folds) para un intervalo de confianza más informativo.

## Estado global de implementación (2026-07-01)

Las 3 fases están **implementadas y probadas contra la sandbox Neon aislada**
(`tests/conftest.py`), nunca contra Supabase de producción. Todos los cambios de
comportamiento están detrás de flags cuyo **default reproduce exactamente el
comportamiento legacy** — desplegar este código a producción, tal cual, **no cambia
nada** hasta que se tomen las siguientes acciones explícitas (ninguna se ejecutó):

1. **Aplicar `db/migrations/012_fitness_oos_prometido.sql` en Supabase producción**
   (manual, patrón establecido — GH Actions no aplica migraciones). **Obligatorio antes
   de desplegar el código de Fase 1**: el `INSERT` de `_insert_new_agent` ya referencia
   las columnas nuevas sin condicional.
2. **(Opcional, cuando se decida)** `TOURNAMENT_GATE_MODE=bootstrap` en `.env`/Secrets de
   producción para activar la Fase 2.
3. **(Opcional, condicionado a criterio go/no-go)** `BACKTEST_MODE=multifold` para activar
   la Fase 3 — solo tras revisar `v_decaimiento_oos` con datos reales de producción.

Suite de tests: **99/99 verdes** (89 previos a Fase 3 + 8 de multi-fold + 2 del presupuesto
de tiempo de repoblación), todos corridos contra la sandbox.
