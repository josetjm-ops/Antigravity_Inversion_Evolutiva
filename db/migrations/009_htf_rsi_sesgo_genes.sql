-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 009
-- Versión: 009
-- Fecha: 2026-05-29
-- Descripción: Retro-aplica los genes nuevos de las Fases 1-4
--              a todos los agentes existentes.
--
-- Genes añadidos:
--
--   params_smc.htf_filter_enabled  (int  1|0, default 1)
--     Fase 1 — Filtro de tendencia de temporalidad superior.
--     Bloquea señales que contradicen la dirección EMA50/EMA200 en 1h.
--     Se almacena como 1/0 (no float) para que _mutate_block lo ignore;
--     el gen es una decisión estratégica, no un parámetro continuo.
--
--   params_macro.peso_sesgo_tendencia  (float 0.20–0.65, default 0.40)
--     Fase 3 — Intensidad del sesgo de tendencia HTF cuando no hay
--     eventos de alto impacto en el calendario. Gen mutable gaussianamente.
--
--   params_tecnicos.rsi_zona_muerta  (float 1.0–15.0, default 5.0)
--     Fase 2 — Banda de neutralidad alrededor del RSI 50 en modo momentum.
--     Valores fuera de esta banda generan sesgo débil BUY/SELL.
--     Gen mutable gaussianamente.
--
--   params_tecnicos.rsi_modo  (string "momentum"|"reversion", default "momentum")
--     Fase 2 — Filosofía de señalización RSI. "momentum" usa cruce del
--     nivel 50; "reversion" usa el comportamiento legacy (sobreventa/sobrecompra).
--     No es numérico → _mutate_block lo ignora; solo cambia por crossover.
--
-- Patrón: merge idempotente (`||` solo si la clave no existe).
-- Seguro para re-ejecutar: no sobreescribe valores ya evolucionados.
-- ============================================================

BEGIN;

-- ── 1. params_smc: htf_filter_enabled ──────────────────────────────────────
UPDATE agentes
SET params_smc = params_smc || '{"htf_filter_enabled": 1}'::jsonb
WHERE params_smc IS NOT NULL
  AND NOT (params_smc ? 'htf_filter_enabled');

UPDATE agentes
SET params_smc = '{"htf_filter_enabled": 1}'::jsonb
WHERE params_smc IS NULL;

-- ── 2. params_macro: peso_sesgo_tendencia ──────────────────────────────────
UPDATE agentes
SET params_macro = params_macro || '{"peso_sesgo_tendencia": 0.40}'::jsonb
WHERE params_macro IS NOT NULL
  AND NOT (params_macro ? 'peso_sesgo_tendencia');

-- ── 3. params_tecnicos: rsi_zona_muerta ────────────────────────────────────
UPDATE agentes
SET params_tecnicos = params_tecnicos || '{"rsi_zona_muerta": 5.0}'::jsonb
WHERE params_tecnicos IS NOT NULL
  AND NOT (params_tecnicos ? 'rsi_zona_muerta');

-- ── 4. params_tecnicos: rsi_modo ───────────────────────────────────────────
UPDATE agentes
SET params_tecnicos = params_tecnicos || '{"rsi_modo": "momentum"}'::jsonb
WHERE params_tecnicos IS NOT NULL
  AND NOT (params_tecnicos ? 'rsi_modo');

-- ── Verificación post-migración (informativa) ───────────────────────────────
DO $$
DECLARE
    v_total   integer;
    v_sin_htf integer;
    v_sin_ses integer;
    v_sin_zona integer;
    v_sin_modo integer;
BEGIN
    SELECT COUNT(*) INTO v_total   FROM agentes WHERE estado = 'activo';
    SELECT COUNT(*) INTO v_sin_htf  FROM agentes WHERE estado = 'activo' AND NOT (params_smc    ? 'htf_filter_enabled');
    SELECT COUNT(*) INTO v_sin_ses  FROM agentes WHERE estado = 'activo' AND NOT (params_macro  ? 'peso_sesgo_tendencia');
    SELECT COUNT(*) INTO v_sin_zona FROM agentes WHERE estado = 'activo' AND NOT (params_tecnicos ? 'rsi_zona_muerta');
    SELECT COUNT(*) INTO v_sin_modo FROM agentes WHERE estado = 'activo' AND NOT (params_tecnicos ? 'rsi_modo');

    RAISE NOTICE '=== Migración 009 — verificación ===';
    RAISE NOTICE 'Agentes activos          : %', v_total;
    RAISE NOTICE 'Sin htf_filter_enabled   : % (esperado 0)', v_sin_htf;
    RAISE NOTICE 'Sin peso_sesgo_tendencia : % (esperado 0)', v_sin_ses;
    RAISE NOTICE 'Sin rsi_zona_muerta      : % (esperado 0)', v_sin_zona;
    RAISE NOTICE 'Sin rsi_modo             : % (esperado 0)', v_sin_modo;

    IF v_sin_htf > 0 OR v_sin_ses > 0 OR v_sin_zona > 0 OR v_sin_modo > 0 THEN
        RAISE EXCEPTION 'Migración 009 incompleta: quedan agentes sin los genes nuevos.';
    END IF;
END $$;

COMMIT;
