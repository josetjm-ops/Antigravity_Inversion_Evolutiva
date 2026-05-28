-- Migración 008: Verificación intra-vela de SL/TP con OHLC 1-minuto
--
-- Añade la columna `timestamp_ultima_verificacion` a operaciones para
-- soportar el nuevo verificador intra-vela del trade_monitor.
--
-- En cada ciclo de monitoreo (cada 15 min), el monitor descargará las
-- velas de 1 min de Yahoo Finance desde `timestamp_ultima_verificacion`
-- hasta ahora y revisará vela por vela si `high`/`low` tocó SL o TP.
-- Esto elimina el sesgo del check por snapshot, que perdía las mechas
-- intra-bar (ver el caso de la operación #2803 del 2026-05-27).
--
-- Patrón idempotente: IF NOT EXISTS + backfill condicional. Se puede
-- aplicar varias veces sin efectos colaterales.

BEGIN;

-- ── Nueva columna en operaciones ─────────────────────────────────────────────
--
-- timestamp_ultima_verificacion:
--   - Para operaciones abiertas: hasta qué momento (UTC) ya se examinó
--     OHLC para esta operación. Inicialmente = timestamp_entrada;
--     se va avanzando con la última vela procesada en cada ciclo.
--   - Para operaciones cerradas: marca la última vela revisada antes
--     del cierre (útil para auditoría retroactiva).
ALTER TABLE operaciones
    ADD COLUMN IF NOT EXISTS timestamp_ultima_verificacion TIMESTAMPTZ;

-- ── Backfill: operaciones existentes ─────────────────────────────────────────
--
-- Para todas las operaciones (abiertas y cerradas) sin valor, usar
-- timestamp_entrada como punto de partida. Esto garantiza que el primer
-- ciclo posterior a la migración no reexamine velas anteriores al trade.
UPDATE operaciones
SET timestamp_ultima_verificacion = timestamp_entrada
WHERE timestamp_ultima_verificacion IS NULL
  AND timestamp_entrada IS NOT NULL;

COMMIT;
