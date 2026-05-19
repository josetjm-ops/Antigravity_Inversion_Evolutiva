-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 002 (DEPRECADA)
-- ============================================================
--
-- ⚠️  Esta migración ha sido DEPRECADA y reemplazada por
--     005_cleanup_oanda_columns.sql.
--
-- Originalmente añadía columnas para integración con OANDA, pero
-- el aplicativo nunca usó esa integración: el broker es simulado
-- (Yahoo Finance) y todas las operaciones son virtuales.
--
-- Si una base de datos existente ya tiene las columnas creadas
-- por esta migración, la migración 005 las elimina de forma
-- idempotente.
--
-- Este archivo se mantiene solo como marcador de número de
-- migración para no romper el conteo cronológico.
-- ============================================================

BEGIN;
-- No-op: ver 005_cleanup_oanda_columns.sql para la limpieza efectiva.
SELECT 1;
COMMIT;
