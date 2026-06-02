"""
seed_15_agents.py — Expansión de la población a 15 agentes (5 por especie).

Distribucion objetivo:
  tendencia : 4 actuales + 1 nuevo  = 5
  reversion : 3 actuales + 2 nuevos = 5
  ruptura   : 3 actuales + 2 nuevos = 5

Proceso:
  1. Lee los agentes activos actuales agrupados por especie.
  2. Por cada especie con < 5 agentes, cría tantos hijos como falten usando
     los 2 mejores agentes de esa especie como padres (crossover + mutación).
  3. Inserta los nuevos agentes en la DB.
  4. Redistribuye el pool de capital equitativamente entre los 15 activos.

Los nuevos agentes reciben la generacion = max_gen_activa + 1.

Uso:
  python scripts/seed_15_agents.py [--dry-run]
  --dry-run : muestra lo que haría sin ejecutar cambios en la DB.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("seed_15_agents")

TARGET_PER_SPECIES = 5
TODAY = date.today()


def main(dry_run: bool = False) -> None:
    from db.connection import get_conn, get_dict_cursor
    from evolution.evolution_engine import (
        breed_agent, SIGMA_WEIGHTS, SIGMA_PERIODS, SIGMA_RISK,
    )
    from utils.sheets_logger import SheetsLogger
    import json

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute("""
            SELECT id, generacion, especie,
                   params_tecnicos, params_macro, params_riesgo, params_smc,
                   capital_actual::float, roi_total::float,
                   COALESCE(rh.fitness_score, 0)::float AS fitness_score
            FROM agentes a
            LEFT JOIN LATERAL (
                SELECT fitness_score FROM ranking_historico
                WHERE agente_id = a.id ORDER BY fecha DESC LIMIT 1
            ) rh ON true
            WHERE a.estado = 'activo'
            ORDER BY especie, COALESCE(rh.fitness_score, 0) DESC
        """)
        current = [dict(r) for r in cur.fetchall()]

        cur2 = conn.cursor()
        cur2.execute("SELECT COALESCE(MAX(generacion),1) FROM agentes")
        max_gen = int(cur2.fetchone()[0])

        cur2.execute("SELECT COALESCE(SUM(capital_actual),0)::float FROM agentes WHERE estado='activo'")
        pool_total = float(cur2.fetchone()[0])

        cur2.execute("SELECT COUNT(*) FROM agentes WHERE fecha_nacimiento = %s", (TODAY,))
        next_idx = int(cur2.fetchone()[0]) + 1

    # ── Agrupar por especie ──────────────────────────────────────────────────
    by_species: dict[str, list[dict]] = {"tendencia": [], "reversion": [], "ruptura": []}
    for a in current:
        sp = a.get("especie") or "tendencia"
        if sp in by_species:
            by_species[sp].append(a)

    log.info("Población actual: tendencia=%d reversion=%d ruptura=%d  pool=$%.4f",
             len(by_species["tendencia"]), len(by_species["reversion"]),
             len(by_species["ruptura"]), pool_total)

    # ── Determinar cuántos crear por especie ─────────────────────────────────
    needed: dict[str, int] = {}
    for sp, agents in by_species.items():
        n = TARGET_PER_SPECIES - len(agents)
        if n > 0:
            needed[sp] = n
            log.info("  %s: faltan %d agentes", sp, n)
        else:
            log.info("  %s: ya tiene %d (OK)", sp, len(agents))

    if not needed:
        log.info("La poblacion ya tiene %d agentes por especie. Nada que hacer.", TARGET_PER_SPECIES)
        return

    total_new = sum(needed.values())
    new_pool_size = len(current) + total_new
    capital_por_agente = round(pool_total / new_pool_size, 4)
    log.info("Nuevos agentes a crear: %d  ·  capital redistribuido: $%.4f/agente",
             total_new, capital_por_agente)

    if dry_run:
        log.info("[DRY-RUN] Sin cambios en la DB. Agentes nuevos que se crearían:")

    # ── Criar y registrar agentes nuevos ────────────────────────────────────
    new_agents: list[dict] = []
    slot = 0
    for sp, n_to_create in needed.items():
        parents = by_species[sp]
        if len(parents) < 2:
            # Si solo hay 1 padre de esta especie, duplicar con mutación alta
            if len(parents) == 1:
                parents = parents * 2
            else:
                # Sin padres de esta especie: usar los mejores globales
                parents = sorted(current, key=lambda a: float(a.get("fitness_score", 0)), reverse=True)[:2]
                log.warning("  %s sin padres de la especie — usando padres globales", sp)

        p1, p2 = parents[0], parents[1]

        for _ in range(n_to_create):
            child_id = f"{TODAY.strftime('%Y-%m-%d')}_{next_idx + slot:02d}"
            slot += 1

            child = breed_agent(
                p1, p2, child_id, TODAY, max_gen + 1,
                sigma_weights=SIGMA_WEIGHTS,
                sigma_periods=SIGMA_PERIODS,
                sigma_risk=SIGMA_RISK,
                especie=sp,
            )
            child["capital_inicial"] = capital_por_agente
            child["capital_actual"]  = capital_por_agente
            new_agents.append(child)

            log.info("  [%s] %s  padres: %s × %s  fitness_padres: %.4f / %.4f",
                     sp, child_id, p1["id"], p2["id"],
                     float(p1.get("fitness_score", 0)),
                     float(p2.get("fitness_score", 0)))

    if dry_run:
        for a in new_agents:
            log.info("  CREAR: %s (%s)  capital=$%.4f", a["id"], a["especie"], a["capital_actual"])
        log.info("[DRY-RUN] Pool redistribuido: $%.4f / %d = $%.4f c/u",
                 pool_total, new_pool_size, capital_por_agente)
        return

    # ── Insertar + redistribuir en una transacción ───────────────────────────
    with get_conn() as conn:
        cur = conn.cursor()

        for child in new_agents:
            cur.execute("""
                INSERT INTO agentes (
                    id, fecha_nacimiento, generacion,
                    padre_1_id, padre_2_id,
                    params_tecnicos, params_macro, params_riesgo, params_smc,
                    capital_inicial, capital_actual, especie, estado
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, 'activo'
                )
            """, (
                child["id"], child["fecha_nacimiento"], child["generacion"],
                child["padre_1_id"], child["padre_2_id"],
                json.dumps(child["params_tecnicos"]),
                json.dumps(child["params_macro"]),
                json.dumps(child["params_riesgo"]),
                json.dumps(child.get("params_smc", {})),
                child["capital_inicial"],
                child["capital_actual"],
                child["especie"],
            ))

        # Redistribuir capital entre TODOS los activos (los 10 originales + los nuevos)
        cur.execute(
            "UPDATE agentes SET capital_actual = %s, capital_inicial = %s "
            "WHERE id = ANY(%s) AND estado = 'activo'",
            (capital_por_agente, capital_por_agente, [a["id"] for a in new_agents]),
        )
        cur.execute(
            "UPDATE agentes SET capital_actual = %s WHERE estado = 'activo' AND id != ANY(%s)",
            (capital_por_agente, [a["id"] for a in new_agents]),
        )

    log.info("DB actualizada. Pool $%.4f redistribuido: $%.4f × %d agentes.",
             pool_total, capital_por_agente, new_pool_size)

    # ── Sincronizar Sheets ───────────────────────────────────────────────────
    try:
        sl = SheetsLogger()
        for child in new_agents:
            sl.log_agent(child)
            log.info("  Sheets: agente %s registrado.", child["id"])
        # Actualizar capital de todos los agentes activos en Sheets
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            cur.execute("""
                SELECT id, roi_total::float, operaciones_total, operaciones_ganadoras
                FROM agentes WHERE estado = 'activo'
            """)
            for ag in cur.fetchall():
                sl.update_agent_live(
                    agent_id=ag["id"],
                    capital=capital_por_agente,
                    roi=float(ag["roi_total"] or 0),
                    ops=int(ag["operaciones_total"] or 0),
                    ops_ganadoras=int(ag["operaciones_ganadoras"] or 0),
                )
        log.info("Sheets sincronizado: capital $%.4f para todos los agentes activos.", capital_por_agente)
    except Exception as e:
        log.warning("Sheets no actualizado (continuando): %s", e)

    # ── Resumen final ────────────────────────────────────────────────────────
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT especie, COUNT(*) n
            FROM agentes WHERE estado = 'activo'
            GROUP BY especie ORDER BY especie
        """)
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM agentes WHERE estado='activo'")
        total = cur.fetchone()[0]

    log.info("=== Resultado final ===")
    for sp, n in rows:
        log.info("  %-10s : %d agentes", sp, n)
    log.info("  TOTAL      : %d agentes · $%.4f/agente", total, capital_por_agente)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Expande la población a 15 agentes (5/especie).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra los cambios sin ejecutarlos.")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
