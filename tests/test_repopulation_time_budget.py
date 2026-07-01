"""
Test de la Fase 3 (PLAN_DE_MEJORA.md) — presupuesto de tiempo de repoblación.

Verifica que, SOLO cuando BACKTEST_MODE=multifold, si el presupuesto de
tiempo se agota, los cupos restantes saltan directo a la cascada de
degradación (forzado_cruce) sin gastar backtests en rondas de torneo/HoF —
así el ciclo nunca excede el timeout de judge_daily.yml por el costo extra
del multi-fold (~1.68× medido, ver hallazgo 3 de PLAN_DE_MEJORA.md).

En modo single (default) esta comprobación no se activa: comportamiento
legacy verificado por la suite existente (test_sesion18_repopulacion.py).

Todos con mocks — sin DB ni red.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _agent(id_: str, especie: str, fitness: float = 0.05) -> dict:
    return {
        "id": id_,
        "especie": especie,
        "fitness_score": fitness,
        "roi_total": fitness * 10,
        "generacion": 1,
        "params_tecnicos": {"rsi_periodo": 14, "peso_rsi": 0.35, "peso_ema": 0.35,
                            "peso_macd": 0.30, "ema_rapida": 9, "ema_lenta": 21,
                            "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
                            "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
                            "rsi_zona_muerta": 5.0},
        "params_macro": {"peso_noticias_alto": 0.6, "peso_noticias_medio": 0.25,
                         "peso_noticias_bajo": 0.1, "umbral_sentimiento_compra": 0.65,
                         "umbral_sentimiento_venta": 0.35, "ventana_noticias_horas": 4,
                         "peso_total_macro": 0.4, "peso_sesgo_tendencia": 0.4},
        "params_riesgo": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04,
                          "max_drawdown_diario_pct": 0.10, "capital_por_operacion_pct": 0.5,
                          "umbral_confianza_minima": 0.60, "peso_tecnico_vs_macro": 0.55},
        "params_smc": {"fvg_min_pips": 5.0, "ob_impulse_pips": 10.0,
                       "range_spike_multiplier": 1.5, "risk_reward_target": 2.0,
                       "macro_quarantine_minutes": 60, "risk_pct_per_trade": 0.015,
                       "peso_fvg": 0.15, "peso_ob": 0.15, "atr_factor": 1.5,
                       "trailing_activation_pips": 15.0, "trailing_distance_pips": 10.0,
                       "atr_period": 14, "htf_filter_enabled": 1,
                       "breakout_lookback_bars": 20, "breakout_min_pips": 5.0,
                       "peso_breakout": 0.40, "adx_period": 14, "adx_threshold": 25.0},
    }


def test_time_budget_no_afecta_modo_single_default():
    """Sin flag multifold (default), el presupuesto de tiempo nunca se activa."""
    from evolution import evolution_engine as ee
    import evolution.backtester as bt_mod

    assert bt_mod.BACKTEST_MODE == "single"  # default esperado en este proceso

    current = (
        [_agent(f"T_{i}", "tendencia") for i in range(1)]
        + [_agent(f"R_{i}", "reversion") for i in range(5)]
        + [_agent(f"B_{i}", "ruptura")   for i in range(5)]
    )

    def _mock_breed(p1, p2, child_id, today, gen, **kw):
        return _agent(child_id, kw.get("especie", "tendencia"))

    fake_bt = {"fitness": 0.05, "n_trades": 10, "oos_trades": []}
    engine = ee.EvolutionEngine(date(2026, 7, 1))

    with patch("evolution.evolution_engine.breed_agent", side_effect=_mock_breed), \
         patch("evolution.backtester.run_backtest", return_value=fake_bt), \
         patch.object(engine, "_get_hof_parents", return_value=[]):
        recovered, slots_rec_log, _ = engine._try_repopulate(
            current_population=current, parent_pool=current,
            backtest_data={"df_15m": None, "df_1h": None},
            start_idx=10, max_gen=1, sw=0.05, sp=0.08, sr=0.10,
        )

    assert len(recovered) == 4
    # Sin presupuesto activo, el torneo pasa a la primera ronda (origen="torneo").
    assert all(s["origen"] == "torneo" for s in slots_rec_log)


def test_time_budget_activo_en_multifold_salta_a_degradacion(monkeypatch):
    """Con BACKTEST_MODE=multifold y presupuesto agotado, se salta el torneo/HoF
    y se llena el cupo vía cruce forzado, sin gastar backtests de más."""
    from evolution import evolution_engine as ee
    import evolution.backtester as bt_mod

    monkeypatch.setattr(bt_mod, "BACKTEST_MODE", "multifold")
    # Primera llamada = captura de _repop_t_start (0.0); todas las siguientes
    # ya exceden el presupuesto, para cualquier cupo evaluado.
    times = iter([0.0] + [ee.REPOPULATION_TIME_BUDGET_SECONDS + 100.0] * 50)
    monkeypatch.setattr(ee.time, "monotonic", lambda: next(times))

    current = (
        [_agent(f"T_{i}", "tendencia") for i in range(1)]
        + [_agent(f"R_{i}", "reversion") for i in range(5)]
        + [_agent(f"B_{i}", "ruptura")   for i in range(5)]
    )

    def _mock_breed(p1, p2, child_id, today, gen, **kw):
        return _agent(child_id, kw.get("especie", "tendencia"))

    call_count = {"n": 0}

    def _mock_run_backtest(data, agent):
        call_count["n"] += 1
        return {"fitness": 0.05, "n_trades": 10, "oos_trades": []}

    engine = ee.EvolutionEngine(date(2026, 7, 1))
    with patch("evolution.evolution_engine.breed_agent", side_effect=_mock_breed), \
         patch("evolution.backtester.run_backtest", side_effect=_mock_run_backtest), \
         patch.object(engine, "_get_hof_parents", return_value=[]):
        recovered, slots_rec_log, deficit_restante = engine._try_repopulate(
            current_population=current, parent_pool=current,
            backtest_data={"df_15m": None, "df_1h": None},
            start_idx=10, max_gen=1, sw=0.05, sp=0.08, sr=0.10,
        )

    # Los 4 cupos de tendencia se llenan igual: la cascada nunca deja vacante.
    assert len(recovered) == 4
    assert deficit_restante == {}
    # Ninguno vino de rondas de torneo/HoF (se saltaron); todos por cruce forzado.
    assert all(s["origen"] == "forzado_cruce" for s in slots_rec_log)
    # Un solo backtest por cupo (el del cruce forzado), no N_CANDIDATE_CHILDREN
    # candidatos × rondas de reintento.
    assert call_count["n"] == 4
