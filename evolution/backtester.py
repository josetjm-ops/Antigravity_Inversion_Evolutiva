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
import random
import statistics
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

# ── Fase 2 PLAN_DE_MEJORA.md: gate estadístico del torneo ────────────────────
# legacy    : umbral débil actual (fitness OOS > 0 & n_trades >= 5).
# bootstrap : exige que el límite inferior del IC de la expectancy sea > 0.
TOURNAMENT_GATE_MODE   = os.getenv("TOURNAMENT_GATE_MODE", "legacy").lower()
BOOTSTRAP_ITERS         = int(os.getenv("BOOTSTRAP_ITERS", "1000"))
BOOTSTRAP_CI            = float(os.getenv("BOOTSTRAP_CI", "0.80"))
BOOTSTRAP_MIN_TRADES    = int(os.getenv("BOOTSTRAP_MIN_TRADES", "8"))

# Velas 15m por día de trading (≈6.5h × 4 velas/h)
_CANDLES_PER_DAY = 26
# Evaluar nueva posición cada N velas (= cadencia del cron de producción)
_CHECK_EVERY     = 4
# Máximo lookback para el slice de señales (limita O(N) por llamada)
_LOOKBACK        = 300

# ── Fase 3 PLAN_DE_MEJORA.md: walk-forward multi-fold ───────────────────────
# single    : split único TRAIN/VALIDATE actual (comportamiento de siempre).
# multifold : N folds deslizantes con purge gap, fitness agregado penalizado
#             por varianza entre folds — reduce la sensibilidad al régimen de
#             mercado específico de un único tramo OOS.
BACKTEST_MODE           = os.getenv("BACKTEST_MODE", "single").lower()
MULTIFOLD_N_FOLDS       = int(os.getenv("MULTIFOLD_N_FOLDS", "3"))
MULTIFOLD_TRAIN_DAYS    = int(os.getenv("MULTIFOLD_TRAIN_DAYS", "30"))
MULTIFOLD_VALIDATE_DAYS = int(os.getenv("MULTIFOLD_VALIDATE_DAYS", "10"))
MULTIFOLD_PURGE_DAYS    = int(os.getenv("MULTIFOLD_PURGE_DAYS", "1"))
MULTIFOLD_STEP_DAYS     = int(os.getenv("MULTIFOLD_STEP_DAYS", "10"))
MULTIFOLD_LAMBDA        = float(os.getenv("MULTIFOLD_LAMBDA", "0.5"))


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

def _walk_forward_trades(
    df_15m: pd.DataFrame,
    oos_start: int,
    n_end: int,
    htf_trend: dict,
    params_tec: dict,
    params_smc: dict,
    params_riesgo: dict,
    especie: str,
) -> list[dict]:
    """
    Núcleo walk-forward compartido entre el modo single-split y multi-fold:
    simula señales → entrada → gestión de SL/TP/BE/reversal sobre el tramo
    [oos_start, n_end) de df_15m, con htf_trend fijo para todo el tramo
    (igual que el comportamiento original: el filtro HTF no se recalcula
    vela a vela dentro de un mismo fold — ver docstring del módulo).

    Extraído de run_backtest() sin cambiar una sola línea de la lógica de
    trading, para poder reutilizarlo también en el modo multi-fold (Fase 3
    de PLAN_DE_MEJORA.md) sin duplicar ni divergir el comportamiento.
    """
    from data.indicators      import calc_signals
    from agents.sub_agent_technical import SubAgentTechnical
    from agents.sub_agent_risk      import SubAgentRisk

    friction_pips = float(os.getenv("TRADE_FRICTION_PIPS", "1.4"))
    min_sl_pips   = float(os.getenv("MIN_SL_PIPS", "10.0"))

    be_r          = float(params_smc.get("be_activation_r", 0) or 0)
    exit_rev      = int(params_smc.get("exit_on_reversal", 0) or 0)
    min_profit_r  = float(params_smc.get("min_profit_for_exit_r", 0.4) or 0.4)
    umbral_conf   = float(params_riesgo.get("umbral_confianza_minima", 0.60) or 0.60)

    sub_tec  = SubAgentTechnical("bt", params_tec, params_smc)
    sub_risk = SubAgentRisk("bt", params_riesgo, params_smc)
    sub_tec.reason  = lambda _p: '{"recomendacion":"HOLD","confianza":0.0,"razon":""}'
    sub_risk.reason = lambda _p: '{"accion_final":"HOLD","confianza_final":0.0}'

    open_pos: dict | None = None
    capital  = 10.0
    trades: list[dict] = []

    for i in range(oos_start, n_end):
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
            elif be_r > 0:
                # ── Break-even stop (Sesión 22) — igual que trade_monitor:
                # tras el chequeo de hits, si el extremo favorable de la vela
                # alcanza be_r × R, el SL sube a entrada ± fricción.
                r_pips = open_pos.get("sl_pips") or (abs(entry - sl) * 10_000)
                favorable = candle_hi if accion == "BUY" else candle_lo
                profit_pips = (
                    (favorable - entry) * 10_000 if accion == "BUY"
                    else (entry - favorable) * 10_000
                )
                if r_pips > 0 and profit_pips >= be_r * r_pips:
                    fr = friction_pips * 0.0001
                    if accion == "BUY":
                        open_pos["stop_loss"] = max(sl, round(entry + fr, 5))
                    else:
                        open_pos["stop_loss"] = min(sl, round(entry - fr, 5))

        # ── 2. Cada N velas: evaluar señal (entrada o salida por reversa) ─
        cadence = (i - oos_start) % _CHECK_EVERY == 0
        needs_signal = cadence and (open_pos is None or exit_rev == 1)
        if needs_signal:
            lo = max(0, i - _LOOKBACK)
            df_slice = df_15m.iloc[lo: i + 1]
            if len(df_slice) < 30:
                continue

            try:
                signals = calc_signals(df_slice, params_tec, params_smc,
                                       htf_trend=htf_trend)
            except Exception:
                continue

            # ── 2a. Posición abierta + gen activo: salida por señal contraria
            #        (Sesión 22). Mismas 3 condiciones que trade_monitor:
            #        opuesta + confianza >= umbral propio + ganancia mínima en R.
            if open_pos is not None:
                senal = sub_tec.analyze(signals, especie=especie)
                accion = open_pos["accion"]
                opuesta = (
                    (accion == "BUY" and senal["recomendacion"] == "SELL")
                    or (accion == "SELL" and senal["recomendacion"] == "BUY")
                )
                if opuesta and float(senal.get("confianza", 0)) >= umbral_conf:
                    entry    = open_pos["precio_entrada"]
                    cap_used = open_pos["capital_usado"]
                    r_pips   = open_pos.get("sl_pips") or (
                        abs(entry - open_pos["stop_loss"]) * 10_000
                    )
                    profit_pips = (
                        (precio - entry) * 10_000 if accion == "BUY"
                        else (entry - precio) * 10_000
                    )
                    if r_pips > 0 and profit_pips >= min_profit_r * r_pips:
                        pnl = (
                            (precio - entry) / entry * cap_used if accion == "BUY"
                            else (entry - precio) / entry * cap_used
                        )
                        pnl -= friction_pips * 0.0001 / entry * cap_used
                        pnl  = round(pnl, 6)
                        capital += pnl
                        trades.append({
                            "accion": accion, "entry": entry, "exit": precio,
                            "pnl": pnl, "hit": "REV",
                        })
                        open_pos = None
                continue

            # ── 2b. Sin posición: evaluar nueva entrada ───────────────────
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
                "sl_pips":        sl_pips,
            }

    # ── Cerrar posición abierta al precio final del FOLD (no del dataset) ────
    # n_end-1 es la última vela de este tramo OOS: en modo single n_end==n_total
    # (idéntico al comportamiento original); en multi-fold cada fold cierra en
    # su propio borde, nunca "espía" velas de folds posteriores.
    if open_pos is not None:
        precio_final = float(df_15m["close"].iloc[n_end - 1])
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

    return trades


# ── Modo single (comportamiento original, default) ──────────────────────────

def _run_backtest_single(data: dict, agent: dict) -> dict:
    """
    Split único TRAIN/VALIDATE: comportamiento exacto de antes de la Fase 3
    (PLAN_DE_MEJORA.md). HTF se calcula UNA VEZ con la última fila disponible
    de calc_htf_trend_series (tendencia HTF "actual", como siempre).
    """
    from data.indicators import calc_htf_trend_series

    df_15m        = data["df_15m"]
    df_1h         = data["df_1h"]
    params_tec    = agent.get("params_tecnicos") or {}
    params_smc    = agent.get("params_smc")      or {}
    params_riesgo = agent.get("params_riesgo")   or {}
    especie       = str(agent.get("especie", "tendencia"))

    n_total = len(df_15m)
    oos_start = max(0, n_total - BACKTEST_VALIDATE_DAYS * _CANDLES_PER_DAY)

    if oos_start < 50:
        log.warning("[Backtester] Datos insuficientes (%d velas). Skip.", n_total)
        return _empty_result()

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

    trades = _walk_forward_trades(
        df_15m, oos_start, n_total, htf_trend,
        params_tec, params_smc, params_riesgo, especie,
    )
    return _calc_metrics(trades)


# ── Modo multi-fold (Fase 3 PLAN_DE_MEJORA.md) ──────────────────────────────

def _lookup_htf_at(htf_series: pd.DataFrame | None, ts) -> dict:
    """HTF vigente al timestamp `ts` (última fila con timestamp <= ts).

    A diferencia del modo single (que usa siempre la última tendencia HTF
    disponible), cada fold multi-fold valida un tramo histórico distinto y
    debe usar la tendencia HTF que existía EN ESE MOMENTO — de lo contrario
    folds antiguos usarían información del futuro (fuga hacia adelante).
    """
    if htf_series is None or htf_series.empty:
        return {"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0}
    mask = htf_series["timestamp"] <= ts
    if not mask.any():
        return {"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0}
    row = htf_series[mask].iloc[-1]
    return {
        "direccion":  str(row["htf_direccion"]),
        "ema_rapida": float(row["htf_ema_rapida"]),
        "ema_lenta":  float(row["htf_ema_lenta"]),
    }


def _compute_fold_bounds(n_total: int) -> list[tuple[int, int, int]]:
    """
    Índices [(train_start, oos_start, oos_end), ...] para MULTIFOLD_N_FOLDS
    folds deslizantes, avanzando MULTIFOLD_STEP_DAYS días entre folds, con
    purge gap de MULTIFOLD_PURGE_DAYS entre train y validate:

        train_k    = [train_start,             train_start+TRAIN_DAYS)
        purge      = MULTIFOLD_PURGE_DAYS días descartados
        validate_k = [oos_start,                oos_start+VALIDATE_DAYS)

    Folds cuyo oos_end excede los datos disponibles se omiten (dataset corto).
    """
    bounds: list[tuple[int, int, int]] = []
    for k in range(MULTIFOLD_N_FOLDS):
        train_start = k * MULTIFOLD_STEP_DAYS * _CANDLES_PER_DAY
        oos_start = train_start + (MULTIFOLD_TRAIN_DAYS + MULTIFOLD_PURGE_DAYS) * _CANDLES_PER_DAY
        oos_end = oos_start + MULTIFOLD_VALIDATE_DAYS * _CANDLES_PER_DAY
        if oos_end > n_total:
            continue
        bounds.append((train_start, oos_start, oos_end))
    return bounds


def _run_backtest_multifold(data: dict, agent: dict) -> dict:
    """
    N folds deslizantes; fitness agregado = mean(fitness_folds) -
    MULTIFOLD_LAMBDA * stdev(fitness_folds). Penaliza candidatos inestables
    entre regímenes de mercado distintos (ver Fase 3, PLAN_DE_MEJORA.md).

    Fallback automático a modo single si el dataset no alcanza para los
    folds configurados (nunca deja el candidato sin evaluar).
    """
    from data.indicators import calc_htf_trend_series

    df_15m        = data["df_15m"]
    df_1h         = data["df_1h"]
    params_tec    = agent.get("params_tecnicos") or {}
    params_smc    = agent.get("params_smc")      or {}
    params_riesgo = agent.get("params_riesgo")   or {}
    especie       = str(agent.get("especie", "tendencia"))

    n_total = len(df_15m)
    bounds = _compute_fold_bounds(n_total)
    if not bounds:
        log.warning(
            "[Backtester] multifold: datos insuficientes (%d velas) para "
            "%d folds — fallback a modo single.", n_total, MULTIFOLD_N_FOLDS,
        )
        return _run_backtest_single(data, agent)

    try:
        htf_series = calc_htf_trend_series(df_1h)
    except Exception:
        htf_series = None

    fold_metrics: list[dict] = []
    all_trades:   list[dict] = []
    for _train_start, oos_start, oos_end in bounds:
        ts_fold = df_15m["timestamp"].iloc[oos_start]
        htf_trend = _lookup_htf_at(htf_series, ts_fold)
        trades = _walk_forward_trades(
            df_15m, oos_start, oos_end, htf_trend,
            params_tec, params_smc, params_riesgo, especie,
        )
        fold_metrics.append(_calc_metrics(trades))
        all_trades.extend(trades)

    fitnesses = [f["fitness"] for f in fold_metrics]
    mean_fit  = statistics.fmean(fitnesses)
    stdev_fit = statistics.pstdev(fitnesses) if len(fitnesses) > 1 else 0.0
    fitness_agg = mean_fit - MULTIFOLD_LAMBDA * stdev_fit

    n_trades_agg = sum(f["n_trades"] for f in fold_metrics)
    expectancies = [f["expectancy"] for f in fold_metrics]
    win_rate_agg = (
        sum(1 for t in all_trades if t["pnl"] > 0) / len(all_trades)
        if all_trades else 0.0
    )
    max_dd_agg = max((f["max_drawdown"] for f in fold_metrics), default=0.0)

    return {
        "n_trades":       n_trades_agg,
        "win_rate":       round(win_rate_agg, 4),
        "expectancy":     round(statistics.fmean(expectancies), 6) if expectancies else 0.0,
        "max_drawdown":   round(max_dd_agg, 4),
        "fitness":        round(fitness_agg, 6),
        "oos_trades":     all_trades,
        # Auditoría: fitness de cada fold individual (no lo usa el motor,
        # útil para tests y diagnóstico de estabilidad entre regímenes).
        "fold_fitnesses": [round(f, 6) for f in fitnesses],
    }


# ── Punto de entrada público (dispatcher) ───────────────────────────────────

def run_backtest(data: dict, agent: dict) -> dict:
    """
    Ejecuta el backtest walk-forward OOS sobre un agente candidato.

    BACKTEST_MODE=single (default): split único TRAIN/VALIDATE — comporta-
    miento histórico exacto, sin cambios.
    BACKTEST_MODE=multifold: N folds deslizantes con purge gap y fitness
    agregado penalizado por varianza (Fase 3, PLAN_DE_MEJORA.md).

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
      "fitness"     : float,   # expectancy / (max_drawdown + 1) [multifold: agregado]
      "oos_trades"  : list,    # detalle de cada trade (todos los folds en multifold)
    }
    """
    if BACKTEST_MODE == "multifold":
        return _run_backtest_multifold(data, agent)
    return _run_backtest_single(data, agent)


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


# ── Fase 2 (PLAN_DE_MEJORA.md): gate estadístico bootstrap ──────────────────

def bootstrap_edge_ok(
    oos_trades: list[dict],
    iters: int | None = None,
    ci: float | None = None,
    min_trades: int | None = None,
    seed: int | None = None,
) -> tuple[bool, float | None]:
    """
    Bootstrap sobre los P&L de los trades OOS de un candidato: resample con
    reemplazo `iters` veces, calcula la expectancy de cada resample y toma el
    límite inferior del intervalo de confianza `ci`.

    Reemplaza el umbral débil `fitness OOS > 0 & n_trades >= 5`: con muestras
    de 5 trades una expectancy apenas positiva es indistinguible de azar (ver
    hallazgo 5 de PLAN_DE_MEJORA.md: 2026-06-27_01 pasaba con fitness=0.0105/
    n=5 pero su IC80 inferior era -0.062).

    Parameters
    ----------
    oos_trades : lista de dicts con clave "pnl" (formato de run_backtest()["oos_trades"]).
    iters       : nº de resamples (default BOOTSTRAP_ITERS = 1000).
    ci          : nivel de confianza, p.ej. 0.80 (default BOOTSTRAP_CI).
    min_trades  : mínimo de trades OOS para intentar el bootstrap
                  (default BOOTSTRAP_MIN_TRADES = 8; con menos, se rechaza
                  directamente — muestra insuficiente incluso para resamplear).
    seed        : semilla opcional (tests deterministas); None = aleatorio real.

    Returns
    -------
    (pasa, limite_inferior) — pasa=True si limite_inferior > 0.
    limite_inferior es None si no hay suficientes trades para evaluar.
    """
    iters      = BOOTSTRAP_ITERS      if iters      is None else iters
    ci         = BOOTSTRAP_CI         if ci         is None else ci
    min_trades = BOOTSTRAP_MIN_TRADES if min_trades is None else min_trades

    pnls = [float(t["pnl"]) for t in oos_trades]
    n = len(pnls)
    if n < min_trades:
        return False, None

    rng = random.Random(seed)
    exps = sorted(
        sum(pnls[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(iters)
    )
    lower_idx = int((1 - ci) / 2 * iters)
    lower = exps[lower_idx]
    return lower > 0, round(lower, 6)
