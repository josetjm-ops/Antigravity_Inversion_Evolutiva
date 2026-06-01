-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 010
-- Versión: 010
-- Fecha: 2026-06-01
-- Descripción: Fase 2 — Diversidad por especies.
--
-- 1. Añade columna `especie` a `agentes` (VARCHAR 20, default 'tendencia').
--    Tres arquetipos decorrelacionados:
--      - 'tendencia' : momentum multi-timeframe (comportamiento previo)
--      - 'reversion' : mean-reversion en extremos RSI / OB / FVG en rango
--      - 'ruptura'   : breakout con expansión de volatilidad
--
-- 2. Distribuye las 10 agentes existentes en 4 / 3 / 3.
--
-- 3. Reconfigura genes propios de cada especie:
--      S2 reversion : rsi_modo=reversion, htf_filter_enabled=0
--      S3 ruptura   : añade breakout_lookback_bars, breakout_min_pips a params_smc
--
-- Idempotente: usa IF NOT EXISTS / solo aplica si la clave aún no existe.
-- ============================================================

BEGIN;

-- ── 1. Columna especie ──────────────────────────────────────────────────────
ALTER TABLE agentes
    ADD COLUMN IF NOT EXISTS especie VARCHAR(20) DEFAULT 'tendencia';

-- ── 2. Asignar especie a los 10 agentes activos existentes ──────────────────
-- Ordenados por fecha_nacimiento ASC, id ASC:
--   posiciones 1-4  → tendencia
--   posiciones 5-7  → reversion
--   posiciones 8-10 → ruptura
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (ORDER BY fecha_nacimiento ASC, id ASC) AS rn
    FROM agentes
    WHERE estado = 'activo'
)
UPDATE agentes a
SET especie = CASE
    WHEN r.rn <= 4 THEN 'tendencia'
    WHEN r.rn <= 7 THEN 'reversion'
    ELSE 'ruptura'
END
FROM ranked r
WHERE a.id = r.id
  AND (a.especie IS NULL OR a.especie = 'tendencia');

-- ── 3a. S2 reversion: rsi_modo=reversion, htf_filter_enabled=0 ─────────────
UPDATE agentes
SET params_tecnicos = params_tecnicos
        || '{"rsi_modo": "reversion"}'::jsonb,
    params_smc = params_smc
        || '{"htf_filter_enabled": 0}'::jsonb
WHERE especie = 'reversion'
  AND estado  = 'activo';

-- ── 3b. S3 ruptura: añadir genes de breakout a params_smc ──────────────────
UPDATE agentes
SET params_smc = params_smc
        || '{"breakout_lookback_bars": 20, "breakout_min_pips": 5.0}'::jsonb
WHERE especie = 'ruptura'
  AND estado  = 'activo'
  AND NOT (params_smc ? 'breakout_lookback_bars');

-- ── 4. Verificación post-migración ─────────────────────────────────────────
DO $$
DECLARE
    v_total      integer;
    v_tendencia  integer;
    v_reversion  integer;
    v_ruptura    integer;
    v_sin_especie integer;
BEGIN
    SELECT COUNT(*) INTO v_total     FROM agentes WHERE estado = 'activo';
    SELECT COUNT(*) INTO v_tendencia FROM agentes WHERE estado = 'activo' AND especie = 'tendencia';
    SELECT COUNT(*) INTO v_reversion FROM agentes WHERE estado = 'activo' AND especie = 'reversion';
    SELECT COUNT(*) INTO v_ruptura   FROM agentes WHERE estado = 'activo' AND especie = 'ruptura';
    SELECT COUNT(*) INTO v_sin_especie FROM agentes WHERE estado = 'activo' AND especie IS NULL;

    RAISE NOTICE '=== Migración 010 — verificación ===';
    RAISE NOTICE 'Agentes activos  : %', v_total;
    RAISE NOTICE '  tendencia      : %', v_tendencia;
    RAISE NOTICE '  reversion      : %', v_reversion;
    RAISE NOTICE '  ruptura        : %', v_ruptura;
    RAISE NOTICE '  sin especie    : % (esperado 0)', v_sin_especie;

    IF v_sin_especie > 0 THEN
        RAISE EXCEPTION 'Migración 010 incompleta: agentes sin especie asignada.';
    END IF;
    IF v_reversion < 2 OR v_ruptura < 2 THEN
        RAISE WARNING 'Menos de 2 agentes en alguna especie — normal si hay < 10 agentes activos.';
    END IF;
END $$;

COMMIT;
