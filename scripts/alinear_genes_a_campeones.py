"""
Alinea los genes de la población viva al perfil validado — 2026-08-13.

QUÉ SE VALIDÓ
-------------
Evolución offline de 30 generaciones con el backtester ya honesto (cierre EOD +
trailing + extremo favorable acumulado), sobre 2 años de histórico y holdout
intocable de 6 meses. Dos campeones pasaron el gate bootstrap:

    #2  exp=+0.6275R  n=108  WR=49%  IC80_inf=+0.0034
    #5  exp=+0.5979R  n=102  WR=47%  IC80_inf=+0.0021

Sus genes, en el rango que ambos comparten:
    trailing_enabled     = 0        (los DOS lo apagaron por su cuenta)
    atr_factor           1.03 - 1.20
    risk_reward_target   3.19 - 3.50
    be_activation_r      1.14 - 1.20
    sesion_trading       = overlap  (los dos)

POR QUÉ HACE FALTA CORREGIR LA POBLACIÓN
-----------------------------------------
El 12-ago se aplicaron a mano valores que quedaron huérfanos de validación:

    gen                  aplicado a mano   validado por la evolución
    atr_factor           1.40 - 1.80       1.03 - 1.20
    risk_reward_target   2.50 - 3.00       3.19 - 3.50
    be_activation_r      0.85 - 1.09       1.14 - 1.20

El error de método fue medir la curva de `atr_factor` con el trailing
ENCENDIDO (el SMC de prueba no llevaba el gen y caía al default 1), concluir
"el óptimo es 1.5", aplicarlo, y acto seguido APAGAR el trailing en 11
agentes. Los dos parámetros interactúan: con trailing activo un stop ancho
compensa que el trailing corte pronto; sin trailing el stop puede volver a ser
estrecho. Se aplicó el óptimo de un escenario al escenario contrario.

QUÉ HACE
--------
Reescala los tres genes al rango validado conservando el orden relativo de
cada agente — no aplasta la población a un único valor, para no destruir la
diversidad genética restante. `sesion_trading` no se toca aquí (ya se fijó por
especie con evidencia propia el 10-ago).

EL GRUPO DE CONTROL DEL TRAILING SE RESPETA
--------------------------------------------
`trailing_enabled` NO se modifica. Hay un experimento pareado en marcha desde
el 12-ago (11 agentes OFF / 4 ON sobre las mismas entradas) y es la única
evidencia EN VIVO que vamos a obtener sobre el trailing. Las dos validaciones
que tenemos —la medición controlada y la elección de la evolución— son ambas
de backtest; conviene confirmarlas con mercado real antes de cerrar el tema.

Uso:
    python -m scripts.alinear_genes_a_campeones
    python -m scripts.alinear_genes_a_campeones --confirmar
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("AlinearGenes")

# Rango validado por los 2 campeones que pasaron bootstrap (2026-08-12).
DESTINO = {
    "atr_factor":         (1.03, 1.20),
    "risk_reward_target": (3.19, 3.50),
    "be_activation_r":    (1.14, 1.20),
}

# Los campeones offline ya traen los genes validados: no se tocan (reescalarlos
# los movería fuera del valor que precisamente se validó).
#
# Se identifican por `padre_1_id IS NULL`, no por fecha ni por tener
# fitness_oos_prometido. Razón: el Juez cría agentes por torneo que TAMBIÉN
# llevan fitness_oos_prometido (2026-08-13_01 nació así esa misma noche, con
# una promesa de +0.124R frente a los +0.60R de los campeones offline), y un
# filtro por prefijo de fecha los habría dejado sin alinear. Solo
# promover_campeon.py inserta con padre_1_id=None, porque el linaje de un
# campeón offline es la evolución, no un cruce de producción.
SQL_CAMPEONES_OFFLINE = "padre_1_id IS NULL AND fitness_oos_prometido IS NOT NULL"


def _reescalar(valor: float, o_min: float, o_max: float,
               d_min: float, d_max: float) -> float:
    if o_max <= o_min:
        return round((d_min + d_max) / 2, 6)
    frac = max(0.0, min(1.0, (valor - o_min) / (o_max - o_min)))
    return round(d_min + frac * (d_max - d_min), 6)


def main() -> int:
    ap = argparse.ArgumentParser(description="Alinea genes al perfil validado")
    ap.add_argument("--confirmar", action="store_true",
                    help="Aplica los cambios. Sin este flag es dry-run.")
    args = ap.parse_args()

    from db.connection import get_conn, get_dict_cursor

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            f"""
            SELECT id, especie, params_smc,
                   ({SQL_CAMPEONES_OFFLINE}) AS es_campeon_offline
            FROM agentes WHERE estado='activo' ORDER BY id
            """
        )
        agentes = []
        for a in cur.fetchall():
            smc = a["params_smc"] if isinstance(a["params_smc"], dict) else json.loads(a["params_smc"])
            agentes.append((a["id"], a["especie"], smc, bool(a["es_campeon_offline"])))

        objetivo = [a for a in agentes if not a[3]]
        campeones = [a for a in agentes if a[3]]

        if not objetivo:
            log.info("No hay agentes que alinear.")
            return 0

        log.info("Poblacion activa: %d  |  a alinear: %d  |  campeones intactos: %d",
                 len(agentes), len(objetivo), len(campeones))
        log.info("")

        # Rango observado por gen, para preservar el orden relativo.
        observado = {}
        for gen in DESTINO:
            vals = [float(smc.get(gen) or 0) for _i, _e, smc, _c in objetivo]
            observado[gen] = (min(vals), max(vals))

        cambios: dict[str, dict] = {}
        for aid, esp, smc, _c in objetivo:
            nuevo = dict(smc)
            linea = []
            for gen, (d_min, d_max) in DESTINO.items():
                o_min, o_max = observado[gen]
                viejo = float(smc.get(gen) or 0)
                val = _reescalar(viejo, o_min, o_max, d_min, d_max)
                nuevo[gen] = val
                linea.append(f"{gen.split('_')[0]}: {viejo:.2f}->{val:.2f}")
            cambios[aid] = nuevo
            log.info("  %-15s %-10s %s", aid, esp, "  |  ".join(linea))

        log.info("")
        log.info("trailing_enabled NO se toca: hay un experimento pareado en curso.")
        for aid, esp, smc, _c in agentes:
            if smc.get("trailing_enabled") == 1:
                log.info("    control (trailing ON): %s (%s)", aid, esp)

        if not args.confirmar:
            log.info("")
            log.info("DRY-RUN: no se modifico nada. Repite con --confirmar.")
            return 0

        cur2 = conn.cursor()
        for aid, smc in cambios.items():
            cur2.execute("UPDATE agentes SET params_smc=%s WHERE id=%s",
                         (json.dumps(smc), aid))

        log.info("")
        log.info("ALINEADOS %d agentes al perfil validado.", len(cambios))

    return 0


if __name__ == "__main__":
    sys.exit(main())
