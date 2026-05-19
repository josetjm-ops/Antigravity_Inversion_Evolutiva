-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 006
-- Versión: 006
-- Fecha: 2026-05-18
-- Descripción: Retro-aplica `atr_period: 14` a todos los agentes
--              que aún no lo tienen en su params_smc.
--
-- Contexto:
--   La doc lista `atr_period` (7–21) como gen SMC mutable, y
--   `_DEFAULT_SMC_PARAMS` en evolution_engine.py lo incluye con
--   default 14. Sin embargo, ni la migración 003 ni la 004 lo
--   inyectaron en agentes existentes, por lo que los agentes
--   semilla (Generación 1) y aquellos creados antes del cambio
--   no tienen este gen en su genoma persistido.
--
--   Esta migración garantiza que TODOS los agentes (incluidos los
--   pioneros de Gen 1) tengan `atr_period` registrado, para que
--   el dashboard y las consultas SQL puedan leerlo de forma
--   consistente. La mutación genética posterior sigue su curso
--   normal sobre esta nueva línea base.
-- ============================================================

BEGIN;

-- Merge idempotente: solo añade la clave si NO existe
UPDATE agentes
SET params_smc = params_smc || '{"atr_period": 14}'::jsonb
WHERE params_smc IS NOT NULL
  AND NOT (params_smc ? 'atr_period');

-- Para registros con params_smc nulo (edge case, no deberían existir)
UPDATE agentes
SET params_smc = '{"atr_period": 14}'::jsonb
WHERE params_smc IS NULL;

COMMIT;
