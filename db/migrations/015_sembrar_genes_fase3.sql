-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 015
-- Versión: 015
-- Fecha: 2026-07-07
-- Descripción: Sembrado de los genes de Fase 3 (rediseño 2026-07-02) en la
-- población existente. Hallazgo de la auditoría 2026-07-07: los genes
-- `partial_tp_r` y `sesion_trading` se añadieron a _DEFAULT_SMC_PARAMS y a
-- los bounds del motor (PR #20), pero NINGÚN agente vivo los tiene en su
-- params_smc — y el crossover solo hereda claves que los padres ya poseen,
-- así que tampoco aparecían en los hijos:
--
--   - partial_tp_r: la mutación gaussiana (_mutate_block) solo perturba
--     claves EXISTENTES → sin sembrado, el gen jamás entra al pool y la
--     salida parcial + runner (la mejora de payoff central de Fase 3)
--     queda efectivamente inactiva en producción (0 ejecuciones observadas).
--   - sesion_trading: la mutación categórica sí puede introducirlo (sorteo
--     con prob. 10% por crianza aunque la clave falte), pero sembrarlo
--     explícitamente en "cualquiera" documenta el rasgo en el genoma y evita
--     depender del azar para que exista.
--
-- Mismo problema y mismo patrón de solución que exit_on_reversal en la
-- migración 011 (Sesión 22).
--
-- Valores de sembrado:
--   partial_tp_r  : diversidad inicial 0.8 / 1.0 / 1.2 rotando por posición
--                   entre los agentes ACTIVOS (dentro de los bounds 0.5–2.0)
--                   para que la evolución tenga señal comparativa inmediata;
--                   1.0 (default del motor) para los no activos.
--   sesion_trading: "cualquiera" para todos — replica el comportamiento
--                   previo (sin restricción horaria adicional); la mutación
--                   por sorteo explorará londres/ny/overlap.
--
-- Patrón: merge idempotente (`||` solo si la clave no existe).
-- Seguro para re-ejecutar.
-- ============================================================

BEGIN;

-- ── 1. partial_tp_r: semilla con diversidad 0.8/1.0/1.2 en activos ──────────
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (ORDER BY id ASC) AS rn
    FROM agentes
    WHERE estado = 'activo'
)
UPDATE agentes a
SET params_smc = a.params_smc ||
    CASE r.rn % 3
         WHEN 1 THEN '{"partial_tp_r": 0.8}'::jsonb
         WHEN 2 THEN '{"partial_tp_r": 1.0}'::jsonb
         ELSE        '{"partial_tp_r": 1.2}'::jsonb
    END
FROM ranked r
WHERE a.id = r.id
  AND a.params_smc IS NOT NULL
  AND NOT (a.params_smc ? 'partial_tp_r');

-- Resto (eliminados/retirados — pueden ser padres vía Hall of Fame): default 1.0
UPDATE agentes
SET params_smc = params_smc || '{"partial_tp_r": 1.0}'::jsonb
WHERE params_smc IS NOT NULL
  AND NOT (params_smc ? 'partial_tp_r');

-- ── 2. sesion_trading: "cualquiera" para todos (sin restricción, como antes) ─
UPDATE agentes
SET params_smc = params_smc || '{"sesion_trading": "cualquiera"}'::jsonb
WHERE params_smc IS NOT NULL
  AND NOT (params_smc ? 'sesion_trading');

-- ── Verificación post-migración ─────────────────────────────────────────────
DO $$
DECLARE
    v_total      integer;
    v_sin_ptr    integer;
    v_sin_ses    integer;
    v_ptr_oob    integer;
BEGIN
    SELECT COUNT(*) INTO v_total   FROM agentes WHERE estado = 'activo';
    SELECT COUNT(*) INTO v_sin_ptr FROM agentes WHERE estado = 'activo' AND NOT (params_smc ? 'partial_tp_r');
    SELECT COUNT(*) INTO v_sin_ses FROM agentes WHERE estado = 'activo' AND NOT (params_smc ? 'sesion_trading');
    SELECT COUNT(*) INTO v_ptr_oob FROM agentes WHERE estado = 'activo'
        AND ((params_smc->>'partial_tp_r')::numeric < 0.5
             OR (params_smc->>'partial_tp_r')::numeric > 2.0);

    RAISE NOTICE '=== Migración 015 — verificación ===';
    RAISE NOTICE 'Agentes activos           : %', v_total;
    RAISE NOTICE 'Sin partial_tp_r          : % (esperado 0)', v_sin_ptr;
    RAISE NOTICE 'Sin sesion_trading        : % (esperado 0)', v_sin_ses;
    RAISE NOTICE 'partial_tp_r fuera bounds : % (esperado 0)', v_ptr_oob;

    IF v_sin_ptr > 0 OR v_sin_ses > 0 OR v_ptr_oob > 0 THEN
        RAISE EXCEPTION 'Migración 015 incompleta.';
    END IF;
END $$;

COMMIT;
