"""
Tests unitarios para Sesión 17 Fases 1-5:
  (a) Torneo: threshold OOS rechaza candidatos con fitness <= umbral
  (b) Inmunidad revocada por drawdown (Fase 3)
  (c) Elegibilidad híbrida por días hábiles (Fase 4)
  (d) Gate de régimen: ruptura bloqueada en RANGO (Fase 5)
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── (a) Torneo: threshold OOS ───────────────────────────────────────────────

def test_tournament_threshold_strictly_greater():
    """Fase 1: fitness > threshold (estrictamente) y n_trades >= mínimo para desplegar."""
    from evolution.evolution_engine import TOURNAMENT_MIN_OOS_FITNESS, TOURNAMENT_MIN_OOS_TRADES

    assert TOURNAMENT_MIN_OOS_FITNESS == 0.0
    assert TOURNAMENT_MIN_OOS_TRADES == 5

    def _passes(fitness, n_trades):
        return fitness > TOURNAMENT_MIN_OOS_FITNESS and n_trades >= TOURNAMENT_MIN_OOS_TRADES

    # fitness exactamente en umbral → NO pasa (strictly greater)
    assert not _passes(0.0, 5), "fitness=0.0 exacto no debe pasar el torneo"
    # fitness positivo con trades suficientes → pasa
    assert _passes(0.001, 5), "fitness positivo con trades suficientes debe pasar"
    # trades insuficientes → no pasa aunque fitness sea bueno
    assert not _passes(0.05, 4), "trades < mínimo no debe pasar"
    # fitness negativo → no pasa
    assert not _passes(-0.01, 10), "fitness negativo no debe pasar"


# ─── (b) Inmunidad revocada por drawdown ─────────────────────────────────────

def test_immunity_revoked_by_drawdown():
    """Fase 3: agente inmune por muestra pierde inmunidad si roi <= -IMMUNITY_MAX_LOSS_PCT."""
    from evolution.evolution_engine import EvolutionEngine, IMMUNITY_MAX_LOSS_PCT

    today = date(2026, 6, 9)   # martes
    engine = EvolutionEngine(today)

    agents = [
        # immune_sample=True (trades=5<15, age=3bd<7bd) PERO roi=-10 <= -8 → revocada
        {
            "id": "A_REVOKED",
            "operaciones_total": 5,
            "fecha_nacimiento": date(2026, 6, 4),
            "roi_total": -(IMMUNITY_MAX_LOSS_PCT + 2.0),  # -10.0 %
        },
        # immune_sample=True, roi=-5 > -8 → sigue inmune (no revocada)
        {
            "id": "A_IMMUNE_OK",
            "operaciones_total": 5,
            "fecha_nacimiento": date(2026, 6, 4),
            "roi_total": -(IMMUNITY_MAX_LOSS_PCT - 3.0),  # -5.0 %
        },
        # ops=0, Periodo de Gracia Operativa — inviolable aunque roi sea devastador
        {
            "id": "A_GRACE_INVIOLABLE",
            "operaciones_total": 0,
            "fecha_nacimiento": date(2026, 6, 8),
            "roi_total": -99.0,
        },
    ]

    immune, eligible = engine._classify_eligibility(agents)
    immune_ids   = {a["id"] for a in immune}
    eligible_ids = {a["id"] for a in eligible}

    assert "A_REVOKED" in eligible_ids, "A_REVOKED debe pasar a eligible tras revocar inmunidad"
    revoked_agent = next(a for a in eligible if a["id"] == "A_REVOKED")
    assert revoked_agent.get("_immunity_revoked") is True, \
        "Debe tener flag _immunity_revoked=True para documentarlo en razon_eliminacion"

    assert "A_IMMUNE_OK" in immune_ids, \
        "A_IMMUNE_OK debe seguir inmune (roi > -IMMUNITY_MAX_LOSS_PCT)"
    assert "A_GRACE_INVIOLABLE" in immune_ids, \
        "Periodo de Gracia es inviolable; roi negativo no puede revocarlo"


# ─── (c) Elegibilidad híbrida por días hábiles ───────────────────────────────

def test_hybrid_eligibility_by_days():
    """Fase 4: elegible si n_trades >= MIN_SAMPLE_TRADES OR edad >= MIN_SAMPLE_DAYS bd."""
    from evolution.evolution_engine import EvolutionEngine, MIN_SAMPLE_TRADES, MIN_SAMPLE_DAYS

    today = date(2026, 6, 9)  # martes
    engine = EvolutionEngine(today)

    # Fechas calibradas para today=2026-06-09:
    #   date(2026, 6, 4) → age = 3 bd (jue, vie, lun) < MIN_SAMPLE_DAYS=7
    #   date(2026, 5, 28) → age = 8 bd  >= MIN_SAMPLE_DAYS=7

    agents = [
        # immune: ambas condiciones por debajo (trades=5<15 AND age=3bd<7bd)
        {
            "id": "A_IMMUNE_BOTH_LOW",
            "operaciones_total": 5,
            "fecha_nacimiento": date(2026, 6, 4),
            "roi_total": 0,
        },
        # eligible by days: trades=5<15 PERO age=8bd >= 7bd → condición híbrida cumplida
        {
            "id": "A_ELIGIBLE_BY_DAYS",
            "operaciones_total": 5,
            "fecha_nacimiento": date(2026, 5, 28),
            "roi_total": 0,
        },
        # eligible by trades: age=3bd<7bd PERO trades=20 >= 15 → condición híbrida cumplida
        {
            "id": "A_ELIGIBLE_BY_TRADES",
            "operaciones_total": 20,
            "fecha_nacimiento": date(2026, 6, 4),
            "roi_total": 0,
        },
    ]

    immune, eligible = engine._classify_eligibility(agents)
    immune_ids   = {a["id"] for a in immune}
    eligible_ids = {a["id"] for a in eligible}

    assert "A_IMMUNE_BOTH_LOW"    in immune_ids,   \
        "Debe ser inmune (trades < MIN y días < MIN)"
    assert "A_ELIGIBLE_BY_DAYS"   in eligible_ids, \
        "Debe ser elegible (edad >= MIN_SAMPLE_DAYS, aunque trades < MIN)"
    assert "A_ELIGIBLE_BY_TRADES" in eligible_ids, \
        "Debe ser elegible (trades >= MIN_SAMPLE_TRADES, aunque edad < MIN)"


# ─── (d) Gate: ruptura bloqueada en RANGO ────────────────────────────────────

def test_ruptura_gate_defaults():
    """Fase 5: _RUPTURA_SOLO_TENDENCIA=True por defecto en backtester y trade_monitor."""
    from evolution import backtester as bt
    from cron import trade_monitor as tm

    assert bt._RUPTURA_SOLO_TENDENCIA is True, \
        "backtester._RUPTURA_SOLO_TENDENCIA debe ser True por defecto"
    assert tm._RUPTURA_SOLO_TENDENCIA is True, \
        "trade_monitor._RUPTURA_SOLO_TENDENCIA debe ser True por defecto"


def test_ruptura_bloqueada_en_rango_backtester():
    """Fase 5: run_backtest genera 0 trades para ruptura cuando todo el OOS es régimen RANGO."""
    import pandas as pd
    from unittest.mock import patch, MagicMock
    from evolution.backtester import run_backtest

    # Necesita oos_start >= 50: n_total - 20*26 >= 50 → n >= 570
    n = 600
    prices = [1.10] * n
    df_15m = pd.DataFrame({"open": prices, "high": prices, "close": prices, "low": prices})
    df_1h  = pd.DataFrame({
        "open": prices[:60], "high": prices[:60],
        "close": prices[:60], "low": prices[:60],
    })

    # signals mock siempre devuelve RANGO
    signals_mock       = MagicMock()
    signals_mock.regime_estado = "RANGO"

    # htf_trend como DataFrame de una fila (calc_htf_trend_series devuelve DataFrame)
    htf_df = pd.DataFrame({
        "htf_direccion":  ["NEUTRAL"],
        "htf_ema_rapida": [1.10],
        "htf_ema_lenta":  [1.10],
    })

    agent_ruptura   = {
        "params_tecnicos": {}, "params_smc": {}, "params_riesgo": {}, "especie": "ruptura",
    }
    agent_tendencia = {
        "params_tecnicos": {}, "params_smc": {}, "params_riesgo": {}, "especie": "tendencia",
    }

    with patch("data.indicators.calc_signals",          return_value=signals_mock), \
         patch("data.indicators.calc_htf_trend_series", return_value=htf_df), \
         patch("agents.sub_agent_technical.SubAgentTechnical"), \
         patch("agents.sub_agent_risk.SubAgentRisk"):

        res_ruptura   = run_backtest({"df_15m": df_15m, "df_1h": df_1h}, agent_ruptura)
        res_tendencia = run_backtest({"df_15m": df_15m, "df_1h": df_1h}, agent_tendencia)

    assert res_ruptura["n_trades"] == 0, \
        f"ruptura en RANGO debe generar 0 trades, generó {res_ruptura['n_trades']}"
    assert res_tendencia["n_trades"] == 0, \
        f"tendencia en RANGO también bloqueada (regla previa), generó {res_tendencia['n_trades']}"


def test_ruptura_no_bloqueada_en_tendencia_backtester():
    """Fase 5: ruptura en régimen TENDENCIA pasa el gate (analyze es invocado)."""
    import pandas as pd
    from unittest.mock import patch, MagicMock
    import evolution.backtester as bt

    # Necesita oos_start >= 50: n_total - 20*26 >= 50 → n >= 570
    n = 600
    prices = [1.10] * n
    df_15m = pd.DataFrame({"open": prices, "high": prices, "close": prices, "low": prices})
    df_1h  = pd.DataFrame({
        "open": prices[:60], "high": prices[:60],
        "close": prices[:60], "low": prices[:60],
    })

    # signals mock: régimen TENDENCIA
    signals_mock = MagicMock()
    signals_mock.regime_estado = "TENDENCIA"

    htf_df = pd.DataFrame({
        "htf_direccion":  ["TENDENCIA"],
        "htf_ema_rapida": [1.10],
        "htf_ema_lenta":  [1.09],
    })

    # sub_tec.analyze retorna HOLD → n_trades=0 (pero analyze sí es llamado = gate pasó)
    mock_tec_instance = MagicMock()
    mock_tec_instance.analyze.return_value = {"recomendacion": "HOLD"}

    agent_ruptura = {
        "params_tecnicos": {}, "params_smc": {}, "params_riesgo": {}, "especie": "ruptura",
    }

    with patch("data.indicators.calc_signals",          return_value=signals_mock), \
         patch("data.indicators.calc_htf_trend_series", return_value=htf_df), \
         patch("agents.sub_agent_technical.SubAgentTechnical", return_value=mock_tec_instance), \
         patch("agents.sub_agent_risk.SubAgentRisk"):

        bt.run_backtest({"df_15m": df_15m, "df_1h": df_1h}, agent_ruptura)

    # Si analyze fue llamado → el gate de RANGO no bloqueó (especie=ruptura, régimen=TENDENCIA)
    assert mock_tec_instance.analyze.call_count > 0, \
        "ruptura en TENDENCIA debe pasar el gate y llamar sub_tec.analyze"
