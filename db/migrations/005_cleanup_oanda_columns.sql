-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 005
-- Versión: 005
-- Fecha: 2026-05-18
-- Descripción: Limpieza de columnas OANDA (vestigio histórico).
--
-- Contexto:
--   La migración 002_oanda_integration.sql añadió columnas de
--   ejecución para una integración con el broker OANDA que finalmente
--   fue descartada. El broker definitivo es simulado (Yahoo Finance)
--   y nunca se ejecutaron órdenes reales contra OANDA.
--   Esta migración elimina las columnas para mantener el schema
--   consistente con el código y la documentación.
-- ============================================================

BEGIN;

-- Eliminar índice asociado (idempotente)
DROP INDEX IF EXISTS idx_operaciones_oanda_trade;

-- Eliminar columnas (idempotente)
ALTER TABLE operaciones
    DROP COLUMN IF EXISTS oanda_trade_id,
    DROP COLUMN IF EXISTS oanda_units,
    DROP COLUMN IF EXISTS oanda_realized_pl;

COMMIT;
