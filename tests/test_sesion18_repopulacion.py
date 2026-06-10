"""
Tests unitarios para Sesión 18: recuperación de cupos vacantes.

Todos usan mocks — sin DB ni red.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Fixtures de agentes ──────────────────────────────────────────────────────

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


# ─── (1) Recuperación normal: déficit detectado y slot cubierto ───────────────

def test_repopulation_fills_deficit():
    """Con déficit y backtest OK, _try_repopulate devuelve un agente recuperado."""
    from evolution.evolution_engine import EvolutionEngine

    today = date(2026, 6, 9)
    engine = EvolutionEngine(today)

    # Población actual: solo 1 tendencia + 5 reversion + 5 ruptura
    # → déficit de 4 en tendencia
    current = (
        [_agent(f"T_{i}", "tendencia") for i in range(1)]
        + [_agent(f"R_{i}", "reversion") for i in range(5)]
        + [_agent(f"B_{i}", "ruptura")   for i in range(5)]
    )
    parents = current  # same pool

    # Backtest mock que SIEMPRE aprueba el umbral
    fake_bt = {"fitness": 0.05, "n_trades": 10}

    def _mock_breed(p1, p2, child_id, today, gen, **kw):
        return _agent(child_id, kw.get("especie", "tendencia"))

    with patch("evolution.evolution_engine.breed_agent", side_effect=_mock_breed), \
         patch("evolution.backtester.run_backtest", return_value=fake_bt), \
         patch.object(engine, "_get_hof_parents", return_value=[]):

        from evolution.backtester import run_backtest as _rt
        recovered, slots_rec_log, deficit_restante = engine._try_repopulate(
            current_population=current,
            parent_pool=parents,
            backtest_data={"df_15m": None, "df_1h": None},
            start_idx=10,
            max_gen=1,
            sw=0.05, sp=0.08, sr=0.10,
        )

    # Debe recuperar al menos 1 (cap = REPOPULATION_MAX_PER_CYCLE=3, déficit=4)
    assert len(recovered) >= 1, f"Se esperaba >= 1 recuperado, obtuvo {len(recovered)}"
    assert all(r.get("especie") == "tendencia" for r in recovered)
    assert len(slots_rec_log) == len(recovered)
    assert all(s["origen"] in ("torneo", "hall_of_fame") for s in slots_rec_log)
    # déficit restante > 0 porque el cap (3) < déficit (4)
    assert deficit_restante.get("tendencia", 0) >= 1


# ─── (2) Sin backtest → repopulación omitida silenciosamente ─────────────────

def test_repopulation_skips_without_backtest():
    """Si backtest_data es None, _try_repopulate devuelve listas vacías."""
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 9))
    current = [_agent(f"T_{i}", "tendencia") for i in range(2)]  # déficit de 3

    recovered, slots_rec_log, deficit_restante = engine._try_repopulate(
        current_population=current,
        parent_pool=current,
        backtest_data=None,
        start_idx=5,
        max_gen=1,
        sw=0.05, sp=0.08, sr=0.10,
    )

    assert recovered == [], "Sin backtest no debe recuperar ningún agente"
    assert slots_rec_log == []
    # El déficit se reporta pero sin candidatos
    assert deficit_restante.get("tendencia", 0) == 3


# ─── (3) Cap REPOPULATION_MAX_PER_CYCLE se respeta ───────────────────────────

def test_repopulation_respects_max_per_cycle():
    """Nunca se intentan más de REPOPULATION_MAX_PER_CYCLE slots."""
    from evolution.evolution_engine import EvolutionEngine, REPOPULATION_MAX_PER_CYCLE

    engine = EvolutionEngine(date(2026, 6, 9))
    # Déficit muy grande: 0 agentes en cada especie → 5+5+5=15 slots vacantes
    current: list[dict] = []
    parents = [_agent(f"X_{i}", "tendencia") for i in range(3)]

    fake_bt = {"fitness": 0.05, "n_trades": 10}

    def _mock_breed(p1, p2, child_id, today, gen, **kw):
        return _agent(child_id, kw.get("especie", "tendencia"))

    with patch("evolution.evolution_engine.breed_agent", side_effect=_mock_breed), \
         patch("evolution.backtester.run_backtest", return_value=fake_bt), \
         patch.object(engine, "_get_hof_parents", return_value=[]):

        recovered, _, _ = engine._try_repopulate(
            current_population=current,
            parent_pool=parents,
            backtest_data={"df_15m": None, "df_1h": None},
            start_idx=0,
            max_gen=1,
            sw=0.05, sp=0.08, sr=0.10,
        )

    assert len(recovered) <= REPOPULATION_MAX_PER_CYCLE, (
        f"No debe superar REPOPULATION_MAX_PER_CYCLE={REPOPULATION_MAX_PER_CYCLE}, "
        f"obtuvo {len(recovered)}"
    )


# ─── (4) Fallback a Hall of Fame ──────────────────────────────────────────────

def test_repopulation_hof_fallback():
    """Cuando el torneo falla, el HoF debe cubrir el slot y marcar origen='hall_of_fame'."""
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 9))
    # Solo falta 1 tendencia; las otras especies están completas.
    current = (
        [_agent(f"T_{i}", "tendencia")  for i in range(4)]  # déficit de 1
        + [_agent(f"R_{i}", "reversion") for i in range(5)]  # completo
        + [_agent(f"B_{i}", "ruptura")   for i in range(5)]  # completo
    )
    parents = [a for a in current if a["especie"] == "tendencia"]  # pool de tendencia

    # Torneo siempre falla; HoF siempre pasa.
    bad_bt  = {"fitness": 0.0, "n_trades": 0}
    good_bt = {"fitness": 0.03, "n_trades": 8}
    hof_pool = [_agent(f"HOF_{i}", "tendencia", fitness=0.10) for i in range(2)]

    def _mock_bt(data, agent, **kw):
        return good_bt if agent.get("id", "").startswith("HOF") or "_10" in agent.get("id", "") else bad_bt

    def _mock_breed(p1, p2, child_id, today, gen, **kw):
        return _agent(child_id, kw.get("especie", "tendencia"))

    # Patch run_backtest globally and also inside _try_repopulate's local import
    with patch("evolution.evolution_engine.breed_agent", side_effect=_mock_breed), \
         patch("evolution.backtester.run_backtest", return_value=bad_bt), \
         patch.object(engine, "_get_hof_parents", return_value=hof_pool):

        # Manually patch _try_repopulate's internal run_backtest to use good_bt for HoF
        import evolution.backtester as _bt_mod
        original_rb = _bt_mod.run_backtest

        call_count = {"n": 0}

        def _counting_bt(data, agent, **kw):
            call_count["n"] += 1
            # HoF-bred children get id like "2026-06-09_1X" — use good_bt
            # Tournament children also get same id but we only call good_bt after N_CANDIDATE
            return bad_bt  # torneo always fails

        # Use a simpler approach: mock _get_hof_parents to return agents that,
        # when backtested, get good_bt. Distinguish by setting a special attr.
        hof_child_ids = set()

        def _breed_tracking(p1, p2, child_id, today, gen, **kw):
            child = _agent(child_id, kw.get("especie", "tendencia"))
            if p1.get("id", "").startswith("HOF") or p2.get("id", "").startswith("HOF"):
                hof_child_ids.add(child_id)
            return child

        def _bt_aware(data, agent, **kw):
            if agent.get("id") in hof_child_ids:
                return good_bt
            return bad_bt

        with patch("evolution.evolution_engine.breed_agent", side_effect=_breed_tracking), \
             patch("evolution.backtester.run_backtest", side_effect=_bt_aware), \
             patch.object(engine, "_get_hof_parents", return_value=hof_pool):

            recovered, slots_rec_log, deficit_restante = engine._try_repopulate(
                current_population=current,
                parent_pool=parents,
                backtest_data={"df_15m": None, "df_1h": None},
                start_idx=10,
                max_gen=1,
                sw=0.05, sp=0.08, sr=0.10,
            )

    assert len(recovered) == 1, f"HoF debe cubrir el slot, obtuvo {len(recovered)}"
    assert slots_rec_log[0]["origen"] == "hall_of_fame", (
        f"Origen esperado 'hall_of_fame', obtuvo '{slots_rec_log[0]['origen']}'"
    )
    assert deficit_restante == {}


# ─── (5) Nunca se fuerza un agente que no pasa el umbral OOS ─────────────────

def test_repopulation_no_force_insert():
    """Si ni torneo ni HoF superan el umbral, el slot queda vacante."""
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 9))
    current = [_agent(f"T_{i}", "tendencia") for i in range(4)]  # déficit de 1

    bad_bt = {"fitness": 0.0, "n_trades": 3}  # nunca supera umbral

    def _mock_breed(p1, p2, child_id, today, gen, **kw):
        return _agent(child_id, kw.get("especie", "tendencia"))

    hof_pool = [_agent(f"HOF_{i}", "tendencia") for i in range(2)]

    with patch("evolution.evolution_engine.breed_agent", side_effect=_mock_breed), \
         patch("evolution.backtester.run_backtest", return_value=bad_bt), \
         patch.object(engine, "_get_hof_parents", return_value=hof_pool):

        recovered, slots_rec_log, deficit_restante = engine._try_repopulate(
            current_population=current,
            parent_pool=current,
            backtest_data={"df_15m": None, "df_1h": None},
            start_idx=10,
            max_gen=1,
            sw=0.05, sp=0.08, sr=0.10,
        )

    assert recovered == [], (
        "Nunca debe insertar un agente que no superó el umbral OOS"
    )
    assert slots_rec_log == []
    assert deficit_restante.get("tendencia", 0) == 1, (
        "El déficit debe reportarse como restante"
    )
