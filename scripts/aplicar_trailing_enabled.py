"""
Siembra el gen trailing_enabled en la población viva — 2026-08-12.

QUÉ SE MIDIÓ
------------
Al modelar por fin el trailing en el backtester (antes era una "simplificación
intencional") se midió sobre 6 meses de holdout, con todo lo demás fijo:

    SIN trailing: +0.319R   n=120   SL: 76   TP: 11   PARCIAL: 17
    CON trailing: +0.131R   n=117   SL: 101  TP:  3   PARCIAL:  8
    efecto:       -0.189R

El trailing convierte ganadores en stops: salta a mitad de camino del objetivo
y una retracción normal cierra la posición.

DISEÑO: COMPARACIÓN PAREADA
----------------------------
Los agentes vivos están fuertemente correlacionados — entran al mismo precio en
el mismo minuto (factor medido 2.3x). Eso, que es un problema para la
diversificación, aquí es una ventaja: si unos llevan el trailing apagado y
otros encendido sobre LAS MISMAS entradas, la diferencia de resultado aísla el
efecto del trailing sin ruido de selección de entrada. Es el experimento más
limpio disponible con esta población.

Por eso NO se apaga en todos: se deja un grupo de control con trailing activo.

NOTA SOBRE EL DEFAULT
---------------------
Los agentes existentes no tienen el gen, y el código cae a `get(..., 1)` =
trailing ENCENDIDO (compatibilidad hacia atrás). Sin este script el cambio no
les llegaría nunca.

Uso:
    python -m scripts.aplicar_trailing_enabled
    python -m scripts.aplicar_trailing_enabled --confirmar
"""
from __future__ import annotations

import argparse
import json
import logging
import os
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
log = logging.getLogger("TrailingEnabled")

# Cuántos agentes conservan el trailing ENCENDIDO como grupo de control.
N_CONTROL = int(os.getenv("TRAILING_N_CONTROL", "4"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Siembra trailing_enabled con grupo de control")
    ap.add_argument("--confirmar", action="store_true",
                    help="Aplica los cambios. Sin este flag es dry-run.")
    args = ap.parse_args()

    from db.connection import get_conn, get_dict_cursor

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT id, especie, params_smc FROM agentes WHERE estado='activo' ORDER BY id"
        )
        agentes = []
        for a in cur.fetchall():
            smc = a["params_smc"] if isinstance(a["params_smc"], dict) else json.loads(a["params_smc"])
            agentes.append((a["id"], a["especie"], smc))

        if not agentes:
            log.error("No hay agentes activos.")
            return 1

        # Control repartido entre especies (los últimos de cada una por id) para
        # que el grupo de control no sea todo de la misma especie y confunda el
        # efecto del trailing con el de la estrategia.
        por_especie: dict[str, list] = {}
        for aid, esp, smc in agentes:
            por_especie.setdefault(esp, []).append((aid, esp, smc))

        control_ids: list[str] = []
        while len(control_ids) < N_CONTROL:
            antes = len(control_ids)
            for esp in sorted(por_especie):
                if len(control_ids) >= N_CONTROL:
                    break
                restantes = [a for a in por_especie[esp] if a[0] not in control_ids]
                if restantes:
                    control_ids.append(restantes[-1][0])
            if len(control_ids) == antes:
                break  # no quedan candidatos

        log.info("Poblacion: %d agentes", len(agentes))
        log.info("  TRAILING OFF (tratamiento): %d", len(agentes) - len(control_ids))
        log.info("  TRAILING ON  (control)    : %d -> %s", len(control_ids), control_ids)
        log.info("")
        for aid, esp, smc in agentes:
            destino = 1 if aid in control_ids else 0
            actual = smc.get("trailing_enabled", "(ausente -> 1)")
            log.info("  %-15s %-10s trailing_enabled: %-16s -> %d",
                     aid, esp, str(actual), destino)

        if not args.confirmar:
            log.info("")
            log.info("DRY-RUN: no se modifico nada. Repite con --confirmar.")
            return 0

        cur2 = conn.cursor()
        for aid, _esp, smc in agentes:
            nuevo_smc = dict(smc)
            nuevo_smc["trailing_enabled"] = 1 if aid in control_ids else 0
            cur2.execute("UPDATE agentes SET params_smc=%s WHERE id=%s",
                         (json.dumps(nuevo_smc), aid))

        log.info("")
        log.info("APLICADO. Las posiciones ABIERTAS mantienen la configuracion con "
                 "la que nacieron (esta en su decision_riesgo); el gen aplica desde "
                 "la proxima operacion.")
        log.info("Comparar en unos dias: mismas entradas, distinta gestion de salida.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
