"""
Repoblación con perfil campeón — Fase 2 de la auditoría forex (2026-07-31).

CONTEXTO
--------
La auditoría de producción encontró P&L bruto +$0.33 sobre 206 operaciones
(30 días) pero fricción de -$2.73 (neto -$2.41): el problema no era mala
selección de señales, era objetivos demasiado pequeños frente al costo fijo
por operación. La evolución offline (2 años + holdout de 6 meses jamás
vistos) ya encontró y validó contra bootstrap el perfil que resuelve esto:
risk_reward_target~3.8, be_activation_r~0.9, sesion_trading=overlap —
expectancy +0.50R a +0.71R vs -0.108R en producción.

ALCANCE: SOLO REVERSIÓN
-----------------------
Este script SOLO toca la especie "reversion". Motivo verificado con datos
antes de escribir el script (no asumido): el hallazgo de que operar dentro
de la ventana overlap (12-16 UTC) mejora el resultado NO generaliza a las
otras especies.

  Evidencia (30 días, por especie, dentro vs fuera de overlap):
    reversion:  fuera -$1.68 (WR 33%)  |  dentro +$0.56 (WR 56%)  -> validado
    ruptura:    fuera -$0.00 (WR 44%)  |  dentro -$0.72 (WR 21%)  -> INVERTIDO
    tendencia:  muestra insuficiente (7 vs 15 trades) para concluir nada

Forzar sesion=overlap en ruptura habría empeorado esa especie. Tendencia y
ruptura NO tienen un campeón que haya pasado el gate bootstrap en holdout
(ver evolucion offline 2026-07-24/25): no existe un perfil validado que
"copiar" para ellas. Sí reciben los pisos económicos universales de la Fase 1
(risk_reward_target>=2.5, be_activation_r>=0.8), que son matemática de
fricción válida para cualquier estrategia, no un hallazgo específico de
reversión.

DISEÑO: EXPERIMENTO CONTROLADO, IGUAL QUE LA PROMOCIÓN ORIGINAL
----------------------------------------------------------------
No se tocan los genes de ningún agente vivo. Se retiran (estado=eliminado)
los agentes LEGACY con menos historial de trades — el costo de oportunidad
de reemplazarlos es mínimo, porque apenas han operado — y se los reemplaza
por hijos del ÚNICO campeón validado que sigue vivo (2026-07-24_02; su
hermano 2026-07-24_01 fue eliminado por selección natural antes de esta
intervención, ver auditoría). Se conservan 2 agentes legacy con más historial
como GRUPO DE CONTROL explícito, y se deja intacto cualquier agente que ya
haya convergido orgánicamente hacia el perfil campeón (ej. hijos recientes
con risk_reward_target/be_activation_r ya altos) — no tiene sentido
retirarlos si sus genes ya son buenos.

El capital NO se infla: los nuevos agentes reciben su cuota del pool
existente vía la MISMA _redistribute_capital que usa el Juez.

Uso:
    python -m scripts.repoblar_perfil_campeon --dry-run
    python -m scripts.repoblar_perfil_campeon --confirmar
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
log = logging.getLogger("RepoblarPerfilCampeon")

# IDs a retirar: legacy de reversión con MENOS historial de trades (menor
# costo de oportunidad). Elegidos por consulta explícita a producción
# (2026-07-31), no por regla automática — quedan documentados aquí para que
# la decisión sea auditable.
#   2026-07-29_03: 0 trades cerrados desde su nacimiento
#   2026-07-31_02: 2 trades cerrados, nacido el mismo día
RETIRAR_IDS = ["2026-07-29_03", "2026-07-31_02"]

# Agentes con más historial que se conservan como grupo de control (NO se
# tocan): 2026-06-12_06 (3 trades) y 2026-06-16_02 (15 trades) — el perfil
# legacy sigue vivo y comparable contra los descendientes del campeón.
CONTROL_IDS = ["2026-06-12_06", "2026-06-16_02"]

# Único campeón validado (bootstrap + holdout) que sigue vivo en producción.
# Su hermano 2026-07-24_01 fue eliminado por selección natural antes de esta
# intervención — no se resucita, se cría a partir del sobreviviente real.
CHAMPION_ID = "2026-07-24_02"

N_HIJOS = 2  # reemplaza exactamente a los 2 retirados; no infla la población


def _siguiente_indice(conn, hoy: date) -> int:
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
    ap = argparse.ArgumentParser(description="Repuebla reversion con descendientes del campeon validado")
    ap.add_argument("--confirmar", action="store_true",
                    help="Ejecuta los cambios reales. Sin este flag es dry-run.")
    args = ap.parse_args()

    from db.connection import get_conn, get_dict_cursor
    from evolution.evolution_engine import (
        EvolutionEngine, breed_agent, calc_fitness_scores, SIGMA_WEIGHTS,
        SIGMA_PERIODS, SIGMA_RISK,
    )

    hoy = date.today()

    with get_conn() as conn:
        cur = get_dict_cursor(conn)

        cur.execute(
            "SELECT * FROM agentes WHERE id = %s AND estado = 'activo'", (CHAMPION_ID,)
        )
        champion = cur.fetchone()
        if not champion:
            log.error("El campeon %s no esta activo. Abortando.", CHAMPION_ID)
            return 1

        cur.execute(
            "SELECT id, especie, estado FROM agentes WHERE id = ANY(%s)",
            (RETIRAR_IDS,),
        )
        a_retirar = cur.fetchall()
        faltantes = set(RETIRAR_IDS) - {r["id"] for r in a_retirar}
        if faltantes:
            log.error("IDs a retirar no encontrados: %s. Abortando.", faltantes)
            return 1
        no_activos = [r["id"] for r in a_retirar if r["estado"] != "activo"]
        if no_activos:
            log.error("Ya no estan activos (¿se adelanto otro ciclo?): %s. Abortando.", no_activos)
            return 1

        log.info("Campeon fuente: %s (fitness_oos_prometido=%s, n_trades_oos_prometido=%s)",
                  CHAMPION_ID, champion.get("fitness_oos_prometido"),
                  champion.get("n_trades_oos_prometido"))
        log.info("A retirar (%d): %s", len(RETIRAR_IDS), RETIRAR_IDS)
        log.info("Grupo de control conservado (%d): %s", len(CONTROL_IDS), CONTROL_IDS)

        fitness_map = calc_fitness_scores(conn)
        champion_dict = {
            "id": champion["id"],
            "fitness_score": fitness_map.get(champion["id"], 0.0),
            "params_tecnicos": champion["params_tecnicos"],
            "params_macro": champion["params_macro"],
            "params_riesgo": champion["params_riesgo"],
            "params_smc": champion["params_smc"],
        }
        for k in ("params_tecnicos", "params_macro", "params_riesgo", "params_smc"):
            if isinstance(champion_dict[k], str):
                champion_dict[k] = json.loads(champion_dict[k])

        cur.execute("SELECT COALESCE(MAX(generacion),1) AS g FROM agentes")
        generacion = int(cur.fetchone()["g"]) + 1
        idx = _siguiente_indice(conn, hoy)

        hijos = []
        for i in range(N_HIJOS):
            child_id = f"{hoy.isoformat()}_{idx:02d}"
            # Self-cross del unico campeon vivo: crossover(x,x)=x, la
            # diversidad viene de la mutacion gaussiana normal (mismas sigmas
            # que usa el ciclo de produccion, no una tasa especial).
            child = breed_agent(
                champion_dict, champion_dict, child_id, hoy, generacion,
                sigma_weights=SIGMA_WEIGHTS, sigma_periods=SIGMA_PERIODS,
                sigma_risk=SIGMA_RISK, especie="reversion",
            )
            child["especie"] = "reversion"
            child["capital_inicial"] = 0.0
            child["capital_actual"] = 0.0
            child["fitness_oos_prometido"] = None
            child["n_trades_oos_prometido"] = None
            hijos.append(child)
            idx += 1

        log.info("Genomas hijos generados (%d):", len(hijos))
        for h in hijos:
            s = h["params_smc"]
            log.info(
                "  %s  RR=%.2f  be=%.2f  atr_f=%.2f  sesion=%s",
                h["id"], s.get("risk_reward_target", 0), s.get("be_activation_r", 0),
                s.get("atr_factor", 0), s.get("sesion_trading"),
            )

        if not args.confirmar:
            log.info("")
            log.info("DRY-RUN: no se modifico nada. Repite con --confirmar para ejecutar.")
            return 0

        # Pool ANTES de retirar/insertar: no debe inflarse ni desinflarse
        # fuera de lo que el propio P&L ya movio.
        cur.execute("SELECT COALESCE(SUM(capital_actual),0) AS pool FROM agentes WHERE estado='activo'")
        pool_previo = float(cur.fetchone()["pool"])

        for aid in RETIRAR_IDS:
            cur.execute(
                """
                UPDATE agentes SET estado='eliminado', fecha_eliminacion=%s,
                       razon_eliminacion=%s
                WHERE id=%s
                """,
                (hoy, "Fase 2 auditoria forex 2026-07-31: repoblacion con perfil "
                      "campeon validado (bootstrap+holdout); retirado por bajo "
                      "historial de trades frente al grupo de control conservado.",
                 aid),
            )
            log.info("  RETIRADO %s", aid)

        engine = EvolutionEngine(hoy)
        nuevos_ids = []
        for h in hijos:
            h["padre_1_id"] = CHAMPION_ID
            h["padre_2_id"] = CHAMPION_ID
            engine._insert_new_agent(conn, h)
            nuevos_ids.append(h["id"])
            log.info("  INSERTADO %s (hijo de %s)", h["id"], CHAMPION_ID)

        fitness_map2 = calc_fitness_scores(conn)
        pool_total, cuota = engine._redistribute_capital(
            conn, nuevos_ids, pool_override=pool_previo, fitness_map=fitness_map2,
        )

        cur2 = conn.cursor()
        for aid in nuevos_ids:
            cur2.execute(
                """
                INSERT INTO logs_juez (fecha, tipo_evento, agente_afectado_id,
                                       descripcion, datos_json)
                VALUES (%s, 'nuevo_agente', %s, %s, %s)
                """,
                (hoy, aid,
                 f"Repoblacion Fase 2 (auditoria forex 2026-07-31): hijo del "
                 f"campeon validado {CHAMPION_ID}.",
                 json.dumps({"origen": "repoblacion_perfil_campeon",
                             "padre": CHAMPION_ID})),
            )

        cur.execute("SELECT COUNT(*) AS n FROM agentes WHERE estado='activo'")
        n_final = int(cur.fetchone()["n"])

    log.info("")
    log.info("REPOBLACION COMPLETA: %d retirados, %d insertados. Poblacion activa: %d.",
              len(RETIRAR_IDS), len(nuevos_ids), n_final)
    log.info("Pool: $%.4f (sin inflar) | cuota base: $%.4f", pool_total, cuota)
    return 0


if __name__ == "__main__":
    sys.exit(main())
