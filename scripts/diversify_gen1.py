"""
Aplica mutación gaussiana individualizada a los 10 agentes Gen1.

Se ejecuta UNA SOLA VEZ cuando todos los agentes se sebraron con parámetros
idénticos (seed sin mutación). Da a cada agente ADN propio para que el motor
evolutivo pueda diferenciar estrategias y los decisiones no sean idénticas.

Sigma elevada (≈ 1.5× la del motor evolutivo normal) para garantizar
diversidad desde el arranque.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from db.connection import get_conn, get_dict_cursor
from evolution.evolution_engine import (
    _mutate_block,
    _normalize_weights,
    _enforce_ema_constraint,
    _enforce_sl_tp_constraint,
    _BOUNDS_TECNICOS_PERIODS,
    _BOUNDS_TECNICOS_WEIGHTS,
    _BOUNDS_MACRO,
    _BOUNDS_RIESGO,
    _BOUNDS_SMC,
)

# Sigmas elevadas para diversidad inicial robusta
SIGMA_W = 0.08   # pesos de indicadores
SIGMA_P = 0.12   # períodos (RSI, EMA, MACD)
SIGMA_R = 0.15   # riesgo y SMC

print("=== Diversificación genética de agentes Gen1 ===\n")

with get_conn() as conn:
    cur = get_dict_cursor(conn)
    cur.execute("""
        SELECT id, params_tecnicos, params_macro, params_riesgo, params_smc
        FROM agentes
        WHERE estado = 'activo'
        ORDER BY id
    """)
    agents = [dict(r) for r in cur.fetchall()]

print(f"Agentes a diversificar: {len(agents)}\n")

updates = []
for a in agents:
    tec    = dict(a["params_tecnicos"] or {})
    mac    = dict(a["params_macro"]    or {})
    riesgo = dict(a["params_riesgo"]   or {})
    smc    = dict(a["params_smc"]      or {})

    tec    = _mutate_block(tec,    _BOUNDS_TECNICOS_PERIODS, SIGMA_P)
    tec    = _mutate_block(tec,    _BOUNDS_TECNICOS_WEIGHTS, SIGMA_W)
    mac    = _mutate_block(mac,    _BOUNDS_MACRO,            SIGMA_W)
    riesgo = _mutate_block(riesgo, _BOUNDS_RIESGO,           SIGMA_R)
    smc    = _mutate_block(smc,    _BOUNDS_SMC,              SIGMA_R)

    tec    = _normalize_weights(tec, ["peso_rsi", "peso_ema", "peso_macd"])
    tec    = _enforce_ema_constraint(tec)
    riesgo = _enforce_sl_tp_constraint(riesgo)

    updates.append((a["id"], tec, mac, riesgo, smc))
    print(
        f"  {a['id']}: ema={tec.get('ema_rapida')}/{tec.get('ema_lenta')} "
        f"rsi={tec.get('rsi_periodo')} "
        f"rr={smc.get('risk_reward_target', 0):.2f} "
        f"atr_f={smc.get('atr_factor', 0):.2f} "
        f"trail_act={smc.get('trailing_activation_pips', 0):.1f} "
        f"sl_pct={riesgo.get('stop_loss_pct', 0):.4f}"
    )

print()
respuesta = input("¿Aplicar estos cambios en la BD? (s/N): ").strip().lower()
if respuesta != "s":
    print("Operación cancelada.")
    sys.exit(0)

with get_conn() as conn:
    cur = conn.cursor()
    for agent_id, tec, mac, riesgo, smc in updates:
        cur.execute(
            """
            UPDATE agentes SET
                params_tecnicos = %s,
                params_macro    = %s,
                params_riesgo   = %s,
                params_smc      = %s
            WHERE id = %s
            """,
            (
                json.dumps(tec),
                json.dumps(mac),
                json.dumps(riesgo),
                json.dumps(smc),
                agent_id,
            ),
        )

print(f"\n✓ {len(updates)} agentes diversificados exitosamente.")
print("Los agentes ahora tienen ADN propio y producirán decisiones distintas.")
