"""
Backtest determinista de la estrategia de los agentes (Fase 0 — línea base).

Reproduce el pipeline real Técnico → Macro(HOLD) → Riesgo vela por vela sobre
datos históricos de Yahoo Finance, y simula el cierre por SL/TP/trailing con las
MISMAS convenciones que cron/trade_monitor.py + data/simulated_broker.py.

Objetivo: medir win-rate, PnL y drawdown de la lógica ACTUAL para tener un punto
de comparación numérico antes de aplicar las correcciones de las fases siguientes.
Ejecutar este mismo script sobre la rama con cambios produce el "después".

Decisiones de fidelidad:
  - LLM neutralizado: se fuerza la ruta heurística determinista (idéntica a cuando
    DeepSeek no está disponible). Así el backtest es reproducible y aísla la lógica.
  - Macro = HOLD(0.35): replica el caso dominante "sin eventos de alto impacto"
    (no hay archivo histórico de noticias Finnhub para fechas pasadas).
  - SL/TP intra-vela sobre velas de 15m (no 1m): aproximación más gruesa que prod,
    pero consistente entre "antes" y "después". Convención de peor caso: si una
    vela toca SL y TP, gana SL (igual que check_sl_tp_intrabar).
  - Sin cierre EOD 16:45 Bogotá: las posiciones se cierran solo por SL/TP o al final
    del dataset. Limitación conocida (las pérdidas observadas son por SL, no EOD).

Uso:
    python scripts/backtest_estrategia.py
    python scripts/backtest_estrategia.py --range 60d --warmup 150
    python scripts/backtest_estrategia.py --agent-id 2026-05-01_03   # genes reales desde DB
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

# Raíz del proyecto en sys.path para imports cuando se ejecuta como script suelto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.indicators import fetch_ohlcv, calc_signals, calc_htf_trend_series
from data.simulated_broker import check_sl_tp_intrabar, exit_price_for

# Silenciar los warnings "LLM no disponible" que emiten los sub-agentes al caer
# a la ruta heurística (es el comportamiento deseado en el backtest).
logging.getLogger("agents.sub_agent_technical").setLevel(logging.ERROR)
logging.getLogger("agents.sub_agent_risk").setLevel(logging.ERROR)
logging.getLogger("data.indicators").setLevel(logging.ERROR)


# ── Genes por defecto (agente semilla representativo) ─────────────────────────

DEFAULT_PARAMS_TECNICOS = {
    "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
    "ema_rapida": 9, "ema_lenta": 21,
    "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
    "peso_rsi": 0.25, "peso_ema": 0.25, "peso_macd": 0.20,
}

DEFAULT_PARAMS_RIESGO = {
    "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
    "max_drawdown_diario_pct": 0.10, "capital_por_operacion_pct": 0.50,
    "umbral_confianza_minima": 0.50, "peso_tecnico_vs_macro": 0.55,
}

DEFAULT_PARAMS_SMC = {
    "fvg_min_pips": 5.0, "ob_impulse_pips": 10.0, "range_spike_multiplier": 1.5,
    "risk_reward_target": 2.0, "macro_quarantine_minutes": 60,
    "risk_pct_per_trade": 0.015, "peso_fvg": 0.15, "peso_ob": 0.15,
    "atr_factor": 1.5, "trailing_activation_pips": 15.0,
    "trailing_distance_pips": 10.0, "atr_period": 14,
    "htf_filter_enabled": True,   # Fase 1: filtro de tendencia 1h
}

_MACRO_HOLD_BASE = {"recomendacion": "HOLD", "confianza": 0.35, "sentimiento_score": 0.0}


def _macro_signal_from_htf(htf_trend: dict, params_macro: dict) -> dict:
    """
    Replica la lógica de SubAgentMacro._sesgo_tendencia + _fallback_score para
    el backtest (sin llamar Finnhub ni LLM). Sin eventos de alto impacto, la
    tendencia HTF es la mejor señal macro disponible.
    """
    direccion = htf_trend.get("direccion", "NEUTRAL") if htf_trend else "NEUTRAL"
    peso = float(params_macro.get("peso_sesgo_tendencia", 0.40))
    if direccion == "BULL":
        return {"recomendacion": "BUY",  "confianza": round(min(0.55, peso), 4),
                "sentimiento_score": 0.0, "htf_sesgo": direccion}
    if direccion == "BEAR":
        return {"recomendacion": "SELL", "confianza": round(min(0.55, peso), 4),
                "sentimiento_score": 0.0, "htf_sesgo": direccion}
    return {**_MACRO_HOLD_BASE, "htf_sesgo": "NEUTRAL"}


# ── Trade ─────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    accion: str
    ts_entrada: datetime
    precio_entrada: float
    stop_loss: float
    take_profit: float
    capital_usado: float
    sl_fuente: str
    sl_pips: float
    trailing_activation_pips: float
    trailing_distance_pips: float
    precio_extremo_favorable: float
    # rellenados al cerrar
    ts_salida: datetime | None = None
    precio_salida: float | None = None
    resultado: str | None = None       # HIT_SL | HIT_TP | EOF
    pnl: float = 0.0


def _stub_reason(*_args, **_kwargs):
    """Fuerza la ruta heurística: los sub-agentes capturan la excepción y siguen
    con el cálculo determinista (idéntico a 'LLM no disponible')."""
    raise RuntimeError("LLM deshabilitado en backtest")


def _apply_trailing(trade: Trade, current_price: float) -> None:
    """Réplica de _apply_trailing_stop de trade_monitor.py sobre el objeto Trade."""
    activation_pips = trade.trailing_activation_pips or 0.0
    if activation_pips <= 0:
        return
    trailing_dist = (trade.trailing_distance_pips or 10.0) * 0.0001

    if trade.accion == "BUY":
        nuevo_extremo = max(trade.precio_extremo_favorable, current_price)
        profit_pips = (nuevo_extremo - trade.precio_entrada) * 10_000
    else:
        nuevo_extremo = min(trade.precio_extremo_favorable, current_price)
        profit_pips = (trade.precio_entrada - nuevo_extremo) * 10_000

    trade.precio_extremo_favorable = nuevo_extremo
    if profit_pips < activation_pips:
        return

    if trade.accion == "BUY":
        sl_propuesto = round(nuevo_extremo - trailing_dist, 5)
        trade.stop_loss = max(trade.stop_loss, sl_propuesto)
    else:
        sl_propuesto = round(nuevo_extremo + trailing_dist, 5)
        trade.stop_loss = min(trade.stop_loss, sl_propuesto)


def _pnl(trade: Trade) -> float:
    """Réplica de InvestorAgent.close_operation: P&L = Δ%_precio × nocional."""
    pe, ps, cap = trade.precio_entrada, trade.precio_salida, trade.capital_usado
    if trade.accion == "BUY":
        return round((ps - pe) / pe * cap, 4)
    return round((pe - ps) / pe * cap, 4)


# ── Motor de backtest ─────────────────────────────────────────────────────────

def _lookup_htf(htf_series: pd.DataFrame | None, ts: pd.Timestamp) -> dict:
    """Devuelve el sesgo HTF vigente al timestamp dado (última fila con ts <= ts)."""
    if htf_series is None or htf_series.empty:
        return {"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0}
    mask = htf_series["timestamp"] <= ts
    if not mask.any():
        return {"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0}
    row = htf_series[mask].iloc[-1]
    return {
        "direccion":  row["htf_direccion"],
        "ema_rapida": float(row["htf_ema_rapida"]),
        "ema_lenta":  float(row["htf_ema_lenta"]),
    }


def run_backtest(
    df: pd.DataFrame,
    params_tec: dict,
    params_riesgo: dict,
    params_smc: dict,
    params_macro: dict | None = None,
    htf_series: pd.DataFrame | None = None,
    capital_inicial: float = 10.0,
    warmup: int = 120,
) -> tuple[list[Trade], list[float]]:
    """
    Walk-forward: una posición a la vez (igual que el sistema real).

    htf_series: DataFrame de calc_htf_trend_series() con columnas
                timestamp, htf_ema_rapida, htf_ema_lenta, htf_direccion.
                Si es None el filtro HTF queda en NEUTRAL (desactivado).
    """
    from agents.sub_agent_technical import SubAgentTechnical
    from agents.sub_agent_risk import SubAgentRisk

    tech = SubAgentTechnical("backtest", params_tec, params_smc)
    risk = SubAgentRisk("backtest", params_riesgo, params_smc)
    tech.reason = _stub_reason   # type: ignore[assignment]
    risk.reason = _stub_reason   # type: ignore[assignment]

    trades: list[Trade] = []
    equity = capital_inicial
    equity_curve: list[float] = [equity]
    open_trade: Trade | None = None

    n = len(df)
    for i in range(warmup, n):
        ts = df["timestamp"].iloc[i]

        # ── Gestión de posición abierta: chequear SL/TP en la vela actual ──────
        if open_trade is not None:
            candle = {
                "high": float(df["high"].iloc[i]),
                "low":  float(df["low"].iloc[i]),
                "close": float(df["close"].iloc[i]),
            }
            resultado = check_sl_tp_intrabar(
                action=open_trade.accion,
                stop_loss=open_trade.stop_loss,
                take_profit=open_trade.take_profit,
                candle=candle,
            )
            if resultado != "OPEN":
                precio_salida = exit_price_for(
                    resultado, open_trade.stop_loss, open_trade.take_profit, candle["close"]
                )
                open_trade.ts_salida = ts
                open_trade.precio_salida = precio_salida
                open_trade.resultado = resultado
                open_trade.pnl = _pnl(open_trade)
                equity = round(equity + open_trade.pnl, 4)
                trades.append(open_trade)
                open_trade = None
            else:
                # Sin hit → aplicar trailing con el extremo favorable de la vela
                favorable = candle["low"] if open_trade.accion == "SELL" else candle["high"]
                _apply_trailing(open_trade, favorable)
            equity_curve.append(equity)
            continue

        # ── Sin posición: evaluar señal sobre la ventana hasta la vela i ───────
        window = df.iloc[: i + 1]
        htf_trend = _lookup_htf(htf_series, ts)
        signals = calc_signals(window, params_tec, params_smc, htf_trend=htf_trend)
        senal_tec = tech.analyze(signals)
        senal_mac = _macro_signal_from_htf(htf_trend, params_macro or {})
        decision = risk.analyze(senal_tec, senal_mac, capital_disponible=equity)

        if decision.accion_final in ("BUY", "SELL") and decision.stop_loss and decision.take_profit:
            pe = float(signals.precio_actual)
            open_trade = Trade(
                accion=decision.accion_final,
                ts_entrada=ts,
                precio_entrada=pe,
                stop_loss=float(decision.stop_loss),
                take_profit=float(decision.take_profit),
                capital_usado=float(decision.capital_a_usar),
                sl_fuente=decision.sl_fuente,
                sl_pips=round(abs(pe - float(decision.stop_loss)) * 10_000, 2),
                trailing_activation_pips=decision.trailing_activation_pips,
                trailing_distance_pips=decision.trailing_distance_pips,
                precio_extremo_favorable=pe,
            )
        equity_curve.append(equity)

    # Cierre forzado al final del dataset (resultado=EOF)
    if open_trade is not None:
        precio_salida = float(df["close"].iloc[-1])
        open_trade.ts_salida = df["timestamp"].iloc[-1]
        open_trade.precio_salida = precio_salida
        open_trade.resultado = "EOF"
        open_trade.pnl = _pnl(open_trade)
        equity = round(equity + open_trade.pnl, 4)
        trades.append(open_trade)
        equity_curve.append(equity)

    return trades, equity_curve


# ── Métricas y reporte ────────────────────────────────────────────────────────

def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    return round(max_dd * 100, 2)


def _resumen(trades: list[Trade], capital_inicial: float, curve: list[float], etiqueta: str) -> None:
    cerradas = [t for t in trades if t.resultado is not None]
    n = len(cerradas)
    if n == 0:
        print(f"\n=== {etiqueta} ===\nSin operaciones generadas.")
        return

    wins = [t for t in cerradas if t.pnl > 0]
    losses = [t for t in cerradas if t.pnl <= 0]
    pnl_total = round(sum(t.pnl for t in cerradas), 4)
    avg_win = round(sum(t.pnl for t in wins) / len(wins), 4) if wins else 0.0
    avg_loss = round(sum(t.pnl for t in losses) / len(losses), 4) if losses else 0.0
    buys = [t for t in cerradas if t.accion == "BUY"]
    sells = [t for t in cerradas if t.accion == "SELL"]

    by_result: dict[str, int] = {}
    for t in cerradas:
        by_result[t.resultado] = by_result.get(t.resultado, 0) + 1

    print(f"\n========== {etiqueta} ==========")
    print(f"Operaciones cerradas : {n}")
    print(f"  Ganadoras          : {len(wins)}  ({len(wins)/n*100:.1f}%)")
    print(f"  Perdedoras         : {len(losses)}  ({len(losses)/n*100:.1f}%)")
    print(f"Cierre por nivel     : {by_result}")
    print(f"Direccion            : BUY={len(buys)}  SELL={len(sells)}")
    print(f"PnL total            : ${pnl_total:+.4f}")
    print(f"Capital ${capital_inicial:.2f} -> ${capital_inicial + pnl_total:.4f}")
    print(f"PnL promedio ganadora: ${avg_win:+.4f}")
    print(f"PnL promedio perdedora: ${avg_loss:+.4f}")
    print(f"Max drawdown         : {_max_drawdown(curve)}%")


def _detalle_dia(trades: list[Trade], fecha: str) -> None:
    """Imprime las operaciones cerradas en una fecha concreta (YYYY-MM-DD)."""
    dia = [
        t for t in trades
        if t.ts_salida is not None and t.ts_salida.strftime("%Y-%m-%d") == fecha
    ]
    if not dia:
        print(f"\n(No hay operaciones cerradas el {fecha} en este dataset)")
        return
    print(f"\n--- Operaciones cerradas el {fecha} ---")
    for t in dia:
        print(
            f"  {t.accion:4s} entrada={t.precio_entrada:.5f} salida={t.precio_salida:.5f} "
            f"SL={t.stop_loss:.5f}({t.sl_fuente},{t.sl_pips:.0f}p) TP={t.take_profit:.5f} "
            f"-> {t.resultado:6s} pnl=${t.pnl:+.4f}"
        )


def _load_genes_from_db(agent_id: str) -> tuple[dict, dict, dict]:
    from db.connection import get_conn, get_dict_cursor
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT params_tecnicos, params_macro, params_riesgo, params_smc "
            "FROM agentes WHERE id = %s",
            (agent_id,),
        )
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"Agente {agent_id} no encontrado en DB.")
    return row["params_tecnicos"], row["params_riesgo"], row["params_smc"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest determinista de la estrategia (Fase 0).")
    ap.add_argument("--interval", default="15m", help="Intervalo de vela (default 15m)")
    ap.add_argument("--range", dest="range_str", default="1mo",
                    help="Rango histórico Yahoo (1mo, 60d). Default 1mo.")
    ap.add_argument("--warmup", type=int, default=120,
                    help="Velas de calentamiento antes de operar (default 120)")
    ap.add_argument("--capital", type=float, default=10.0, help="Capital inicial USD")
    ap.add_argument("--agent-id", default=None,
                    help="Cargar genes reales de un agente desde la DB (opcional)")
    ap.add_argument("--dia", default=None,
                    help="Detallar operaciones cerradas en una fecha YYYY-MM-DD")
    args = ap.parse_args()

    DEFAULT_PARAMS_MACRO = {"peso_sesgo_tendencia": 0.40, "peso_total_macro": 0.45}

    if args.agent_id:
        params_tec, params_riesgo, params_smc = _load_genes_from_db(args.agent_id)
        params_macro = DEFAULT_PARAMS_MACRO
        etiqueta = f"BASELINE genes={args.agent_id}"
    else:
        params_tec   = dict(DEFAULT_PARAMS_TECNICOS)
        params_riesgo = dict(DEFAULT_PARAMS_RIESGO)
        params_smc   = dict(DEFAULT_PARAMS_SMC)
        params_macro  = DEFAULT_PARAMS_MACRO
        etiqueta = "BASELINE genes=semilla(default)"

    print(f"Descargando OHLCV EUR/USD interval={args.interval} range={args.range_str}...")
    df = fetch_ohlcv(interval=args.interval, range_str=args.range_str)
    print(f"  {len(df)} velas  ({df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]})")
    print(f"  precio inicial={df['close'].iloc[0]:.5f}  final={df['close'].iloc[-1]:.5f}")

    htf_series = None
    htf_enabled = bool(params_smc.get("htf_filter_enabled", True))
    if htf_enabled:
        print("Descargando datos HTF (1h / 3mo) para filtro de tendencia...")
        df_1h = fetch_ohlcv(interval="1h", range_str="3mo")
        htf_series = calc_htf_trend_series(df_1h)
        ultima_htf = htf_series.iloc[-1]
        print(f"  HTF actual: EMA50={ultima_htf['htf_ema_rapida']:.5f}  "
              f"EMA200={ultima_htf['htf_ema_lenta']:.5f}  "
              f"direccion={ultima_htf['htf_direccion']}")

    trades, curve = run_backtest(
        df, params_tec, params_riesgo, params_smc,
        params_macro=params_macro,
        htf_series=htf_series,
        capital_inicial=args.capital, warmup=args.warmup,
    )
    _resumen(trades, args.capital, curve, etiqueta)

    fecha_detalle = args.dia or df["timestamp"].iloc[-1].strftime("%Y-%m-%d")
    _detalle_dia(trades, fecha_detalle)


if __name__ == "__main__":
    main()
