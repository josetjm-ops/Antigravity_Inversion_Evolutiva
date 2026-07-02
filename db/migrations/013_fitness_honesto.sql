-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 013
-- Versión: 013
-- Fecha: 2026-07-02
-- Descripción: Fase 1 del rediseño de rentabilidad (PLAN_REDISENO_RENTABILIDAD.md,
-- evaluación de arquitectura 2026-07-02) — "fitness honesto":
--
-- 1. Añade estrategias_exitosas.fitness_registro: persiste el fitness_score
--    (expectancy en R ajustada por riesgo) del agente al momento de su
--    inscripción en el Hall of Fame. Antes solo se guardaba roi_que_genero,
--    una suma aritmética de pnl_pct sin relación con el desempeño real
--    (ver hallazgo F2 de la auditoría 2026-07-01 y evolution_engine._real_roi_pct).
--    NULL para registros anteriores a esta migración — _get_hof_parents()
--    usa roi_que_genero como aproximación de respaldo SOLO para esos casos
--    legacy (documentado como limitación, no resoluble retroactivamente).
--
-- 2. Actualiza v_decaimiento_oos (migración 012) para comparar 'realizado'
--    con la MISMA fórmula que ahora usa evolution_engine._fitness_cte():
--    expectancy en R (no en USD) y drawdown sobre la curva de capital con
--    base real (capital_inicial), no una suma sin base. Sin este cambio la
--    vista compararía fitness_oos_prometido (ya en R, desde el backtester
--    actualizado) contra un 'realizado' en la escala vieja (USD) — inválido.
--
-- Aditiva e idempotente: ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE VIEW.
-- No modifica ninguna operación ni agente existente. Reversible: la columna
-- puede quedar sin usar y la vista se puede revertir a la definición previa.
-- ============================================================

BEGIN;

-- ── 1. Columna nueva en estrategias_exitosas ────────────────────────────────
ALTER TABLE estrategias_exitosas
    ADD COLUMN IF NOT EXISTS fitness_registro NUMERIC;

COMMENT ON COLUMN estrategias_exitosas.fitness_registro IS
    'Fitness score (expectancy en R ajustada por riesgo) del agente al '
    'inscribirse en el Hall of Fame. NULL para registros previos a esta '
    'migración — _get_hof_parents() usa roi_que_genero como respaldo solo '
    'para esos casos legacy.';

-- ── 2. Vista de comparación prometido vs realizado (fórmula en R) ──────────
-- Reusa la misma fórmula de fitness real que evolution_engine._fitness_cte()
-- (expectancy en R / (max_drawdown+1) * confianza_estadistica - overtrading
-- continuo). MIN_SAMPLE_TRADES sigue hardcodeado a 15 (default de .env);
-- si se cambia en el futuro, esta vista debe actualizarse a mano (SQL puro,
-- no lee el .env) — mismo patrón que la migración 012.
CREATE OR REPLACE VIEW v_decaimiento_oos AS
WITH capital_series AS (
    SELECT o.agente_id, o.timestamp_entrada,
           a.capital_inicial + SUM(o.pnl) OVER (
               PARTITION BY o.agente_id ORDER BY o.timestamp_entrada
           ) AS capital_acumulado
    FROM operaciones o
    JOIN agentes a ON a.id = o.agente_id
    WHERE o.estado = 'cerrada'
),
drawdown_calc AS (
    SELECT agente_id,
           MAX(capital_acumulado) OVER (
               PARTITION BY agente_id
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS peak,
           capital_acumulado
    FROM capital_series
),
max_dd AS (
    SELECT agente_id,
           MAX((peak - capital_acumulado) / NULLIF(peak, 0)) AS max_drawdown
    FROM drawdown_calc GROUP BY agente_id
),
ops_diarias AS (
    SELECT agente_id, AVG(ops_dia) AS avg_ops_dia
    FROM (
        SELECT agente_id, DATE(timestamp_entrada) AS dia, COUNT(*) AS ops_dia
        FROM operaciones
        WHERE estado IN ('cerrada', 'abierta')
        GROUP BY agente_id, DATE(timestamp_entrada)
    ) sub GROUP BY agente_id
),
ops_stats AS (
    SELECT agente_id,
           COUNT(*)                        AS n_trades,
           COUNT(*) FILTER (WHERE pnl > 0) AS n_wins,
           COALESCE(AVG(
               pnl / NULLIF(capital_usado * pips_sl * 0.0001
                            / NULLIF(precio_entrada, 0), 0)
           ) FILTER (WHERE pnl > 0), 0)       AS avg_win_r,
           COALESCE(AVG(ABS(
               pnl / NULLIF(capital_usado * pips_sl * 0.0001
                            / NULLIF(precio_entrada, 0), 0)
           )) FILTER (WHERE pnl < 0), 0)       AS avg_loss_r
    FROM operaciones
    WHERE estado = 'cerrada'
    GROUP BY agente_id
),
real AS (
    SELECT a.id,
           COALESCE(s.n_trades, 0) AS n_trades,
           (
               CASE WHEN COALESCE(s.n_trades, 0) > 0 THEN
                   (s.n_wins::float / s.n_trades)        * s.avg_win_r
                   - (1.0 - s.n_wins::float / s.n_trades) * s.avg_loss_r
               ELSE 0 END
               / (COALESCE(d.max_drawdown, 0.01) + 1)
               * LEAST(1.0, COALESCE(s.n_trades, 0)::float / 15)
           )
           - LEAST(0.3, GREATEST(0, COALESCE(o.avg_ops_dia, 0) - 3) * 0.03
                   * GREATEST(0, 0.5 - COALESCE(
                       s.n_wins::float / NULLIF(s.n_trades, 0), 0.5)))
             AS fitness_score
    FROM agentes a
    LEFT JOIN max_dd      d ON a.id = d.agente_id
    LEFT JOIN ops_diarias o ON a.id = o.agente_id
    LEFT JOIN ops_stats   s ON a.id = s.agente_id
)
SELECT
    a.id, a.especie, a.generacion, a.fecha_nacimiento,
    a.fitness_oos_prometido               AS prometido,
    a.n_trades_oos_prometido              AS n_trades_prometido,
    r.fitness_score                       AS realizado,
    r.n_trades                            AS n_trades_real,
    ROUND((r.fitness_score - a.fitness_oos_prometido)::numeric, 6) AS decaimiento
FROM agentes a
JOIN real r ON r.id = a.id
WHERE a.fitness_oos_prometido IS NOT NULL
  AND r.n_trades >= 15;

COMMIT;
