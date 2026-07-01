-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 012
-- Versión: 012
-- Fecha: 2026-07-01
-- Descripción: Fase 1 del PLAN_DE_MEJORA.md — Instrumentación del
-- decaimiento OOS → producción.
--
-- 1. Añade columnas fitness_oos_prometido / n_trades_oos_prometido a
--    `agentes`, pobladas al nacer el hijo con el fitness OOS del torneo
--    walk-forward (evolution_engine.py). NULL para agentes ya existentes
--    o nacidos por caminos de degradación sin backtest.
--
-- 2. Crea la vista v_decaimiento_oos: compara fitness_oos_prometido
--    (promesa del torneo) contra el fitness real ya en producción, solo
--    para agentes con muestra madura (n_trades >= MIN_SAMPLE_TRADES).
--
-- Aditiva e idempotente: ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE VIEW.
-- No modifica ninguna ruta de decisión existente. Reversible con
-- DROP VIEW v_decaimiento_oos; y las columnas pueden quedar sin usar.
-- ============================================================

BEGIN;

-- ── 1. Columnas nuevas ───────────────────────────────────────────────────────
ALTER TABLE agentes
    ADD COLUMN IF NOT EXISTS fitness_oos_prometido NUMERIC;

ALTER TABLE agentes
    ADD COLUMN IF NOT EXISTS n_trades_oos_prometido INTEGER;

COMMENT ON COLUMN agentes.fitness_oos_prometido IS
    'Fitness OOS del candidato ganador del torneo walk-forward al nacer '
    '(evolution_engine.best_bt["fitness"]). NULL para agentes previos a '
    'esta migración o nacidos por cruce/clon forzado sin backtest.';

COMMENT ON COLUMN agentes.n_trades_oos_prometido IS
    'Número de trades OOS sobre los que se calculó fitness_oos_prometido. '
    'Contexto de confianza estadística de la promesa del torneo.';

-- ── 2. Vista de comparación prometido vs realizado ──────────────────────────
-- Reusa la misma fórmula de fitness real que evolution_engine._get_active_agents_ranked
-- (expectancy neta / (max_drawdown+1) * confianza_estadistica - overtrading).
-- MIN_SAMPLE_TRADES está hardcodeado a 15 (valor por defecto de .env al momento
-- de esta migración); si se cambia MIN_SAMPLE_TRADES en el futuro, esta vista
-- debe actualizarse a mano (no lee el .env, es SQL puro).
CREATE OR REPLACE VIEW v_decaimiento_oos AS
WITH capital_series AS (
    SELECT agente_id, timestamp_entrada,
           SUM(pnl) OVER (PARTITION BY agente_id ORDER BY timestamp_entrada)
               AS capital_acumulado
    FROM operaciones WHERE estado = 'cerrada'
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
ops_stats AS (
    SELECT agente_id,
           COUNT(*)                                          AS n_trades,
           COUNT(*) FILTER (WHERE pnl > 0)                  AS n_wins,
           COALESCE(AVG(pnl)      FILTER (WHERE pnl > 0), 0) AS avg_win,
           COALESCE(AVG(ABS(pnl)) FILTER (WHERE pnl < 0), 0) AS avg_loss
    FROM operaciones
    WHERE estado = 'cerrada'
    GROUP BY agente_id
),
real AS (
    SELECT a.id,
           COALESCE(s.n_trades, 0) AS n_trades,
           (
               CASE WHEN COALESCE(s.n_trades, 0) > 0 THEN
                   (s.n_wins::float / s.n_trades)         * s.avg_win
                   - (1.0 - s.n_wins::float / s.n_trades) * s.avg_loss
               ELSE 0 END
               / (COALESCE(d.max_drawdown, 0.01) + 1)
               * LEAST(1.0, COALESCE(s.n_trades, 0)::float / 15)
           ) AS fitness_score
    FROM agentes a
    LEFT JOIN max_dd    d ON a.id = d.agente_id
    LEFT JOIN ops_stats s ON a.id = s.agente_id
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
