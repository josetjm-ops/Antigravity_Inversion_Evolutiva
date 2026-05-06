"""
Monitor de posiciones abiertas — broker simulado.

Obtiene el precio actual de EUR/USD desde Yahoo Finance (gratuito, sin límite)
y verifica si alguna posición abierta alcanzó su Stop Loss o Take Profit.
Cuando se activa SL o TP, cierra la posición al precio exacto del nivel
y actualiza el P&L y capital del agente en PostgreSQL.

Estrategia intraday: --force-close-all cierra todas las posiciones abiertas
al precio de mercado al final del día (llamado por judge_daily.yml antes del
ciclo evolutivo) para que ningún agente llegue a evaluación con trades pendientes.

Modos de operación:
  --run-once       : verifica SL/TP una vez y termina (GitHub Actions cada 15 min)
  --force-close-all: cierra TODAS las posiciones al precio actual (EOD intraday)
  --daemon         : bucle continuo cada TRADE_MONITOR_POLL_SECONDS segundos

Uso:
  python -m cron.trade_monitor --run-once
  python -m cron.trade_monitor --force-close-all
  python -m cron.trade_monitor --daemon
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("TradeMonitor")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_POLL_SECONDS = int(os.getenv("TRADE_MONITOR_POLL_SECONDS", "60"))


# ── Sincronización SL/TP ──────────────────────────────────────────────────────

def sync_once() -> dict:
    """
    Obtiene el precio actual de EUR/USD y cierra todas las posiciones
    que hayan alcanzado su SL o TP.
    Retorna: {'checked': N, 'synced': N, 'errors': N}
    """
    from data.simulated_broker import get_current_price, check_sl_tp, exit_price_for
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    # 1. Obtener posiciones abiertas con SL/TP definidos
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT
                o.id,
                o.agente_id,
                o.accion,
                o.precio_entrada::float          AS precio_entrada,
                o.capital_usado::float            AS capital_usado,
                (o.decision_riesgo->>'stop_loss')::float   AS stop_loss,
                (o.decision_riesgo->>'take_profit')::float AS take_profit
            FROM operaciones o
            WHERE o.estado = 'abierta'
              AND o.accion IN ('BUY', 'SELL')
              AND o.precio_entrada IS NOT NULL
              AND o.decision_riesgo->>'stop_loss'  IS NOT NULL
              AND o.decision_riesgo->>'take_profit' IS NOT NULL
            ORDER BY o.timestamp_entrada ASC
            """
        )
        open_ops = [dict(row) for row in cur.fetchall()]

    if not open_ops:
        log.info("[TradeMonitor] Sin posiciones abiertas para monitorear.")
        return {"checked": 0, "synced": 0, "errors": 0}

    # 2. Precio actual de mercado (1 sola llamada para todas las posiciones)
    try:
        current_price = get_current_price()
        log.info(
            "[TradeMonitor] EUR/USD = %.5f — revisando %d posiciones abiertas.",
            current_price, len(open_ops),
        )
    except Exception as exc:
        log.error("[TradeMonitor] No se pudo obtener precio de mercado: %s", exc)
        return {"checked": 0, "synced": 0, "errors": 1}

    synced = errors = 0

    for op in open_ops:
        try:
            resultado = check_sl_tp(
                action=op["accion"],
                entry_price=op["precio_entrada"],
                stop_loss=op["stop_loss"],
                take_profit=op["take_profit"],
                current_price=current_price,
            )

            if resultado == "OPEN":
                log.debug(
                    "[TradeMonitor] Op %d (%s) abierta — precio=%.5f SL=%.5f TP=%.5f",
                    op["id"], op["accion"], current_price, op["stop_loss"], op["take_profit"],
                )
                continue

            # Posición cerrada por SL o TP
            precio_salida = exit_price_for(
                resultado, op["stop_loss"], op["take_profit"], current_price
            )

            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT capital_actual FROM agentes WHERE id = %s",
                    (op["agente_id"],),
                )
                row = cur.fetchone()
                capital_actual = float(row[0]) if row else 10.0

            agent  = InvestorAgent(op["agente_id"], {})
            result = agent.close_operation(
                op_id=op["id"],
                precio_salida=precio_salida,
                capital_disponible=capital_actual,
            )

            log.info(
                "[TradeMonitor] Op %d %s → %s: salida=%.5f pnl=%.4f capital=%.4f",
                op["id"], op["accion"], resultado,
                precio_salida, result.get("pnl", 0), result.get("nuevo_capital", 0),
            )
            synced += 1

        except Exception as exc:
            log.error("[TradeMonitor] Error procesando op %d: %s", op["id"], exc)
            errors += 1

    log.info(
        "[TradeMonitor] Ciclo completado — revisadas=%d cerradas=%d errores=%d",
        len(open_ops), synced, errors,
    )
    return {"checked": len(open_ops), "synced": synced, "errors": errors}


# ── Cierre forzado EOD (intraday) ─────────────────────────────────────────────

def force_close_all() -> dict:
    """
    Cierra TODAS las posiciones abiertas al precio actual de mercado.
    Se llama antes del ciclo evolutivo del Juez (16:45 Bogotá) para garantizar
    que todos los agentes tengan sus resultados del día completamente liquidados.
    """
    from data.simulated_broker import get_current_price
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    log.info("[TradeMonitor] EOD — Iniciando cierre intraday de todas las posiciones...")

    # Precio de cierre EOD
    try:
        current_price = get_current_price()
        log.info("[TradeMonitor] EOD — Precio de cierre: %.5f", current_price)
    except Exception as exc:
        log.error("[TradeMonitor] EOD — No se pudo obtener precio: %s", exc)
        return {"closed": 0, "errors": 1}

    # Posiciones abiertas con BUY/SELL y precio de entrada
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT o.id, o.agente_id, o.accion,
                   o.precio_entrada::float AS precio_entrada,
                   o.capital_usado::float  AS capital_usado
            FROM operaciones o
            WHERE o.estado = 'abierta'
              AND o.accion IN ('BUY', 'SELL')
            """
        )
        open_ops = [dict(row) for row in cur.fetchall()]

    # Marcar como canceladas las operaciones sin precio de entrada (HOLD residuales)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE operaciones
            SET estado = 'cancelada'
            WHERE estado = 'abierta'
              AND (accion = 'HOLD' OR precio_entrada IS NULL)
            """
        )
        orphaned = cur.rowcount

    if orphaned:
        log.info("[TradeMonitor] EOD — %d operaciones sin precio marcadas canceladas.", orphaned)

    if not open_ops:
        log.info("[TradeMonitor] EOD — Sin posiciones abiertas para cerrar.")
        return {"closed": 0, "errors": 0}

    closed = errors = 0

    for op in open_ops:
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT capital_actual FROM agentes WHERE id = %s",
                    (op["agente_id"],),
                )
                row        = cur.fetchone()
                capital_actual = float(row[0]) if row else 10.0

            agent  = InvestorAgent(op["agente_id"], {})
            result = agent.close_operation(
                op_id=op["id"],
                precio_salida=current_price,
                capital_disponible=capital_actual,
            )
            log.info(
                "[TradeMonitor] EOD — Op %d cerrada: accion=%s pnl=%.4f",
                op["id"], op["accion"], result.get("pnl", 0),
            )
            closed += 1

        except Exception as exc:
            log.error("[TradeMonitor] EOD — Error cerrando op %d: %s", op["id"], exc)
            errors += 1

    log.info(
        "[TradeMonitor] EOD completado — cerradas=%d errores=%d", closed, errors
    )
    return {"closed": closed, "errors": errors}


# ── Modo demonio ──────────────────────────────────────────────────────────────

def run_daemon() -> None:
    log.info("[TradeMonitor] Modo demonio — polling cada %ds.", _POLL_SECONDS)
    while True:
        try:
            sync_once()
        except Exception as exc:
            log.error("[TradeMonitor] Error inesperado: %s", exc)
        time.sleep(_POLL_SECONDS)


# ── Punto de entrada ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor de posiciones simuladas — INVERSIÓN EVOLUTIVA"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-once", action="store_true",
        help="Verifica SL/TP una vez y termina (GitHub Actions).",
    )
    group.add_argument(
        "--force-close-all", action="store_true",
        help="Cierra todas las posiciones al precio actual (EOD intraday).",
    )
    group.add_argument(
        "--daemon", action="store_true",
        help=f"Bucle continuo cada {_POLL_SECONDS}s.",
    )
    args = parser.parse_args()

    if args.run_once:
        result = sync_once()
        sys.exit(0 if result["errors"] == 0 else 1)
    elif args.force_close_all:
        result = force_close_all()
        sys.exit(0 if result["errors"] == 0 else 1)
    else:
        run_daemon()


if __name__ == "__main__":
    main()
