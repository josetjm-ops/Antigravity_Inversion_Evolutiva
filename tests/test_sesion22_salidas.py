"""
Tests unitarios para Sesión 22: salidas inteligentes como genes evolutivos.

  - Break-even stop (gen be_activation_r) en _apply_trailing_stop
  - Tope estructural de SL (_MAX_SL_PIPS) en sub_agent_risk
  - Genes nuevos: bounds, defaults y bit-flip de exit_on_reversal
  - Salida por señal contraria: las 3 condiciones (opuesta + confianza + ganancia)

Todos usan mocks — sin DB ni red.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Break-even stop en _apply_trailing_stop ──────────────────────────────────

def _op_buy(be_r: float = 0.6) -> dict:
    """BUY a 1.10000 con SL 1.09800 (20 pips) y trailing genes estándar."""
    return {
        "id": 1, "accion": "BUY",
        "precio_entrada": 1.10000,
        "stop_loss": 1.09800,
        "pips_sl": 20.0,
        "precio_extremo_favorable": 1.10000,
        "trailing_activation_pips": 15.0,
        "trailing_distance_pips": 10.0,
        "be_activation_r": be_r,
    }


def test_be_moves_sl_to_breakeven_buy():
    """BUY: al ganar be_r×R (0.6×20=12 pips) el SL sube a entrada + fricción."""
    from cron.trade_monitor import _apply_trailing_stop, _FRICTION_PIPS

    op = _op_buy(be_r=0.6)
    # Precio favorable +12 pips = justo el umbral BE; trailing (1R=20) NO activa.
    nuevo_sl, extremo = _apply_trailing_stop(op, 1.10120)
    esperado = round(1.10000 + _FRICTION_PIPS * 0.0001, 5)
    assert nuevo_sl == esperado, f"SL debía ser BE {esperado}, fue {nuevo_sl}"
    assert extremo == 1.10120


def test_be_moves_sl_to_breakeven_sell():
    """SELL: el BE baja el SL a entrada - fricción."""
    from cron.trade_monitor import _apply_trailing_stop, _FRICTION_PIPS

    op = {
        "id": 2, "accion": "SELL",
        "precio_entrada": 1.10000,
        "stop_loss": 1.10200,
        "pips_sl": 20.0,
        "precio_extremo_favorable": 1.10000,
        "trailing_activation_pips": 15.0,
        "trailing_distance_pips": 10.0,
        "be_activation_r": 0.5,
    }
    nuevo_sl, _ = _apply_trailing_stop(op, 1.09890)  # +11 pips > 0.5×20=10
    esperado = round(1.10000 - _FRICTION_PIPS * 0.0001, 5)
    assert nuevo_sl == esperado


def test_be_not_triggered_below_threshold():
    """Sin alcanzar be_r×R, el SL no se mueve."""
    from cron.trade_monitor import _apply_trailing_stop

    op = _op_buy(be_r=0.6)
    nuevo_sl, _ = _apply_trailing_stop(op, 1.10110)  # +11 pips < 12
    assert nuevo_sl == 1.09800, "El SL no debía moverse antes del umbral BE"


def test_be_disabled_when_gene_zero():
    """Gen be_activation_r=0 → BE desactivado (agentes pre-migración)."""
    from cron.trade_monitor import _apply_trailing_stop

    op = _op_buy(be_r=0.0)
    nuevo_sl, _ = _apply_trailing_stop(op, 1.10150)  # +15 pips, trailing aún no (1R=20)
    assert nuevo_sl == 1.09800


def test_be_never_worsens_sl():
    """Si el trailing ya subió el SL por encima del BE, el BE no lo baja."""
    from cron.trade_monitor import _apply_trailing_stop

    op = _op_buy(be_r=0.3)
    op["stop_loss"] = 1.10100  # SL ya mejor que el BE (trailing previo)
    nuevo_sl, _ = _apply_trailing_stop(op, 1.10150)
    assert nuevo_sl >= 1.10100, "El BE nunca debe empeorar un SL ya mejorado"


# ─── Tope estructural de SL (Sesión 22) ───────────────────────────────────────

def test_structural_sl_too_far_falls_to_atr():
    """Un SL estructural (OB/FVG) a más de _MAX_SL_PIPS se descarta y cae a ATR."""
    from agents.sub_agent_risk import SubAgentRisk, _MAX_SL_PIPS

    riesgo = {"stop_loss_pct": 0.02, "take_profit_pct": 0.04,
              "capital_por_operacion_pct": 0.5, "umbral_confianza_minima": 0.6}
    smc = {"atr_factor": 1.5, "risk_reward_target": 2.0, "risk_pct_per_trade": 0.015}
    sub = SubAgentRisk("t", riesgo, smc)

    precio = 1.15687
    senal = {
        "indicadores": {
            # OB a 61 pips (como la op #9284 real) — debe descartarse
            "ob_activo": True, "ob_direccion": "BULL",
            "ob_nivel_inf": precio - 0.00612, "ob_nivel_sup": precio,
            "fvg_activo": False, "fvg_direccion": "NONE",
            "fvg_nivel_inf": 0, "fvg_nivel_sup": 0,
            "atr": 0.0010,  # ATR 10 pips → SL ATR = 15 pips
        },
    }
    sl, tp, cap, sl_pips, fuente, _ = sub._compute_levels(precio, "BUY", 10.0, senal)
    assert sl_pips <= _MAX_SL_PIPS, f"SL de {sl_pips} pips supera el tope {_MAX_SL_PIPS}"
    assert fuente != "OB", "La fuente estructural a 61 pips debía descartarse"


def test_atr_sl_capped_at_max():
    """ATR alto × factor alto queda recortado al tope _MAX_SL_PIPS."""
    from agents.sub_agent_risk import SubAgentRisk, _MAX_SL_PIPS

    riesgo = {"stop_loss_pct": 0.02, "capital_por_operacion_pct": 0.5}
    smc = {"atr_factor": 1.8, "risk_reward_target": 2.0, "risk_pct_per_trade": 0.015}
    sub = SubAgentRisk("t", riesgo, smc)
    senal = {"indicadores": {"fvg_activo": False, "ob_activo": False,
                             "fvg_direccion": "NONE", "ob_direccion": "NONE",
                             "atr": 0.0040}}  # 40 pips × 1.8 = 72 → cap
    sl, tp, cap, sl_pips, fuente, _ = sub._compute_levels(1.15000, "BUY", 10.0, senal)
    assert sl_pips <= _MAX_SL_PIPS


# ─── Genes nuevos: bounds, defaults y bit-flip ────────────────────────────────

def test_new_genes_in_defaults_and_bounds():
    from evolution.evolution_engine import _DEFAULT_SMC_PARAMS, _BOUNDS_SMC

    assert _DEFAULT_SMC_PARAMS["be_activation_r"] == 0.6
    assert _DEFAULT_SMC_PARAMS["exit_on_reversal"] == 0
    assert _DEFAULT_SMC_PARAMS["min_profit_for_exit_r"] == 0.4
    assert _BOUNDS_SMC["be_activation_r"] == (0.3, 1.0, False)
    assert _BOUNDS_SMC["min_profit_for_exit_r"] == (0.2, 1.0, False)
    # exit_on_reversal NO se muta gaussianamente
    assert "exit_on_reversal" not in _BOUNDS_SMC
    # atr_factor recortado a 1.8
    assert _BOUNDS_SMC["atr_factor"][1] == 1.8


def test_breed_respects_new_bounds_and_flips_reversal():
    """Los hijos mantienen los genes nuevos dentro de bounds y el bit-flip
    de exit_on_reversal produce ambos valores a lo largo de muchas crianzas."""
    from evolution.evolution_engine import breed_agent, _DEFAULT_SMC_PARAMS

    base = {
        "id": "P1", "roi_total": 1.0, "generacion": 1,
        "params_tecnicos": {"rsi_periodo": 14, "rsi_sobrecompra": 70,
                            "rsi_sobreventa": 30, "ema_rapida": 9, "ema_lenta": 21,
                            "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
                            "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
                            "rsi_zona_muerta": 5.0},
        "params_macro": {"peso_noticias_alto": 0.6, "peso_noticias_medio": 0.25,
                         "peso_noticias_bajo": 0.1, "umbral_sentimiento_compra": 0.65,
                         "umbral_sentimiento_venta": 0.35, "ventana_noticias_horas": 4,
                         "peso_total_macro": 0.4, "peso_sesgo_tendencia": 0.4},
        "params_riesgo": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04,
                          "max_drawdown_diario_pct": 0.10,
                          "capital_por_operacion_pct": 0.5,
                          "umbral_confianza_minima": 0.60,
                          "peso_tecnico_vs_macro": 0.55},
        "params_smc": dict(_DEFAULT_SMC_PARAMS),
    }

    valores_reversal = set()
    for i in range(120):
        child = breed_agent(base, base, f"C{i}", date(2026, 6, 12), 2)
        smc = child["params_smc"]
        assert 0.3 <= smc["be_activation_r"] <= 1.0
        assert 0.2 <= smc["min_profit_for_exit_r"] <= 1.0
        assert 0.8 <= smc["atr_factor"] <= 1.8
        assert smc["exit_on_reversal"] in (0, 1)
        valores_reversal.add(smc["exit_on_reversal"])

    # Con flip 10% en 120 crianzas, ambos valores deben aparecer
    assert valores_reversal == {0, 1}, (
        f"El bit-flip debía producir 0 y 1; produjo {valores_reversal}"
    )


# ─── Salida por señal contraria: las 3 condiciones ────────────────────────────

def _reversal_env(senal: dict, ops: list[dict], precio_actual: float = 1.10120):
    """Construye los mocks comunes para _check_reversal_exits."""
    import pandas as pd

    df = pd.DataFrame({"close": [1.10000, precio_actual]})

    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = ops
    conn.cursor.return_value = cur

    class _Ctx:
        def __enter__(self):  return conn
        def __exit__(self, *a): return False

    mock_tec = MagicMock()
    mock_tec.analyze.return_value = senal

    return df, _Ctx(), cur, mock_tec


def _open_buy_op(exit_gene: int = 1, min_profit_r: float = 0.4) -> dict:
    return {
        "id": 77, "agente_id": "AG_1", "accion": "BUY",
        "precio_entrada": 1.10000, "pips_sl": 20.0, "stop_loss": 1.09800,
        "params_tecnicos": {}, "especie": "tendencia",
        "params_smc": {"exit_on_reversal": exit_gene,
                       "min_profit_for_exit_r": min_profit_r},
        "params_riesgo": {"umbral_confianza_minima": 0.60},
    }


def test_reversal_exit_closes_on_strong_opposite_signal():
    """BUY +12 pips (≥0.4R=8) + señal SELL conf 0.75 ≥ 0.60 → cierra."""
    import cron.trade_monitor as tm

    df, ctx, cur, mock_tec = _reversal_env(
        {"recomendacion": "SELL", "confianza": 0.75}, [_open_buy_op()],
    )
    closed_calls = []
    with patch.object(tm, "_close_op",
                      side_effect=lambda *a, **k: closed_calls.append((a, k))), \
         patch("db.connection.get_conn", return_value=ctx), \
         patch("data.indicators.calc_signals", return_value=MagicMock()), \
         patch("agents.sub_agent_technical.SubAgentTechnical", return_value=mock_tec):
        n = tm._check_reversal_exits(df, {"direccion": "NEUTRAL"})

    assert n == 1, "Debía cerrar la posición por señal contraria fuerte"
    assert len(closed_calls) == 1
    _args, _kwargs = closed_calls[0]
    assert _kwargs.get("resultado") == "REVERSAL"


def test_reversal_exit_skips_below_profit_floor():
    """Con ganancia bajo el piso (min_profit_r alto) NO cierra aunque la señal
    sea opuesta y fuerte — nunca se sale en pérdida/ganancia insuficiente."""
    import cron.trade_monitor as tm

    df, ctx, cur, mock_tec = _reversal_env(
        {"recomendacion": "SELL", "confianza": 0.90},
        [_open_buy_op(min_profit_r=1.0)],  # exige 20 pips; solo hay 12
    )
    with patch.object(tm, "_close_op") as mock_close, \
         patch("db.connection.get_conn", return_value=ctx), \
         patch("data.indicators.calc_signals", return_value=MagicMock()), \
         patch("agents.sub_agent_technical.SubAgentTechnical", return_value=mock_tec):
        n = tm._check_reversal_exits(df, {"direccion": "NEUTRAL"})

    assert n == 0
    mock_close.assert_not_called()


def test_reversal_exit_skips_weak_or_same_direction_signal():
    """Señal débil (conf < umbral) o misma dirección → no cierra."""
    import cron.trade_monitor as tm

    # Caso 1: opuesta pero débil
    df, ctx, cur, mock_tec = _reversal_env(
        {"recomendacion": "SELL", "confianza": 0.40}, [_open_buy_op()],
    )
    with patch.object(tm, "_close_op") as mock_close, \
         patch("db.connection.get_conn", return_value=ctx), \
         patch("data.indicators.calc_signals", return_value=MagicMock()), \
         patch("agents.sub_agent_technical.SubAgentTechnical", return_value=mock_tec):
        assert tm._check_reversal_exits(df, {"direccion": "NEUTRAL"}) == 0
    mock_close.assert_not_called()

    # Caso 2: fuerte pero misma dirección
    df, ctx, cur, mock_tec = _reversal_env(
        {"recomendacion": "BUY", "confianza": 0.90}, [_open_buy_op()],
    )
    with patch.object(tm, "_close_op") as mock_close, \
         patch("db.connection.get_conn", return_value=ctx), \
         patch("data.indicators.calc_signals", return_value=MagicMock()), \
         patch("agents.sub_agent_technical.SubAgentTechnical", return_value=mock_tec):
        assert tm._check_reversal_exits(df, {"direccion": "NEUTRAL"}) == 0
    mock_close.assert_not_called()
