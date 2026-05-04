"""
Monitor de trades abiertos en OANDA.

Consulta OANDA por cada operación con estado='abierta' y oanda_trade_id registrado.
Cuando OANDA reporta el trade como CLOSED (SL o TP tocado), sincroniza el resultado
en PostgreSQL actualizando la tabla operaciones y el capital del agente.

Estrategia intraday: --force-close-all cierra todas las posiciones abiertas al
final del día (llamado por judge_daily.yml antes del ciclo evolutivo) para que
ningún agente llegue a la evaluación con trades pendientes.

Modos de operación:
  --run-once       : sincroniza trades cerrados una vez y termina (GitHub Actions)
  --force-close-all: cierra TODAS las posiciones abiertas (EOD intraday)
  --daemon         : corre continuamente cada TRADE_MONITOR_POLL_SECONDS segundos

Uso:
  python -m cron.trade_monitor --run-once
  python -m cron.trade_monitor --force-close-all
  python -m cron.trade_monitor --daemon
"""

from __future__ import annotations

import argparse
import json
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


# ── Lógica de sincronización ──────────────────────────────────────────────────

def sync_once() -> dict:
    """
    Revisa todas las operaciones abiertas con oanda_trade_id y sincroniza
    las que OANDA ya cerró (SL o TP alcanzado).
    Retorna resumen: {'checked': N, 'synced': N, 'errors': N}
    """
    from data import oanda_client
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    # 1. Operaciones abiertas en nuestra DB que tienen trade en OANDA
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT o.id, o.oanda_trade_id, o.agente_id, o.capital_usado
            FROM operaciones o
            WHERE o.estado = 'abierta'
              AND o.oanda_trade_id IS NOT NULL
            ORDER BY o.timestamp_entrada ASC
            """
        )
        open_ops = [dict(row) for row in cur.fetchall()]

    if not open_ops:
        log.info("[TradeMonitor] Sin operaciones abiertas con OANDA trade_id.")
        return {"checked": 0, "synced": 0, "errors": 0}

    log.info("[TradeMonitor] Revisando %d operaciones abiertas en OANDA...", len(open_ops))
    synced = errors = 0

    for op in open_ops:
        trade_id = op["oanda_trade_id"]
        agent_id = op["agente_id"]
        op_id    = op["id"]

        try:
            response   = oanda_client.get_trade(trade_id)
            trade_data = response.get("trade", {})
            state      = trade_data.get("state", "OPEN")

            if state != "CLOSED":
                log.debug("[TradeMonitor] Trade %s aún abierto (agent=%s)", trade_id, agent_id)
                continue

            # Trade cerrado por OANDA (SL o TP)
            realized_pl = float(trade_data.get("realizedPL", 0))
            close_price = float(trade_data.get("averageClosePrice", 0))

            # Capital actual del agente (fuente de verdad para actualizar)
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT capital_actual FROM agentes WHERE id = %s",
                    (agent_id,),
                )
                row = cur.fetchone()
                capital_actual = float(row[0]) if row else 10.0

            agent  = InvestorAgent(agent_id, {})
            result = agent.close_operation_from_oanda(
                op_id=op_id,
                oanda_realized_pl=realized_pl,
                close_price=close_price,
                capital_disponible=capital_actual,
            )

            log.info(
                "[TradeMonitor] Sincronizado: op_id=%d agent=%s pnl=%.4f capital=%.4f",
                op_id, agent_id,
                result.get("pnl", 0),
                result.get("nuevo_capital", capital_actual),
            )
            synced += 1

        except Exception as exc:
            log.error(
                "[TradeMonitor] Error procesando op_id=%d trade=%s: %s",
                op_id, trade_id, exc,
            )
            errors += 1

    log.info(
        "[TradeMonitor] Ciclo completado — revisadas=%d sincronizadas=%d errores=%d",
        len(open_ops), synced, errors,
    )
    return {"checked": len(open_ops), "synced": synced, "errors": errors}


# ── Cierre forzado EOD (intraday) ────────────────────────────────────────────

def force_close_all() -> dict:
    """
    Cierra TODAS las posiciones abiertas en OANDA a precio de mercado.
    Se llama al final del día (16:45 Bogotá) antes de que el Juez evalúe,
    garantizando que la estrategia sea completamente intraday.

    Para cada trade cerrado manualmente, sincroniza el P&L real de OANDA
    con nuestra base de datos igual que sync_once().
    """
    from data import oanda_client
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    log.info("[TradeMonitor] EOD — Cerrando todas las posiciones abiertas (intraday)...")

    # Operaciones abiertas con trade_id en OANDA
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT o.id, o.oanda_trade_id, o.agente_id, o.capital_usado
            FROM operaciones o
            WHERE o.estado = 'abierta'
              AND o.oanda_trade_id IS NOT NULL
            """
        )
        open_ops = [dict(row) for row in cur.fetchall()]

    # Operaciones abiertas sin trade OANDA (se marcan canceladas)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE operaciones
            SET estado = 'cancelada'
            WHERE estado = 'abierta'
              AND oanda_trade_id IS NULL
            """
        )
        orphaned = cur.rowcount
        if orphaned:
            log.info("[TradeMonitor] EOD — %d operaciones sin trade_id marcadas canceladas.", orphaned)

    if not open_ops:
        log.info("[TradeMonitor] EOD — Sin posiciones abiertas en OANDA.")
        return {"closed": 0, "errors": 0}

    closed = errors = 0

    for op in open_ops:
        trade_id = op["oanda_trade_id"]
        agent_id = op["agente_id"]
        op_id    = op["id"]

        try:
            # Verificar estado antes de intentar cerrar
            response   = oanda_client.get_trade(trade_id)
            trade_data = response.get("trade", {})
            state      = trade_data.get("state", "OPEN")

            if state == "CLOSED":
                # Ya cerrado por SL/TP, solo sincronizar
                realized_pl = float(trade_data.get("realizedPL", 0))
                close_price = float(trade_data.get("averageClosePrice", 0))
            else:
                # Cerrar manualmente a precio de mercado
                result      = oanda_client.close_trade(trade_id)
                realized_pl = result["realized_pl"]
                close_price = result["close_price"]
                log.info(
                    "[TradeMonitor] EOD — Trade %s cerrado manualmente: pl=%.4f precio=%.5f",
                    trade_id, realized_pl, close_price,
                )

            # Sincronizar en DB
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT capital_actual FROM agentes WHERE id = %s", (agent_id,)
                )
                row = cur.fetchone()
                capital_actual = float(row[0]) if row else 10.0

            agent = InvestorAgent(agent_id, {})
            agent.close_operation_from_oanda(
                op_id=op_id,
                oanda_realized_pl=realized_pl,
                close_price=close_price,
                capital_disponible=capital_actual,
            )
            closed += 1

        except Exception as exc:
            log.error(
                "[TradeMonitor] EOD — Error cerrando op_id=%d trade=%s: %s",
                op_id, trade_id, exc,
            )
            errors += 1

    log.info(
        "[TradeMonitor] EOD completado — cerradas=%d errores=%d", closed, errors
    )
    return {"closed": closed, "errors": errors}


# ── Modos de ejecución ────────────────────────────────────────────────────────

def run_daemon() -> None:
    """Ejecuta sync_once() en bucle cada _POLL_SECONDS segundos."""
    log.info(
        "[TradeMonitor] Modo demonio — polling cada %d segundos.", _POLL_SECONDS
    )
    while True:
        try:
            sync_once()
        except Exception as exc:
            log.error("[TradeMonitor] Error inesperado en ciclo: %s", exc)
        time.sleep(_POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor de trades OANDA — INVERSIÓN EVOLUTIVA"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-once",
        action="store_true",
        help="Sincroniza trades cerrados una vez y termina (GitHub Actions).",
    )
    group.add_argument(
        "--force-close-all",
        action="store_true",
        help="Cierra TODAS las posiciones abiertas (EOD intraday, antes del Juez).",
    )
    group.add_argument(
        "--daemon",
        action="store_true",
        help=f"Bucle continuo cada {_POLL_SECONDS}s (Railway worker).",
    )
    args = parser.parse_args()

    if args.run_once:
        log.info("[TradeMonitor] Modo: one-shot")
        result = sync_once()
        sys.exit(0 if result["errors"] == 0 else 1)
    elif args.force_close_all:
        log.info("[TradeMonitor] Modo: force-close-all (EOD intraday)")
        result = force_close_all()
        sys.exit(0 if result["errors"] == 0 else 1)
    else:
        run_daemon()


if __name__ == "__main__":
    main()
