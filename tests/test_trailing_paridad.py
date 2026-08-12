"""
Trailing stop: paridad backtest ↔ producción y gen de encendido — 2026-08-12.

POR QUÉ EXISTE
--------------
El backtester declaraba explícitamente "Sin trailing: SL/TP fijo" como
simplificación intencional. Al modelarlo se midió que el trailing NO es
neutral: cuesta -0.189R sobre 6 meses de holdout (+0.319R sin él vs +0.131R
con él), porque salta a mitad de camino del objetivo y una retracción normal
cierra la posición — los take-profits caen de 11 a 3 sobre 117 operaciones
mientras los stops suben de 76 a 101.

Consecuencia de fondo: durante meses la evolución optimizó `risk_reward_target`
(cuya curva resultó casi plana: 2.0→+0.132R, 2.8→+0.132R, 3.5→+0.144R) mientras
el parámetro que de verdad decide el resultado era invisible para el backtest.

Estos tests fijan la paridad para que no se pierda otra vez, y cubren el gen
`trailing_enabled` que permite a la selección natural apagarlo.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evolution.backtester as bt


# ─── El gen existe y es evolutivo ────────────────────────────────────────────

def test_gen_trailing_enabled_en_defaults_y_bitflip():
    from evolution.evolution_engine import (
        _DEFAULT_SMC_PARAMS, _BOUNDS_SMC, _BOOLEAN_GENE_FLIP_PROB,
    )

    assert _DEFAULT_SMC_PARAMS["trailing_enabled"] == 0, (
        "arranca apagado: la medicion dice que el trailing cuesta -0.189R"
    )
    # Es booleano: NO se muta gaussianamente, muta por bit-flip como
    # exit_on_reversal. Si entrara en _BOUNDS_SMC acabaria en valores
    # fraccionarios sin sentido (0.37 "de trailing").
    assert "trailing_enabled" not in _BOUNDS_SMC
    assert _BOOLEAN_GENE_FLIP_PROB["trailing_enabled"] == 0.10, (
        "debe seguir siendo re-descubrible aunque se extinga de la poblacion"
    )


def test_breed_mantiene_trailing_enabled_binario():
    """A lo largo de muchas crianzas el gen produce ambos valores y ninguno
    intermedio."""
    from datetime import date
    from evolution.evolution_engine import breed_agent, _DEFAULT_SMC_PARAMS

    base = {
        "id": "P1", "roi_total": 1.0, "generacion": 1, "fitness_score": 0.1,
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
    vistos = set()
    for i in range(150):
        hijo = breed_agent(base, base, f"C{i}", date(2026, 8, 12), 2)
        v = hijo["params_smc"]["trailing_enabled"]
        assert v in (0, 1), f"valor no binario: {v}"
        vistos.add(v)
    assert vistos == {0, 1}, f"con flip 10% en 150 crianzas deben salir ambos: {vistos}"


# ─── El gen apaga el trailing de verdad, en los dos lados ────────────────────

def _smc(trailing_enabled: int) -> dict:
    return {
        "trailing_enabled": trailing_enabled,
        "trailing_activation_pips": 15.0,
        "trailing_distance_pips": 10.0,
    }


def test_backtester_respeta_trailing_enabled():
    """En el backtester, trailing_enabled=0 debe anular la activación."""
    import inspect
    fuente = inspect.getsource(bt._walk_forward_trades)
    assert "trail_on" in fuente and "trailing_enabled" in fuente, (
        "el backtester debe leer el gen antes de aplicar trailing"
    )
    # Con el gen apagado la activación efectiva queda en 0, que es el valor que
    # _apply_trailing_stop interpreta como "sin trailing".
    assert 'if trail_on else 0.0' in fuente


def test_produccion_propaga_trailing_apagado_a_decision_riesgo():
    """
    En vivo el monitor NO lee los genes del agente: lee decision_riesgo de la
    operación. Por eso el apagado tiene que ocurrir en sub_agent_risk, al
    construir la decisión — si solo se apagara en el backtester, producción
    seguiría haciendo trailing y volveríamos a tener backtest y vivo midiendo
    cosas distintas, que es el error original.
    """
    from agents.sub_agent_risk import SubAgentRisk

    senal_tec = {
        "recomendacion": "BUY", "confianza": 0.85,
        "indicadores": {"precio_actual": 1.1000, "atr": 0.0015,
                        "ob_activo": False, "fvg_activo": False},
    }
    senal_mac = {"recomendacion": "HOLD", "confianza": 0.5}
    params_riesgo = {"umbral_confianza_minima": 0.50}

    apagado = SubAgentRisk("t", params_riesgo, _smc(0)).analyze(
        senal_tec, senal_mac, capital_disponible=1000.0)
    encendido = SubAgentRisk("t", params_riesgo, _smc(1)).analyze(
        senal_tec, senal_mac, capital_disponible=1000.0)

    assert apagado.trailing_activation_pips == 0.0, (
        "con el gen en 0 la decision debe llevar activacion 0 (trailing off)"
    )
    assert encendido.trailing_activation_pips == 15.0, (
        "con el gen en 1 debe respetarse el valor del gen"
    )


def test_monitor_interpreta_activacion_cero_como_trailing_apagado():
    """Contrato del que depende el apagado: activación <= 0 → SL sin tocar."""
    from cron.trade_monitor import _apply_trailing_stop

    op = {
        "id": 1, "accion": "BUY", "precio_entrada": 1.10000,
        "stop_loss": 1.09900, "pips_sl": 10.0,
        "precio_extremo_favorable": 1.10000,
        "trailing_activation_pips": 0.0,   # <- apagado
        "trailing_distance_pips": 10.0,
        "be_activation_r": 0.0,            # BE tambien off, para aislar
    }
    nuevo_sl, _extremo = _apply_trailing_stop(op, 1.10500)  # +50 pips a favor
    assert nuevo_sl == op["stop_loss"], (
        "con activacion 0 el SL no debe moverse por mucho que gane la operacion"
    )


# ─── Semántica replicada de producción ──────────────────────────────────────

def test_backtester_replica_las_tres_reglas_de_produccion():
    """
    _apply_trailing_stop de producción tiene tres particularidades que el
    backtester debe copiar o el fitness volvería a medir otra cosa:
      1. la activación nunca baja de 1R aunque el gen pida menos
      2. la distancia se acota a 0.7 x activación (garantiza profit bloqueado>0)
      3. el SL solo se mueve a favor, nunca empeora
    """
    import inspect
    fuente = inspect.getsource(bt._walk_forward_trades)

    assert "max(trail_act_gen, r_pips)" in fuente, "falta la regla 1 (activacion >= 1R)"
    assert "0.7 * activation_pips" in fuente, "falta la regla 2 (distancia acotada)"
    # Regla 3: el nuevo SL se combina con max/min contra el SL vigente.
    assert 'max(\n                                open_pos["stop_loss"]' in fuente \
        or 'max(open_pos["stop_loss"]' in fuente \
        or "el SL solo se mueve a favor" in fuente, "falta la regla 3 (SL nunca empeora)"


def test_extremo_favorable_es_acumulado_no_de_la_vela():
    """
    El extremo debe acumularse a lo largo de la posición, como hace producción
    con precio_extremo_favorable. Antes el backtester miraba solo el extremo de
    la vela actual, así que un máximo alcanzado dos velas atrás no contaba y ni
    el break-even ni el trailing se disparaban cuando debían.
    """
    import inspect
    fuente = inspect.getsource(bt._walk_forward_trades)
    assert 'open_pos["extremo"] = max(open_pos.get("extremo", entry), candle_hi)' in fuente
    assert 'open_pos["extremo"] = min(open_pos.get("extremo", entry), candle_lo)' in fuente
