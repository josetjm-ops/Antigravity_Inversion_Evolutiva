"""
Tests de la Fase 1 del rediseño de rentabilidad (PLAN_REDISENO_RENTABILIDAD.md,
evaluación de arquitectura 2026-07-02) — "fitness honesto":

  1. _real_roi_pct(): ROI real (capital_actual vs capital_inicial), reemplaza
     el uso de roi_total (suma aritmética rota) en decisiones evolutivas.
  2. _r_multiple() / _calc_metrics() del backtester: expectancy en R en vez
     de dólares absolutos — misma fórmula que evolution_engine._fitness_cte.
  3. Migración 013: columna estrategias_exitosas.fitness_registro y la
     vista v_decaimiento_oos actualizada existen en la sandbox.

Puros donde es posible (sin DB); (3) usa la sandbox Neon (conftest.py fuerza
DATABASE_URL) — requiere que la migración 013 esté aplicada en la sandbox.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution.evolution_engine import _real_roi_pct
from evolution.backtester import _r_multiple, _calc_metrics


# ─── (1) _real_roi_pct ────────────────────────────────────────────────────────

def test_real_roi_pct_ganancia():
    agent = {"capital_inicial": 10.0, "capital_actual": 12.0}
    assert _real_roi_pct(agent) == 20.0


def test_real_roi_pct_perdida():
    agent = {"capital_inicial": 10.0, "capital_actual": 9.0}
    assert _real_roi_pct(agent) == -10.0


def test_real_roi_pct_default_sin_capital_inicial():
    # Agente sin capital_inicial/capital_actual (dict mínimo de test) → 0.0,
    # nunca dispara la revocación de inmunidad por accidente.
    assert _real_roi_pct({}) == 0.0


def test_real_roi_pct_capital_inicial_cero_no_explota():
    agent = {"capital_inicial": 0.0, "capital_actual": 5.0}
    assert _real_roi_pct(agent) == 0.0


# ─── (2) _r_multiple / _calc_metrics: expectancy en R ─────────────────────────

def _trade(pnl: float, capital_usado: float = 100.0, sl_pips: float = 20.0,
           entry: float = 1.1000) -> dict:
    return {
        "pnl": pnl, "capital_usado": capital_usado, "sl_pips": sl_pips,
        "entry": entry, "accion": "BUY", "exit": entry, "hit": "TP",
    }


def test_r_multiple_ganador_es_positivo():
    # riesgo_usd = 100 * 20 * 0.0001 / 1.10 = 0.18182 → pnl=0.36364 → R=2.0
    t = _trade(pnl=0.36364)
    r = _r_multiple(t)
    assert r is not None
    assert abs(r - 2.0) < 0.01


def test_r_multiple_perdedor_es_menos_uno_al_tocar_sl():
    # Perder exactamente el riesgo planificado → R = -1.0
    riesgo_usd = 100.0 * 20.0 * 0.0001 / 1.10
    t = _trade(pnl=-riesgo_usd)
    r = _r_multiple(t)
    assert r is not None
    assert abs(r - (-1.0)) < 0.01


def test_r_multiple_none_sin_datos_de_riesgo():
    # Trade sin capital_usado/sl_pips (formato legacy) → None, no división por cero.
    t = {"pnl": 5.0, "entry": 1.10}
    assert _r_multiple(t) is None


def test_calc_metrics_expectancy_escala_invariante_a_capital():
    """
    Dos agentes con capital MUY distinto pero el MISMO perfil de riesgo (ganan/
    pierden el mismo múltiplo de su riesgo planificado) deben tener la MISMA
    expectancy en R. Esto es exactamente lo que el diseño anterior (dólares
    absolutos) rompía — ver hallazgo F1 de la auditoría 2026-07-01.

    NOTA: "fitness" (expectancy / (max_drawdown+1)) NO se afirma invariante
    aquí a propósito: max_drawdown se mide sobre la curva de capital en
    dólares con base FIJA 10.0 (ver _calc_metrics), que en el backtester real
    siempre es consistente porque capital_usado se deriva de esa misma base
    vía el sizer de riesgo. Un capital_usado 50x desacoplado de la base (como
    el agente "grande" abajo) no es un escenario que el backtester real
    produzca — por eso solo se compara expectancy, la métrica que sí importa
    que sea escala-invariante entre agentes con capital real distinto.
    """
    # Agente "pequeño": capital_usado=100, gana 2R, pierde 1R, alternando.
    trades_small = [
        _trade(pnl=0.36364, capital_usado=100.0),   # +2R
        _trade(pnl=-0.18182, capital_usado=100.0),  # -1R
        _trade(pnl=0.36364, capital_usado=100.0),   # +2R
        _trade(pnl=-0.18182, capital_usado=100.0),  # -1R
    ]
    # Agente "grande": mismo perfil de R, 50x el capital.
    trades_big = [
        _trade(pnl=0.36364 * 50, capital_usado=5000.0),
        _trade(pnl=-0.18182 * 50, capital_usado=5000.0),
        _trade(pnl=0.36364 * 50, capital_usado=5000.0),
        _trade(pnl=-0.18182 * 50, capital_usado=5000.0),
    ]
    m_small = _calc_metrics(trades_small)
    m_big   = _calc_metrics(trades_big)
    assert abs(m_small["expectancy"] - m_big["expectancy"]) < 1e-6


def test_calc_metrics_vacio():
    m = _calc_metrics([])
    assert m["n_trades"] == 0
    assert m["fitness"] == 0.0


# ─── (3) Migración 013 aplicada en la sandbox ─────────────────────────────────

def test_migracion_013_columna_fitness_registro_existe():
    from db.connection import get_conn, get_dict_cursor
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='estrategias_exitosas' "
            "AND column_name='fitness_registro'"
        )
        assert cur.fetchone() is not None


def test_migracion_013_vista_v_decaimiento_oos_actualizada():
    from db.connection import get_conn, get_dict_cursor
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_name='v_decaimiento_oos'"
        )
        assert cur.fetchone() is not None
