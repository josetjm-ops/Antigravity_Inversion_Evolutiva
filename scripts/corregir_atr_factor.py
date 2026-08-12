"""
Corrección de atr_factor hacia el óptimo medido — 2026-08-12.

QUÉ SE MIDIÓ
------------
Con el backtester ya honesto (cierre EOD + trailing modelados) se barrió
atr_factor sobre 6 meses de holdout, con todo lo demás fijo:

    atr_factor 1.0 -> +0.156R   <- valor que dejó la migración del 10-ago
    atr_factor 1.5 -> +0.290R   <- óptimo
    atr_factor 1.8 -> +0.276R
    atr_factor 2.2 -> +0.164R
    atr_factor 2.6 -> +0.145R
    atr_factor 3.0 -> +0.206R   (ya es ruido: fuera del rango permitido)

La migración propagó el atr_factor ~1.0 del campeón, que resultó ser el PEOR
valor del rango permitido. Este script mueve la población viva a la zona
1.4-1.8, sin tocar ningún otro gen.

MATIZ IMPORTANTE (corrige una afirmación previa)
-------------------------------------------------
En un análisis anterior se dijo que atr_factor era un "gen muerto" porque el
100% de las operaciones tenían stop de exactamente 10 pips (el piso
_MIN_SL_PIPS). Esa medición se tomó sobre 2 días de mercado muy tranquilo. Con
el ATR de 15m del EUR/USD en 5.9 pips de mediana pero 11.2 en el percentil 90,
el gen SÍ manda cuando la volatilidad sube — que es justo cuando el tamaño del
stop más importa. La afirmación correcta es que el gen es inerte en calma y
decisivo en volatilidad, no que esté muerto. Por eso el rango de _BOUNDS_SMC
(0.8-1.8) NO se ensancha: el óptimo ya cae dentro.

Uso:
    python -m scripts.corregir_atr_factor
    python -m scripts.corregir_atr_factor --confirmar
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
log = logging.getLogger("CorregirATR")

# Zona destino: centrada en el óptimo medido (1.5) sin salirse del techo
# permitido por _BOUNDS_SMC (1.8).
ATR_MIN = float(os.getenv("ATR_DESTINO_MIN", "1.40"))
ATR_MAX = float(os.getenv("ATR_DESTINO_MAX", "1.80"))


def _reescalar(valor: float, origen_min: float, origen_max: float) -> float:
    """
    Mapea el rango observado al destino conservando el orden relativo, para no
    colapsar toda la población al mismo número y destruir la poca diversidad
    genética que queda tras la migración.
    """
    if origen_max <= origen_min:
        return round((ATR_MIN + ATR_MAX) / 2, 6)
    frac = (valor - origen_min) / (origen_max - origen_min)
    frac = max(0.0, min(1.0, frac))
    return round(ATR_MIN + frac * (ATR_MAX - ATR_MIN), 6)


def main() -> int:
    ap = argparse.ArgumentParser(description="Mueve atr_factor al optimo medido")
    ap.add_argument("--confirmar", action="store_true",
                    help="Aplica los cambios. Sin este flag es dry-run.")
    args = ap.parse_args()

    from db.connection import get_conn, get_dict_cursor

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute("SELECT id, especie, params_smc FROM agentes WHERE estado='activo' ORDER BY id")
        agentes = cur.fetchall()

        vivos = []
        for a in agentes:
            smc = a["params_smc"] if isinstance(a["params_smc"], dict) else json.loads(a["params_smc"])
            vivos.append((a["id"], a["especie"], float(smc.get("atr_factor") or 0), smc))

        fuera = [v for v in vivos if not (ATR_MIN <= v[2] <= ATR_MAX)]
        if not fuera:
            log.info("Todos los agentes ya estan en [%.2f, %.2f] — nada que corregir.",
                     ATR_MIN, ATR_MAX)
            return 0

        o_min = min(v[2] for v in fuera)
        o_max = max(v[2] for v in fuera)

        log.info("Agentes fuera de la zona optima: %d de %d", len(fuera), len(vivos))
        log.info("Rango observado: %.3f - %.3f  ->  destino %.2f - %.2f",
                 o_min, o_max, ATR_MIN, ATR_MAX)
        log.info("  %-15s %-10s %8s -> %8s", "agente", "especie", "atr", "atr nuevo")

        cambios = []
        for aid, esp, viejo, smc in fuera:
            nuevo = _reescalar(viejo, o_min, o_max)
            cambios.append((aid, nuevo, smc))
            log.info("  %-15s %-10s %8.3f -> %8.3f", aid, esp, viejo, nuevo)

        if not args.confirmar:
            log.info("")
            log.info("DRY-RUN: no se modifico nada. Repite con --confirmar.")
            return 0

        cur2 = conn.cursor()
        for aid, nuevo, smc in cambios:
            nuevo_smc = dict(smc)
            nuevo_smc["atr_factor"] = nuevo
            cur2.execute("UPDATE agentes SET params_smc=%s WHERE id=%s",
                         (json.dumps(nuevo_smc), aid))

        log.info("")
        log.info("CORREGIDOS %d agentes. Las posiciones ABIERTAS conservan su SL "
                 "original (se fijo a la entrada); el gen nuevo aplica desde la "
                 "proxima operacion.", len(cambios))

    return 0


if __name__ == "__main__":
    sys.exit(main())
