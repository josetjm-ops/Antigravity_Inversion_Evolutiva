"""
Monitor de posiciones + motor de trading intraday cada 15 minutos.

Dos responsabilidades en cada ciclo:
  1. SL/TP: verifica posiciones abiertas y las cierra si tocaron Stop Loss o Take Profit.
  2. Nuevas posiciones: para agentes sin posición abierta, calcula indicadores frescos
     desde Yahoo Finance y ejecuta el pipeline de inversión. Pueden operar múltiples
     veces al día, de forma secuencial (una posición abierta a la vez por agente).

Horario de apertura de nuevas posiciones (configurable vía env, formato HH:MM en UTC):
  TRADING_START_TIME_UTC  : hora UTC desde la que se permite abrir
                            (default 06:30 = 1:30 am Bogotá)
  TRADING_CUTOFF_TIME_UTC : hora UTC límite para abrir
                            (default 04:00 = 11:00 pm Bogotá — DÍA SIGUIENTE UTC)

  La ventana cruza la medianoche UTC: 06:30 UTC del día N hasta 04:00 UTC
  del día N+1. _within_trading_hours() maneja explícitamente este caso.

  → En la práctica el último monitor en correr es el de las 03:30 UTC
    (10:30 pm Bogotá), porque el cierre forzoso del Juez ocurre a las
    03:45 UTC (10:45 pm Bogotá) y el ciclo evolutivo a las 04:00 UTC
    (11:00 pm Bogotá).
  → Las posiciones abiertas se siguen monitoreando fuera de ese horario
    hasta el cierre forzoso.

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
from datetime import datetime, time as dtime, timezone
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
_MIN_CAPITAL  = float(os.getenv("MIN_CAPITAL_TO_TRADE", "2.0"))


def _parse_hhmm(value: str, fallback: str) -> dtime:
    """Parsea un string 'HH:MM' a datetime.time. Si falla, usa el fallback."""
    raw = (value or fallback).strip()
    try:
        h, m = raw.split(":")
        return dtime(int(h), int(m))
    except (ValueError, AttributeError):
        log.warning("[TradeMonitor] Hora '%s' inválida — usando fallback %s.", raw, fallback)
        h, m = fallback.split(":")
        return dtime(int(h), int(m))


# Ventana de apertura de nuevas posiciones (en UTC).
# Default: 06:30 UTC – 04:00 UTC (siguiente día UTC)
#          = 1:30 am – 11:00 pm Bogotá. Cruza la medianoche UTC.
_TRADING_START_TIME_UTC  = _parse_hhmm(os.getenv("TRADING_START_TIME_UTC"),  "06:30")
_TRADING_CUTOFF_TIME_UTC = _parse_hhmm(os.getenv("TRADING_CUTOFF_TIME_UTC"), "04:00")


# Eventos macro críticos que activan la ventana de cuarentena (silencio operacional)
_CRITICAL_KEYWORDS = [
    "Non-Farm", "NFP", "CPI", "GDP", "Unemployment",
    "ECB", "Fed", "FOMC", "Interest Rate", "Inflation",
    "Retail Sales", "PMI",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _within_trading_hours() -> bool:
    """
    True si la hora UTC actual está dentro del horario permitido para abrir
    nuevas posiciones. Compara con precisión de minutos (HH:MM).

    Soporta ventanas que CRUZAN la medianoche UTC. Por defecto la ventana es
    06:30 UTC – 04:00 UTC del día siguiente (= 1:30 am – 11:00 pm Bogotá),
    así que la rama "cruza medianoche" es la que se evalúa habitualmente.
    """
    now_utc_time = datetime.now(timezone.utc).time()
    if _TRADING_START_TIME_UTC <= _TRADING_CUTOFF_TIME_UTC:
        # Ventana convencional: start < cutoff dentro del mismo día UTC.
        return _TRADING_START_TIME_UTC <= now_utc_time < _TRADING_CUTOFF_TIME_UTC
    # Ventana que cruza la medianoche UTC:
    #   activa si la hora actual >= start (tramo nocturno UTC) o
    #   si la hora actual < cutoff (tramo de madrugada UTC del día siguiente).
    return (now_utc_time >= _TRADING_START_TIME_UTC) or (now_utc_time < _TRADING_CUTOFF_TIME_UTC)


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
    configured_act  = op.get("trailing_activation_pips") or 0.0
    sl_actual       = op["stop_loss"]
    precio_entrada  = op["precio_entrada"]
    extremo_actual  = op.get("precio_extremo_favorable") or precio_entrada
    accion          = op["accion"]

    if configured_act <= 0:
        return sl_actual, extremo_actual

    # Fase 0 — payoff coherente: el trailing nunca se activa antes de +1R de
    # ganancia (R = distancia original del SL). Así un ganador jamás se recorta
    # por debajo de break-even. Si el gen pedía una activación menor que 1R, se
    # eleva a 1R; la distancia se acota para que el profit bloqueado sea > 0.
    r_pips = op.get("pips_sl") or (abs(precio_entrada - sl_actual) * 10_000)
    activation_pips = max(configured_act, r_pips)
    dist_pips = min(op.get("trailing_distance_pips") or 10.0, 0.7 * activation_pips)
    trailing_dist = dist_pips * 0.0001

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


# ── Verificador intra-vela de SL/TP ───────────────────────────────────────────

def _verify_position_intrabar(op: dict, fallback_price: float | None) -> dict:
    """
    Verifica SL/TP de una posición abierta usando OHLC de 1 minuto desde
    `timestamp_ultima_verificacion` hasta ahora. Cierra la operación al
    precio exacto del nivel si alguna vela lo tocó, o avanza el cursor
    de verificación si no hubo hit.

    Si Yahoo no devuelve velas (fin de semana, fallo de API), cae al
    comportamiento legacy: chequea con `fallback_price` (snapshot único)
    para no bloquear el ciclo.

    Convenciones intra-vela:
      1) Por cada vela en orden cronológico, primero se chequea SL/TP con
         el SL ANTES del trailing de esa misma vela.
      2) Si no hubo hit, se aplica trailing usando el extremo favorable
         de la vela (low para SELL, high para BUY) como current_price.
      3) Si una vela toca SL y TP a la vez → SL gana (peor caso, ver
         check_sl_tp_intrabar).

    Devuelve: {"closed": bool, "candles_checked": int, "fallback": bool}.
    """
    from data.simulated_broker import (
        check_sl_tp, check_sl_tp_intrabar, exit_price_for, get_intrabar_candles,
    )
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    op_id        = op["id"]
    accion       = op["accion"]
    take_profit  = float(op["take_profit"])

    since = op.get("timestamp_ultima_verificacion") or op.get("timestamp_entrada")
    candles = get_intrabar_candles(since=since) if since is not None else []

    # ── Fallback: sin velas OHLC, usar snapshot legacy ────────────────────────
    if not candles:
        if fallback_price is None:
            log.debug("[TradeMonitor] Op %d sin velas y sin snapshot — skip.", op_id)
            return {"closed": False, "candles_checked": 0, "fallback": True}

        # Aplicar trailing una vez con el snapshot
        nuevo_sl, nuevo_extremo = _apply_trailing_stop(op, fallback_price)
        _persist_trailing(op, nuevo_sl, nuevo_extremo, since_ts=None)
        op["stop_loss"] = nuevo_sl
        op["precio_extremo_favorable"] = nuevo_extremo

        resultado = check_sl_tp(
            action=accion,
            entry_price=op["precio_entrada"],
            stop_loss=op["stop_loss"],
            take_profit=take_profit,
            current_price=fallback_price,
        )
        if resultado == "OPEN":
            return {"closed": False, "candles_checked": 0, "fallback": True}

        precio_salida = exit_price_for(
            resultado, op["stop_loss"], take_profit, fallback_price
        )
        _close_op(op, precio_salida, ts_salida=None, resultado=resultado)
        return {"closed": True, "candles_checked": 0, "fallback": True}

    # ── Camino normal: iterar velas 1m en orden cronológico ───────────────────
    extremo_actualizado_alguna_vez = False
    last_candle_ts = None

    for candle in candles:
        last_candle_ts = candle["timestamp"]

        # (a) Chequear SL/TP primero con el SL pre-trailing de esta vela
        resultado = check_sl_tp_intrabar(
            action=accion,
            stop_loss=op["stop_loss"],
            take_profit=take_profit,
            candle=candle,
        )
        if resultado != "OPEN":
            precio_salida = exit_price_for(
                resultado, op["stop_loss"], take_profit, float(candle["close"])
            )
            # Persistir SL/extremo si cambió durante este loop antes del hit
            if extremo_actualizado_alguna_vez:
                _persist_trailing(
                    op, op["stop_loss"], op["precio_extremo_favorable"],
                    since_ts=last_candle_ts,
                )
            _close_op(op, precio_salida, ts_salida=last_candle_ts, resultado=resultado)
            log.info(
                "[TradeMonitor] Op %d %s INTRABAR → %s en vela %s: salida=%.5f",
                op_id, accion, resultado, last_candle_ts.isoformat(), precio_salida,
            )
            return {
                "closed": True,
                "candles_checked": candles.index(candle) + 1,
                "fallback": False,
            }

        # (b) Sin hit en esta vela → aplicar trailing con el extremo favorable
        favorable_extreme = float(candle["low"]) if accion == "SELL" else float(candle["high"])
        nuevo_sl, nuevo_extremo = _apply_trailing_stop(op, favorable_extreme)
        if nuevo_sl != op["stop_loss"] or nuevo_extremo != op.get("precio_extremo_favorable"):
            extremo_actualizado_alguna_vez = True
            op["stop_loss"] = nuevo_sl
            op["precio_extremo_favorable"] = nuevo_extremo

    # Sin cierre: persistir SL/extremo final + avanzar cursor de verificación
    _persist_trailing(
        op, op["stop_loss"], op["precio_extremo_favorable"], since_ts=last_candle_ts,
    )
    log.debug(
        "[TradeMonitor] Op %d intra-vela OK — %d velas procesadas, SL=%.5f extremo=%.5f",
        op_id, len(candles), op["stop_loss"], op["precio_extremo_favorable"],
    )
    return {"closed": False, "candles_checked": len(candles), "fallback": False}


def _persist_trailing(op: dict, sl: float, extremo: float, since_ts) -> None:
    """
    Persiste sl_dinamico, precio_extremo_favorable y opcionalmente
    timestamp_ultima_verificacion para la operación.
    """
    from db.connection import get_conn

    if since_ts is not None:
        sql = """
            UPDATE operaciones
            SET sl_dinamico = %s,
                precio_extremo_favorable = %s,
                timestamp_ultima_verificacion = %s
            WHERE id = %s
        """
        params = (sl, extremo, since_ts, op["id"])
    else:
        sql = """
            UPDATE operaciones
            SET sl_dinamico = %s,
                precio_extremo_favorable = %s
            WHERE id = %s
        """
        params = (sl, extremo, op["id"])

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)


def _close_op(op: dict, precio_salida: float, ts_salida, resultado: str) -> None:
    """
    Cierra la operación reusando InvestorAgent.close_operation y propaga
    el timestamp_salida real cuando proviene del verificador intra-vela.
    """
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT capital_actual FROM agentes WHERE id = %s",
            (op["agente_id"],),
        )
        row = cur.fetchone()
        capital_actual = float(row["capital_actual"]) if row else 10.0

    agent = InvestorAgent(op["agente_id"], {})
    result = agent.close_operation(
        op_id=op["id"],
        precio_salida=precio_salida,
        capital_disponible=capital_actual,
        timestamp_salida=ts_salida,
    )
    log.info(
        "[TradeMonitor] Op %d %s → %s: salida=%.5f pnl=%.4f capital=%.4f",
        op["id"], op["accion"], resultado,
        precio_salida, result.get("pnl", 0), result.get("nuevo_capital", 0),
    )


# ── 1. Guardia EOD (red de seguridad ante retrasos del judge_daily) ───────────

def _eod_guard() -> None:
    """
    Red de seguridad EOD: detecta posiciones del día anterior que no fueron
    cerradas por judge_daily.yml (GitHub Actions puede retrasarse horas) y
    las cierra al precio actual antes del ciclo normal de SL/TP.

    Lógica: si hay posiciones con timestamp_entrada ANTERIOR al inicio del
    día de trading UTC de hoy (_TRADING_START_TIME_UTC), significa que el
    force-close-all programado a las 03:45 UTC no corrió a tiempo.
    """
    from db.connection import get_conn, get_dict_cursor

    now_utc = datetime.now(timezone.utc)
    today_trading_start = now_utc.replace(
        hour=_TRADING_START_TIME_UTC.hour,
        minute=_TRADING_START_TIME_UTC.minute,
        second=0,
        microsecond=0,
    )

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM operaciones
            WHERE estado = 'abierta'
              AND accion IN ('BUY', 'SELL')
              AND timestamp_entrada < %s
            """,
            (today_trading_start,),
        )
        n_stale = int((cur.fetchone() or {}).get("n") or 0)

    if n_stale > 0:
        log.warning(
            "[TradeMonitor] EOD GUARD: %d posicion(es) del dia anterior sin cerrar "
            "(judge_daily demorado). Ejecutando cierre forzoso de emergencia...",
            n_stale,
        )
        force_close_all()
    else:
        log.debug("[TradeMonitor] EOD GUARD: sin posiciones huerfanas. OK.")


# ── 2. Monitoreo SL/TP ───────────────────────────────────────────────────────

def sync_once() -> dict:
    """
    Ciclo completo de 15 minutos:
      a) Guardia EOD: cierra posiciones del día anterior si judge_daily se retrasó.
      b) Verifica SL/TP de posiciones abiertas y las cierra si corresponde.
      c) Para agentes sin posición, evalúa si abrir una nueva (si es horario de trading).
    """
    # Red de seguridad: cierra posiciones huérfanas del día anterior si el
    # force-close-all del juez no corrió a tiempo (falla común en GH Actions).
    _eod_guard()

    from data.simulated_broker import (
        get_current_price, check_sl_tp, exit_price_for,
        check_sl_tp_intrabar, get_intrabar_candles,
    )
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
                o.timestamp_entrada,
                o.timestamp_ultima_verificacion,
                o.precio_entrada::float AS precio_entrada,
                o.capital_usado::float  AS capital_usado,
                o.pips_sl::float        AS pips_sl,
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
        # Snapshot único para fallback si no hay velas OHLC disponibles
        try:
            current_price = get_current_price()
            log.info(
                "[TradeMonitor] EUR/USD = %.5f — revisando %d posiciones abiertas (intra-vela 1m).",
                current_price, len(open_ops),
            )
        except Exception as exc:
            log.error("[TradeMonitor] No se pudo obtener precio snapshot: %s", exc)
            current_price = None

        for op in open_ops:
            try:
                result_intrabar = _verify_position_intrabar(op, current_price)
                sltp_checked += 1
                if result_intrabar.get("closed"):
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


# ── 3. Nuevas posiciones (trading intraday) ───────────────────────────────────

def _evaluate_new_positions() -> dict:
    """
    Para cada agente activo que cumpla las condiciones, evalúa si abrir posición:
      - Sin posición BUY/SELL abierta (secuencial: una a la vez)
      - Capital >= MIN_CAPITAL_TO_TRADE
      - Dentro del horario de trading (TRADING_START_TIME_UTC – TRADING_CUTOFF_TIME_UTC)

    Descarga 1 DataFrame OHLCV compartido; calcula indicadores individuales
    por agente en memoria usando sus propios parámetros genéticos.
    """
    if not _within_trading_hours():
        now_hhmm = datetime.now(timezone.utc).strftime("%H:%M")
        log.info(
            "[TradeMonitor] Fuera de horario de trading (%s UTC). "
            "Horario: %s–%s UTC (1:30 am – 11:00 pm Bogotá). "
            "No se evalúan nuevas posiciones.",
            now_hhmm,
            _TRADING_START_TIME_UTC.strftime("%H:%M"),
            _TRADING_CUTOFF_TIME_UTC.strftime("%H:%M"),
        )
        return {"evaluated": 0, "opened": 0, "errors": 0}

    from agents.investor_agent import InvestorAgent
    from data.indicators import fetch_ohlcv, calc_signals, fetch_htf_trend
    from data.macro_scraper import fetch_macro_snapshot
    from db.connection import get_conn, get_dict_cursor

    # Agentes sin posición abierta con capital suficiente
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT a.id,
                   a.generacion,
                   a.especie,
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
        from data.indicators import calc_regime
        df_ohlcv = fetch_ohlcv()
        htf_trend = fetch_htf_trend()      # sesgo 1h compartido entre todos los agentes
        macro_snapshot = fetch_macro_snapshot(ventana_horas=4)
        regime = calc_regime(df_ohlcv)     # régimen compartido: TENDENCIA / RANGO / NEUTRAL
        log.info(
            "[TradeMonitor] OHLCV listo (%d velas) · último cierre=%.5f · HTF=%s · ADX=%.1f → %s",
            len(df_ohlcv), float(df_ohlcv["close"].iloc[-1]),
            htf_trend["direccion"], regime["adx"], regime["estado"],
        )
    except Exception as exc:
        log.error("[TradeMonitor] Error descargando datos de mercado: %s", exc)
        return {"evaluated": 0, "opened": 0, "errors": 1}

    evaluated = opened = errors = 0

    for agent_data in candidates:
        agent_id = agent_data["id"]
        try:
            especie = str(agent_data.get("especie") or "tendencia")

            # ── Gate de régimen (Fase 2) ──────────────────────────────────────
            # S1 tendencia : sólo opera en mercados con tendencia (ADX alto)
            # S2 reversion : sólo opera en mercados en rango (ADX bajo)
            # S3 ruptura   : opera en ambos regímenes (busca la explosión)
            # NEUTRAL : cualquier especie puede operar (régimen indefinido)
            regime_estado = regime["estado"]
            bloqueado_por_regimen = False
            if regime_estado != "NEUTRAL":
                if especie == "tendencia" and regime_estado == "RANGO":
                    bloqueado_por_regimen = True
                elif especie == "reversion" and regime_estado == "TENDENCIA":
                    bloqueado_por_regimen = True
            if bloqueado_por_regimen:
                log.info(
                    "[TradeMonitor] %s (%s) — bloqueado por régimen %s (ADX=%.1f). HOLD.",
                    agent_id, especie, regime_estado, regime["adx"],
                )
                evaluated += 1
                continue

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
                htf_trend=htf_trend,
            )

            params = {
                "params_tecnicos": agent_data["params_tecnicos"],
                "params_macro":    agent_data["params_macro"],
                "params_riesgo":   agent_data["params_riesgo"],
                "params_smc":      smc_params or None,
                "capital_actual":  agent_data["capital_actual"],
                "generacion":      str(agent_data.get("generacion", "")),
                "especie":         especie,
            }
            agent  = InvestorAgent(agent_id, params)
            result = agent.run_cycle(
                tech_signals=tech_signals,
                macro_snapshot=macro_snapshot,
                htf_trend=htf_trend,
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


# ── 4. Cierre forzado EOD ─────────────────────────────────────────────────────

def force_close_all() -> dict:
    """
    Cierra TODAS las posiciones abiertas al precio actual de mercado.
    Llamado por judge_daily.yml antes del ciclo evolutivo (10:45 pm Bogotá).
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


# ── 5. Modo demonio ───────────────────────────────────────────────────────────

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
