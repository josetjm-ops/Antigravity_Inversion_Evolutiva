"""
Tests de la Fase 3 (PLAN_DE_MEJORA.md) — walk-forward multi-fold.

Puros — sin DB ni red. Cubren:
  1. Cálculo de índices de folds (purge gap, avance entre folds, fallback
     cuando el dataset es demasiado corto).
  2. Lookup de tendencia HTF por timestamp (sin fuga hacia adelante entre folds).
  3. Agregación de fitness entre folds (media penalizada por varianza).
  4. El dispatcher run_backtest() respeta BACKTEST_MODE.
  5. Regresión del bug encontrado durante la extracción del núcleo walk-forward:
     el cierre EOD debe usar el borde del FOLD (n_end-1), no la última vela
     del dataset completo — de lo contrario los folds tempranos "verían"
     precios del futuro al cerrar posiciones abiertas al final del tramo.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evolution.backtester as bt


# ─── (1) Índices de folds ──────────────────────────────────────────────────────

def test_compute_fold_bounds_purge_gap_and_step():
    # 3 folds, train=30d, purge=1d, validate=10d, step=10d, CANDLES_PER_DAY=26.
    n_total = (0 + 30 + 1 + 10) * bt._CANDLES_PER_DAY + 2 * 10 * bt._CANDLES_PER_DAY + 5
    bounds = bt._compute_fold_bounds(n_total)
    assert len(bounds) == bt.MULTIFOLD_N_FOLDS

    cpd = bt._CANDLES_PER_DAY
    train0, oos0, end0 = bounds[0]
    assert train0 == 0
    assert oos0 == (bt.MULTIFOLD_TRAIN_DAYS + bt.MULTIFOLD_PURGE_DAYS) * cpd
    assert end0 == oos0 + bt.MULTIFOLD_VALIDATE_DAYS * cpd

    # Fold 1 avanza exactamente MULTIFOLD_STEP_DAYS respecto al fold 0.
    train1, oos1, _ = bounds[1]
    assert train1 == train0 + bt.MULTIFOLD_STEP_DAYS * cpd
    assert oos1   == oos0   + bt.MULTIFOLD_STEP_DAYS * cpd

    # Purge gap real: hay un hueco entre fin de train y comienzo de validate.
    purge_candles = oos0 - (train0 + bt.MULTIFOLD_TRAIN_DAYS * cpd)
    assert purge_candles == bt.MULTIFOLD_PURGE_DAYS * cpd


def test_compute_fold_bounds_empty_when_dataset_corto():
    bounds = bt._compute_fold_bounds(n_total=100)  # muy por debajo de 1 fold completo
    assert bounds == []


def test_run_backtest_multifold_fallback_a_single_si_dataset_corto(monkeypatch):
    calls = {"single": 0}

    def _fake_single(data, agent):
        calls["single"] += 1
        return bt._empty_result()

    monkeypatch.setattr(bt, "_run_backtest_single", _fake_single)
    tiny_df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=100, freq="15min"),
        "open": 1.10, "high": 1.10, "low": 1.10, "close": 1.10,
    })
    result = bt._run_backtest_multifold({"df_15m": tiny_df, "df_1h": tiny_df}, {})
    assert calls["single"] == 1
    assert result == bt._empty_result()


# ─── (2) Lookup HTF por timestamp ──────────────────────────────────────────────

def test_lookup_htf_at_usa_fila_vigente_no_futura():
    htf_series = pd.DataFrame({
        "timestamp":     pd.to_datetime(["2026-01-01", "2026-01-05", "2026-01-10"]),
        "htf_direccion": ["BULL", "BEAR", "BULL"],
        "htf_ema_rapida": [1.10, 1.11, 1.12],
        "htf_ema_lenta":  [1.09, 1.10, 1.11],
    })
    # Justo antes del cambio a BEAR: debe devolver la fila BULL del 01-01,
    # NUNCA la BEAR del 01-05 (eso sería fuga hacia adelante).
    r = bt._lookup_htf_at(htf_series, pd.Timestamp("2026-01-03"))
    assert r["direccion"] == "BULL"

    r2 = bt._lookup_htf_at(htf_series, pd.Timestamp("2026-01-06"))
    assert r2["direccion"] == "BEAR"


def test_lookup_htf_at_sin_datos_previos_es_neutral():
    htf_series = pd.DataFrame({
        "timestamp":      pd.to_datetime(["2026-01-05"]),
        "htf_direccion":  ["BULL"],
        "htf_ema_rapida": [1.10],
        "htf_ema_lenta":  [1.09],
    })
    r = bt._lookup_htf_at(htf_series, pd.Timestamp("2026-01-01"))  # antes de cualquier dato
    assert r["direccion"] == "NEUTRAL"


# ─── (3) Agregación de fitness entre folds ─────────────────────────────────────

def test_run_backtest_multifold_agrega_media_penalizada_por_varianza(monkeypatch):
    # 3 folds con trades canned de fitness conocido vía _calc_metrics real
    # (monkeypatchamos _walk_forward_trades para devolver trades fijos por fold).
    fold_trades = [
        [{"accion": "BUY", "entry": 1.10, "exit": 1.101, "pnl": 0.05, "hit": "TP"}],
        [{"accion": "BUY", "entry": 1.10, "exit": 1.099, "pnl": -0.03, "hit": "SL"}],
        [{"accion": "BUY", "entry": 1.10, "exit": 1.1015, "pnl": 0.08, "hit": "TP"}],
    ]
    call_iter = iter(fold_trades)
    monkeypatch.setattr(bt, "_walk_forward_trades", lambda *a, **k: next(call_iter))

    n_total = 3 * (bt.MULTIFOLD_TRAIN_DAYS + bt.MULTIFOLD_PURGE_DAYS + bt.MULTIFOLD_VALIDATE_DAYS) * bt._CANDLES_PER_DAY
    df_15m = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n_total, freq="15min"),
        "open": 1.10, "high": 1.10, "low": 1.10, "close": 1.10,
    })

    import evolution.backtester as bt_mod
    monkeypatch.setattr(bt_mod, "calc_htf_trend_series", None, raising=False)

    # calc_htf_trend_series se importa dentro de la función (lazy import desde
    # data.indicators); lo neutralizamos monkeypatcheando el módulo real para
    # que devuelva una serie vacía y el lookup caiga a NEUTRAL sin error.
    import data.indicators as indicators_mod
    monkeypatch.setattr(indicators_mod, "calc_htf_trend_series",
                         lambda df_1h: pd.DataFrame(columns=["timestamp", "htf_direccion",
                                                              "htf_ema_rapida", "htf_ema_lenta"]))

    result = bt._run_backtest_multifold({"df_15m": df_15m, "df_1h": df_15m}, {"params_tecnicos": {}})

    expected_fitnesses = [bt._calc_metrics(t)["fitness"] for t in fold_trades]
    import statistics
    mean_fit  = statistics.fmean(expected_fitnesses)
    stdev_fit = statistics.pstdev(expected_fitnesses)
    expected_agg = round(mean_fit - bt.MULTIFOLD_LAMBDA * stdev_fit, 6)

    assert result["fitness"] == expected_agg
    assert result["n_trades"] == 3  # 1 trade por fold × 3 folds
    assert len(result["oos_trades"]) == 3
    assert result["fold_fitnesses"] == [round(f, 6) for f in expected_fitnesses]


# ─── (4) Dispatcher respeta BACKTEST_MODE ──────────────────────────────────────

def test_run_backtest_dispatcher_respeta_flag_default_single(monkeypatch):
    sentinel_single = {"marker": "single"}
    sentinel_multi  = {"marker": "multifold"}
    monkeypatch.setattr(bt, "_run_backtest_single", lambda data, agent: sentinel_single)
    monkeypatch.setattr(bt, "_run_backtest_multifold", lambda data, agent: sentinel_multi)

    monkeypatch.setattr(bt, "BACKTEST_MODE", "single")
    assert bt.run_backtest({}, {}) == sentinel_single

    monkeypatch.setattr(bt, "BACKTEST_MODE", "multifold")
    assert bt.run_backtest({}, {}) == sentinel_multi


# ─── (5) Regresión: EOD cierra en el borde del FOLD, no del dataset ────────────

def test_walk_forward_eod_cierra_en_borde_del_fold_no_del_dataset(monkeypatch):
    from agents.sub_agent_technical import SubAgentTechnical
    from agents.sub_agent_risk import SubAgentRisk
    import data.indicators as indicators_mod

    class _FakeSignals:
        regime_estado = "NEUTRAL"

    monkeypatch.setattr(indicators_mod, "calc_signals",
                         lambda *a, **k: _FakeSignals())

    # Señal siempre BUY con confianza alta (ignora el contenido de signals).
    monkeypatch.setattr(
        SubAgentTechnical, "analyze",
        lambda self, signals, especie="tendencia": {
            "recomendacion": "BUY", "confianza": 0.9, "razon": "",
        },
    )
    # SL/TP deliberadamente lejísimos (0.5 = ~5000 pips): jamás se tocan
    # dentro del rango de precios sintético (1.0980-1.1020), así la posición
    # permanece abierta hasta el cierre EOD del fold.
    monkeypatch.setattr(
        SubAgentRisk, "_compute_levels",
        lambda self, precio, rec, capital, senal: (
            precio - 0.5, precio + 0.5, capital * 0.5, 5000.0, "test", None,
        ),
    )

    n_total = 400
    prices = [1.10] * 200 + [2.00] * 200  # salto brutal tras el fold (índice 200+)
    df_15m = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n_total, freq="15min"),
        "open": prices, "high": prices, "low": prices, "close": prices,
    })

    oos_start, oos_end = 100, 150  # fold termina en el índice 149, MUY antes del salto a 2.00
    trades = bt._walk_forward_trades(
        df_15m, oos_start, oos_end,
        htf_trend={"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0},
        params_tec={}, params_smc={},
        params_riesgo={"umbral_confianza_minima": 0.6},
        especie="tendencia",
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade["hit"] == "EOD"
    # El cierre debe usar el precio del borde del fold (1.10, índice 149),
    # NUNCA el precio post-salto (2.00) que vive fuera de [oos_start, oos_end).
    assert trade["exit"] == pytest.approx(df_15m["close"].iloc[oos_end - 1])
    assert trade["exit"] < 1.5  # si el bug estuviera presente, sería ~2.00
