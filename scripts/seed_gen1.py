"""
Seed inicial: crea 10 agentes Gen 1 con parámetros por defecto y $10 cada uno.
Ejecutar una sola vez al migrar a nueva BD.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from db.connection import get_conn

HOY = date(2026, 5, 19)
CAPITAL = 10.00

DEFAULT_TECNICOS = {
    "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
    "ema_rapida": 9, "ema_lenta": 21,
    "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
    "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
}
DEFAULT_MACRO = {
    "peso_noticias_alto": 0.60, "peso_noticias_medio": 0.25,
    "peso_noticias_bajo": 0.10, "umbral_sentimiento_compra": 0.65,
    "umbral_sentimiento_venta": 0.35, "ventana_noticias_horas": 4,
    "peso_total_macro": 0.40,
}
DEFAULT_RIESGO = {
    "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
    "max_drawdown_diario_pct": 0.10, "capital_por_operacion_pct": 0.50,
    "umbral_confianza_minima": 0.60, "peso_tecnico_vs_macro": 0.55,
}
DEFAULT_SMC = {
    "fvg_min_pips": 5.0, "ob_impulse_pips": 10.0,
    "range_spike_multiplier": 1.5, "risk_reward_target": 2.0,
    "macro_quarantine_minutes": 60, "risk_pct_per_trade": 0.015,
    "peso_fvg": 0.15, "peso_ob": 0.15, "atr_period": 14,
    "atr_factor": 1.5, "trailing_activation_pips": 15.0,
    "trailing_distance_pips": 10.0,
}

agentes = [
    {"id": f"2026-05-19_{i:02d}", "generacion": 1}
    for i in range(1, 11)
]

with get_conn() as conn:
    cur = conn.cursor()
    for a in agentes:
        cur.execute(
            """
            INSERT INTO agentes (
                id, fecha_nacimiento, generacion,
                padre_1_id, padre_2_id,
                params_tecnicos, params_macro, params_riesgo, params_smc,
                capital_inicial, capital_actual, estado
            ) VALUES (%s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, 'activo')
            ON CONFLICT (id) DO NOTHING
            """,
            (
                a["id"], HOY, a["generacion"],
                json.dumps(DEFAULT_TECNICOS),
                json.dumps(DEFAULT_MACRO),
                json.dumps(DEFAULT_RIESGO),
                json.dumps(DEFAULT_SMC),
                CAPITAL, CAPITAL,
            )
        )
        print(f"  Agente {a['id']} creado.")

print(f"\n✓ {len(agentes)} agentes Gen 1 sembrados en Supabase. Pool total: ${CAPITAL * len(agentes):.2f}")
