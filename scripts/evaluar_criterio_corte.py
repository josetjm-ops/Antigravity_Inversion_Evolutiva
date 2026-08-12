"""
Evaluador del criterio de corte — ventana de validación 2026-08-12 → 2026-09-09.

QUÉ DECIDE
----------
Tras la migración al perfil campeón y los dos arreglos del backtester (cierre
EOD y R:R realista), se abrió una ventana de 4 semanas para decidir si el
sistema tiene edge real en producción o se cierra con conclusión documentada.
Este script calcula el veredicto con los datos del momento; no opina.

POR QUÉ MIDE "DECISIONES", NO "OPERACIONES"
--------------------------------------------
Los 15 agentes están fuertemente correlacionados: entran al mismo precio en el
mismo minuto (medido 2026-08-12: 42 operaciones = 18 eventos de entrada
distintos, factor 2.3). Contar 42 observaciones cuando en realidad hubo 18
decisiones infla la confianza estadística y es exactamente el error que llevó
a intervenir con muestra insuficiente el 12 de agosto.

Por eso:
  - La muestra se mide en EVENTOS de entrada (mismo minuto + misma dirección).
  - El bootstrap remuestrea EVENTOS, no operaciones sueltas.

CRITERIOS (todos deben cumplirse para continuar)
-------------------------------------------------
  1. Muestra    : >= MIN_EVENTOS decisiones independientes
  2. Expectancy : >= MIN_EXPECTANCY_R por operación
  3. Significancia: límite inferior del IC 80% por bootstrap > 0
  4. Capital    : el pool crece respecto al inicio de la ventana

ALARMA TEMPRANA: si el pool cae por debajo de ALARMA_POOL_PCT del inicial, se
recomienda cortar sin esperar a la fecha — no tiene sentido sangrar 4 semanas
para confirmar lo que ya es evidente.

Uso:
    python -m scripts.evaluar_criterio_corte
    python -m scripts.evaluar_criterio_corte --desde 2026-08-12
"""
from __future__ import annotations

import argparse
import os
import random
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

# ── Parámetros de la ventana de validación ──────────────────────────────────
INICIO_VENTANA = os.getenv("VENTANA_INICIO", "2026-08-12")
FIN_VENTANA    = os.getenv("VENTANA_FIN",    "2026-09-09")
POOL_INICIAL   = float(os.getenv("VENTANA_POOL_INICIAL", "15000.0"))

MIN_EVENTOS        = int(os.getenv("CORTE_MIN_EVENTOS", "100"))
MIN_EXPECTANCY_R   = float(os.getenv("CORTE_MIN_EXPECTANCY_R", "0.15"))
BOOTSTRAP_CI       = float(os.getenv("CORTE_BOOTSTRAP_CI", "0.80"))
BOOTSTRAP_ITERS    = int(os.getenv("CORTE_BOOTSTRAP_ITERS", "10000"))
ALARMA_POOL_PCT    = float(os.getenv("CORTE_ALARMA_POOL_PCT", "0.90"))  # -10%


def main() -> int:
    ap = argparse.ArgumentParser(description="Evalua el criterio de corte")
    ap.add_argument("--desde", default=INICIO_VENTANA)
    args = ap.parse_args()

    from db.connection import get_conn, get_dict_cursor

    random.seed(42)  # reproducible: dos corridas el mismo dia dan lo mismo

    with get_conn() as conn:
        cur = get_dict_cursor(conn)

        # Operaciones de la ventana, con su R planificado y su evento de entrada.
        cur.execute(
            """
            SELECT o.pnl,
                   COALESCE(o.capital_usado_original, o.capital_usado)
                     * o.pips_sl * 0.0001 / o.precio_entrada       AS r_usd,
                   date_trunc('minute', o.timestamp_entrada) || '|' || o.accion AS evento
            FROM operaciones o
            WHERE o.estado = 'cerrada'
              AND o.timestamp_salida >= %s
              AND o.pips_sl > 0 AND o.precio_entrada > 0
            """,
            (args.desde,),
        )
        filas = cur.fetchall()

        cur.execute(
            "SELECT COALESCE(SUM(capital_actual),0) AS pool, COUNT(*) AS n "
            "FROM agentes WHERE estado='activo'"
        )
        fila = cur.fetchone()
        pool_actual, n_agentes = float(fila["pool"]), int(fila["n"])

    print("=" * 68)
    print(f"CRITERIO DE CORTE — ventana {args.desde} -> {FIN_VENTANA}")
    print("=" * 68)

    if not filas:
        print("  Sin operaciones cerradas en la ventana todavia.")
        print(f"  Pool: ${pool_actual:,.2f} / {n_agentes} agentes")
        return 0

    # R por operación y agrupación por evento de entrada.
    por_evento: dict[str, list[float]] = {}
    rs_ops: list[float] = []
    for f in filas:
        r_usd = float(f["r_usd"] or 0)
        if not r_usd:
            continue
        r = float(f["pnl"]) / r_usd
        rs_ops.append(r)
        por_evento.setdefault(str(f["evento"]), []).append(r)

    # Cada evento aporta UNA observación: el R medio de las operaciones que lo
    # componen (los agentes que tomaron la misma decisión).
    rs_eventos = [sum(v) / len(v) for v in por_evento.values()]
    n_ops, n_ev = len(rs_ops), len(rs_eventos)
    exp_ops = sum(rs_ops) / n_ops
    exp_ev  = sum(rs_eventos) / n_ev

    # Bootstrap sobre EVENTOS (respeta la correlación entre agentes).
    boots = sorted(
        sum(random.choices(rs_eventos, k=n_ev)) / n_ev
        for _ in range(BOOTSTRAP_ITERS)
    )
    alfa = (1.0 - BOOTSTRAP_CI) / 2.0
    ic_inf = boots[int(alfa * BOOTSTRAP_ITERS)]
    ic_sup = boots[int((1 - alfa) * BOOTSTRAP_ITERS) - 1]
    p_pos = 100.0 * sum(1 for b in boots if b > 0) / len(boots)

    delta_pool = pool_actual - POOL_INICIAL

    print(f"  Operaciones cerradas : {n_ops}")
    print(f"  Decisiones independientes: {n_ev}   (factor correlacion "
          f"{n_ops / n_ev:.1f}x)")
    print(f"  Expectancy por operacion : {exp_ops:+.3f} R")
    print(f"  Expectancy por decision  : {exp_ev:+.3f} R")
    print(f"  IC {BOOTSTRAP_CI:.0%} (sobre decisiones): [{ic_inf:+.3f} , {ic_sup:+.3f}] R")
    print(f"  P(expectancy > 0)        : {p_pos:.1f}%")
    print(f"  Pool: ${pool_actual:,.2f}  (inicio ${POOL_INICIAL:,.2f}, "
          f"{delta_pool:+,.2f} = {100 * delta_pool / POOL_INICIAL:+.2f}%)")
    print()

    # ── Criterios ────────────────────────────────────────────────────────
    checks = [
        (f"Muestra >= {MIN_EVENTOS} decisiones", n_ev >= MIN_EVENTOS,
         f"{n_ev}/{MIN_EVENTOS}"),
        (f"Expectancy >= {MIN_EXPECTANCY_R:+.2f} R", exp_ops >= MIN_EXPECTANCY_R,
         f"{exp_ops:+.3f} R"),
        ("IC inferior > 0 (edge confirmado)", ic_inf > 0, f"{ic_inf:+.3f} R"),
        ("El pool crece", delta_pool > 0, f"{delta_pool:+,.2f} USD"),
    ]
    print("  CRITERIOS")
    for nombre, ok, detalle in checks:
        print(f"    [{'CUMPLE' if ok else '  NO  '}] {nombre:<38} {detalle}")

    cumplidos = sum(1 for _n, ok, _d in checks if ok)
    print()

    # ── Alarma temprana ──────────────────────────────────────────────────
    umbral_alarma = POOL_INICIAL * ALARMA_POOL_PCT
    if pool_actual < umbral_alarma:
        print(f"  *** ALARMA: el pool (${pool_actual:,.2f}) cayo por debajo del "
              f"{100 * ALARMA_POOL_PCT:.0f}% inicial (${umbral_alarma:,.2f}).")
        print("      RECOMENDACION: cortar sin esperar al 2026-09-09.")
        return 2

    hoy = date.today().isoformat()
    if hoy < FIN_VENTANA:
        print(f"  VENTANA ABIERTA (hoy {hoy}, cierra {FIN_VENTANA}) — "
              f"{cumplidos}/4 criterios cumplidos.")
        if n_ev < MIN_EVENTOS:
            print(f"  Aun sin muestra suficiente: NO intervenir todavia. "
                  f"Faltan {MIN_EVENTOS - n_ev} decisiones.")
        return 0

    print("  VENTANA CERRADA — VEREDICTO:")
    if cumplidos == 4:
        print("    CONTINUAR: el edge quedo confirmado en produccion.")
        return 0
    print(f"    CERRAR: solo {cumplidos}/4 criterios. El sistema no demostro "
          f"edge en 4 semanas con la muestra suficiente.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
