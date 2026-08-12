"""
Corrección de emergencia del R:R inalcanzable — 2026-08-12.

QUÉ PASÓ
--------
La migración del 10-ago fijó risk_reward_target en 3.4-4.0 (perfil "campeón").
En las 24 operaciones cerradas del 11 y 12 de agosto:

    - CERO take-profits, cero trailing, cero parciales.
    - 58% cerradas a la fuerza (EOD/GUARDIA), no por la estrategia.
    - Máximo favorable medio: 15.2% del objetivo. Mejor caso: 31.2%.
    - Resultado: -$238.52 en dos días.

Con el stop clavado en el piso de 10 pips, un R:R de 3.4-4.0 sitúa el objetivo
en 34-40 pips. Sobre 90 días de salidas ganadoras reales la mediana es 16.5
pips y el percentil 90 es 27.7: el objetivo estaba por encima del máximo de
casi todas las salidas ganadoras históricas.

HIPÓTESIS INICIAL — REFUTADA
-----------------------------
Se sospechó que la causa era que el backtester NO cerraba las posiciones por
fin de día (corrían días hasta tocar SL/TP) mientras producción las cierra a
las 03:45 UTC. Se midió sobre el mismo holdout de 6 meses, con idéntico filtro
de sesión, y el efecto resultó PEQUEÑO: +0.435R sin cierre EOD → +0.366R con
él, apenas −0.069R. El hueco era real y se corrigió (evolution/backtester.py),
pero NO explica la pérdida en producción.

Peor aún: el backtest ya corregido sigue prefiriendo R:R alto
(3.82→+0.366R, 2.7→+0.315R, 2.2→+0.185R), así que bajar a 2.2 sobrerreaccionaba
a una muestra de 2 días — y en 15 agentes fuertemente correlacionados esos 24
trades son apenas un puñado de decisiones independientes.

DÓNDE QUEDÓ EL VALOR Y POR QUÉ
-------------------------------
Rango destino 2.5-3.5 (piso efectivo 2.8), no vuelta a 3.4-4.0. Razón: queda
otro hueco conocido entre backtest y producción — el backtester tampoco modela
el TRAILING STOP, que en vivo cierra ganadores antes del objetivo (69 salidas
por trailing en 90 días, a 11.4 pips medios frente a 22.3 de los TP completos).
El backtest deja correr al ganador hasta un TP lejano que en vivo rara vez se
cobra, así que su óptimo está sesgado hacia arriba. 2.5-3.5 da objetivos de
25-35 pips, en el entorno del percentil 90 real de las salidas ganadoras (27.7).

POR QUÉ HACE FALTA ESTE SCRIPT
-------------------------------
El piso MIN_RISK_REWARD_TARGET solo SUBE el valor efectivo (`max(gen, piso)`),
nunca lo baja. Cambiar la constante no toca a un agente cuyo gen ya está fuera
del rango: hay que reescribir el gen en la base.

QUÉ HACE
--------
Reescala `risk_reward_target` al rango destino en los agentes activos que
queden fuera, preservando su posición relativa — no los aplasta todos al mismo
valor, para no destruir la diversidad genética que queda. Ningún otro gen se
toca.

Uso:
    python -m scripts.corregir_rr_inalcanzable
    python -m scripts.corregir_rr_inalcanzable --confirmar
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
log = logging.getLogger("CorregirRR")

# Rango destino y rango de origen. Configurables porque este script se corrió
# dos veces el mismo día: primero 3.4-4.0 -> 2.0-3.0 (reacción a 2 días de
# pérdidas), y luego 2.0-3.0 -> 2.5-3.5 al medir que el cierre EOD NO era la
# causa y que el backtest corregido sigue prefiriendo R:R alto.
RR_MIN = float(os.getenv("RR_DESTINO_MIN", "2.5"))
RR_MAX = float(os.getenv("RR_DESTINO_MAX", "3.5"))
RR_VIEJO_MIN = float(os.getenv("RR_ORIGEN_MIN", "2.0"))
RR_VIEJO_MAX = float(os.getenv("RR_ORIGEN_MAX", "3.0"))


def _reescalar(valor: float) -> float:
    """
    Mapea linealmente el rango de origen al destino conservando el orden.
    Un agente que estaba en el extremo alto del rango viejo queda en el
    extremo alto del nuevo: se preserva la diversidad en vez de colapsar
    todos los genes al mismo número.
    """
    if valor <= RR_VIEJO_MIN:
        return RR_MIN
    if valor >= RR_VIEJO_MAX:
        return RR_MAX
    frac = (valor - RR_VIEJO_MIN) / (RR_VIEJO_MAX - RR_VIEJO_MIN)
    return round(RR_MIN + frac * (RR_MAX - RR_MIN), 6)


def main() -> int:
    ap = argparse.ArgumentParser(description="Corrige risk_reward_target inalcanzable")
    ap.add_argument("--confirmar", action="store_true",
                    help="Aplica los cambios. Sin este flag es dry-run.")
    args = ap.parse_args()

    from db.connection import get_conn, get_dict_cursor

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute("SELECT id, especie, params_smc FROM agentes WHERE estado='activo' ORDER BY id")
        agentes = cur.fetchall()

        cambios = []
        for a in agentes:
            smc = a["params_smc"] if isinstance(a["params_smc"], dict) else json.loads(a["params_smc"])
            rr = float(smc.get("risk_reward_target") or 0)
            if RR_MIN <= rr <= RR_MAX:
                continue
            nuevo = _reescalar(rr)
            cambios.append((a["id"], a["especie"], rr, nuevo, smc))

        if not cambios:
            log.info("Ningun agente por encima de R:R %.1f — nada que corregir.", RR_MAX)
            return 0

        log.info("Agentes a corregir: %d de %d activos", len(cambios), len(agentes))
        log.info("  %-15s %-10s %8s -> %8s   %s", "agente", "especie", "R:R", "R:R nuevo", "objetivo (SL 10p)")
        for aid, esp, viejo, nuevo, _ in cambios:
            log.info("  %-15s %-10s %8.2f -> %8.2f   %.0f pips -> %.0f pips",
                     aid, esp, viejo, nuevo, viejo * 10, nuevo * 10)

        if not args.confirmar:
            log.info("")
            log.info("DRY-RUN: no se modifico nada. Repite con --confirmar.")
            return 0

        cur2 = conn.cursor()
        for aid, _esp, _viejo, nuevo, smc in cambios:
            nuevo_smc = dict(smc)
            nuevo_smc["risk_reward_target"] = nuevo
            cur2.execute("UPDATE agentes SET params_smc=%s WHERE id=%s",
                         (json.dumps(nuevo_smc), aid))
        log.info("")
        log.info("CORREGIDOS %d agentes. El proximo ciclo del monitor ya usa el R:R nuevo.",
                 len(cambios))

    return 0


if __name__ == "__main__":
    sys.exit(main())
