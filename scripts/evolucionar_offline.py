"""
Runner de evolución offline — Fase B del plan de rentabilidad (2026-07-23).

Corre la búsqueda genética sobre años de histórico de Dukascopy y reporta
qué campeones son PROMOVIBLES a producción.

Protocolo anti-overfit (el punto entero del ejercicio):
  1. El histórico se parte en EVOLUCIÓN (todo menos el último tramo) y
     HOLDOUT (el tramo final). La evolución jamás ve el holdout.
  2. Dentro de evolución, el fitness es multi-fold penalizado por varianza.
  3. Un campeón solo se marca PROMOVIBLE si en el holdout cumple las tres:
       - expectancy en R > 0
       - pasa el gate bootstrap (IC 80% inferior > 0)
       - al menos MIN_TRADES_HOLDOUT operaciones (muestra suficiente)

Uso:
    python -m scripts.evolucionar_offline --generaciones 30 --poblacion 40
    python -m scripts.evolucionar_offline --especie reversion --generaciones 50
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("EvolucionOffline")

from data import dukascopy_client as dk
from evolution import offline_evolution as oe

# Un campeón necesita muestra real en holdout para ser creíble.
MIN_TRADES_HOLDOUT = 20
SALIDA_DIR = ROOT / "resultados_offline"


def main() -> int:
    ap = argparse.ArgumentParser(description="Evolución genética offline sobre histórico largo")
    ap.add_argument("--simbolo", default="EURUSD")
    ap.add_argument("--desde", type=date.fromisoformat,
                    help="Inicio del histórico (default: hace 2 años)")
    ap.add_argument("--hasta", type=date.fromisoformat, help="Fin (default: hoy)")
    ap.add_argument("--holdout-dias", type=int, default=120,
                    help="Días finales reservados como holdout intocable")
    ap.add_argument("--especie", choices=list(oe.ESPECIES),
                    help="Evolucionar solo una especie (default: las tres)")
    ap.add_argument("--generaciones", type=int, default=30)
    ap.add_argument("--poblacion", type=int, default=40)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--procesos", type=int, default=None,
                    help="Procesos paralelos (default: nº de núcleos)")
    ap.add_argument("--reanudar", action="store_true",
                    help="Continuar desde el checkpoint de cada especie si existe")
    args = ap.parse_args()

    hasta = args.hasta or date.today()
    desde = args.desde or (hasta - timedelta(days=730))

    log.info("Cargando histórico %s %s → %s ...", args.simbolo, desde, hasta)
    df_15m = dk.cargar_historico(args.simbolo, desde, hasta, regla="15min")
    df_1h = dk.cargar_historico(args.simbolo, desde, hasta, regla="1h")

    if df_15m.empty:
        log.error("Sin datos en caché. Corre primero: python -m scripts.descargar_historico")
        return 1

    velas_por_dia = 96  # 15m × 24h (el FX opera 24h; Dukascopy las cubre todas)
    corte = len(df_15m) - args.holdout_dias * velas_por_dia
    if corte < velas_por_dia * 60:
        log.error("Histórico insuficiente: %d velas. Descarga más días.", len(df_15m))
        return 1

    df_evolucion = df_15m.iloc[:corte].reset_index(drop=True)
    df_holdout = df_15m.iloc[corte:].reset_index(drop=True)

    log.info(
        "Evolución: %d velas (%s → %s) | HOLDOUT intocable: %d velas (%s → %s)",
        len(df_evolucion), df_evolucion["timestamp"].iloc[0].date(),
        df_evolucion["timestamp"].iloc[-1].date(),
        len(df_holdout), df_holdout["timestamp"].iloc[0].date(),
        df_holdout["timestamp"].iloc[-1].date(),
    )

    # HTF fijo, calculado sobre el tramo de evolución (nunca del holdout:
    # sería fuga de información del futuro hacia la búsqueda).
    htf_trend = _htf_de(df_1h, hasta_fecha=df_evolucion["timestamp"].iloc[-1])

    especies = [args.especie] if args.especie else list(oe.ESPECIES)
    SALIDA_DIR.mkdir(exist_ok=True)
    informe: dict = {"generado": date.today().isoformat(), "campeones": {}}

    for especie in especies:
        log.info("=" * 70)
        log.info("EVOLUCIONANDO especie: %s", especie)
        log.info("=" * 70)

        poblacion = oe.evolucionar(
            df_evolucion, htf_trend, especie,
            generaciones=args.generaciones, tam_poblacion=args.poblacion,
            n_folds=args.folds, semilla=args.semilla, velas_por_dia=velas_por_dia,
            procesos=args.procesos,
            reanudar=args.reanudar,
            checkpoint=SALIDA_DIR / f"checkpoint_{especie}.json",
        )

        log.info("Validando los 5 mejores de %s contra el HOLDOUT...", especie)
        campeones = []
        for i, genoma in enumerate(poblacion[:5], 1):
            hold = oe.evaluar_holdout(genoma, df_holdout, htf_trend,
                                      velas_por_dia=velas_por_dia)

            # "holdout_insuficiente" NO es un veredicto sobre el genoma: es un
            # problema técnico (el tramo de holdout es más corto que el warmup).
            # Distinguirlo evita leer un fallo de configuración como "sin edge"
            # y, peor, como señal para cerrar el proyecto.
            insuficiente = hold.get("motivo") == "holdout_insuficiente"
            promovible = (
                not insuficiente
                and hold["n_trades"] >= MIN_TRADES_HOLDOUT
                and hold.get("expectancy_R", 0) > 0
                and hold.get("pasa_bootstrap", False)
            )
            etiqueta = ("HOLDOUT INSUFICIENTE (sube --holdout-dias)" if insuficiente
                        else "PROMOVIBLE" if promovible else "descartado")
            log.info(
                "  #%d evo_fitness=%+.4f | HOLDOUT: exp=%+.4fR n=%d WR=%.0f%% boot=%s → %s",
                i, genoma["_eval"]["fitness"], hold.get("expectancy_R", 0),
                hold["n_trades"], hold.get("win_rate", 0) * 100,
                "SI" if hold.get("pasa_bootstrap") else "no",
                etiqueta,
            )
            campeones.append({
                "rank": i,
                "evolucion": {k: v for k, v in genoma["_eval"].items() if k != "oos_trades"},
                "holdout": hold,
                "promovible": promovible,
                "genoma": {k: v for k, v in genoma.items() if k != "_eval"},
            })

        informe["campeones"][especie] = campeones

    ruta = SALIDA_DIR / f"campeones_{date.today().isoformat()}.json"
    ruta.write_text(json.dumps(informe, indent=2, default=str), encoding="utf-8")

    total = sum(1 for esp in informe["campeones"].values()
                for c in esp if c["promovible"])
    # Si TODO el holdout fue insuficiente, el resultado no dice nada sobre el
    # edge — es un problema de configuración, no un veredicto.
    todos = [c for esp in informe["campeones"].values() for c in esp]
    todo_insuficiente = todos and all(
        c["holdout"].get("motivo") == "holdout_insuficiente" for c in todos)

    log.info("=" * 70)
    if todo_insuficiente:
        log.warning("HOLDOUT INSUFICIENTE en todas las especies — sin veredicto.")
        log.warning("  El tramo de holdout es más corto que el warmup de indicadores.")
        log.warning("  Re-ejecuta con --holdout-dias mayor (recomendado: 90-120).")
        return 0

    log.info("RESULTADO: %d campeones PROMOVIBLES (pasaron el holdout)", total)
    log.info("Informe: %s", ruta)
    if total == 0:
        log.info("Ningún genoma superó el holdout — resultado honesto y valioso:")
        log.info("  significa que la búsqueda no encontró edge real, no que falló el proceso.")
    return 0


def _htf_de(df_1h, hasta_fecha):
    """Tendencia HTF calculada solo con datos anteriores al corte."""
    from data.indicators import calc_htf_trend_series
    try:
        recorte = df_1h[df_1h["timestamp"] <= hasta_fecha]
        serie = calc_htf_trend_series(recorte if not recorte.empty else df_1h)
        ultima = serie.iloc[-1]
        return {
            "direccion": str(ultima["htf_direccion"]),
            "ema_rapida": float(ultima["htf_ema_rapida"]),
            "ema_lenta": float(ultima["htf_ema_lenta"]),
        }
    except Exception as exc:
        log.warning("HTF no disponible (%s) — se usa NEUTRAL.", exc)
        return {"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0}


if __name__ == "__main__":
    sys.exit(main())
