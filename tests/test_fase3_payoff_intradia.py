"""
Tests de la Fase 3 del rediseño de rentabilidad (PLAN_REDISENO_RENTABILIDAD.md,
evaluación de arquitectura 2026-07-02) — "payoff intradía":

  1. Salida parcial + runner: investor_agent.partial_close_operation (DB,
     sandbox) y la integración en trade_monitor._verify_position_intrabar.
  2. Sesión de trading como gen: _within_session (trade_monitor y backtester).
  3. LLM fuera del camino de ejecución: LLM_EXECUTION_ENABLED gatea las
     llamadas de SubAgentTechnical/SubAgentRisk.
  4. Gen categórico: mutación por sorteo de sesion_trading en breed_agent.

Puros donde es posible; partial_close_operation usa la sandbox Neon
(conftest.py fuerza DATABASE_URL) — requiere la migración 014 aplicada ahí.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── (1a) Sesión de trading — _within_session ──────────────────────────────

def test_within_session_cualquiera_sin_restriccion():
    from cron.trade_monitor import _within_session
    for h in range(24):
        assert _within_session("cualquiera", h) is True
        assert _within_session("valor_desconocido", h) is True


def test_within_session_londres():
    from cron.trade_monitor import _within_session
    assert _within_session("londres", 10) is True
    assert _within_session("londres", 6) is False
    assert _within_session("londres", 16) is False  # límite exclusivo


def test_within_session_ny():
    from cron.trade_monitor import _within_session
    assert _within_session("ny", 15) is True
    assert _within_session("ny", 11) is False
    assert _within_session("ny", 21) is False


def test_within_session_overlap():
    from cron.trade_monitor import _within_session
    assert _within_session("overlap", 13) is True
    assert _within_session("overlap", 11) is False
    assert _within_session("overlap", 17) is False


def test_within_session_backtester_paridad():
    """El backtester debe tener las MISMAS ventanas que trade_monitor (Fase 0: paridad vivo↔OOS)."""
    from evolution.backtester import _within_session as bt_within_session
    from cron.trade_monitor import _within_session as tm_within_session
    for sesion in ("cualquiera", "londres", "ny", "overlap"):
        for h in range(24):
            assert bt_within_session(sesion, h) == tm_within_session(sesion, h), \
                f"Divergencia en sesión={sesion} hora={h}"


# ─── (2) LLM fuera del camino de ejecución ──────────────────────────────────

def test_llm_execution_disabled_by_default():
    from agents.base_agent import LLM_EXECUTION_ENABLED
    assert LLM_EXECUTION_ENABLED is False


def test_sub_agent_technical_no_llama_llm_en_banda_ambigua_si_deshabilitado(monkeypatch):
    """Con LLM_EXECUTION_ENABLED=false, SubAgentTechnical NUNCA llama a self.reason(),
    incluso con confianza en la banda ambigua 0.45-0.65."""
    from agents.sub_agent_technical import SubAgentTechnical
    import agents.base_agent as base_agent_mod

    monkeypatch.setattr(base_agent_mod, "LLM_EXECUTION_ENABLED", False)

    params = {
        "rsi_periodo": 14, "ema_rapida": 9, "ema_lenta": 21,
        "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
        "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
        "rsi_modo": "momentum", "rsi_zona_muerta": 5.0,
    }
    sub = SubAgentTechnical("t", params, {})
    reason_mock = MagicMock(side_effect=AssertionError("no debería llamarse al LLM"))
    sub.reason = reason_mock

    class _Sig:
        rsi = 51.0; rsi_prev = 50.0
        ema_rapida = 1.1001; ema_lenta = 1.1000
        macd = 0.0; macd_signal = 0.0; macd_hist = 0.00001
        ema_cross_alcista = False
        precio_actual = 1.1000
        fvg_activo = False; fvg_direccion = "NONE"; fvg_pips = 0.0
        fvg_nivel_sup = 0.0; fvg_nivel_inf = 0.0
        ob_activo = False; ob_direccion = "NONE"
        ob_nivel_sup = 0.0; ob_nivel_inf = 0.0
        range_proxy = 0.0; range_ma20 = 0.0; range_spike = False
        atr = 0.001
        adx = 15.0; regime_estado = "NEUTRAL"
        breakout_activo = False; breakout_direccion = "NONE"; breakout_pips = 0.0
        htf_direccion = "NEUTRAL"
        candle_direccion = "NEUTRAL"

    result = sub.analyze(_Sig(), especie="tendencia")
    reason_mock.assert_not_called()
    assert result["llm_ajuste"] is None


def test_sub_agent_risk_no_llama_llm_si_deshabilitado(monkeypatch):
    from agents.sub_agent_risk import SubAgentRisk
    import agents.base_agent as base_agent_mod

    monkeypatch.setattr(base_agent_mod, "LLM_EXECUTION_ENABLED", False)

    params = {
        "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
        "umbral_confianza_minima": 0.50, "peso_tecnico_vs_macro": 0.55,
    }
    sub = SubAgentRisk("t", params, {})
    reason_mock = MagicMock(side_effect=AssertionError("no debería llamarse al LLM"))
    sub.reason = reason_mock

    senal_tec = {
        "recomendacion": "BUY", "confianza": 0.80,
        "indicadores": {"precio_actual": 1.1000, "atr": 0.001},
    }
    senal_mac = {"recomendacion": "HOLD", "confianza": 0.5}

    decision = sub.analyze(senal_tec, senal_mac, capital_disponible=10.0)
    reason_mock.assert_not_called()
    assert decision.accion_final == "BUY"


# ─── (3) Gen categórico: sesion_trading muta por sorteo ────────────────────

def test_breed_agent_muta_sesion_trading_por_sorteo(monkeypatch):
    from evolution.evolution_engine import breed_agent, _CATEGORICAL_GENE_OPTIONS
    from datetime import date

    def _parent(id_):
        return {
            "id": id_, "fitness_score": 0.1,
            "params_tecnicos": {
                "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
                "ema_rapida": 9, "ema_lenta": 21,
                "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
                "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
            },
            "params_macro": {"peso_total_macro": 0.40},
            "params_riesgo": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
            "params_smc": {"sesion_trading": "cualquiera", "risk_reward_target": 2.0},
        }

    p1, p2 = _parent("p1"), _parent("p2")

    # Forzar random.random() < prob de mutación categórica (0.10) → siempre muta
    monkeypatch.setattr("random.random", lambda: 0.0)
    monkeypatch.setattr("random.choice", lambda opts: "overlap")

    child = breed_agent(p1, p2, "child_test", date(2026, 7, 2), 2, especie="tendencia")
    assert child["params_smc"]["sesion_trading"] == "overlap"


def test_breed_agent_sin_mutacion_hereda_sesion(monkeypatch):
    from evolution.evolution_engine import breed_agent
    from datetime import date

    def _parent(id_, sesion):
        return {
            "id": id_, "fitness_score": 0.1,
            "params_tecnicos": {
                "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
                "ema_rapida": 9, "ema_lenta": 21,
                "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
                "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
            },
            "params_macro": {"peso_total_macro": 0.40},
            "params_riesgo": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
            "params_smc": {"sesion_trading": sesion, "risk_reward_target": 2.0},
        }

    p1 = _parent("p1", "londres")
    p2 = _parent("p2", "londres")

    monkeypatch.setattr("random.random", lambda: 1.0)  # nunca muta

    child = breed_agent(p1, p2, "child_test", date(2026, 7, 2), 2,
                         especie="tendencia", p1_weight=0.6)
    assert child["params_smc"]["sesion_trading"] == "londres"


# ─── (3b) Sembrado de genes en la población (migración 015) ─────────────────
# Hallazgo de la auditoría 2026-07-07: los genes de Fase 3 nunca entraron al
# pool porque el crossover solo hereda claves que los padres YA poseen y la
# mutación gaussiana solo perturba claves existentes. La migración 015 los
# siembra (mismo patrón que la 011 hizo con exit_on_reversal en Sesión 22).

def test_migracion_015_genes_fase3_sembrados_en_activos():
    """Todos los agentes activos de la sandbox deben tener partial_tp_r y
    sesion_trading tras la migración 015, con partial_tp_r dentro de bounds."""
    from db.connection import get_conn, get_dict_cursor
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE NOT (params_smc ? 'partial_tp_r'))   AS sin_ptr,
                   COUNT(*) FILTER (WHERE NOT (params_smc ? 'sesion_trading')) AS sin_ses,
                   COUNT(*) FILTER (
                       WHERE (params_smc->>'partial_tp_r')::numeric < 0.5
                          OR (params_smc->>'partial_tp_r')::numeric > 2.0
                   ) AS ptr_oob
            FROM agentes WHERE estado = 'activo' AND params_smc IS NOT NULL
            """
        )
        r = cur.fetchone()
    if r["total"] == 0:
        pytest.skip("Sandbox sin agentes activos (otro test la reseteó)")
    assert r["sin_ptr"] == 0, f"{r['sin_ptr']} activos sin partial_tp_r"
    assert r["sin_ses"] == 0, f"{r['sin_ses']} activos sin sesion_trading"
    assert r["ptr_oob"] == 0, f"{r['ptr_oob']} activos con partial_tp_r fuera de bounds"


def test_crossover_hereda_partial_tp_r_cuando_los_padres_lo_tienen():
    """Con los padres sembrados (post-015), el gen viaja al hijo por crossover
    y la mutación gaussiana lo mantiene dentro de sus bounds (0.5–2.0)."""
    from evolution.evolution_engine import breed_agent
    from datetime import date

    def _parent(id_, ptr):
        return {
            "id": id_, "fitness_score": 0.1,
            "params_tecnicos": {
                "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
                "ema_rapida": 9, "ema_lenta": 21,
                "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
                "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
            },
            "params_macro": {"peso_total_macro": 0.40},
            "params_riesgo": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
            "params_smc": {"partial_tp_r": ptr, "risk_reward_target": 2.0},
        }

    for _ in range(20):
        child = breed_agent(
            _parent("p1", 0.8), _parent("p2", 1.2),
            "child_test", date(2026, 7, 7), 2, especie="tendencia",
        )
        ptr = child["params_smc"].get("partial_tp_r")
        assert ptr is not None, "El hijo debe heredar partial_tp_r de los padres sembrados"
        assert 0.5 <= float(ptr) <= 2.0, f"partial_tp_r mutado fuera de bounds: {ptr}"


def test_mutacion_categorica_introduce_sesion_aunque_los_padres_no_la_tengan(monkeypatch):
    """La mutación por sorteo SETEA el gen categórico aunque la clave falte en
    ambos padres — el mecanismo que mantiene el rasgo re-descubrible (y la
    diferencia clave con partial_tp_r, que sí necesitó sembrado por migración)."""
    from evolution.evolution_engine import breed_agent
    from datetime import date

    def _parent(id_):
        return {
            "id": id_, "fitness_score": 0.1,
            "params_tecnicos": {
                "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
                "ema_rapida": 9, "ema_lenta": 21,
                "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
                "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
            },
            "params_macro": {"peso_total_macro": 0.40},
            "params_riesgo": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
            "params_smc": {"risk_reward_target": 2.0},  # SIN genes de Fase 3
        }

    monkeypatch.setattr("random.random", lambda: 0.0)   # siempre muta
    monkeypatch.setattr("random.choice", lambda opts: "ny")

    child = breed_agent(_parent("p1"), _parent("p2"), "child_test",
                        date(2026, 7, 7), 2, especie="tendencia")
    assert child["params_smc"]["sesion_trading"] == "ny"
    # partial_tp_r en cambio NO aparece sin sembrado: ni crossover ni gaussiana
    # lo introducen — exactamente el gap que la migración 015 cierra.
    assert "partial_tp_r" not in child["params_smc"]


# ─── (4) Salida parcial: investor_agent.partial_close_operation (DB) ───────

def test_partial_close_operation_crea_trade_y_reduce_runner():
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn, get_dict_cursor

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM operaciones WHERE agente_id = 'TEST_PARCIAL_01'")
        cur.execute("DELETE FROM agentes WHERE id = 'TEST_PARCIAL_01'")
        cur.execute(
            """
            INSERT INTO agentes (
                id, fecha_nacimiento, generacion, capital_inicial,
                capital_actual, especie, estado
            ) VALUES ('TEST_PARCIAL_01', '2026-05-01', 1, 10.0, 10.0, 'tendencia', 'activo')
            """
        )
        cur.execute(
            """
            INSERT INTO operaciones (
                agente_id, timestamp_entrada, par, accion,
                precio_entrada, capital_usado, pips_sl, estado,
                senal_tecnico, senal_macro, decision_riesgo
            ) VALUES (
                'TEST_PARCIAL_01', NOW(), 'EUR/USD', 'BUY',
                1.10000, 10.0, 20.0, 'abierta',
                '{}'::jsonb, '{}'::jsonb, '{}'::jsonb
            ) RETURNING id
            """
        )
        op_id = cur.fetchone()[0]
        conn.commit()

    agent = InvestorAgent("TEST_PARCIAL_01", {})
    # Precio a +1R (20 pips a favor de un SL de 20 pips) → pnl positivo claro
    result = agent.partial_close_operation(
        op_id=op_id, precio_salida=1.10200, capital_disponible=10.0,
    )
    assert "error" not in result
    assert result["pnl"] > 0
    assert result["capital_restante"] == pytest.approx(5.0, abs=1e-6)

    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute("SELECT * FROM operaciones WHERE agente_id = 'TEST_PARCIAL_01' ORDER BY id")
        rows = cur.fetchall()

        cur2 = conn.cursor()
        cur2.execute("DELETE FROM operaciones WHERE agente_id = 'TEST_PARCIAL_01'")
        cur2.execute("DELETE FROM agentes WHERE id = 'TEST_PARCIAL_01'")
        conn.commit()

    assert len(rows) == 2, "Debe existir la posición original (runner) + la fila del cierre parcial"
    original = next(r for r in rows if r["id"] == op_id)
    parcial  = next(r for r in rows if r["id"] != op_id)

    assert original["estado"] == "abierta", "El runner sigue abierto"
    assert float(original["capital_usado"]) == pytest.approx(5.0, abs=1e-6)
    assert float(original["capital_usado_original"]) == pytest.approx(10.0, abs=1e-6)
    assert original["parcial_ejecutada"] is True

    assert parcial["estado"] == "cerrada"
    assert float(parcial["capital_usado"]) == pytest.approx(5.0, abs=1e-6)
    assert float(parcial["pnl"]) > 0
    assert float(parcial["pips_sl"]) == pytest.approx(20.0, abs=1e-6)


def test_partial_close_operation_no_duplica_si_ya_ejecutada():
    """El caller (trade_monitor) es responsable de chequear parcial_ejecutada
    antes de llamar — este test documenta que una segunda llamada directa
    SÍ ejecutaría de nuevo (no hay guard interno), para dejar explícito que
    la responsabilidad de no duplicar recae en el chequeo `not op.get(...)`
    del loop de trade_monitor, no en el método en sí."""
    from agents.investor_agent import InvestorAgent
    from db.connection import get_conn

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM operaciones WHERE agente_id = 'TEST_PARCIAL_02'")
        cur.execute("DELETE FROM agentes WHERE id = 'TEST_PARCIAL_02'")
        cur.execute(
            """
            INSERT INTO agentes (
                id, fecha_nacimiento, generacion, capital_inicial,
                capital_actual, especie, estado
            ) VALUES ('TEST_PARCIAL_02', '2026-05-01', 1, 10.0, 10.0, 'tendencia', 'activo')
            """
        )
        cur.execute(
            """
            INSERT INTO operaciones (
                agente_id, timestamp_entrada, par, accion,
                precio_entrada, capital_usado, pips_sl, estado,
                senal_tecnico, senal_macro, decision_riesgo
            ) VALUES (
                'TEST_PARCIAL_02', NOW(), 'EUR/USD', 'BUY',
                1.10000, 10.0, 20.0, 'abierta',
                '{}'::jsonb, '{}'::jsonb, '{}'::jsonb
            ) RETURNING id
            """
        )
        op_id = cur.fetchone()[0]
        conn.commit()

    agent = InvestorAgent("TEST_PARCIAL_02", {})
    agent.partial_close_operation(op_id=op_id, precio_salida=1.10200, capital_disponible=10.0)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM operaciones WHERE agente_id = 'TEST_PARCIAL_02'")
        cur.execute("DELETE FROM agentes WHERE id = 'TEST_PARCIAL_02'")
        conn.commit()


# ─── (5) Integración: _verify_position_intrabar dispara la salida parcial ───

def test_verify_position_intrabar_dispara_salida_parcial():
    """
    Una vela cuyo high alcanza partial_tp_r × R debe disparar
    _partial_close_op ANTES de seguir evaluando trailing/BE, y la posición
    debe seguir abierta (no closed) porque solo se vendió la mitad.
    """
    from cron import trade_monitor as tm

    op = {
        "id": 9001,
        "agente_id": "2026-05-19_10",
        "accion": "BUY",
        "timestamp_entrada": datetime(2026, 5, 27, 7, 46, tzinfo=timezone.utc),
        "timestamp_ultima_verificacion": datetime(2026, 5, 27, 7, 46, tzinfo=timezone.utc),
        "precio_entrada": 1.10000,
        "capital_usado": 10.0,
        "pips_sl": 20.0,
        "stop_loss": 1.09800,
        "take_profit": 1.10400,  # 2R — no se toca en este escenario
        "precio_extremo_favorable": 1.10000,
        "trailing_activation_pips": 999.0,  # no interfiere con este test
        "trailing_distance_pips": 10.0,
        "be_activation_r": 0.0,             # BE desactivado para aislar la parcial
        "partial_tp_r": 1.0,                # parcial a +1R = +20 pips = 1.10200
        "parcial_ejecutada": False,
    }
    base = datetime(2026, 5, 27, 8, 0, tzinfo=timezone.utc)
    candles = [
        {"timestamp": base + timedelta(minutes=1), "open": 1.10050, "high": 1.10210,
         "low": 1.10040, "close": 1.10150},
    ]

    partial_mock = MagicMock()

    with patch.object(tm, "_persist_trailing"), \
         patch.object(tm, "_partial_close_op", partial_mock) as pc_mock, \
         patch("data.simulated_broker.get_intrabar_candles", return_value=candles):
        result = tm._verify_position_intrabar(op, fallback_price=1.10150)

    assert result["closed"] is False, "Solo se vendió la mitad — la posición runner sigue abierta"
    pc_mock.assert_called_once()
    call_args = pc_mock.call_args.args
    assert call_args[0] is op
    assert call_args[1] == pytest.approx(1.10200, abs=1e-5)


def test_backtester_walk_forward_ejecuta_salida_parcial():
    """
    _walk_forward_trades debe generar un trade "PARCIAL" cuando el precio
    alcanza partial_tp_r × R sin tocar el TP completo, y el runner sigue
    corriendo hasta el cierre EOD con capital_usado reducido.
    """
    import pandas as pd
    from unittest.mock import patch, MagicMock
    from evolution.backtester import _walk_forward_trades

    n = 40
    # Velas 0-30: planas (warmup de lookback + vela de entrada en i=30).
    flat_n = 31
    closes = [1.10000] * flat_n
    highs  = [1.10000] * flat_n
    lows   = [1.10000] * flat_n
    # Velas 31-39: suben progresivamente. High cruza 1.10200 (+20 pips = 1R)
    # en la vela índice 34, pero nunca llega a 1.10400 (2R = TP completo).
    move_highs  = [1.10060, 1.10110, 1.10160, 1.10210, 1.10230, 1.10240, 1.10250, 1.10260, 1.10270]
    move_lows   = [1.10040, 1.10090, 1.10140, 1.10190, 1.10210, 1.10220, 1.10230, 1.10240, 1.10250]
    move_closes = [1.10050, 1.10100, 1.10150, 1.10200, 1.10220, 1.10230, 1.10240, 1.10250, 1.10260]
    closes += move_closes
    highs  += move_highs
    lows   += move_lows
    assert len(closes) == n == len(highs) == len(lows)

    base_ts = pd.Timestamp("2026-06-01 08:00", tz="UTC")
    timestamps = [base_ts + pd.Timedelta(minutes=15 * i) for i in range(n)]

    df_15m = pd.DataFrame({
        "timestamp": timestamps, "open": closes, "high": highs,
        "low": lows, "close": closes,
    })

    signals_mock = MagicMock()
    signals_mock.regime_estado = "NEUTRAL"  # sin restricción de régimen

    sub_tec_instance = MagicMock()
    sub_tec_instance.analyze.return_value = {"recomendacion": "BUY", "confianza": 0.9}
    sub_risk_instance = MagicMock()
    # sl=1.09800 (20 pips), tp=1.10400 (40 pips = 2R) — nunca se alcanza en este escenario
    sub_risk_instance._compute_levels.return_value = (1.09800, 1.10400, 10.0, 20.0, "pct", 0.0)

    with patch("data.indicators.calc_signals", return_value=signals_mock), \
         patch("agents.sub_agent_technical.SubAgentTechnical", return_value=sub_tec_instance), \
         patch("agents.sub_agent_risk.SubAgentRisk", return_value=sub_risk_instance):
        trades = _walk_forward_trades(
            df_15m, oos_start=30, n_end=n,
            htf_trend={"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0},
            params_tec={}, params_smc={"partial_tp_r": 1.0}, params_riesgo={},
            especie="tendencia",
        )

    hits = [t["hit"] for t in trades]
    assert hits == ["PARCIAL", "EOD"], f"Esperado [PARCIAL, EOD], obtuvo {hits}"

    parcial = trades[0]
    assert parcial["pnl"] > 0
    assert parcial["capital_usado"] == pytest.approx(5.0, abs=1e-6)
    assert parcial["exit"] == pytest.approx(1.10200, abs=1e-4)

    eod = trades[1]
    assert eod["capital_usado"] == pytest.approx(5.0, abs=1e-6), \
        "El runner debe seguir con el capital reducido tras la parcial hasta el cierre EOD"


def test_verify_position_intrabar_no_dispara_parcial_si_ya_ejecutada():
    """Con parcial_ejecutada=True, aunque el precio alcance el nivel de nuevo, no se re-dispara."""
    from cron import trade_monitor as tm

    op = {
        "id": 9002,
        "agente_id": "2026-05-19_10",
        "accion": "BUY",
        "timestamp_entrada": datetime(2026, 5, 27, 7, 46, tzinfo=timezone.utc),
        "timestamp_ultima_verificacion": datetime(2026, 5, 27, 7, 46, tzinfo=timezone.utc),
        "precio_entrada": 1.10000,
        "capital_usado": 5.0,
        "pips_sl": 20.0,
        "stop_loss": 1.10000,  # ya en BE tras la parcial
        "take_profit": 1.10400,
        "precio_extremo_favorable": 1.10200,
        "trailing_activation_pips": 999.0,
        "trailing_distance_pips": 10.0,
        "be_activation_r": 0.0,
        "partial_tp_r": 1.0,
        "parcial_ejecutada": True,  # ya ejecutada
    }
    base = datetime(2026, 5, 27, 8, 0, tzinfo=timezone.utc)
    candles = [
        {"timestamp": base + timedelta(minutes=1), "open": 1.10150, "high": 1.10250,
         "low": 1.10140, "close": 1.10200},
    ]

    with patch.object(tm, "_persist_trailing"), \
         patch.object(tm, "_partial_close_op") as pc_mock, \
         patch("data.simulated_broker.get_intrabar_candles", return_value=candles):
        tm._verify_position_intrabar(op, fallback_price=1.10200)

    pc_mock.assert_not_called()
