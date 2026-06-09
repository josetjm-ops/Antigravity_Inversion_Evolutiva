"""
Backtester walk-forward para el ciclo evolutivo — Fase 3.

Simula el pipeline técnico completo (señales → decisión → SL/TP → P&L)
sobre 60 días de historia de EUR/USD usando los mismos componentes que
producción. Integrado en el motor evolutivo para pre-seleccionar hijos:
se crían N candidatos, se elige el de mayor fitness OOS.

Walk-forward
------------
  Train   : primeros BACKTEST_TRAIN_DAYS días (warmup de indicadores, ignorado)
  Validate: últimos  BACKTEST_VALIDATE_DAYS días (OOS — define el fitness)

Simplificaciones intencionales vs producción
--------------------------------------------
  - Sin LLM       : reason() devuelve HOLD vacío → análisis siempre heurístico.
  - Sin macro      : solo señales técnicas + régimen ADX.
  - Sin trailing   : SL/TP fijo; simplifica el backtester sin cambiar el edge.
  - Fricción igual : TRADE_FRICTION_PIPS descontado de cada trade.
  - HTF una vez    : se calcula al inicio del OOS, no se actualiza cada vela.
  - Cadencia 1h    : nueva posición se evalúa cada 4 velas de 15m (= cron real).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# ── Configuración (todas sobreescribibles desde .env) ────────────────────────
BACKTEST_TRAIN_DAYS    = int(os.getenv("BACKTEST_TRAIN_DAYS",    "40"))
BACKTEST_VALIDATE_DAYS = int(os.getenv("BACKTEST_VALIDATE_DAYS", "20"))
N_CANDIDATE_CHILDREN   = int(os.getenv("N_CANDIDATE_CHILDREN",    "3"))

# Fase 5 Sesión 17: ruptura bloqueada en RANGO (mismo gate que trade_monitor).
# Coherencia entre backtest y producción es crítica para la validez del fitness OOS.
_RUPTURA_SOLO_TENDENCIA = os.getenv("RUPTURA_SOLO_TENDENCIA", "true").lower() != "false"

# Velas 15m por día de trading (≈6.5h × 4 velas/h)
_CANDLES_PER_DAY = 26
# Evaluar nueva posición cada N velas (= cadencia del cron de producción)
_CHECK_EVERY     = 4
# Máximo lookback para el slice de señales (limita O(N) por llamada)
_LOOKBACK        = 300


# ── Descarga única de datos históricos ───────────────────────────────────────

def fetch_backtest_data() -> dict:
    """
    Descarga datos históricos para el backtest.
    Llamar UNA VEZ por ciclo evolutivo y reusar para todos los candidatos.

    Returns
    -------
    {"df_15m": DataFrame(60d/15m), "df_1h": DataFrame(3mo/1h)}
    """
    from data.indicators import fetch_ohlcv
    df_15m = fetch_ohlcv(interval="15m", range_str="60d")
    df_1h  = fetch_ohlcv(interval="1h",  range_str="3mo")
    log.info(
        "[Backtester] Datos listos: %d velas 15m (60d) · %d velas 1h (3mo)",
        len(df_15m), len(df_1h),
    )
    return {"df_15m": df_15m, "df_1h": df_1h}


# ── Motor de backtest ─────────────────────────────────────────────────────────

def run_backtest(data: dict, agent: dict) -> dict:
    """
    Ejecuta el backtest walk-forward OOS sobre un agente candidato.

    Parameters
    ----------
    data  : dict de fetch_backtest_data()
    agent : dict con params_tecnicos, params_smc, params_riesgo, especie

    Returns
    -------
    {
      "n_trades"    : int,
      "win_rate"    : float,
      "expectancy"  : float,   # expectancy neta por trade (OOS)
      "max_drawdown": float,
      "fitness"     : float,   # expectancy / (max_drawdown + 1)
      "oos_trades"  : list,    # detalle de cada trade
    }
    """
    from data.indicators      import calc_signals, calc_htf_trend_series
    from agents.sub_agent_technical import SubAgentTechnical
    from agents.sub_agent_risk      import SubAgentRisk

    df_15m        = data["df_15m"]
    df_1h         = data["df_1h"]
    params_tec    = agent.get("params_tecnicos") or {}
    params_smc    = agent.get("params_smc")      or {}
    params_riesgo = agent.get("params_riesgo")   or {}
    especie       = str(agent.get("especie", "tendencia"))
    friction_pips = float(os.getenv("TRADE_FRICTION_PIPS", "1.4"))
    min_sl_pips   = float(os.getenv("MIN_SL_PIPS", "10.0"))

    n_total = len(df_15m)
    oos_start = max(0, n_total - BACKTEST_VALIDATE_DAYS * _CANDLES_PER_DAY)

    # Mínimo de datos para que el warmup sea válido
    if oos_start < 50:
        log.warning("[Backtester] Datos insuficientes (%d velas). Skip.", n_total)
        return _empty_result()

    # ── HTF trend al inicio del período OOS ──────────────────────────────────
    try:
        htf_series = calc_htf_trend_series(df_1h)
        last_htf   = htf_series.iloc[-1]
        htf_trend  = {
            "direccion":  str(last_htf["htf_direccion"]),
            "ema_rapida": float(last_htf["htf_ema_rapida"]),
            "ema_lenta":  float(last_htf["htf_ema_lenta"]),
        }
    except Exception:
        htf_trend = {"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0}

    # ── Sub-agentes sin LLM ───────────────────────────────────────────────────
    # reason() devuelve JSON mínimo que no modifica el resultado heurístico.
    sub_tec  = SubAgentTechnical("bt", params_tec, params_smc)
    sub_risk = SubAgentRisk("bt", params_riesgo, params_smc)
    sub_tec.reason  = lambda _p: '{"recomendacion":"HOLD","confianza":0.0,"razon":""}'
    sub_risk.reason = lambda _p: '{"accion_final":"HOLD","confianza_final":0.0}'

    # ── Walk-forward OOS ──────────────────────────────────────────────────────
    open_pos: dict | None = None
    capital  = 10.0
    trades: list[dict] = []

    for i in range(oos_start, n_total):
        candle_hi = float(df_15m["high"].iloc[i])
        candle_lo = float(df_15m["low"].iloc[i])
        precio    = float(df_15m["close"].iloc[i])

        # ── 1. Verificar SL/TP si hay posición abierta ────────────────────
        if open_pos is not None:
            accion   = open_pos["accion"]
            sl       = open_pos["stop_loss"]
            tp       = open_pos["take_profit"]
            entry    = open_pos["precio_entrada"]
            cap_used = open_pos["capital_usado"]

            # Peor caso intra-vela: si ambos se tocan, gana SL (convención)
            hit_sl = (candle_lo <= sl) if accion == "BUY" else (candle_hi >= sl)
            hit_tp = (candle_hi >= tp) if accion == "BUY" else (candle_lo <= tp)

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                if accion == "BUY":
                    pnl = (exit_p - entry) / entry * cap_used
                else:
                    pnl = (entry - exit_p) / entry * cap_used
                pnl -= friction_pips * 0.0001 / entry * cap_used
                pnl  = round(pnl, 6)
                capital += pnl
                trades.append({
                    "accion": accion, "entry": entry, "exit": exit_p,
                    "pnl": pnl, "hit": "SL" if hit_sl else "TP",
                })
                open_pos = None

        # ── 2. Cada N velas: evaluar nueva posición ───────────────────────
        if open_pos is None and (i - oos_start) % _CHECK_EVERY == 0:
            lo = max(0, i - _LOOKBACK)
            df_slice = df_15m.iloc[lo: i + 1]
            if len(df_slice) < 30:
                continue

            try:
                signals = calc_signals(df_slice, params_tec, params_smc,
                                       htf_trend=htf_trend)
            except Exception:
                continue

            # Gate de régimen (igual que trade_monitor)
            r_estado = signals.regime_estado
            if r_estado != "NEUTRAL":
                if especie == "tendencia" and r_estado == "RANGO":
                    continue
                if especie == "reversion" and r_estado == "TENDENCIA":
                    continue
                if especie == "ruptura" and r_estado == "RANGO" and _RUPTURA_SOLO_TENDENCIA:
                    continue

            senal = sub_tec.analyze(signals, especie=especie)
            if senal["recomendacion"] not in ("BUY", "SELL"):
                continue

            sl, tp, cap_uso, sl_pips, _, _ = sub_risk._compute_levels(
                precio, senal["recomendacion"], capital, senal
            )
            if sl is None or tp is None or sl_pips < min_sl_pips:
                continue

            open_pos = {
                "accion":         senal["recomendacion"],
                "precio_entrada": precio,
                "stop_loss":      sl,
                "take_profit":    tp,
                "capital_usado":  cap_uso,
            }

    # ── Cerrar posición abierta al precio final (EOD del período) ────────────
    if open_pos is not None:
        precio_final = float(df_15m["close"].iloc[-1])
        accion  = open_pos["accion"]
        entry   = open_pos["precio_entrada"]
        cap_used = open_pos["capital_usado"]
        pnl = (
            (precio_final - entry) / entry * cap_used if accion == "BUY"
            else (entry - precio_final) / entry * cap_used
        )
        pnl -= friction_pips * 0.0001 / entry * cap_used
        trades.append({
            "accion": accion, "entry": entry, "exit": precio_final,
            "pnl": round(pnl, 6), "hit": "EOD",
        })

    return _calc_metrics(trades)


# ── Métricas OOS ─────────────────────────────────────────────────────────────

def _calc_metrics(trades: list[dict]) -> dict:
    if not trades:
        return _empty_result()

    n     = len(trades)
    wins  = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses= [t["pnl"] for t in trades if t["pnl"] <= 0]

    win_rate   = len(wins) / n
    avg_win    = sum(wins)           / len(wins)   if wins   else 0.0
    avg_loss   = abs(sum(losses)     / len(losses)) if losses else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    # Max drawdown sobre la curva de capital (base = 10.0 por agente)
    _CAPITAL_BASE = 10.0
    acum   = _CAPITAL_BASE
    peak   = _CAPITAL_BASE
    max_dd = 0.0
    for t in trades:
        acum  += t["pnl"]
        peak   = max(peak, acum)
        dd     = (peak - acum) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    fitness = expectancy / (max_dd + 1.0)

    return {
        "n_trades":     n,
        "win_rate":     round(win_rate,   4),
        "expectancy":   round(expectancy, 6),
        "max_drawdown": round(max_dd,     4),
        "fitness":      round(fitness,    6),
        "oos_trades":   trades,
    }


def _empty_result() -> dict:
    return {
        "n_trades": 0, "win_rate": 0.0, "expectancy": 0.0,
        "max_drawdown": 0.0, "fitness": 0.0, "oos_trades": [],
    }
