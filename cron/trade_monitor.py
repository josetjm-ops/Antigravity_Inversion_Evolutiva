"""
Monitor de posiciones + motor de trading intraday cada 15 minutos.

Dos responsabilidades en cada ciclo:
  1. SL/TP: verifica posiciones abiertas y las cierra si tocaron Stop Loss o Take Profit.
  2. Nuevas posiciones: para agentes sin posición abierta, calcula indicadores frescos
     desde Yahoo Finance y ejecuta el pipeline de inversión. Pueden operar múltiples
     veces al día, de forma secuencial (una posición abierta a la vez por agente).

Horario de apertura de nuevas posiciones (configurable vía env):
  TRADING_START_UTC  : hora UTC en que se permite abrir (default 7 = 2:00 am Bogotá)
  TRADING_CUTOFF_UTC : hora UTC límite para abrir (default 20 = 3pm Bogotá)
  → Las posiciones abiertas se siguen monitoreando fuera de ese horario.
  → El EOD (force-close-all) corre a las 22:00 UTC (5pm Bogotá).

Capital mínimo para operar:
  MIN_CAPITAL_TO_TRADE: default $2.00 (20% del capital inicial de $10).

Modos de operación:
  --run-once       : un ciclo completo (SL/TP + nuevas posiciones) y termina
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
from datetime import datetime, timezone
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

_POLL_SECONDS        = int(os.getenv("TRADE_MONITOR_POLL_SECONDS", "60"))
_MIN_CAPITAL         = float(os.getenv("MIN_CAPITAL_TO_TRADE", "2.0"))
_TRADING_START_UTC   = int(os.getenv("TRADING_START_UTC",   "7"))    # 2:00 am Bogotá (sesión asiática/europea)
_TRADING_CUTOFF_UTC  = int(os.getenv("TRADING_CUTOFF_UTC",  "20"))   # 3:00 pm Bogotá


# Eventos macro críticos que activan la ventana de cuarentena (silencio operacional)
_CRITICAL_KEYWORDS = [
    "Non-Farm", "NFP", "CPI", "GDP", "Unemployment",
    "ECB", "Fed", "FOMC", "Interest Rate", "Inflation",
    "Retail Sales", "PMI",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _within_trading_hours() -> bool:
    """True si la hora UTC actual está dentro del horario permitido para abrir posiciones."""
    now_utc = datetime.now(timezone.utc)
    return _TRADING_START_UTC <= now_utc.hour < _TRADING_CUTOFF_UTC


def _is_critical_event(titulo: str) -> bool:
    """True si el título del evento contiene alguna keyword crítica."""
    titulo_lower = titulo.lower()
    return any(kw.lower() in titulo_lower for kw in _CRITICAL_KEYWORDS)


def _in_macro_quarantine(snapshot, quarantine_min: int) -> tuple[bool, str]:
    """
    True si hay un evento de alto impacto crítico dentro de la ventana de cuarentena.
    Retorna (en_cuarentena, nombre_evento).
    Solo examina eventos con hora_utc definida e impacto = "alto".
    """
    from datetime import timedelta
    if quarantine_min <= 0:
        return False, ""

    now_utc  = datetime.now(timezone.utc)
    window   = timedelta(minutes=quarantine_min)

    for evento in snapshot.eventos:
        if evento.impacto != "alto":
            continue
        if evento.hora_utc is None:
            continue
        if not _is_critical_event(evento.titulo):
            continue
        if abs((evento.hora_utc - now_utc).total_seconds()) <= window.total_seconds():
            return True, evento.titulo

    return False, ""


# ── Trailing Stop ─────────────────────────────────────────────────────────────

def _apply_trailing_stop(op: dict, current_price: float) -> tuple[float, float]:
    """
    Calcula el nuevo SL dinámico y extremo favorable si el trailing está activo.

    El trailing solo se activa cuando el profit supera `trailing_activation_pips`.
    El SL nunca empeora (solo se mueve a favor del trader).
    Retorna (nuevo_sl, nuevo_extremo_favorable).
    """
    activation_pips = op.get("trailing_activation_pips") or 0.0
    sl_actual       = op["stop_loss"]
    precio_entrada  = op["precio_entrada"]
    extremo_actual  = op.get("precio_extremo_favorable") or precio_entrada
    accion          = op["accion"]

    if activation_pips <= 0:
        return sl_actual, extremo_actual

    trailing_dist = (op.get("trailing_distance_pips") or 10.0) * 0.0001

    # Actualizar extremo favorable
    if accion == "BUY":
        nuevo_extremo = max(extremo_actual, current_price)
        profit_pips   = (nuevo_extremo - precio_entrada) * 10_000
    else:
        nuevo_extremo = min(extremo_actual, current_price)
        profit_pips   = (precio_entrada - nuevo_extremo) * 10_000

    if profit_pips < activation_pips:
        return sl_actual, nuevo_extremo

    # Proponer nuevo SL — nunca empeora
    if accion == "BUY":
        sl_propuesto = round(nuevo_extremo - trailing_dist, 5)
        nuevo_sl     = max(sl_actual, sl_propuesto)
    else:
        sl_propuesto = round(nuevo_extremo + trailing_dist, 5)
        nuevo_sl     = min(sl_actual, sl_propuesto)

    return nuevo_sl, nuevo_extremo


# ── 1. Monitoreo SL/TP ───────────────────────────────────────────────────────

def sync_once() -> dict:
    """
    Ciclo completo de 15 minutos:
      a) Verifica SL/TP de posiciones abiertas y las cierra si corresponde.
      b) Para agentes sin posición, evalúa si abrir una nueva (si es horario de trading).
    """
    from data.simulated_broker import get_current_price, check_sl_tp, exit_price_for
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    # ── a) Revisar posiciones abiertas ────────────────────────────────────────
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT
                o.id,
                o.agente_id,
                o.accion,
                o.precio_entrada::float AS precio_entrada,
                o.capital_usado::float  AS capital_usado,
                COALESCE(o.sl_dinamico,
                    (o.decision_riesgo->>'stop_loss')::float)           AS stop_loss,
                (o.decision_riesgo->>'take_profit')::float              AS take_profit,
                COALESCE(o.precio_extremo_favorable,
                    o.precio_entrada)::float                            AS precio_extremo_favorable,
                (o.decision_riesgo->>'trailing_activation_pips')::float AS trailing_activation_pips,
                (o.decision_riesgo->>'trailing_distance_pips')::float   AS trailing_distance_pips
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

    sltp_checked = sltp_synced = sltp_errors = 0

    if open_ops:
        try:
            current_price = get_current_price()
            log.info(
                "[TradeMonitor] EUR/USD = %.5f — revisando %d posiciones abiertas.",
                current_price, len(open_ops),
            )
        except Exception as exc:
            log.error("[TradeMonitor] No se pudo obtener precio: %s", exc)
            current_price = None

        if current_price is not None:
            for op in open_ops:
                try:
                    # Aplicar trailing stop antes de verificar SL/TP
                    nuevo_sl, nuevo_extremo = _apply_trailing_stop(op, current_price)
                    if nuevo_sl != op["stop_loss"] or nuevo_extremo != op.get("precio_extremo_favorable"):
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                """
                                UPDATE operaciones
                                SET sl_dinamico = %s, precio_extremo_favorable = %s
                                WHERE id = %s
                                """,
                                (nuevo_sl, nuevo_extremo, op["id"]),
                            )
                        log.info(
                            "[TradeMonitor] Trailing — Op %d (%s) SL %.5f→%.5f extremo=%.5f",
                            op["id"], op["accion"], op["stop_loss"], nuevo_sl, nuevo_extremo,
                        )
                        op["stop_loss"] = nuevo_sl

                    resultado = check_sl_tp(
                        action=op["accion"],
                        entry_price=op["precio_entrada"],
                        stop_loss=op["stop_loss"],
                        take_profit=op["take_profit"],
                        current_price=current_price,
                    )
                    sltp_checked += 1

                    if resultado == "OPEN":
                        log.debug(
                            "[TradeMonitor] Op %d (%s) abierta — precio=%.5f SL=%.5f TP=%.5f",
                            op["id"], op["accion"], current_price,
                            op["stop_loss"], op["take_profit"],
                        )
                        continue

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
                    sltp_synced += 1

                except Exception as exc:
                    log.error("[TradeMonitor] Error procesando op %d: %s", op["id"], exc)
                    sltp_errors += 1

        log.info(
            "[TradeMonitor] SL/TP — revisadas=%d cerradas=%d errores=%d",
            sltp_checked, sltp_synced, sltp_errors,
        )
    else:
        log.info("[TradeMonitor] Sin posiciones abiertas para monitorear.")

    # ── b) Evaluar nuevas posiciones ──────────────────────────────────────────
    new_result = _evaluate_new_positions()

    return {
        "sltp_checked": sltp_checked,
        "sltp_closed":  sltp_synced,
        "sltp_errors":  sltp_errors,
        "new_evaluated": new_result.get("evaluated", 0),
        "new_opened":    new_result.get("opened", 0),
        "errors":        sltp_errors + new_result.get("errors", 0),
    }


# ── 2. Nuevas posiciones (trading intraday) ───────────────────────────────────

def _evaluate_new_positions() -> dict:
    """
    Para cada agente activo que cumpla las condiciones, evalúa si abrir posición:
      - Sin posición BUY/SELL abierta (secuencial: una a la vez)
      - Capital >= MIN_CAPITAL_TO_TRADE
      - Dentro del horario de trading (TRADING_START_UTC – TRADING_CUTOFF_UTC)

    Descarga 1 DataFrame OHLCV compartido; calcula indicadores individuales
    por agente en memoria usando sus propios parámetros genéticos.
    """
    if not _within_trading_hours():
        now_h = datetime.now(timezone.utc).hour
        log.info(
            "[TradeMonitor] Fuera de horario de trading (%d UTC). "
            "Horario: %d–%d UTC. No se evalúan nuevas posiciones.",
            now_h, _TRADING_START_UTC, _TRADING_CUTOFF_UTC,
        )
        return {"evaluated": 0, "opened": 0, "errors": 0}

    from agents.investor_agent import InvestorAgent
    from data.indicators import fetch_ohlcv, calc_signals
    from data.macro_scraper import fetch_macro_snapshot
    from db.connection import get_conn, get_dict_cursor

    # Agentes sin posición abierta con capital suficiente
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT a.id,
                   a.params_tecnicos,
                   a.params_macro,
                   a.params_riesgo,
                   a.params_smc,
                   a.capital_actual::float AS capital_actual
            FROM agentes a
            WHERE a.estado = 'activo'
              AND a.capital_actual >= %s
              AND NOT EXISTS (
                  SELECT 1 FROM operaciones o
                  WHERE o.agente_id = a.id
                    AND o.estado    = 'abierta'
                    AND o.accion   IN ('BUY', 'SELL')
              )
            ORDER BY a.roi_total DESC
            """,
            (_MIN_CAPITAL,),
        )
        candidates = [dict(row) for row in cur.fetchall()]

    if not candidates:
        log.info("[TradeMonitor] Sin agentes disponibles para nuevas posiciones.")
        return {"evaluated": 0, "opened": 0, "errors": 0}

    log.info(
        "[TradeMonitor] %d agentes candidatos para nueva posición — descargando OHLCV...",
        len(candidates),
    )

    # 1 request HTTP para todos; cada agente calcula sus propios indicadores en memoria
    try:
        df_ohlcv = fetch_ohlcv()
        macro_snapshot = fetch_macro_snapshot(ventana_horas=4)
        log.info(
            "[TradeMonitor] OHLCV listo (%d velas) · último cierre=%.5f",
            len(df_ohlcv), float(df_ohlcv["close"].iloc[-1]),
        )
    except Exception as exc:
        log.error("[TradeMonitor] Error descargando datos de mercado: %s", exc)
        return {"evaluated": 0, "opened": 0, "errors": 1}

    evaluated = opened = errors = 0

    for agent_data in candidates:
        agent_id = agent_data["id"]
        try:
            # Ventana de cuarentena macro — gen propio del agente
            smc_params      = agent_data.get("params_smc") or {}
            quarantine_min  = int(smc_params.get("macro_quarantine_minutes", 60))
            in_q, evento_q  = _in_macro_quarantine(macro_snapshot, quarantine_min)
            if in_q:
                log.info(
                    "[TradeMonitor] %s — QUARANTINE (%dmin) por '%s' — HOLD forzado.",
                    agent_id, quarantine_min, evento_q,
                )
                evaluated += 1
                continue

            # Indicadores con parámetros genéticos propios del agente
            tech_signals = calc_signals(
                df_ohlcv,
                agent_data["params_tecnicos"],
                smc_params or None,
            )

            params = {
                "params_tecnicos": agent_data["params_tecnicos"],
                "params_macro":    agent_data["params_macro"],
                "params_riesgo":   agent_data["params_riesgo"],
                "params_smc":      smc_params or None,
                "capital_actual":  agent_data["capital_actual"],
            }
            agent  = InvestorAgent(agent_id, params)
            result = agent.run_cycle(
                tech_signals=tech_signals,
                macro_snapshot=macro_snapshot,
            )

            if result.get("skipped"):
                log.debug("[TradeMonitor] %s — ciclo omitido (posición ya abierta).", agent_id)
                continue

            action = result.get("decision", {}).get("accion_final", "HOLD")
            conf   = result.get("decision", {}).get("confianza_final", 0)
            log.info("[TradeMonitor] %s → %s (conf=%.2f)", agent_id, action, conf)

            if action in ("BUY", "SELL"):
                opened += 1
            evaluated += 1

        except Exception as exc:
            log.error("[TradeMonitor] Error evaluando agente %s: %s", agent_id, exc)
            errors += 1

    log.info(
        "[TradeMonitor] Nuevas posiciones — evaluados=%d abiertos=%d errores=%d",
        evaluated, opened, errors,
    )
    return {"evaluated": evaluated, "opened": opened, "errors": errors}


# ── 3. Cierre forzado EOD ─────────────────────────────────────────────────────

def force_close_all() -> dict:
    """
    Cierra TODAS las posiciones abiertas al precio actual de mercado.
    Llamado por judge_daily.yml antes del ciclo evolutivo (5:00 pm Bogotá).
    """
    from data.simulated_broker import get_current_price
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    log.info("[TradeMonitor] EOD — Iniciando cierre intraday de todas las posiciones...")

    try:
        current_price = get_current_price()
        log.info("[TradeMonitor] EOD — Precio de cierre: %.5f", current_price)
    except Exception as exc:
        log.error("[TradeMonitor] EOD — No se pudo obtener precio: %s", exc)
        return {"closed": 0, "errors": 1}

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

    closed = errors = 0

    if not open_ops:
        log.info("[TradeMonitor] EOD — Sin posiciones abiertas para cerrar.")
    else:
        for op in open_ops:
            try:
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

        log.info("[TradeMonitor] EOD completado — cerradas=%d errores=%d", closed, errors)

    # Cancelar HOLDs residuales atrapados en 'abierta' (corre DESPUÉS del cierre de BUY/SELL)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE operaciones
            SET estado = 'cancelada'
            WHERE estado = 'abierta' AND accion = 'HOLD'
            """
        )
        orphaned = cur.rowcount
    if orphaned:
        log.info("[TradeMonitor] EOD — %d HOLDs residuales cancelados.", orphaned)

    return {"closed": closed, "errors": errors}


# ── 4. Modo demonio ───────────────────────────────────────────────────────────

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
        description="Monitor de posiciones + trading intraday — INVERSIÓN EVOLUTIVA"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-once", action="store_true",
        help="Verifica SL/TP y evalúa nuevas posiciones (GitHub Actions cada 15 min).",
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
