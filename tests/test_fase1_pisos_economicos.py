"""
Tests de los pisos económicos duros — auditoría forex 2026-07-31.

Contexto: la auditoría de producción encontró P&L bruto +$0.33 sobre 206
operaciones pero fricción de -$2.73 (P&L neto -$2.41). La causa no era mala
selección de señales sino objetivos demasiado pequeños frente al costo fijo
por operación. Estos tests cubren los dos mecanismos que se instalaron en
agents/sub_agent_risk.py como PISOS EN TIEMPO REAL — no solo límites de
mutación evolutiva — para corregir de inmediato a los agentes YA vivos con
genes legacy, sin esperar a que la selección natural los reemplace:

  1. MIN_RISK_REWARD_TARGET: el R:R efectivo nunca baja de 2.5, sea cual sea
     el gen del agente.
  2. Regla de peaje: si el objetivo en pips no llega a
     MIN_TARGET_TO_FRICTION_RATIO × fricción, la operación se rechaza (HOLD).
     Con los pisos de SL (_MIN_SL_PIPS=10) y R:R (2.5) activos, el objetivo
     mínimo ya es 25 pips >> 14 pips (10×fricción 1.4) — la regla es defensa
     en profundidad, se fuerza aquí bajando manualmente el umbral SL para
     ejercitarla de forma aislada.

Todos usan mocks — sin DB ni red.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _senal_buy(precio=1.1000, atr=0.0015):
    return {
        "recomendacion": "BUY",
        "confianza": 0.80,
        "indicadores": {
            "precio_actual": precio,
            "atr": atr,
            "ob_activo": False, "fvg_activo": False,
        },
    }


_SENAL_MAC_NEUTRAL = {"recomendacion": "HOLD", "confianza": 0.5}


def test_risk_reward_floor_se_aplica_pese_a_gen_legacy():
    """
    Un agente con risk_reward_target=1.5 (perfil legacy pre-auditoría) debe
    operar con R:R efectivo >= MIN_RISK_REWARD_TARGET (2.5), no con su gen.
    """
    from agents.sub_agent_risk import SubAgentRisk, MIN_RISK_REWARD_TARGET

    params_smc = {"risk_reward_target": 1.5, "atr_factor": 1.5}
    sub = SubAgentRisk("t", {"umbral_confianza_minima": 0.50}, params_smc)

    decision = sub.analyze(_senal_buy(), _SENAL_MAC_NEUTRAL, capital_disponible=10.0)

    assert decision.accion_final == "BUY"
    sl_pips = abs(decision.stop_loss - 1.1000) * 10_000
    tp_pips = abs(decision.take_profit - 1.1000) * 10_000
    rr_efectivo = tp_pips / sl_pips
    assert rr_efectivo >= MIN_RISK_REWARD_TARGET - 1e-6, (
        f"R:R efectivo {rr_efectivo:.3f} debe ser >= piso {MIN_RISK_REWARD_TARGET} "
        f"pese al gen legacy 1.5"
    )


def test_risk_reward_por_encima_del_piso_no_se_toca():
    """Un agente ya alineado con el perfil campeón (R:R 3.8) no debe verse
    recortado por el piso — el piso solo sube, nunca baja."""
    from agents.sub_agent_risk import SubAgentRisk

    params_smc = {"risk_reward_target": 3.8, "atr_factor": 1.0}
    sub = SubAgentRisk("t", {"umbral_confianza_minima": 0.50}, params_smc)

    decision = sub.analyze(_senal_buy(), _SENAL_MAC_NEUTRAL, capital_disponible=10.0)

    sl_pips = abs(decision.stop_loss - 1.1000) * 10_000
    tp_pips = abs(decision.take_profit - 1.1000) * 10_000
    rr_efectivo = tp_pips / sl_pips
    assert abs(rr_efectivo - 3.8) < 0.05


def test_regla_de_peaje_rechaza_objetivo_insuficiente(monkeypatch):
    """
    Si el objetivo no alcanza a cubrir la fricción con margen amplio, la
    operación se convierte en HOLD con capital_a_usar=0 aunque la señal
    combinada fuera BUY/SELL con confianza alta.

    Se ejercita la regla de forma aislada bajando manualmente el piso de R:R
    a 1.0 (simulando una ruta de cálculo que no pase por MIN_RISK_REWARD_
    TARGET) para que el objetivo quede por debajo de MIN_TARGET_TO_FRICTION_
    RATIO x fricción, que es la condición real que la regla debe atrapar.
    """
    import agents.sub_agent_risk as sar

    monkeypatch.setattr(sar, "MIN_RISK_REWARD_TARGET", 1.0)

    params_smc = {"risk_reward_target": 1.0, "atr_factor": 0.05}
    sub = sar.SubAgentRisk("t", {"umbral_confianza_minima": 0.50}, params_smc)

    # ATR muy pequeño -> el piso _MIN_SL_PIPS (10 pips) fija el SL real, y con
    # RR=1.0 el TP queda en 10 pips, por debajo de 10x1.4=14 -> debe rechazarse.
    decision = sub.analyze(_senal_buy(atr=0.00005), _SENAL_MAC_NEUTRAL,
                            capital_disponible=10.0)

    assert decision.accion_final == "HOLD"
    assert decision.stop_loss is None
    assert decision.take_profit is None
    assert decision.capital_a_usar == 0.0


def test_regla_de_peaje_no_dispara_con_los_pisos_de_produccion():
    """
    Con los pisos de producción activos (SL>=10 pips, R:R>=2.5), el objetivo
    mínimo posible es 25 pips, muy por encima de 10x1.4=14 pips: la regla de
    peaje NUNCA debería disparar en operación normal. Este test documenta esa
    garantía en vez de asumirla.
    """
    from agents.sub_agent_risk import (
        MIN_RISK_REWARD_TARGET, MIN_TARGET_TO_FRICTION_RATIO, _FRICTION_PIPS,
    )
    from agents.sub_agent_risk import _MIN_SL_PIPS

    objetivo_minimo_posible = _MIN_SL_PIPS * MIN_RISK_REWARD_TARGET
    umbral_peaje = _FRICTION_PIPS * MIN_TARGET_TO_FRICTION_RATIO
    assert objetivo_minimo_posible > umbral_peaje, (
        f"objetivo minimo {objetivo_minimo_posible} debe superar el umbral de "
        f"peaje {umbral_peaje} para que la regla sea solo defensa en profundidad"
    )
