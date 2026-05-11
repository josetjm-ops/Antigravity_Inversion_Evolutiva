"""
Runner de trading diario.

Carga todos los agentes activos desde PostgreSQL, ejecuta su pipeline
(Técnico → Macro → Riesgo → Broker Simulado) y registra los resultados.

Diseño:
  - Los 10 agentes comparten el mismo precio de mercado y snapshot macro del
    momento de ejecución para minimizar llamadas a APIs externas.
  - Cada agente aplica sus propios parámetros genéticos (RSI periodo, pesos, etc.)
    sobre esos datos compartidos.
  - Si un agente ya tiene un trade abierto (estado='abierta' en DB), se omite.
  - La ejecución de órdenes y seguimiento de SL/TP la gestiona TradeMonitor
    usando precios reales de Yahoo Finance (broker simulado, sin broker externo).

Nota: Alpha Vantage free tier permite 25 llamadas/día.
  Con 10 agentes × 5 llamadas c/u = 50 llamadas → supera el límite.
  Solución: los datos técnicos se obtienen UNA vez con parámetros por defecto
  y se pasan a todos los agentes. Cada agente aplica sus umbrales sobre los
  mismos valores de indicadores. Para indicadores con períodos muy distintos
  entre agentes considerar el tier premium de Alpha Vantage.

Uso:
  python -m cron.trading_runner --run-now
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("TradingRunner")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_active_agents() -> list[dict]:
    from db.connection import get_conn, get_dict_cursor
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT id, params_tecnicos, params_macro, params_riesgo, params_smc, capital_actual
            FROM agentes
            WHERE estado = 'activo'
            ORDER BY roi_total DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]


def _fetch_shared_market_data():
    """
    Descarga velas OHLCV de Yahoo Finance (1 request) y snapshot macro.
    Retorna (df_ohlcv, MacroSnapshot) para compartir entre agentes.
    Cada agente calculará sus propios indicadores desde el mismo DataFrame.
    """
    from data.indicators import fetch_ohlcv
    from data.macro_scraper import fetch_macro_snapshot

    log.info("[TradingRunner] Descargando velas OHLCV (Yahoo Finance)...")
    df_ohlcv = fetch_ohlcv()

    log.info("[TradingRunner] Obteniendo snapshot macro (scraping)...")
    macro_snapshot = fetch_macro_snapshot(ventana_horas=4)

    log.info(
        "[TradingRunner] OHLCV listo — %d velas · último cierre=%.5f",
        len(df_ohlcv), float(df_ohlcv["close"].iloc[-1]),
    )
    return df_ohlcv, macro_snapshot


# ── Runner principal ──────────────────────────────────────────────────────────

def run_all_agents() -> dict:
    """
    Ejecuta el ciclo de trading para todos los agentes activos.
    Retorna resumen con resultados por agente.
    """
    from agents.investor_agent import InvestorAgent
    from db.connection import health_check

    if not health_check():
        log.error("[TradingRunner] DB health check fallido. Abortando.")
        return {"status": "error", "reason": "DB unavailable"}

    agents_data = _fetch_active_agents()
    log.info("[TradingRunner] %d agentes activos.", len(agents_data))

    if not agents_data:
        log.warning("[TradingRunner] Sin agentes activos. Nada que ejecutar.")
        return {"status": "ok", "total": 0, "results": []}

    # 1 request HTTP para todos; indicadores se calculan por agente en memoria
    try:
        df_ohlcv, macro_snapshot = _fetch_shared_market_data()
    except Exception as exc:
        log.error("[TradingRunner] Error obteniendo datos de mercado: %s", exc)
        return {"status": "error", "reason": str(exc)}

    from data.indicators import calc_signals

    results = []
    started = datetime.now(timezone.utc)

    for agent_data in agents_data:
        agent_id = agent_data["id"]
        try:
            # Indicadores calculados con los parámetros genéticos propios del agente
            tech_signals = calc_signals(
                df_ohlcv,
                agent_data["params_tecnicos"],
                agent_data.get("params_smc"),
            )
            params = {
                "params_tecnicos": agent_data["params_tecnicos"],
                "params_macro":    agent_data["params_macro"],
                "params_riesgo":   agent_data["params_riesgo"],
                "params_smc":      agent_data.get("params_smc"),
                "capital_actual":  float(agent_data["capital_actual"]),
            }
            agent  = InvestorAgent(agent_id, params)
            result = agent.run_cycle(
                tech_signals=tech_signals,
                macro_snapshot=macro_snapshot,
            )
            if result.get("skipped"):
                log.info("[TradingRunner] %s → SKIP (posición abierta)", agent_id)
                results.append({"agent_id": agent_id, "status": "ok", "action": "SKIP"})
                continue

            action = result.get("decision", {}).get("accion_final", "HOLD")
            conf   = result.get("decision", {}).get("confianza_final", 0)
            log.info(
                "[TradingRunner] %s → %s (conf=%.2f)", agent_id, action, conf
            )
            results.append({
                "agent_id":  agent_id,
                "status":    "ok",
                "action":    action,
                "confianza": conf,
            })
        except Exception as exc:
            log.error("[TradingRunner] Error en agente %s: %s", agent_id, exc)
            results.append({"agent_id": agent_id, "status": "error", "error": str(exc)})

    elapsed = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
    ultimo_precio = float(df_ohlcv["close"].iloc[-1])
    log.info(
        "[TradingRunner] Ciclo completado en %.2fs — %d agentes · precio=%.5f",
        elapsed, len(agents_data), ultimo_precio,
    )
    return {
        "status":         "ok",
        "total":          len(agents_data),
        "elapsed_sec":    elapsed,
        "results":        results,
        "precio_mercado": ultimo_precio,
    }


# ── Punto de entrada ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Runner de trading diario — INVERSIÓN EVOLUTIVA"
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        required=True,
        help="Ejecuta el ciclo de trading inmediatamente y termina.",
    )
    parser.parse_args()

    result = run_all_agents()
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
