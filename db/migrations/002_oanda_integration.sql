-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 002
-- Integración OANDA: columnas de ejecución en tabla operaciones
-- ============================================================

BEGIN;

ALTER TABLE operaciones
    ADD COLUMN IF NOT EXISTS oanda_trade_id    VARCHAR(50),
    ADD COLUMN IF NOT EXISTS oanda_units       INTEGER,
    ADD COLUMN IF NOT EXISTS oanda_realized_pl DECIMAL(10, 4);

-- Índice para que el monitor localice rápido las ops abiertas con trade OANDA
CREATE INDEX IF NOT EXISTS idx_operaciones_oanda_trade
    ON operaciones (oanda_trade_id)
    WHERE oanda_trade_id IS NOT NULL;

COMMENT ON COLUMN operaciones.oanda_trade_id
    IS 'ID del trade en OANDA. NULL si la acción fue HOLD o la orden falló.';

COMMENT ON COLUMN operaciones.oanda_units
    IS 'Unidades EUR negociadas en OANDA (positivo=BUY, negativo=SELL).';

COMMENT ON COLUMN operaciones.oanda_realized_pl
    IS 'P&L realizado en USD según OANDA al cierre del trade.';

COMMIT;
