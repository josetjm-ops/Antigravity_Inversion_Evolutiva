"""
Corrección one-off de genealogía — 2026-06-12 (Sesión 21).

Los 4 hijos del ciclo de anoche (2026-06-12_05..08) nacieron por CLON FORZADO
con padre == madre (3 de ellos del genoma tendencia 2026-05-19_10 en cupos de
reversion; el 4to del agente eliminado 2026-06-02_06). Este script los re-cría
con la jerarquía nueva de Sesión 21: torneo de candidatos de DOS padres
distintos validados OOS, y si nadie pasa el umbral estricto, el mejor
candidato de cruce.

Actualiza in-place (conserva id, capital, generación, especie y todas las FKs):
    padre_1_id, padre_2_id, params_tecnicos, params_macro, params_riesgo,
    params_smc.

Uso:  python scripts/fix_genealogia_20260612.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.connection import get_conn, get_dict_cursor
from evolution.evolution_engine import (
    EvolutionEngine,
    breed_agent,
    TOURNAMENT_MIN_OOS_FITNESS,
    TOURNAMENT_MIN_OOS_TRADES,
)
from evolution.backtester import (
    fetch_backtest_data,
    run_backtest,
    N_CANDIDATE_CHILDREN,
)

CHILDREN = ["2026-06-12_05", "2026-06-12_06", "2026-06-12_07", "2026-06-12_08"]
ROUNDS = 3  # rondas torneo→HoF por cupo (la corrección no necesita las 8 del ciclo)
DRY_RUN = "--dry-run" in sys.argv


def _weighted_pair(pool: list[dict]) -> tuple[dict, dict]:
    scores = [max(float(a.get("fitness_score") or a.get("roi_total") or 0), 0.0001)
              for a in pool]
    total = sum(scores)
    weights = [s / total for s in scores]
    p1, p2 = random.choices(pool, weights=weights, k=2)
    if p1["id"] == p2["id"]:
        others = [a for a in pool if a["id"] != p1["id"]]
        if others:
            p2 = random.choice(others)
    return p1, p2


def _passes(bt: dict) -> bool:
    return (bt["fitness"] > TOURNAMENT_MIN_OOS_FITNESS
            and bt["n_trades"] >= TOURNAMENT_MIN_OOS_TRADES)


def main() -> None:
    today = date(2026, 6, 12)
    engine = EvolutionEngine(today)

    agents = engine._get_active_agents_ranked()
    children = {a["id"]: a for a in agents if a["id"] in CHILDREN}
    veterans = [a for a in agents if a["id"] not in CHILDREN]
    assert len(children) == 4, f"Esperaba 4 hijos activos, hay {len(children)}"
    print(f"Veteranos en pool: {len(veterans)} | Hijos a re-criar: {len(children)}")

    print("Descargando datos de backtest (Yahoo Finance)...")
    bt_data = fetch_backtest_data()

    updates: list[dict] = []
    for child_id in CHILDREN:
        esp = str(children[child_id].get("especie") or "tendencia")
        same = [a for a in veterans if str(a.get("especie") or "tendencia") == esp]
        tourn_pool = same if len(same) >= 2 else veterans
        hof = engine._get_hof_parents(esp)

        best_cand, best_bt, origen = None, None, ""
        for _ in range(ROUNDS):
            for pool, src in ((tourn_pool, "torneo"), (hof, "hall_of_fame")):
                if len(pool) < 2:
                    continue
                for _c in range(N_CANDIDATE_CHILDREN):
                    p1, p2 = _weighted_pair(pool)
                    cand = breed_agent(p1, p2, child_id, today,
                                       int(children[child_id]["generacion"]),
                                       especie=esp)
                    try:
                        bt = run_backtest(bt_data, cand)
                    except Exception:
                        bt = {"fitness": 0.0, "n_trades": 0}
                    if _passes(bt):
                        best_cand, best_bt, origen = cand, bt, src
                        break
                    if best_bt is None or bt["fitness"] > best_bt["fitness"]:
                        best_cand, best_bt = cand, bt
                        origen = "mejor_candidato_oos"
                if origen in ("torneo", "hall_of_fame"):
                    break
            if origen in ("torneo", "hall_of_fame"):
                break

        assert best_cand is not None, f"No se pudo criar candidato para {child_id}"
        assert best_cand["padre_1_id"] != best_cand["padre_2_id"], "padre==madre!"
        print(f"  {child_id} ({esp}): {best_cand['padre_1_id']} x "
              f"{best_cand['padre_2_id']} | origen={origen} "
              f"fitness={best_bt['fitness']:.5f} n={best_bt['n_trades']}")
        updates.append(best_cand)

    if DRY_RUN:
        print("DRY RUN — sin escribir en DB.")
        return

    with get_conn() as conn:
        cur = conn.cursor()
        for c in updates:
            cur.execute(
                """
                UPDATE agentes SET
                    padre_1_id = %s, padre_2_id = %s,
                    params_tecnicos = %s, params_macro = %s,
                    params_riesgo = %s, params_smc = %s
                WHERE id = %s
                """,
                (c["padre_1_id"], c["padre_2_id"],
                 json.dumps(c["params_tecnicos"]), json.dumps(c["params_macro"]),
                 json.dumps(c["params_riesgo"]), json.dumps(c["params_smc"]),
                 c["id"]),
            )
        # Registrar la corrección en logs_juez para trazabilidad
        detalle = {
            "motivo": "Sesión 21: corrección de genealogía — clones forzados "
                      "re-criados con cruce de dos padres distintos",
            "hijos": [
                {"id": c["id"], "padre_1": c["padre_1_id"], "padre_2": c["padre_2_id"]}
                for c in updates
            ],
        }
        cur.execute(
            """
            INSERT INTO logs_juez (fecha, tipo_evento, descripcion, datos_json)
            VALUES (%s, 'evaluacion_diaria', %s, %s)
            """,
            (today,
             "Corrección Sesión 21: los 4 agentes nacidos por clon forzado "
             "(padre==madre) fueron re-criados con cruce real de dos padres "
             "distintos validado por backtest OOS.",
             json.dumps(detalle)),
        )
    print("DB actualizada y corrección registrada en logs_juez.")


if __name__ == "__main__":
    main()
