-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 011
-- Versión: 011
-- Fecha: 2026-06-12
-- Descripción: Sesión 22 — Salidas inteligentes como genes evolutivos.
--
-- Genes añadidos a params_smc:
--
--   be_activation_r  (float 0.3–1.0, default 0.6, mutable)
--     Break-even stop: al ganar este múltiplo de R (R = distancia original
--     del SL), el SL se mueve a entrada ± fricción. Protege la ganancia sin
--     recortar el potencial al alza.
--
--   exit_on_reversal  (int 0|1, default 0, muta por bit-flip 10%)
--     1 = el agente cierra su posición si el pipeline técnico emite una señal
--     OPUESTA con confianza ≥ su propio umbral_confianza_minima Y la posición
--     gana al menos min_profit_for_exit_r × R. La evolución decide si el
--     rasgo aporta edge. Se siembra en 1 para la mitad de los agentes activos
--     (diversidad inicial — sin ella el rasgo no existiría en la población).
--
--   min_profit_for_exit_r  (float 0.2–1.0, default 0.4, mutable)
--     Piso de ganancia (en R) para permitir la salida por señal contraria.
--     Nunca se cierra en pérdida por señal: para eso está el SL.
--
-- Además: clamp de atr_factor al nuevo tope 1.8 (antes 3.0). Genomas con
-- atr_factor > 1.8 producían SL de 60+ pips cuyo TP era inalcanzable
-- intradía — fitness sin señal (ver op #9284 del 2026-06-12).
--
-- Patrón: merge idempotente (`||` solo si la clave no existe).
-- Seguro para re-ejecutar.
-- ============================================================

BEGIN;

-- ── 1. be_activation_r ──────────────────────────────────────────────────────
UPDATE agentes
SET params_smc = params_smc || '{"be_activation_r": 0.6}'::jsonb
WHERE params_smc IS NOT NULL
  AND NOT (params_smc ? 'be_activation_r');

-- ── 2. min_profit_for_exit_r ────────────────────────────────────────────────
UPDATE agentes
SET params_smc = params_smc || '{"min_profit_for_exit_r": 0.4}'::jsonb
WHERE params_smc IS NOT NULL
  AND NOT (params_smc ? 'min_profit_for_exit_r');

-- ── 3. exit_on_reversal: semilla de diversidad 50/50 en activos ─────────────
-- Mitad de los agentes activos (por posición alterna en orden de id) nacen
-- con el rasgo activado; el resto en 0. Los eliminados reciben 0.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (ORDER BY id ASC) AS rn
    FROM agentes
    WHERE estado = 'activo'
)
UPDATE agentes a
SET params_smc = a.params_smc ||
    CASE WHEN r.rn % 2 = 1
         THEN '{"exit_on_reversal": 1}'::jsonb
         ELSE '{"exit_on_reversal": 0}'::jsonb
    END
FROM ranked r
WHERE a.id = r.id
  AND NOT (a.params_smc ? 'exit_on_reversal');

UPDATE agentes
SET params_smc = params_smc || '{"exit_on_reversal": 0}'::jsonb
WHERE params_smc IS NOT NULL
  AND NOT (params_smc ? 'exit_on_reversal');

-- ── 4. Clamp de atr_factor al nuevo tope 1.8 ────────────────────────────────
UPDATE agentes
SET params_smc = params_smc ||
    jsonb_build_object('atr_factor',
        LEAST((params_smc->>'atr_factor')::numeric, 1.8))
WHERE params_smc ? 'atr_factor'
  AND (params_smc->>'atr_factor')::numeric > 1.8;

-- ── Verificación post-migración ─────────────────────────────────────────────
DO $$
DECLARE
    v_total       integer;
    v_sin_be      integer;
    v_sin_exit    integer;
    v_sin_minp    integer;
    v_atr_alto    integer;
    v_exit_on     integer;
BEGIN
    SELECT COUNT(*) INTO v_total    FROM agentes WHERE estado = 'activo';
    SELECT COUNT(*) INTO v_sin_be   FROM agentes WHERE estado = 'activo' AND NOT (params_smc ? 'be_activation_r');
    SELECT COUNT(*) INTO v_sin_exit FROM agentes WHERE estado = 'activo' AND NOT (params_smc ? 'exit_on_reversal');
    SELECT COUNT(*) INTO v_sin_minp FROM agentes WHERE estado = 'activo' AND NOT (params_smc ? 'min_profit_for_exit_r');
    SELECT COUNT(*) INTO v_atr_alto FROM agentes WHERE estado = 'activo' AND (params_smc->>'atr_factor')::numeric > 1.8;
    SELECT COUNT(*) INTO v_exit_on  FROM agentes WHERE estado = 'activo' AND (params_smc->>'exit_on_reversal')::int = 1;

    RAISE NOTICE '=== Migración 011 — verificación ===';
    RAISE NOTICE 'Agentes activos              : %', v_total;
    RAISE NOTICE 'Sin be_activation_r          : % (esperado 0)', v_sin_be;
    RAISE NOTICE 'Sin exit_on_reversal         : % (esperado 0)', v_sin_exit;
    RAISE NOTICE 'Sin min_profit_for_exit_r    : % (esperado 0)', v_sin_minp;
    RAISE NOTICE 'Con atr_factor > 1.8         : % (esperado 0)', v_atr_alto;
    RAISE NOTICE 'Con exit_on_reversal = 1     : % (~mitad de activos)', v_exit_on;

    IF v_sin_be > 0 OR v_sin_exit > 0 OR v_sin_minp > 0 OR v_atr_alto > 0 THEN
        RAISE EXCEPTION 'Migración 011 incompleta.';
    END IF;
END $$;

COMMIT;
