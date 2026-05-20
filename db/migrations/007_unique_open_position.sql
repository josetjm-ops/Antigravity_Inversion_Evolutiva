-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 007 (NO-OP)
-- ============================================================
-- Esta migración era para crear un índice parcial único que
-- garantiza una sola posición abierta por agente.
-- Sin embargo, ese índice ya existía desde la Sesión 8 con el
-- nombre `idx_one_open_buysell_per_agent`. Se conserva éste y
-- esta migración se deja como NO-OP documentado para mantener
-- continuidad en el numerado de migraciones.
-- ============================================================
-- Para verificar el índice existente:
--   SELECT indexdef FROM pg_indexes
--   WHERE indexname = 'idx_one_open_buysell_per_agent';
-- ============================================================

-- (Sin operaciones SQL)
SELECT 1;
