-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración Inicial
-- Versión: 001
-- Fecha: 2026-05-03
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- EXTENSIONES
-- ------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ------------------------------------------------------------
-- TABLA 1: agentes
-- Registro central de cada instancia del Agente Inversionista.
-- ID formato YYYY-MM-DD_NN para ordenamiento alfanumérico correcto.
-- ------------------------------------------------------------
CREATE TABLE agentes (
    id                    VARCHAR(20)    PRIMARY KEY,
    fecha_nacimiento      DATE           NOT NULL,
    generacion            INTEGER        NOT NULL DEFAULT 1,
    padre_1_id            VARCHAR(20)    REFERENCES agentes(id) ON DELETE SET NULL,
    padre_2_id            VARCHAR(20)    REFERENCES agentes(id) ON DELETE SET NULL,
    estado                VARCHAR(20)    NOT NULL DEFAULT 'activo'
                              CHECK (estado IN ('activo', 'eliminado', 'retirado')),

    -- Parámetros del Sub-agente A (Técnico): RSI, EMA, MACD y sus pesos
    params_tecnicos       JSONB          NOT NULL DEFAULT '{
        "rsi_periodo": 14,
        "rsi_sobrecompra": 70,
        "rsi_sobreventa": 30,
        "ema_rapida": 9,
        "ema_lenta": 21,
        "macd_rapida": 12,
        "macd_lenta": 26,
        "macd_senal": 9,
        "peso_rsi": 0.35,
        "peso_ema": 0.35,
        "peso_macd": 0.30
    }',

    -- Parámetros del Sub-agente B (Macro): NLP y pesos de noticias
    params_macro          JSONB          NOT NULL DEFAULT '{
        "peso_noticias_alto": 0.60,
        "peso_noticias_medio": 0.25,
        "peso_noticias_bajo": 0.10,
        "umbral_sentimiento_compra": 0.65,
        "umbral_sentimiento_venta": 0.35,
        "ventana_noticias_horas": 4,
        "peso_total_macro": 0.40
    }',

    -- Parámetros del Sub-agente C (Riesgo/Decisión): gestión de capital
    params_riesgo         JSONB          NOT NULL DEFAULT '{
        "stop_loss_pct": 0.02,
        "take_profit_pct": 0.04,
        "max_drawdown_diario_pct": 0.10,
        "capital_por_operacion_pct": 0.50,
        "umbral_confianza_minima": 0.60,
        "peso_tecnico_vs_macro": 0.55
    }',

    -- Métricas de rendimiento
    capital_inicial       DECIMAL(10,4)  NOT NULL DEFAULT 10.00,
    capital_actual        DECIMAL(10,4)  NOT NULL DEFAULT 10.00,
    roi_total             DECIMAL(8,4)   NOT NULL DEFAULT 0.0000,
    operaciones_total     INTEGER        NOT NULL DEFAULT 0,
    operaciones_ganadoras INTEGER        NOT NULL DEFAULT 0,

    -- Datos de ciclo de vida (eliminación / selección natural)
    fecha_eliminacion     DATE,
    razon_eliminacion     TEXT,

    created_at            TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agentes_estado      ON agentes(estado);
CREATE INDEX idx_agentes_generacion  ON agentes(generacion);
CREATE INDEX idx_agentes_roi         ON agentes(roi_total DESC);
CREATE INDEX idx_agentes_nacimiento  ON agentes(fecha_nacimiento);
CREATE INDEX idx_agentes_params_tec  ON agentes USING gin(params_tecnicos);
CREATE INDEX idx_agentes_params_mac  ON agentes USING gin(params_macro);

-- ------------------------------------------------------------
-- TABLA 2: operaciones
-- Log de cada señal BUY/SELL/HOLD emitida por cada agente.
-- Guarda el output de los 3 sub-agentes para trazabilidad.
-- ------------------------------------------------------------
CREATE TABLE operaciones (
    id                   SERIAL         PRIMARY KEY,
    agente_id            VARCHAR(20)    NOT NULL REFERENCES agentes(id),
    timestamp_entrada    TIMESTAMPTZ    NOT NULL,
    timestamp_salida     TIMESTAMPTZ,
    par                  VARCHAR(10)    NOT NULL DEFAULT 'EUR/USD',
    accion               VARCHAR(10)    NOT NULL CHECK (accion IN ('BUY', 'SELL', 'HOLD')),
    precio_entrada       DECIMAL(10,5),
    precio_salida        DECIMAL(10,5),
    capital_usado        DECIMAL(10,4),
    pnl                  DECIMAL(10,4),
    pnl_porcentaje       DECIMAL(8,4),

    -- Señal del Sub-agente A: valores de indicadores y recomendación
    -- Ejemplo: {"rsi": 65.2, "ema_cross": true, "macd_hist": 0.0012,
    --           "recomendacion": "BUY", "confianza": 0.72}
    senal_tecnico        JSONB,

    -- Señal del Sub-agente B: eventos macro y sentimiento NLP
    -- Ejemplo: {"eventos": [...], "sentimiento": 0.68,
    --           "impacto": "alto", "recomendacion": "BUY"}
    senal_macro          JSONB,

    -- Decisión del Sub-agente C: acción final con razonamiento
    -- Ejemplo: {"accion_final": "BUY", "confianza": 0.70,
    --           "razonamiento": "...", "stop_loss": 1.0820, "take_profit": 1.0860}
    decision_riesgo      JSONB,

    estado               VARCHAR(20)    NOT NULL DEFAULT 'abierta'
                             CHECK (estado IN ('abierta', 'cerrada', 'cancelada')),
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_operaciones_agente    ON operaciones(agente_id);
CREATE INDEX idx_operaciones_timestamp ON operaciones(timestamp_entrada DESC);
CREATE INDEX idx_operaciones_accion    ON operaciones(accion);
CREATE INDEX idx_operaciones_estado    ON operaciones(estado);
CREATE INDEX idx_operaciones_pnl       ON operaciones(pnl DESC NULLS LAST);

-- ------------------------------------------------------------
-- TABLA 3: estrategias_exitosas
-- Hall of Fame: parámetros de agentes que superaron el umbral de ROI.
-- Es la fuente de herencia genética para nuevas generaciones.
-- ------------------------------------------------------------
CREATE TABLE estrategias_exitosas (
    id                   SERIAL         PRIMARY KEY,
    agente_origen_id     VARCHAR(20)    NOT NULL REFERENCES agentes(id),
    fecha_registro       DATE           NOT NULL,
    roi_que_genero       DECIMAL(8,4)   NOT NULL,
    win_rate             DECIMAL(5,4),
    params_tecnicos      JSONB          NOT NULL,
    params_macro         JSONB          NOT NULL,
    params_riesgo        JSONB          NOT NULL,
    veces_heredada       INTEGER        NOT NULL DEFAULT 0,
    activa               BOOLEAN        NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_estrategias_roi    ON estrategias_exitosas(roi_que_genero DESC);
CREATE INDEX idx_estrategias_activa ON estrategias_exitosas(activa);
CREATE INDEX idx_estrategias_params ON estrategias_exitosas USING gin(params_tecnicos);

-- ------------------------------------------------------------
-- TABLA 4: ranking_historico
-- Snapshot diario del ranking al cierre de mercado (17:00 UTC-5).
-- Alimenta el heatmap y la gráfica de supervivencia del dashboard.
-- ------------------------------------------------------------
CREATE TABLE ranking_historico (
    id                   SERIAL         PRIMARY KEY,
    fecha                DATE           NOT NULL,
    agente_id            VARCHAR(20)    NOT NULL REFERENCES agentes(id),
    posicion_ranking     INTEGER        NOT NULL,
    roi_diario           DECIMAL(8,4),
    roi_acumulado        DECIMAL(8,4),
    capital_fin_dia      DECIMAL(10,4),
    operaciones_dia      INTEGER        NOT NULL DEFAULT 0,
    evento               VARCHAR(30)    CHECK (evento IN (
                             'supervivencia', 'eliminacion', 'nacimiento', 'evaluacion'
                         )),
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    UNIQUE (fecha, agente_id)
);

CREATE INDEX idx_ranking_fecha    ON ranking_historico(fecha DESC);
CREATE INDEX idx_ranking_agente   ON ranking_historico(agente_id);
CREATE INDEX idx_ranking_posicion ON ranking_historico(posicion_ranking);

-- ------------------------------------------------------------
-- TABLA 5: logs_juez
-- Audit trail del Agente Juez. Guarda el razonamiento de DeepSeek
-- para cada decisión evolutiva (eliminación, mutación, reproducción).
-- ------------------------------------------------------------
CREATE TABLE logs_juez (
    id                   SERIAL         PRIMARY KEY,
    fecha                DATE           NOT NULL,
    tipo_evento          VARCHAR(30)    NOT NULL
                             CHECK (tipo_evento IN (
                                 'evaluacion_diaria',
                                 'eliminacion',
                                 'seleccion_padres',
                                 'reproduccion',
                                 'mutacion',
                                 'nuevo_agente'
                             )),
    agente_afectado_id   VARCHAR(20)    REFERENCES agentes(id),
    descripcion          TEXT           NOT NULL,
    datos_json           JSONB,
    razonamiento_llm     TEXT,
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_logs_juez_fecha  ON logs_juez(fecha DESC);
CREATE INDEX idx_logs_juez_tipo   ON logs_juez(tipo_evento);
CREATE INDEX idx_logs_juez_agente ON logs_juez(agente_afectado_id);

-- ------------------------------------------------------------
-- TRIGGER: updated_at automático en tabla agentes
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agentes_updated_at
    BEFORE UPDATE ON agentes
    FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();

-- ------------------------------------------------------------
-- VISTA: agentes_activos_ranking
-- Consulta principal del dashboard; evita JOINs repetidos.
-- ------------------------------------------------------------
CREATE VIEW agentes_activos_ranking AS
SELECT
    a.id,
    a.generacion,
    a.fecha_nacimiento,
    a.padre_1_id,
    a.padre_2_id,
    a.capital_actual,
    a.roi_total,
    CASE
        WHEN a.operaciones_total > 0
        THEN ROUND(a.operaciones_ganadoras::DECIMAL / a.operaciones_total * 100, 2)
        ELSE 0
    END                                          AS win_rate_pct,
    a.operaciones_total,
    CURRENT_DATE - a.fecha_nacimiento            AS dias_activo,
    a.params_tecnicos,
    a.params_macro,
    a.params_riesgo
FROM agentes a
WHERE a.estado = 'activo'
ORDER BY a.roi_total DESC;

-- ------------------------------------------------------------
-- VISTA: evolucion_por_generacion
-- Para la gráfica de supervivencia del dashboard.
-- ------------------------------------------------------------
CREATE VIEW evolucion_por_generacion AS
SELECT
    generacion,
    COUNT(*) FILTER (WHERE estado = 'activo')    AS agentes_vivos,
    COUNT(*) FILTER (WHERE estado = 'eliminado') AS agentes_eliminados,
    ROUND(AVG(roi_total), 4)                     AS roi_promedio,
    ROUND(MAX(roi_total), 4)                     AS roi_maximo,
    MIN(fecha_nacimiento)                        AS primera_generacion_fecha
FROM agentes
GROUP BY generacion
ORDER BY generacion;

COMMIT;
