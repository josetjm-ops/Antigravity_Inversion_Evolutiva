"""
Descarga masiva de históricos EUR/USD desde Dukascopy — Fase B.

Reanudable: los días ya cacheados se saltan, así que puede interrumpirse
(Ctrl-C, cierre de sesión, corte de red) y relanzarse sin perder trabajo.

Uso:
    python -m scripts.descargar_historico --desde 2024-01-01 --hasta 2026-06-30
    python -m scripts.descargar_historico --anios 2       # atajo: últimos N años
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data import dukascopy_client as dk

# La consola de Windows usa cp1252: forzar UTF-8 evita UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Descarga histórica Dukascopy (reanudable)")
    ap.add_argument("--simbolo", default="EURUSD")
    ap.add_argument("--desde", type=date.fromisoformat)
    ap.add_argument("--hasta", type=date.fromisoformat)
    ap.add_argument("--anios", type=int, help="Atajo: últimos N años hasta hoy")
    args = ap.parse_args()

    hasta = args.hasta or date.today()
    if args.anios:
        desde = hasta - timedelta(days=365 * args.anios)
    elif args.desde:
        desde = args.desde
    else:
        ap.error("Indica --desde/--hasta o --anios")

    habiles = sum(
        1 for i in range((hasta - desde).days + 1)
        if (desde + timedelta(days=i)).weekday() < 5
    )
    print(f"[Descarga] {args.simbolo} {desde} -> {hasta} ({habiles} dias habiles)")
    print(f"[Descarga] Caché: {dk.CACHE_DIR}")
    t0 = time.time()

    def progreso(dia, n_velas, resumen):
        hechos = resumen["dias_nuevos"] + resumen["dias_cacheados"]
        transcurrido = time.time() - t0
        ritmo = hechos / transcurrido if transcurrido > 0 else 0
        faltan = (habiles - hechos) / ritmo / 60 if ritmo > 0 else 0
        print(
            f"  {dia} | {n_velas:>5} velas | {hechos}/{habiles} "
            f"(nuevos={resumen['dias_nuevos']} cache={resumen['dias_cacheados']} "
            f"vacios={resumen['dias_vacios']}) ETA {faltan:.0f} min",
            flush=True,
        )

    resumen = dk.descargar_rango(args.simbolo, desde, hasta, on_progress=progreso)

    mins = (time.time() - t0) / 60
    print(f"\n[Descarga] LISTO en {mins:.1f} min")
    print(f"  dias nuevos={resumen['dias_nuevos']} cacheados={resumen['dias_cacheados']} "
          f"vacios={resumen['dias_vacios']}")
    print(f"  velas totales={resumen['velas']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
