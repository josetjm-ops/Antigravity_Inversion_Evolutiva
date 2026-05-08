-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 003
-- Versión: 003
-- Fecha: 2026-05-08
-- Descripción: Genes SMC en agentes + campos analíticos
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Columna params_smc en tabla agentes
--    Default: valores iniciales de los genes SMC
--    Agentes existentes heredan los defaults; los nuevos
--    nacen con genes evolucionados por crossover + mutación.
-- ------------------------------------------------------------
ALTER TABLE agentes
ADD COLUMN IF NOT EXISTS params_smc JSONB NOT NULL DEFAULT '{
    "fvg_min_pips": 5.0,
    "ob_impulse_pips": 10.0,
    "range_spike_multiplier": 1.5,
    "risk_reward_target": 2.0,
    "macro_quarantine_minutes": 60,
    "risk_pct_per_trade": 0.015,
    "peso_fvg": 0.15,
    "peso_ob": 0.15
}';

-- ------------------------------------------------------------
-- 2. Columna pips_sl en tabla operaciones (para Session 5)
--    Registra la distancia SL en pips al momento de abrir.
--    NULL en operaciones anteriores a esta migración.
-- ------------------------------------------------------------
ALTER TABLE operaciones
ADD COLUMN IF NOT EXISTS pips_sl DECIMAL(8,2);

-- ------------------------------------------------------------
-- 3. Columna fitness_score en ranking_historico (para Session 5)
--    Calmar Ratio proxy calculado por el JudgeAgent.
--    NULL en rankings anteriores a esta migración.
-- ------------------------------------------------------------
ALTER TABLE ranking_historico
ADD COLUMN IF NOT EXISTS fitness_score DECIMAL(10,6);

CREATE INDEX IF NOT EXISTS idx_ranking_fitness
ON ranking_historico(fitness_score DESC);

COMMIT;
