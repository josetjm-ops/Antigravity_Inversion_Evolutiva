"""
Promoción de campeones offline a producción — Fase D (2026-07-24).

Inserta en producción los genomas que la evolución offline validó contra
holdout (datos que jamás vio) Y que pasaron el gate bootstrap.

FILOSOFÍA DEL EXPERIMENTO
-------------------------
NO se tocan los genes de los agentes existentes. Los campeones entran como
agentes NUEVOS y compiten bajo las mismas reglas que todos: si su edge es
real, la presión selectiva que ya existe (capital ∝ fitness) hará que
acumulen capital y dominen; si no lo es, serán eliminados como cualquier
otro. Es un experimento controlado con grupo de control intacto, no un acto
de fe en el backtest.

CONTABILIDAD DEL CAPITAL
------------------------
El pool total NO se infla: los campeones reciben su cuota del pool
existente vía la MISMA `_redistribute_capital` que usa el Juez, lo que
diluye proporcionalmente a todos (exactamente lo que pasa cuando la
población crece por repoblación normal).

TRAZABILIDAD
------------
`fitness_oos_prometido` / `n_trades_oos_prometido` se poblan con el
resultado REAL del holdout, así la vista `v_decaimiento_oos` podrá comparar
después lo prometido contra lo que rindieron en producción — la prueba de
fuego de si la evolución offline predice el desempeño real.

Uso:
    python -m scripts.promover_campeon --archivo resultados_offline/campeones_X.json --dry-run
    python -m scripts.promover_campeon --archivo resultados_offline/campeones_X.json --confirmar
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("PromoverCampeon")


def _siguiente_indice(conn, hoy: date) -> int:
    """Siguiente NN libre para IDs YYYY-MM-DD_NN (numeración consecutiva real)."""
    from db.connection import get_dict_cursor
    cur = get_dict_cursor(conn)
    cur.execute(
        "SELECT id FROM agentes WHERE id LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{hoy.isoformat()}_%",),
    )
    fila = cur.fetchone()
    if not fila:
        return 1
    try:
        return int(str(fila["id"]).split("_")[-1]) + 1
    except (ValueError, IndexError):
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Promueve campeones offline a producción")
    ap.add_argument("--archivo", required=True, help="JSON de campeones de la evolución offline")
    ap.add_argument("--especie", help="Filtrar por especie (default: todas las del archivo)")
    ap.add_argument("--max", type=int, default=None, help="Máximo de campeones a promover")
    ap.add_argument("--confirmar", action="store_true",
                    help="Ejecuta la inserción real. Sin este flag es dry-run.")
    args = ap.parse_args()

    ruta = Path(args.archivo)
    if not ruta.exists():
        log.error("No existe el archivo: %s", ruta)
        return 1

    informe = json.loads(ruta.read_text(encoding="utf-8"))
    candidatos = []
    for especie, lista in informe.get("campeones", {}).items():
        if args.especie and especie != args.especie:
            continue
        for c in lista:
            if c.get("promovible"):
                candidatos.append((especie, c))

    if not candidatos:
        log.error("El archivo no contiene campeones PROMOVIBLES. Nada que hacer.")
        return 1

    if args.max:
        candidatos = candidatos[: args.max]

    log.info("Campeones promovibles encontrados: %d", len(candidatos))
    for especie, c in candidatos:
        h = c["holdout"]
        log.info(
            "  %s | holdout: exp=%+.4fR n=%d WR=%.0f%% IC_inf=%.5f",
            especie, h["expectancy_R"], h["n_trades"], h["win_rate"] * 100,
            h.get("ic_inferior") or 0,
        )

    if not args.confirmar:
        log.info("")
        log.info("DRY-RUN: no se insertó nada. Repite con --confirmar para promover.")
        return 0

    from db.connection import get_conn, get_dict_cursor
    from evolution.evolution_engine import EvolutionEngine, calc_fitness_scores

    hoy = date.today()
    with get_conn() as conn:
        cur = get_dict_cursor(conn)

        # Pool y generación ANTES de insertar: el pool no debe inflarse.
        cur.execute("SELECT COALESCE(SUM(capital_actual),0) AS pool, COUNT(*) AS n "
                    "FROM agentes WHERE estado='activo'")
        fila = cur.fetchone()
        pool_previo, n_previo = float(fila["pool"]), int(fila["n"])
        cur.execute("SELECT COALESCE(MAX(generacion),1) AS g FROM agentes")
        generacion = int(cur.fetchone()["g"]) + 1

        log.info("Pool antes: $%.4f entre %d agentes | generación nueva: %d",
                 pool_previo, n_previo, generacion)

        engine = EvolutionEngine(hoy)
        idx = _siguiente_indice(conn, hoy)
        nuevos_ids = []

        for especie, c in candidatos:
            g = c["genoma"]
            h = c["holdout"]
            agente = {
                "id": f"{hoy.isoformat()}_{idx:02d}",
                "fecha_nacimiento": hoy,
                "generacion": generacion,
                # Sin padres en producción: su linaje es la evolución offline.
                "padre_1_id": None,
                "padre_2_id": None,
                "params_tecnicos": g["params_tecnicos"],
                "params_macro": g["params_macro"],
                "params_riesgo": g["params_riesgo"],
                "params_smc": g["params_smc"],
                # Capital provisional; la redistribución de abajo lo fija.
                "capital_inicial": 0.0,
                "capital_actual": 0.0,
                "especie": especie,
                # La promesa REAL medida en holdout, para v_decaimiento_oos.
                "fitness_oos_prometido": round(float(h["expectancy_R"]), 6),
                "n_trades_oos_prometido": int(h["n_trades"]),
            }
            engine._insert_new_agent(conn, agente)
            nuevos_ids.append(agente["id"])
            log.info("  INSERTADO %s (%s) — promesa OOS %+.4fR sobre %d trades",
                     agente["id"], especie, h["expectancy_R"], h["n_trades"])
            idx += 1

        # Redistribuir el MISMO pool entre los agentes (ahora más): los
        # campeones toman su cuota diluyendo a todos, sin inyectar capital.
        fitness_map = calc_fitness_scores(conn)
        pool_total, cuota = engine._redistribute_capital(
            conn, nuevos_ids, pool_override=pool_previo, fitness_map=fitness_map,
        )

        # Trazabilidad en el log de auditoría del Juez.
        cur2 = conn.cursor()
        for aid in nuevos_ids:
            cur2.execute(
                """
                INSERT INTO logs_juez (fecha, tipo_evento, agente_afectado_id,
                                       descripcion, datos_json)
                VALUES (%s, 'nuevo_agente', %s, %s, %s)
                """,
                (hoy, aid,
                 f"Campeón promovido desde evolución offline (Fase D) — validado "
                 f"contra holdout con gate bootstrap aprobado.",
                 json.dumps({"origen": "evolucion_offline", "archivo": str(ruta.name)})),
            )

        cur.execute("SELECT COUNT(*) AS n FROM agentes WHERE estado='activo'")
        n_final = int(cur.fetchone()["n"])

    log.info("")
    log.info("PROMOCIÓN COMPLETA: %d campeones activos en producción.", len(nuevos_ids))
    log.info("Población: %d → %d | pool: $%.4f (sin inyección) | cuota base: $%.4f",
             n_previo, n_final, pool_total, cuota)
    log.info("Los campeones compiten desde el próximo ciclo del monitor (cada 15 min).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
