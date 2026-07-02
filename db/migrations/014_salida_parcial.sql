-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 014
-- Versión: 014
-- Fecha: 2026-07-02
-- Descripción: Fase 3 del rediseño de rentabilidad (PLAN_REDISENO_RENTABILIDAD.md,
-- evaluación de arquitectura 2026-07-02) — salida parcial + runner.
--
-- Ataca la firma "avg_win≈avg_loss pese a R:R objetivo 2.0" de la auditoría
-- 2026-07-01: el sistema cortaba ganadores (BE/trailing/EOD) antes de que
-- corrieran. Al alcanzar partial_tp_r × R se cierra el 50% de la posición
-- (booking de ganancia real) y el resto sigue corriendo hacia el TP/trailing
-- normal con el SL ya en break-even.
--
-- 1. operaciones.parcial_ejecutada: evita re-disparar el cierre parcial en
--    cada ciclo de 15 min una vez ya ejecutado sobre esa posición.
-- 2. operaciones.capital_usado_original: capital_usado ANTES del cierre
--    parcial — permite reconstruir el tamaño original de la posición para
--    auditoría; NULL si la posición nunca tuvo cierre parcial.
--
-- Aditiva e idempotente: ADD COLUMN IF NOT EXISTS. No modifica ninguna
-- posición existente (default FALSE / NULL). Reversible: las columnas
-- pueden quedar sin usar.
-- ============================================================

BEGIN;

ALTER TABLE operaciones
    ADD COLUMN IF NOT EXISTS parcial_ejecutada BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE operaciones
    ADD COLUMN IF NOT EXISTS capital_usado_original NUMERIC;

COMMENT ON COLUMN operaciones.parcial_ejecutada IS
    'Fase 3: true si ya se ejecutó el cierre parcial a partial_tp_r × R sobre '
    'esta posición — evita re-disparar el cierre parcial en ciclos siguientes.';

COMMENT ON COLUMN operaciones.capital_usado_original IS
    'Fase 3: capital_usado ANTES del cierre parcial (tamaño original de la '
    'posición). NULL si la posición nunca tuvo cierre parcial.';

COMMIT;
