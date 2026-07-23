-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 016
-- Versión: 016
-- Fecha: 2026-07-23
-- Descripción: Fase A del plan de rentabilidad (aprobado 2026-07-23) —
-- ATRIBUCIÓN: saber POR QUÉ termina cada trade y POR QUÉ no se abren más.
--
-- Motivación (auditoría 2026-07-23, primeros datos limpios post-fixes):
-- el sistema pasó a P&L positivo (+$0.18 en 82 ops) PERO con el payoff
-- INVERTIDO: avg_loss ($0.106) ≈ 2× avg_win ($0.057). Ganamos seguido y
-- poco; perdemos rara vez y el doble. La hipótesis es que las salidas
-- parciales / break-even / trailing truncan a los ganadores mientras el SL
-- completo cobra -1R entero — pero HOY NO SE PUEDE PROBAR porque la DB no
-- registra qué mecanismo cerró cada operación.
--
-- 1. operaciones.razon_salida — taxonomía de cierre:
--      TP       : tocó el take profit
--      SL       : tocó el stop loss ORIGINAL (pérdida completa ~-1R)
--      BE       : tocó el stop movido a break-even (entrada ± fricción)
--      TRAILING : tocó el stop movido por el trailing (ganancia parcial)
--      PARCIAL  : fila del 50% vendido en la salida parcial (Fase 3)
--      REV      : cerrada por señal contraria (gen exit_on_reversal)
--      EOD      : cierre forzoso de fin de día del Juez
--      GUARDIA  : cierre de emergencia de la guardia EOD del monitor
--
-- 2. Tabla embudo_decision — por qué NO se abren posiciones. Cada ciclo del
--    monitor registra cuántos agentes candidatos fueron filtrados en cada
--    gate (régimen ADX, sesión, cuarentena macro) y cuántos terminaron en
--    HOLD por confianza insuficiente. Responde con números la pregunta
--    "¿por qué los agentes no son agresivos?".
--
-- 3. Backfill best-effort del histórico: se infiere TP/SL comparando
--    precio_salida contra los niveles registrados en decision_riesgo. Los
--    casos ambiguos quedan en NULL a propósito (mejor un hueco honesto que
--    una etiqueta inventada). Las operaciones nuevas se etiquetan en el
--    momento del cierre, sin inferencia.
--
-- Aditiva e idempotente. No modifica ninguna ruta de decisión.
-- ============================================================

BEGIN;

-- ── 1. Columna de atribución de salida ──────────────────────────────────────
ALTER TABLE operaciones
    ADD COLUMN IF NOT EXISTS razon_salida VARCHAR(12);

COMMENT ON COLUMN operaciones.razon_salida IS
    'Fase A (2026-07-23): mecanismo que cerró la operación — '
    'TP/SL/BE/TRAILING/PARCIAL/REV/EOD/GUARDIA. NULL para operaciones '
    'anteriores a esta migración cuyo cierre no pudo inferirse sin ambigüedad.';

CREATE INDEX IF NOT EXISTS idx_operaciones_razon_salida
    ON operaciones(razon_salida)
    WHERE razon_salida IS NOT NULL;

-- ── 2. Tabla del embudo de decisión ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS embudo_decision (
    id                     SERIAL PRIMARY KEY,
    timestamp_ciclo        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    candidatos             INTEGER NOT NULL DEFAULT 0,  -- agentes libres con capital
    bloqueado_regimen      INTEGER NOT NULL DEFAULT 0,  -- gate ADX vs especie
    bloqueado_sesion       INTEGER NOT NULL DEFAULT 0,  -- gen sesion_trading
    bloqueado_cuarentena   INTEGER NOT NULL DEFAULT 0,  -- evento macro crítico
    hold_por_senal         INTEGER NOT NULL DEFAULT 0,  -- pipeline A→B→C dio HOLD
    abiertos               INTEGER NOT NULL DEFAULT 0,  -- posiciones efectivamente abiertas
    errores                INTEGER NOT NULL DEFAULT 0,
    regimen_estado         VARCHAR(12),                 -- TENDENCIA / RANGO / NEUTRAL del ciclo
    adx                    NUMERIC(6,2)
);

COMMENT ON TABLE embudo_decision IS
    'Fase A (2026-07-23): embudo por ciclo del monitor — cuántos agentes '
    'candidatos se filtran en cada gate antes de abrir posición. Diagnostica '
    'por qué la tasa de operación es baja (~0.8 trades/día/agente).';

CREATE INDEX IF NOT EXISTS idx_embudo_timestamp
    ON embudo_decision(timestamp_ciclo DESC);

-- ── 3. Backfill best-effort del histórico ───────────────────────────────────
-- Tolerancia de 0.5 pips para absorber el redondeo a 5 decimales.
-- Orden importante: TP primero (nivel fijo), luego SL sobre el nivel efectivo.

-- 3a. Take profit: precio_salida ≈ take_profit registrado
UPDATE operaciones
SET razon_salida = 'TP'
WHERE razon_salida IS NULL
  AND estado = 'cerrada'
  AND accion IN ('BUY', 'SELL')
  AND precio_salida IS NOT NULL
  AND decision_riesgo ? 'take_profit'
  AND ABS(precio_salida - (decision_riesgo->>'take_profit')::numeric) <= 0.00005;

-- 3b. Stop loss ORIGINAL: precio_salida ≈ stop_loss de decision_riesgo
UPDATE operaciones
SET razon_salida = 'SL'
WHERE razon_salida IS NULL
  AND estado = 'cerrada'
  AND accion IN ('BUY', 'SELL')
  AND precio_salida IS NOT NULL
  AND decision_riesgo ? 'stop_loss'
  AND ABS(precio_salida - (decision_riesgo->>'stop_loss')::numeric) <= 0.00005;

-- 3c. Break-even: precio_salida ≈ precio_entrada (± fricción de 1.4 pips)
--     y el SL había sido movido (sl_dinamico distinto del original).
UPDATE operaciones
SET razon_salida = 'BE'
WHERE razon_salida IS NULL
  AND estado = 'cerrada'
  AND accion IN ('BUY', 'SELL')
  AND precio_salida IS NOT NULL
  AND sl_dinamico IS NOT NULL
  AND ABS(precio_salida - precio_entrada) <= 0.00021;

-- 3d. Trailing: SL movido, cerró en ganancia, no coincide con TP.
UPDATE operaciones
SET razon_salida = 'TRAILING'
WHERE razon_salida IS NULL
  AND estado = 'cerrada'
  AND accion IN ('BUY', 'SELL')
  AND precio_salida IS NOT NULL
  AND sl_dinamico IS NOT NULL
  AND ABS(precio_salida - sl_dinamico) <= 0.00005
  AND pnl > 0;

-- El resto (EOD, reversal, guardia, y cierres ambiguos) queda en NULL: sin
-- registro del mecanismo no se puede distinguir un EOD de un REV a posteriori.

-- ── Verificación post-migración ─────────────────────────────────────────────
DO $$
DECLARE
    v_col      integer;
    v_tabla    integer;
    v_total    integer;
    v_etiq     integer;
BEGIN
    SELECT COUNT(*) INTO v_col FROM information_schema.columns
        WHERE table_name = 'operaciones' AND column_name = 'razon_salida';
    SELECT COUNT(*) INTO v_tabla FROM information_schema.tables
        WHERE table_name = 'embudo_decision';
    SELECT COUNT(*) INTO v_total FROM operaciones
        WHERE estado = 'cerrada' AND accion IN ('BUY','SELL');
    SELECT COUNT(*) INTO v_etiq FROM operaciones
        WHERE estado = 'cerrada' AND accion IN ('BUY','SELL') AND razon_salida IS NOT NULL;

    RAISE NOTICE '=== Migración 016 — verificación ===';
    RAISE NOTICE 'Columna razon_salida      : % (esperado 1)', v_col;
    RAISE NOTICE 'Tabla embudo_decision     : % (esperado 1)', v_tabla;
    RAISE NOTICE 'Ops cerradas totales      : %', v_total;
    RAISE NOTICE 'Ops con razón inferida    : % (%.1f%% del histórico)',
                 v_etiq, CASE WHEN v_total > 0 THEN v_etiq::float*100/v_total ELSE 0 END;

    IF v_col = 0 OR v_tabla = 0 THEN
        RAISE EXCEPTION 'Migración 016 incompleta.';
    END IF;
END $$;

COMMIT;
