-- ============================================================
-- INVERSIÓN EVOLUTIVA — Seed Inicial
-- Genera 10 agentes génesis (Generación 1) con parámetros
-- intencionalmente diversificados para maximizar la exploración
-- del espacio de estrategias desde el primer ciclo evolutivo.
-- ============================================================

BEGIN;

INSERT INTO agentes (
    id, fecha_nacimiento, generacion,
    params_tecnicos, params_macro, params_riesgo
) VALUES

-- Agente 01: Configuración base balanceada
('2026-05-03_01', '2026-05-03', 1,
    '{"rsi_periodo":14,"rsi_sobrecompra":70,"rsi_sobreventa":30,"ema_rapida":9,"ema_lenta":21,"macd_rapida":12,"macd_lenta":26,"macd_senal":9,"peso_rsi":0.35,"peso_ema":0.35,"peso_macd":0.30}',
    '{"peso_noticias_alto":0.60,"peso_noticias_medio":0.25,"peso_noticias_bajo":0.10,"umbral_sentimiento_compra":0.65,"umbral_sentimiento_venta":0.35,"ventana_noticias_horas":4,"peso_total_macro":0.40}',
    '{"stop_loss_pct":0.02,"take_profit_pct":0.04,"max_drawdown_diario_pct":0.10,"capital_por_operacion_pct":0.50,"umbral_confianza_minima":0.60,"peso_tecnico_vs_macro":0.55}'
),

-- Agente 02: Técnico-dominante, RSI más sensible
('2026-05-03_02', '2026-05-03', 1,
    '{"rsi_periodo":12,"rsi_sobrecompra":72,"rsi_sobreventa":28,"ema_rapida":8,"ema_lenta":20,"macd_rapida":10,"macd_lenta":24,"macd_senal":8,"peso_rsi":0.45,"peso_ema":0.30,"peso_macd":0.25}',
    '{"peso_noticias_alto":0.55,"peso_noticias_medio":0.25,"peso_noticias_bajo":0.10,"umbral_sentimiento_compra":0.68,"umbral_sentimiento_venta":0.32,"ventana_noticias_horas":3,"peso_total_macro":0.30}',
    '{"stop_loss_pct":0.015,"take_profit_pct":0.045,"max_drawdown_diario_pct":0.08,"capital_por_operacion_pct":0.40,"umbral_confianza_minima":0.65,"peso_tecnico_vs_macro":0.65}'
),

-- Agente 03: Macro-dominante, ventana de noticias amplia
('2026-05-03_03', '2026-05-03', 1,
    '{"rsi_periodo":16,"rsi_sobrecompra":68,"rsi_sobreventa":32,"ema_rapida":10,"ema_lenta":22,"macd_rapida":13,"macd_lenta":27,"macd_senal":10,"peso_rsi":0.25,"peso_ema":0.35,"peso_macd":0.40}',
    '{"peso_noticias_alto":0.70,"peso_noticias_medio":0.20,"peso_noticias_bajo":0.05,"umbral_sentimiento_compra":0.60,"umbral_sentimiento_venta":0.40,"ventana_noticias_horas":6,"peso_total_macro":0.60}',
    '{"stop_loss_pct":0.025,"take_profit_pct":0.035,"max_drawdown_diario_pct":0.12,"capital_por_operacion_pct":0.55,"umbral_confianza_minima":0.55,"peso_tecnico_vs_macro":0.40}'
),

-- Agente 04: Conservador, alta confianza mínima, riesgo bajo
('2026-05-03_04', '2026-05-03', 1,
    '{"rsi_periodo":14,"rsi_sobrecompra":75,"rsi_sobreventa":25,"ema_rapida":9,"ema_lenta":21,"macd_rapida":12,"macd_lenta":26,"macd_senal":9,"peso_rsi":0.33,"peso_ema":0.34,"peso_macd":0.33}',
    '{"peso_noticias_alto":0.65,"peso_noticias_medio":0.20,"peso_noticias_bajo":0.10,"umbral_sentimiento_compra":0.70,"umbral_sentimiento_venta":0.30,"ventana_noticias_horas":4,"peso_total_macro":0.45}',
    '{"stop_loss_pct":0.01,"take_profit_pct":0.03,"max_drawdown_diario_pct":0.06,"capital_por_operacion_pct":0.30,"umbral_confianza_minima":0.75,"peso_tecnico_vs_macro":0.55}'
),

-- Agente 05: Agresivo, take-profit alto, capital por operación alto
('2026-05-03_05', '2026-05-03', 1,
    '{"rsi_periodo":10,"rsi_sobrecompra":65,"rsi_sobreventa":35,"ema_rapida":7,"ema_lenta":18,"macd_rapida":10,"macd_lenta":22,"macd_senal":7,"peso_rsi":0.40,"peso_ema":0.30,"peso_macd":0.30}',
    '{"peso_noticias_alto":0.50,"peso_noticias_medio":0.30,"peso_noticias_bajo":0.15,"umbral_sentimiento_compra":0.60,"umbral_sentimiento_venta":0.40,"ventana_noticias_horas":2,"peso_total_macro":0.35}',
    '{"stop_loss_pct":0.03,"take_profit_pct":0.06,"max_drawdown_diario_pct":0.15,"capital_por_operacion_pct":0.70,"umbral_confianza_minima":0.55,"peso_tecnico_vs_macro":0.60}'
),

-- Agente 06: EMA de largo plazo, tendencias lentas
('2026-05-03_06', '2026-05-03', 1,
    '{"rsi_periodo":18,"rsi_sobrecompra":72,"rsi_sobreventa":28,"ema_rapida":12,"ema_lenta":26,"macd_rapida":14,"macd_lenta":28,"macd_senal":10,"peso_rsi":0.30,"peso_ema":0.45,"peso_macd":0.25}',
    '{"peso_noticias_alto":0.60,"peso_noticias_medio":0.25,"peso_noticias_bajo":0.10,"umbral_sentimiento_compra":0.65,"umbral_sentimiento_venta":0.35,"ventana_noticias_horas":5,"peso_total_macro":0.42}',
    '{"stop_loss_pct":0.02,"take_profit_pct":0.05,"max_drawdown_diario_pct":0.10,"capital_por_operacion_pct":0.45,"umbral_confianza_minima":0.62,"peso_tecnico_vs_macro":0.52}'
),

-- Agente 07: MACD-dominante, momentum trader
('2026-05-03_07', '2026-05-03', 1,
    '{"rsi_periodo":14,"rsi_sobrecompra":70,"rsi_sobreventa":30,"ema_rapida":9,"ema_lenta":21,"macd_rapida":11,"macd_lenta":25,"macd_senal":8,"peso_rsi":0.20,"peso_ema":0.25,"peso_macd":0.55}',
    '{"peso_noticias_alto":0.58,"peso_noticias_medio":0.28,"peso_noticias_bajo":0.09,"umbral_sentimiento_compra":0.63,"umbral_sentimiento_venta":0.37,"ventana_noticias_horas":4,"peso_total_macro":0.38}',
    '{"stop_loss_pct":0.022,"take_profit_pct":0.044,"max_drawdown_diario_pct":0.11,"capital_por_operacion_pct":0.52,"umbral_confianza_minima":0.58,"peso_tecnico_vs_macro":0.58}'
),

-- Agente 08: Umbral de sentimiento extremo, sólo noticias muy claras
('2026-05-03_08', '2026-05-03', 1,
    '{"rsi_periodo":14,"rsi_sobrecompra":70,"rsi_sobreventa":30,"ema_rapida":9,"ema_lenta":21,"macd_rapida":12,"macd_lenta":26,"macd_senal":9,"peso_rsi":0.35,"peso_ema":0.35,"peso_macd":0.30}',
    '{"peso_noticias_alto":0.80,"peso_noticias_medio":0.15,"peso_noticias_bajo":0.05,"umbral_sentimiento_compra":0.75,"umbral_sentimiento_venta":0.25,"ventana_noticias_horas":3,"peso_total_macro":0.50}',
    '{"stop_loss_pct":0.018,"take_profit_pct":0.042,"max_drawdown_diario_pct":0.09,"capital_por_operacion_pct":0.48,"umbral_confianza_minima":0.68,"peso_tecnico_vs_macro":0.50}'
),

-- Agente 09: Período RSI corto, scalper de alta frecuencia
('2026-05-03_09', '2026-05-03', 1,
    '{"rsi_periodo":7,"rsi_sobrecompra":68,"rsi_sobreventa":32,"ema_rapida":5,"ema_lenta":13,"macd_rapida":8,"macd_lenta":18,"macd_senal":6,"peso_rsi":0.50,"peso_ema":0.25,"peso_macd":0.25}',
    '{"peso_noticias_alto":0.55,"peso_noticias_medio":0.25,"peso_noticias_bajo":0.15,"umbral_sentimiento_compra":0.62,"umbral_sentimiento_venta":0.38,"ventana_noticias_horas":1,"peso_total_macro":0.25}',
    '{"stop_loss_pct":0.01,"take_profit_pct":0.02,"max_drawdown_diario_pct":0.08,"capital_por_operacion_pct":0.60,"umbral_confianza_minima":0.58,"peso_tecnico_vs_macro":0.72}'
),

-- Agente 10: Balanceado con ventana macro media, perfil mixto
('2026-05-03_10', '2026-05-03', 1,
    '{"rsi_periodo":13,"rsi_sobrecompra":71,"rsi_sobreventa":29,"ema_rapida":10,"ema_lenta":23,"macd_rapida":12,"macd_lenta":26,"macd_senal":9,"peso_rsi":0.32,"peso_ema":0.38,"peso_macd":0.30}',
    '{"peso_noticias_alto":0.62,"peso_noticias_medio":0.23,"peso_noticias_bajo":0.10,"umbral_sentimiento_compra":0.64,"umbral_sentimiento_venta":0.36,"ventana_noticias_horas":4,"peso_total_macro":0.43}',
    '{"stop_loss_pct":0.02,"take_profit_pct":0.04,"max_drawdown_diario_pct":0.10,"capital_por_operacion_pct":0.50,"umbral_confianza_minima":0.61,"peso_tecnico_vs_macro":0.53}'
);

COMMIT;
