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
from datetime import datetime, time as dtime, timedelta, timezone
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

# Fricción round-trip (spread + slippage) — misma constante que investor_agent.
# Usada por el break-even stop (Sesión 22): BE = entrada ± fricción para que
# el cierre en BE no termine en pérdida tras descontar la fricción.
_FRICTION_PIPS = float(os.getenv("TRADE_FRICTION_PIPS", "1.4"))

# ── Piso económico duro de be_activation_r (auditoría forex 2026-07-31) ────
# El backfill mostró que 419 operaciones (44%) morían planas: activaban BE a
# ~0.6R, el precio revertía y la operación cerraba pagando fricción por cero
# beneficio. La evolución offline validó 0.88-0.90R como el valor que preserva
# el edge (holdout +0.50R a +0.71R, campeones 2026-07-24_01/_02) — activar el
# break-even más tarde deja que las operaciones ganadoras respiren antes de
# asegurarlas. Se aplica como PISO EN TIEMPO REAL sobre el valor efectivo
# leído de la operación abierta — corrige de inmediato a las posiciones YA
# abiertas por agentes con genes legacy (be_activation_r 0.5-0.63), sin
# esperar a que la selección natural los reemplace. Si el gen es 0 (BE
# desactivado) se respeta: el piso solo sube valores positivos por debajo del
# mínimo, nunca activa un BE que el agente tenía apagado.
# 2026-08-10: subido 0.8 -> 0.88 por el mismo efecto atractor que RR (los
# agentes del 8-ago nacieron todos clavados en 0.80). 0.88 es el valor del
# campeon validado 2026-07-24_02.
BE_ACTIVATION_MIN_R = float(os.getenv("BE_ACTIVATION_MIN_R", "0.88"))

# Fase 5 Sesión 17: ruptura bloqueada en régimen RANGO.
# Un breakout en mercado lateral tiene un WR muy bajo (~22% observado en prod).
# En NEUTRAL sigue operando (régimen indefinido / sin datos de ADX).
_RUPTURA_SOLO_TENDENCIA = os.getenv("RUPTURA_SOLO_TENDENCIA", "true").lower() != "false"

# Sesión de trading como gen (Fase 3, rediseño 2026-07-02): ventanas en UTC
# por sesión — "cualquiera" (sin restricción adicional) es el default de
# nacimiento; "londres"/"ny"/"overlap" acotan la entrada a la sesión de mayor
# liquidez de EUR/USD. Mismas ventanas que evolution/backtester.py (paridad
# vivo↔OOS, ver Fase 0).
_SESSION_WINDOWS_UTC = {
    "londres": (7, 16),
    "ny":      (12, 21),
    "overlap": (12, 16),
}


def _within_session(sesion: str, hour_utc: int) -> bool:
    """True si hour_utc cae dentro de la ventana de la sesión del gen. Un
    valor no reconocido (incl. 'cualquiera') no restringe."""
    window = _SESSION_WINDOWS_UTC.get(sesion)
    if window is None:
        return True
    start, end = window
    return start <= hour_utc < end


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

# Hora UTC del cierre forzoso EOD del Juez (judge_daily.yml corre el
# force-close-all a las 03:45 UTC = 10:45 pm Bogotá). Es la FRONTERA que usa
# la guardia EOD para decidir si una posición pertenece a un día de trading
# ya cerrado — ver _eod_guard_cutoff() y el bug corregido el 2026-07-11.
_EOD_CLOSE_TIME_UTC = _parse_hhmm(os.getenv("EOD_CLOSE_TIME_UTC"), "03:45")

# Guardia de fin de semana (auditoría 2026-07-11): el mercado FX cierra el
# viernes ~21:00 UTC (4-5 pm Bogotá) y reabre el domingo ~21:00 UTC. La
# ventana de trading (06:30→04:00 UTC) del viernes se extiende hasta la
# madrugada UTC del sábado, cuando el mercado ya está cerrado y Yahoo
# devuelve el último precio CONGELADO — el sistema abría posiciones sobre
# ese precio pagando fricción sin posibilidad de movimiento real (observado
# el 2026-07-10: 16 ops con precio idéntico 1.14194). "false" lo desactiva.
_FOREX_WEEKEND_GUARD = os.getenv("FOREX_WEEKEND_GUARD", "true").lower() != "false"


def _forex_market_closed(now_utc: datetime) -> bool:
    """
    True si el mercado FX institucional está cerrado: viernes desde las
    21:00 UTC, todo el sábado, y domingo hasta las 21:00 UTC. (El cierre real
    del viernes es 21:00 UTC en verano NY / 22:00 UTC en invierno — se usa
    21:00 como frontera conservadora: bloquear una hora de más es barato,
    operar sobre precios congelados no.)
    """
    wd = now_utc.weekday()  # 0=lunes … 4=viernes, 5=sábado, 6=domingo
    if wd == 5:
        return True
    if wd == 4 and now_utc.time() >= dtime(21, 0):
        return True
    if wd == 6 and now_utc.time() < dtime(21, 0):
        return True
    return False


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

    # Actualizar extremo favorable (redondeo a 0.1 milipip: evita que el
    # ruido de coma flotante deje el profit justo bajo un umbral exacto)
    if accion == "BUY":
        nuevo_extremo = max(extremo_actual, current_price)
        profit_pips   = round((nuevo_extremo - precio_entrada) * 10_000, 4)
    else:
        nuevo_extremo = min(extremo_actual, current_price)
        profit_pips   = round((precio_entrada - nuevo_extremo) * 10_000, 4)

    # ── Break-even stop (Sesión 22 — gen be_activation_r) ──────────────────
    # Al ganar be_activation_r × R, el SL sube a entrada ± fricción: la
    # operación ya no puede terminar en pérdida, sin recortar su potencial.
    # Se aplica ANTES del trailing (que exige 1R completo) y nunca empeora.
    # Piso BE_ACTIVATION_MIN_R (auditoría 2026-07-31): un gen positivo pero
    # por debajo del piso se sube al piso; un gen en 0 (BE desactivado) se
    # respeta tal cual.
    be_r = float(op.get("be_activation_r") or 0)
    if be_r > 0:
        be_r = max(be_r, BE_ACTIVATION_MIN_R)
    if be_r > 0 and r_pips > 0 and profit_pips >= be_r * r_pips:
        friction = _FRICTION_PIPS * 0.0001
        if accion == "BUY":
            sl_actual = max(sl_actual, round(precio_entrada + friction, 5))
        else:
            sl_actual = min(sl_actual, round(precio_entrada - friction, 5))

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

        # Salida parcial (Fase 3) con el snapshot único — sin OHLC no se puede
        # saber si se tocó el nivel intra-vela, solo si el precio actual ya
        # lo superó (aproximación consistente con el resto del fallback).
        partial_r = float(op.get("partial_tp_r", 0) or 0)
        if not op.get("parcial_ejecutada") and partial_r > 0:
            r_pips = op.get("pips_sl") or (abs(op["precio_entrada"] - op["stop_loss"]) * 10_000)
            profit_pips_partial = (
                (fallback_price - op["precio_entrada"]) * 10_000 if accion == "BUY"
                else (op["precio_entrada"] - fallback_price) * 10_000
            )
            if r_pips > 0 and profit_pips_partial >= partial_r * r_pips:
                dist_precio = partial_r * r_pips * 0.0001
                precio_parcial = (
                    round(op["precio_entrada"] + dist_precio, 5) if accion == "BUY"
                    else round(op["precio_entrada"] - dist_precio, 5)
                )
                _partial_close_op(op, precio_parcial, ts_salida=None)

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

        # (a2) Sin hit de SL/TP: salida parcial (Fase 3) si aún no se ejecutó
        # y el gen partial_tp_r > 0. Ejecuta al precio exacto del nivel (misma
        # convención que SL/TP), no al close de la vela — evita "pagar de más"
        # en una mecha que toca el nivel y retrocede en la misma vela.
        partial_r = float(op.get("partial_tp_r", 0) or 0)
        if not op.get("parcial_ejecutada") and partial_r > 0:
            r_pips = op.get("pips_sl") or (abs(op["precio_entrada"] - op["stop_loss"]) * 10_000)
            favorable_partial = float(candle["high"]) if accion == "BUY" else float(candle["low"])
            profit_pips_partial = (
                (favorable_partial - op["precio_entrada"]) * 10_000 if accion == "BUY"
                else (op["precio_entrada"] - favorable_partial) * 10_000
            )
            if r_pips > 0 and profit_pips_partial >= partial_r * r_pips:
                dist_precio = partial_r * r_pips * 0.0001
                precio_parcial = (
                    round(op["precio_entrada"] + dist_precio, 5) if accion == "BUY"
                    else round(op["precio_entrada"] - dist_precio, 5)
                )
                _partial_close_op(op, precio_parcial, ts_salida=last_candle_ts)

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


def _clasificar_razon_salida(op: dict, precio_salida: float, resultado: str) -> str:
    """
    Traduce el resultado del verificador a la taxonomía de `razon_salida`
    (Fase A, migración 016). La distinción clave es dentro de HIT_SL: no es
    lo mismo perder -1R completo (SL original) que salir plano (break-even)
    o con ganancia recortada (trailing) — la atribución del histórico mostró
    que el 44% de los trades muere en break-even, así que agruparlos todos
    como "SL" ocultaba exactamente el problema que hay que resolver.

    - HIT_TP           → TP
    - REVERSAL         → REV
    - HIT_SL con el stop en su nivel original            → SL
    - HIT_SL con el stop movido a entrada ± fricción     → BE
    - HIT_SL con el stop movido a favor (ganancia real)  → TRAILING
    """
    if resultado == "HIT_TP":
        return "TP"
    if resultado == "REVERSAL":
        return "REV"
    if resultado != "HIT_SL":
        return "EOD"

    entrada = float(op["precio_entrada"])
    sl_orig = op.get("stop_loss_original")
    # Sin el nivel original registrado no se puede distinguir: se reporta el
    # caso base (SL) en vez de inventar una categoría más favorable.
    if sl_orig is None:
        return "SL"
    if abs(precio_salida - float(sl_orig)) <= 0.00005:
        return "SL"

    # El stop se movió. ¿Dónde quedó respecto a la entrada?
    delta = (precio_salida - entrada) if op["accion"] == "BUY" else (entrada - precio_salida)
    if abs(delta) <= _FRICTION_PIPS * 0.0001 * 1.5:
        return "BE"
    return "TRAILING" if delta > 0 else "SL"


def _close_op(op: dict, precio_salida: float, ts_salida, resultado: str) -> None:
    """
    Cierra la operación reusando InvestorAgent.close_operation y propaga
    el timestamp_salida real cuando proviene del verificador intra-vela,
    más la razón de salida clasificada (Fase A).
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

    razon = _clasificar_razon_salida(op, precio_salida, resultado)
    agent = InvestorAgent(op["agente_id"], {})
    result = agent.close_operation(
        op_id=op["id"],
        precio_salida=precio_salida,
        capital_disponible=capital_actual,
        timestamp_salida=ts_salida,
        razon_salida=razon,
    )
    log.info(
        "[TradeMonitor] Op %d %s → %s (razon=%s): salida=%.5f pnl=%.4f capital=%.4f",
        op["id"], op["accion"], resultado, razon,
        precio_salida, result.get("pnl", 0), result.get("nuevo_capital", 0),
    )


def _partial_close_op(op: dict, precio_salida: float, ts_salida) -> None:
    """
    Ejecuta la salida parcial (Fase 3, rediseño 2026-07-02) reusando
    InvestorAgent.partial_close_operation. Actualiza `op` in-memory
    (capital_usado reducido, parcial_ejecutada=True) para que el resto del
    ciclo (BE/trailing/cierre final) opere sobre el runner correcto.
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
    result = agent.partial_close_operation(
        op_id=op["id"],
        precio_salida=precio_salida,
        capital_disponible=capital_actual,
        timestamp_salida=ts_salida,
    )
    if "error" not in result:
        op["capital_usado"] = result["capital_restante"]
        op["parcial_ejecutada"] = True
    log.info(
        "[TradeMonitor] Op %d %s PARCIAL → salida=%.5f pnl=%.4f runner=%.4f",
        op["id"], op["accion"], precio_salida,
        result.get("pnl", 0), result.get("capital_restante", 0),
    )


# ── 1. Guardia EOD (red de seguridad ante retrasos del judge_daily) ───────────

def _eod_guard_cutoff(now_utc: datetime) -> datetime:
    """
    Frontera de "posición huérfana": el ÚLTIMO cierre forzoso EOD
    (_EOD_CLOSE_TIME_UTC, 03:45 UTC) que YA PASÓ. Una posición abierta antes
    de esa frontera pertenece a un día de trading ya cerrado → es huérfana.

    BUG CORREGIDO (auditoría 2026-07-11): la versión anterior usaba "hoy a las
    06:30 UTC" (_TRADING_START_TIME_UTC) sin manejar que la ventana de trading
    cruza la medianoche UTC. Entre las 00:00 y las 04:00 UTC (7–11 pm Bogotá),
    ese instante quedaba EN EL FUTURO, así que cualquier posición recién
    abierta parecía "del día anterior" → force_close_all() en CADA ciclo de
    15 min, y como la ventana de apertura sigue activa hasta las 04:00 UTC,
    el agente reabría al ciclo siguiente. Resultado: churn de abrir/cerrar
    cada 15 minutos pagando fricción (~-$1.32 acumulados observados; el
    2026-07-10 un solo agente pagó 16 ciclos seguidos), y runners de salida
    parcial ejecutados prematuramente a precio de mercado.

    Con la frontera en el último 03:45 UTC pasado:
      - 12:00 UTC → frontera hoy 03:45: las posiciones de la sesión de hoy
        (abiertas ≥ 06:30) quedan intactas; sobras de ayer se cierran. ✓
      - 00:15 UTC → frontera AYER 03:45: las posiciones de la sesión vigente
        (abiertas desde ayer 06:30) quedan intactas. ✓ (el caso del bug)
      - 05:00 UTC (ventana ciega) → frontera hoy 03:45: las posiciones de la
        sesión que acaba de terminar se cierran si el Juez se retrasó. ✓
        (el propósito original de la guardia, intacto)
    """
    cutoff = now_utc.replace(
        hour=_EOD_CLOSE_TIME_UTC.hour,
        minute=_EOD_CLOSE_TIME_UTC.minute,
        second=0,
        microsecond=0,
    )
    if now_utc < cutoff:
        cutoff -= timedelta(days=1)
    return cutoff


def _eod_guard() -> None:
    """
    Red de seguridad EOD: detecta posiciones de un día de trading YA CERRADO
    que no fueron cerradas por judge_daily.yml (GitHub Actions puede
    retrasarse horas) y las cierra al precio actual antes del ciclo normal
    de SL/TP. La frontera de "día cerrado" es _eod_guard_cutoff().
    """
    from db.connection import get_conn, get_dict_cursor

    cutoff = _eod_guard_cutoff(datetime.now(timezone.utc))

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM operaciones
            WHERE estado = 'abierta'
              AND accion IN ('BUY', 'SELL')
              AND timestamp_entrada < %s
            """,
            (cutoff,),
        )
        n_stale = int((cur.fetchone() or {}).get("n") or 0)

    if n_stale > 0:
        log.warning(
            "[TradeMonitor] EOD GUARD: %d posicion(es) del dia anterior sin cerrar "
            "(judge_daily demorado). Ejecutando cierre forzoso de emergencia...",
            n_stale,
        )
        force_close_all(razon="GUARDIA")
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
                -- Nivel ORIGINAL del stop (Fase A): permite distinguir un SL
                -- completo de un break-even o un trailing al clasificar la salida.
                (o.decision_riesgo->>'stop_loss')::float                AS stop_loss_original,
                (o.decision_riesgo->>'take_profit')::float              AS take_profit,
                COALESCE(o.precio_extremo_favorable,
                    o.precio_entrada)::float                            AS precio_extremo_favorable,
                (o.decision_riesgo->>'trailing_activation_pips')::float AS trailing_activation_pips,
                (o.decision_riesgo->>'trailing_distance_pips')::float   AS trailing_distance_pips,
                COALESCE((a.params_smc->>'be_activation_r')::float, 0)  AS be_activation_r,
                COALESCE((a.params_smc->>'partial_tp_r')::float, 0)     AS partial_tp_r,
                o.parcial_ejecutada
            FROM operaciones o
            JOIN agentes a ON a.id = o.agente_id
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
        "reversal_closed": new_result.get("reversal_closed", 0),
        # 'critical_errors' = fallos al vigilar posiciones ABIERTAS (SL/TP). Son
        # los únicos que tumban el workflow y disparan alerta: significan que una
        # posición real pudo no cerrarse. Los errores de _evaluate_new_positions
        # (p.ej. un timeout transitorio de Yahoo bajando OHLCV para ESCANEAR
        # nuevas entradas) NO son críticos: el siguiente ciclo de 15 min reintenta
        # y ninguna posición abierta queda en riesgo. Se cuentan aparte solo para
        # visibilidad en logs, no para alertar. (Incidente 2026-06-15 17:15 UTC.)
        "critical_errors": sltp_errors,
        "errors":        sltp_errors + new_result.get("errors", 0),
    }


# ── Salida por señal contraria (Sesión 22 — gen exit_on_reversal) ────────────

def _check_reversal_exits(df_ohlcv, htf_trend) -> int:
    """
    Para agentes CON posición abierta y gen exit_on_reversal=1, evalúa la
    señal técnica determinista (sin LLM) con los genes del propio agente y
    cierra la posición si se cumplen LAS TRES condiciones:

      1. La señal es OPUESTA a la posición (BUY abierto + señal SELL, o viceversa).
      2. Confianza de la señal >= umbral_confianza_minima del agente — la misma
         vara que el agente exige para ABRIR una posición en contra.
      3. La posición gana al menos min_profit_for_exit_r × R. Nunca se cierra
         en pérdida por señal: para eso está el Stop Loss.

    La evolución decide si este rasgo aporta: los genes se heredan, mutan y
    compiten como cualquier otro. Devuelve el número de posiciones cerradas.
    """
    from agents.sub_agent_technical import SubAgentTechnical
    from data.indicators import calc_signals
    from db.connection import get_conn, get_dict_cursor

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT o.id, o.agente_id, o.accion,
                   o.precio_entrada::float AS precio_entrada,
                   o.pips_sl::float        AS pips_sl,
                   COALESCE(o.sl_dinamico,
                       (o.decision_riesgo->>'stop_loss')::float)::float AS stop_loss,
                   a.params_tecnicos, a.params_smc, a.params_riesgo,
                   COALESCE(a.especie, 'tendencia') AS especie
            FROM operaciones o
            JOIN agentes a ON a.id = o.agente_id
            WHERE o.estado = 'abierta'
              AND o.accion IN ('BUY', 'SELL')
              AND o.precio_entrada IS NOT NULL
              AND COALESCE((a.params_smc->>'exit_on_reversal')::int, 0) = 1
            """
        )
        ops = [dict(r) for r in cur.fetchall()]

    if not ops:
        return 0

    precio_actual = float(df_ohlcv["close"].iloc[-1])
    closed = 0

    for op in ops:
        try:
            accion  = op["accion"]
            entrada = float(op["precio_entrada"])
            smc     = op.get("params_smc") or {}
            riesgo  = op.get("params_riesgo") or {}

            # Condición 3 primero (barata): ganancia mínima en R
            r_pips = float(op.get("pips_sl") or 0) or (
                abs(entrada - float(op["stop_loss"])) * 10_000
            )
            profit_pips = (
                (precio_actual - entrada) * 10_000 if accion == "BUY"
                else (entrada - precio_actual) * 10_000
            )
            min_profit_r = float(smc.get("min_profit_for_exit_r", 0.4) or 0.4)
            if r_pips <= 0 or profit_pips < min_profit_r * r_pips:
                continue

            # Señal técnica determinista con los genes del agente (sin LLM)
            tech_signals = calc_signals(
                df_ohlcv, op["params_tecnicos"], smc or None, htf_trend=htf_trend,
            )
            sub_tec = SubAgentTechnical(op["agente_id"], op["params_tecnicos"], smc)
            sub_tec.reason = lambda _p: '{"recomendacion":"HOLD","confianza":0.0,"razon":""}'
            senal = sub_tec.analyze(tech_signals, especie=op["especie"])

            opuesta = (
                (accion == "BUY" and senal["recomendacion"] == "SELL")
                or (accion == "SELL" and senal["recomendacion"] == "BUY")
            )
            umbral = float(riesgo.get("umbral_confianza_minima", 0.60) or 0.60)
            if not opuesta or float(senal.get("confianza", 0)) < umbral:
                continue

            _close_op(op, precio_actual, ts_salida=None, resultado="REVERSAL")
            closed += 1
            log.info(
                "[TradeMonitor] Op %d %s (%s) cerrada por SEÑAL CONTRARIA "
                "%s conf=%.2f — profit=%.1f pips (>= %.1f = %.2fR).",
                op["id"], accion, op["agente_id"],
                senal["recomendacion"], float(senal.get("confianza", 0)),
                profit_pips, min_profit_r * r_pips, min_profit_r,
            )
        except Exception as exc:
            log.error(
                "[TradeMonitor] Error en salida por señal para op %d: %s",
                op["id"], exc,
            )

    return closed


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

    # Guardia de fin de semana (auditoría 2026-07-11): la ventana de trading
    # del viernes se extiende hasta las 04:00 UTC del sábado, pero el mercado
    # FX cierra el viernes ~21:00 UTC — abrir posiciones después es operar
    # sobre el último precio congelado de Yahoo (fricción garantizada, cero
    # movimiento real). Las posiciones YA abiertas se siguen monitoreando.
    if _FOREX_WEEKEND_GUARD and _forex_market_closed(datetime.now(timezone.utc)):
        log.info(
            "[TradeMonitor] Mercado FX cerrado (fin de semana UTC). "
            "No se abren posiciones nuevas."
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

    # ── Salidas por señal contraria (Sesión 22) — reusa los datos ya bajados ──
    try:
        reversal_closed = _check_reversal_exits(df_ohlcv, htf_trend)
        if reversal_closed:
            log.info(
                "[TradeMonitor] Salidas por señal contraria: %d posiciones cerradas.",
                reversal_closed,
            )
    except Exception as exc:
        log.error("[TradeMonitor] Error en chequeo de salidas por señal: %s", exc)
        reversal_closed = 0

    evaluated = opened = errors = 0
    # Embudo de decisión (Fase A, migración 016): cuántos candidatos cae en
    # cada gate. Sin esto, "los agentes no operan" es una impresión; con esto
    # es un número atribuible a un filtro concreto.
    embudo = {
        "candidatos": len(candidates),
        "bloqueado_regimen": 0,
        "bloqueado_sesion": 0,
        "bloqueado_cuarentena": 0,
        "hold_por_senal": 0,
    }

    for agent_data in candidates:
        agent_id = agent_data["id"]
        try:
            especie = str(agent_data.get("especie") or "tendencia")

            # ── Gate de régimen (Fase 2 + Fase 5 Sesión 17) ─────────────────
            # S1 tendencia : sólo opera en mercados con tendencia (ADX alto)
            # S2 reversion : sólo opera en mercados en rango (ADX bajo)
            # S3 ruptura   : sólo en TENDENCIA cuando RUPTURA_SOLO_TENDENCIA=true
            #                (un breakout en rango lateral tiene WR ~22% en prod)
            # NEUTRAL : cualquier especie puede operar (régimen indefinido)
            regime_estado = regime["estado"]
            bloqueado_por_regimen = False
            if regime_estado != "NEUTRAL":
                if especie == "tendencia" and regime_estado == "RANGO":
                    bloqueado_por_regimen = True
                elif especie == "reversion" and regime_estado == "TENDENCIA":
                    bloqueado_por_regimen = True
                elif (especie == "ruptura" and regime_estado == "RANGO"
                      and _RUPTURA_SOLO_TENDENCIA):
                    bloqueado_por_regimen = True
            if bloqueado_por_regimen:
                log.info(
                    "[TradeMonitor] %s (%s) — bloqueado por régimen %s (ADX=%.1f). HOLD.",
                    agent_id, especie, regime_estado, regime["adx"],
                )
                embudo["bloqueado_regimen"] += 1
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
                embudo["bloqueado_cuarentena"] += 1
                evaluated += 1
                continue

            # Sesión de trading — gen propio del agente (Fase 3, 2026-07-02)
            sesion_gen = str(smc_params.get("sesion_trading", "cualquiera"))
            hour_utc_now = datetime.now(timezone.utc).hour
            if not _within_session(sesion_gen, hour_utc_now):
                log.info(
                    "[TradeMonitor] %s — fuera de sesión '%s' (hora UTC=%d). HOLD.",
                    agent_id, sesion_gen, hour_utc_now,
                )
                embudo["bloqueado_sesion"] += 1
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
            else:
                embudo["hold_por_senal"] += 1
            evaluated += 1

        except Exception as exc:
            log.error("[TradeMonitor] Error evaluando agente %s: %s", agent_id, exc)
            errors += 1

    log.info(
        "[TradeMonitor] Nuevas posiciones — evaluados=%d abiertos=%d errores=%d",
        evaluated, opened, errors,
    )
    log.info(
        "[TradeMonitor] EMBUDO — candidatos=%d | regimen=%d sesion=%d cuarentena=%d "
        "hold_senal=%d | abiertos=%d",
        embudo["candidatos"], embudo["bloqueado_regimen"], embudo["bloqueado_sesion"],
        embudo["bloqueado_cuarentena"], embudo["hold_por_senal"], opened,
    )
    _persistir_embudo(embudo, opened, errors, regime)

    return {
        "evaluated": evaluated, "opened": opened, "errors": errors,
        "reversal_closed": reversal_closed, "embudo": embudo,
    }


def _persistir_embudo(embudo: dict, opened: int, errors: int, regime: dict | None) -> None:
    """
    Guarda el embudo del ciclo en `embudo_decision` (Fase A, migración 016).

    Nunca propaga excepciones: es telemetría de diagnóstico, no puede tumbar
    un ciclo del monitor cuyo trabajo real (vigilar SL/TP) ya se completó.
    """
    from db.connection import get_conn

    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO embudo_decision (
                    candidatos, bloqueado_regimen, bloqueado_sesion,
                    bloqueado_cuarentena, hold_por_senal, abiertos, errores,
                    regimen_estado, adx
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    embudo["candidatos"], embudo["bloqueado_regimen"],
                    embudo["bloqueado_sesion"], embudo["bloqueado_cuarentena"],
                    embudo["hold_por_senal"], opened, errors,
                    (regime or {}).get("estado"),
                    round(float((regime or {}).get("adx") or 0), 2) or None,
                ),
            )
    except Exception as exc:
        log.warning("[TradeMonitor] No se pudo persistir el embudo: %s", exc)


# ── 4. Cierre forzado EOD ─────────────────────────────────────────────────────

def force_close_all(razon: str = "EOD") -> dict:
    """
    Cierra TODAS las posiciones abiertas al precio actual de mercado.
    Llamado por judge_daily.yml antes del ciclo evolutivo (10:45 pm Bogotá).

    `razon` (Fase A): "EOD" para el cierre programado del Juez, "GUARDIA"
    cuando lo invoca _eod_guard() como red de seguridad — distinguirlos
    permite medir cuánto P&L se pierde por cada mecanismo.
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
                    razon_salida=razon,
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
        critical = result.get("critical_errors", 0)
        non_critical = result.get("errors", 0) - critical
        if critical:
            log.error(
                "[TradeMonitor] Ciclo con %d error(es) CRÍTICO(s) en vigilancia "
                "SL/TP de posiciones abiertas — el workflow fallará para alertar.",
                critical,
            )
        elif non_critical:
            log.warning(
                "[TradeMonitor] Ciclo OK con %d incidencia(s) NO crítica(s) "
                "(p.ej. datos de mercado para nuevas entradas no disponibles este "
                "ciclo). Se reintenta en 15 min — sin alerta ni falla de workflow.",
                non_critical,
            )
        sys.exit(0 if critical == 0 else 1)
    elif args.force_close_all:
        result = force_close_all()
        sys.exit(0 if result["errors"] == 0 else 1)
    else:
        run_daemon()


if __name__ == "__main__":
    main()
