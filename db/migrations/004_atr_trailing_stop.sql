-- Migración 004: SL dinámico basado en ATR + Trailing Stop Loss
--
-- Añade dos columnas a operaciones para soportar trailing stop persistente,
-- y añade los tres nuevos genes SMC (atr_factor, trailing_activation_pips,
-- trailing_distance_pips) a todos los agentes que aún no los tengan.

BEGIN;

-- ── Nuevas columnas en operaciones ────────────────────────────────────────────

-- sl_dinamico: SL actual (puede haberse movido por trailing).
--   Al abrir = stop_loss original. Solo se actualiza si trailing lo mejora.
--   decision_riesgo->>'stop_loss' conserva el SL original como audit trail.
ALTER TABLE operaciones
    ADD COLUMN IF NOT EXISTS sl_dinamico              DECIMAL(10,5),
    ADD COLUMN IF NOT EXISTS precio_extremo_favorable DECIMAL(10,5);

-- Retrocompatibilidad: poblar sl_dinamico para operaciones abiertas existentes
UPDATE operaciones
SET sl_dinamico = (decision_riesgo->>'stop_loss')::DECIMAL
WHERE estado = 'abierta'
  AND sl_dinamico IS NULL
  AND decision_riesgo->>'stop_loss' IS NOT NULL;

-- ── Nuevos genes SMC en agentes ───────────────────────────────────────────────

-- Merge idempotente: solo añade las claves que no existen
UPDATE agentes
SET params_smc = params_smc
    || '{"atr_factor": 1.5, "trailing_activation_pips": 15.0, "trailing_distance_pips": 10.0}'::jsonb
WHERE params_smc IS NOT NULL
  AND NOT (params_smc ? 'atr_factor');

-- Para agentes con params_smc NULL (no deberían existir, pero por seguridad)
UPDATE agentes
SET params_smc = '{"atr_factor": 1.5, "trailing_activation_pips": 15.0, "trailing_distance_pips": 10.0}'::jsonb
WHERE params_smc IS NULL;

COMMIT;
