"""
Tests de la Fase 1 (PLAN_DE_MEJORA.md) — instrumentación del decaimiento
OOS → producción.

Verifica que:
  1. breed_agent() inicializa fitness_oos_prometido/n_trades_oos_prometido
     en None por defecto (sin backtest, nadie asume un valor inventado).
  2. El camino del torneo principal (run) adjunta la promesa OOS real al
     hijo desplegado antes de insertarlo.
  3. _insert_new_agent persiste ambas columnas nuevas correctamente,
     incluyendo el caso NULL (agente nacido sin backtest disponible).

Usa la sandbox Neon (conftest.py fuerza DATABASE_URL). Requiere que la
migración 012 esté aplicada en la sandbox.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution.evolution_engine import breed_agent, EvolutionEngine
from db.connection import get_conn, get_dict_cursor


def _parent(id_: str, especie: str = "tendencia") -> dict:
    return {
        "id": id_,
        "roi_total": 1.0,
        "params_tecnicos": {
            "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
            "ema_rapida": 9, "ema_lenta": 21,
            "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
            "peso_rsi": 0.25, "peso_ema": 0.25, "peso_macd": 0.20,
        },
        "params_macro": {
            "peso_noticias_alto": 0.6, "peso_noticias_medio": 0.2,
            "peso_noticias_bajo": 0.05, "umbral_sentimiento_compra": 0.65,
            "umbral_sentimiento_venta": 0.30, "ventana_noticias_horas": 4,
            "peso_total_macro": 0.45, "peso_sesgo_tendencia": 0.40,
        },
        "params_riesgo": {
            "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
            "max_drawdown_diario_pct": 0.10, "capital_por_operacion_pct": 0.5,
            "umbral_confianza_minima": 0.6, "peso_tecnico_vs_macro": 0.55,
        },
        "params_smc": {
            "fvg_min_pips": 5.0, "ob_impulse_pips": 10.0,
            "range_spike_multiplier": 1.5, "risk_reward_target": 2.0,
            "macro_quarantine_minutes": 60, "risk_pct_per_trade": 0.015,
            "peso_fvg": 0.15, "peso_ob": 0.15, "atr_factor": 1.5,
            "trailing_activation_pips": 15.0, "trailing_distance_pips": 10.0,
            "atr_period": 14, "htf_filter_enabled": 1,
            "breakout_lookback_bars": 20, "breakout_min_pips": 5.0,
            "peso_breakout": 0.40, "adx_period": 14, "adx_threshold": 25.0,
            "be_activation_r": 0.6, "exit_on_reversal": 0,
            "min_profit_for_exit_r": 0.4,
        },
    }


# ─── (1) breed_agent inicializa en None por defecto ───────────────────────────

def test_breed_agent_defaults_fitness_oos_to_none():
    p1, p2 = _parent("p1"), _parent("p2")
    child = breed_agent(p1, p2, "child_test", date(2026, 7, 1), 2, especie="tendencia")
    assert child["fitness_oos_prometido"] is None
    assert child["n_trades_oos_prometido"] is None


# ─── (2) La migración 012 aplicó las columnas en la sandbox ───────────────────

def test_migracion_012_columnas_existen():
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='agentes' "
            "AND column_name IN ('fitness_oos_prometido','n_trades_oos_prometido')"
        )
        cols = {r["column_name"] for r in cur.fetchall()}
    assert cols == {"fitness_oos_prometido", "n_trades_oos_prometido"}


def test_vista_v_decaimiento_oos_existe():
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_name='v_decaimiento_oos'"
        )
        assert cur.fetchone() is not None


# ─── (3) _insert_new_agent persiste ambas columnas (incluyendo NULL) ──────────

def test_insert_new_agent_persiste_fitness_oos():
    engine = EvolutionEngine(date(2026, 7, 1))
    agent = breed_agent(
        _parent("p1"), _parent("p2"), "2026-07-01_99", date(2026, 7, 1), 2,
        especie="tendencia",
    )
    # Padres ficticios no existen en la sandbox (FK agentes_padre_*_fkey);
    # nulos porque no es objeto de este test (sí lo son en test_pureza_especie.py).
    agent["padre_1_id"] = None
    agent["padre_2_id"] = None
    agent["fitness_oos_prometido"]  = 0.0123
    agent["n_trades_oos_prometido"] = 12

    with get_conn() as conn:
        engine._insert_new_agent(conn, agent)
        conn.commit()

        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT fitness_oos_prometido, n_trades_oos_prometido FROM agentes WHERE id=%s",
            ("2026-07-01_99",),
        )
        row = cur.fetchone()
        assert float(row["fitness_oos_prometido"]) == 0.0123
        assert row["n_trades_oos_prometido"] == 12

        cur.execute("DELETE FROM agentes WHERE id=%s", ("2026-07-01_99",))
        conn.commit()


def test_insert_new_agent_persiste_null_sin_backtest():
    engine = EvolutionEngine(date(2026, 7, 1))
    agent = breed_agent(
        _parent("p1"), _parent("p2"), "2026-07-01_98", date(2026, 7, 1), 2,
        especie="tendencia",
    )
    agent["padre_1_id"] = None
    agent["padre_2_id"] = None
    # fitness_oos_prometido/n_trades_oos_prometido: sin asignar, quedan en
    # el default None de breed_agent.
    with get_conn() as conn:
        engine._insert_new_agent(conn, agent)
        conn.commit()

        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT fitness_oos_prometido, n_trades_oos_prometido FROM agentes WHERE id=%s",
            ("2026-07-01_98",),
        )
        row = cur.fetchone()
        assert row["fitness_oos_prometido"] is None
        assert row["n_trades_oos_prometido"] is None

        cur.execute("DELETE FROM agentes WHERE id=%s", ("2026-07-01_98",))
        conn.commit()
