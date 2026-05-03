"""
Test de integración del pipeline serial A → B → C.
Usa datos mock para correr sin necesidad de API keys (Alpha Vantage, DeepSeek).
Valida la lógica algorítmica pura y la persistencia en DB.
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.alpha_vantage_client import TechnicalSignals
from data.macro_scraper import EconomicEvent, MacroSnapshot
from agents.sub_agent_technical import SubAgentTechnical
from agents.sub_agent_macro import SubAgentMacro
from agents.sub_agent_risk import SubAgentRisk
from datetime import datetime, timezone


# ── Fixtures ────────────────────────────────────────────────────────────────

AGENT_ID = "2026-05-03_01"

PARAMS_TECNICOS = {
    "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
    "ema_rapida": 9, "ema_lenta": 21,
    "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
    "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
}

PARAMS_MACRO = {
    "peso_noticias_alto": 0.60, "peso_noticias_medio": 0.25, "peso_noticias_bajo": 0.10,
    "umbral_sentimiento_compra": 0.65, "umbral_sentimiento_venta": 0.35,
    "ventana_noticias_horas": 4, "peso_total_macro": 0.40,
}

PARAMS_RIESGO = {
    "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
    "max_drawdown_diario_pct": 0.10, "capital_por_operacion_pct": 0.50,
    "umbral_confianza_minima": 0.60, "peso_tecnico_vs_macro": 0.55,
}


def make_bullish_signals():
    """Señales técnicas claramente alcistas: RSI sobrevendido, EMA cruzada, MACD positivo."""
    return TechnicalSignals(
        rsi=25.0,
        ema_rapida=1.0855,
        ema_lenta=1.0830,
        macd=0.0005,
        macd_signal=0.0003,
        macd_hist=0.0002,
        precio_actual=1.0850,
        ema_cross_alcista=True,
    )


def make_bearish_signals():
    """Señales técnicas claramente bajistas: RSI sobrecomprado, EMA descendente, MACD negativo."""
    return TechnicalSignals(
        rsi=78.0,
        ema_rapida=1.0820,
        ema_lenta=1.0845,
        macd=-0.0004,
        macd_signal=-0.0001,
        macd_hist=-0.0003,
        precio_actual=1.0820,
        ema_cross_alcista=False,
    )


def make_neutral_signals():
    """Señales neutrales: RSI en zona media, EMAs paralelas."""
    return TechnicalSignals(
        rsi=50.0,
        ema_rapida=1.0840,
        ema_lenta=1.0840,
        macd=0.00001,
        macd_signal=0.00001,
        macd_hist=0.0,
        precio_actual=1.0840,
        ema_cross_alcista=False,
    )


def make_macro_snapshot(with_events: bool = False):
    snapshot = MacroSnapshot(timestamp=datetime.now(timezone.utc))
    if with_events:
        snapshot.eventos = [
            EconomicEvent(
                titulo="ECB Interest Rate Decision",
                moneda="EUR", impacto="alto",
                hora_utc=datetime.now(timezone.utc),
                actual="4.25%", previo="4.00%", estimado="4.25%",
                fuente="test",
            )
        ]
        snapshot.titulares = [
            "ECB raises rates, EUR strengthens against USD",
            "European economy shows resilience amid inflation",
        ]
    return snapshot


# ── Tests del Sub-agente A (Técnico) ────────────────────────────────────────

def test_technical_buy_signal():
    agent = SubAgentTechnical(AGENT_ID, PARAMS_TECNICOS)
    result = agent.analyze(make_bullish_signals())

    assert result["recomendacion"] == "BUY", f"Expected BUY, got {result['recomendacion']}"
    assert result["confianza"] >= 0.5
    assert "indicadores" in result
    assert result["indicadores"]["rsi"] == 25.0
    print(f"  ✓ TÉCNICO BUY: confianza={result['confianza']:.4f}")


def test_technical_sell_signal():
    agent = SubAgentTechnical(AGENT_ID, PARAMS_TECNICOS)
    result = agent.analyze(make_bearish_signals())

    assert result["recomendacion"] == "SELL", f"Expected SELL, got {result['recomendacion']}"
    assert result["confianza"] >= 0.5
    print(f"  ✓ TÉCNICO SELL: confianza={result['confianza']:.4f}")


def test_technical_hold_signal():
    agent = SubAgentTechnical(AGENT_ID, PARAMS_TECNICOS)
    result = agent.analyze(make_neutral_signals())

    assert result["recomendacion"] == "HOLD", f"Expected HOLD, got {result['recomendacion']}"
    print(f"  ✓ TÉCNICO HOLD: confianza={result['confianza']:.4f}")


# ── Tests del Sub-agente B (Macro) ───────────────────────────────────────────

def test_macro_hold_no_events():
    agent = SubAgentMacro(AGENT_ID, PARAMS_MACRO)
    snapshot = make_macro_snapshot(with_events=False)
    result = agent._fallback_score(snapshot)

    assert result["recomendacion"] == "HOLD"
    assert result["confianza"] < 0.5
    print(f"  ✓ MACRO HOLD (sin eventos): confianza={result['confianza']:.4f}")


# ── Tests del Sub-agente C (Riesgo) ─────────────────────────────────────────

def test_risk_concordant_buy():
    agent = SubAgentRisk(AGENT_ID, PARAMS_RIESGO)
    senal_tec = {"recomendacion": "BUY", "confianza": 0.80,
                 "indicadores": {"precio_actual": 1.0850}}
    senal_mac = {"recomendacion": "BUY", "confianza": 0.70,
                 "sentimiento_score": 0.6, "eventos_clave": []}
    decision = agent.analyze(senal_tec, senal_mac, capital_disponible=10.0)

    assert decision.accion_final == "BUY"
    assert decision.stop_loss is not None
    assert decision.take_profit is not None
    assert decision.stop_loss < 1.0850  # SL por debajo del precio
    assert decision.take_profit > 1.0850  # TP por encima
    print(f"  ✓ RIESGO BUY: conf={decision.confianza_final:.4f} "
          f"SL={decision.stop_loss} TP={decision.take_profit}")


def test_risk_conflicting_signals_hold():
    agent = SubAgentRisk(AGENT_ID, PARAMS_RIESGO)
    senal_tec = {"recomendacion": "BUY", "confianza": 0.65,
                 "indicadores": {"precio_actual": 1.0850}}
    senal_mac = {"recomendacion": "SELL", "confianza": 0.62,
                 "sentimiento_score": -0.3, "eventos_clave": []}
    decision = agent.analyze(senal_tec, senal_mac, capital_disponible=10.0)

    assert decision.accion_final == "HOLD", f"Expected HOLD on conflict, got {decision.accion_final}"
    print(f"  ✓ RIESGO HOLD (señales en conflicto): conf={decision.confianza_final:.4f}")


def test_risk_low_confidence_blocked():
    agent = SubAgentRisk(AGENT_ID, PARAMS_RIESGO)
    senal_tec = {"recomendacion": "BUY", "confianza": 0.40,
                 "indicadores": {"precio_actual": 1.0850}}
    senal_mac = {"recomendacion": "BUY", "confianza": 0.38,
                 "sentimiento_score": 0.2, "eventos_clave": []}
    decision = agent.analyze(senal_tec, senal_mac, capital_disponible=10.0)

    assert decision.accion_final == "HOLD", "Debe bloquear cuando confianza < umbral mínimo"
    print(f"  ✓ RIESGO BLOQUEADO (confianza insuficiente): conf={decision.confianza_final:.4f}")


# ── Test del pipeline completo A → B → C ────────────────────────────────────

def test_full_pipeline_dry_run():
    """Valida el pipeline completo sin llamadas a API externas ni DB."""
    tec = SubAgentTechnical(AGENT_ID, PARAMS_TECNICOS)
    mac = SubAgentMacro(AGENT_ID, PARAMS_MACRO)
    risk = SubAgentRisk(AGENT_ID, PARAMS_RIESGO)

    signals = make_bullish_signals()
    snapshot = make_macro_snapshot(with_events=False)

    senal_tecnico = tec.analyze(signals)
    senal_macro = mac._fallback_score(snapshot)
    senal_macro["agente_id"] = AGENT_ID
    senal_macro["peso_macro_aplicado"] = PARAMS_MACRO["peso_total_macro"]
    senal_macro["total_eventos_alto"] = 0
    senal_macro["total_titulares"] = 0

    # Inject precio_actual into senal_macro structure expected by Risk
    senal_tecnico["indicadores"]["precio_actual"] = signals.precio_actual
    decision = risk.analyze(senal_tecnico, senal_macro, capital_disponible=10.0)

    assert decision.accion_final in ("BUY", "SELL", "HOLD")
    assert 0.0 <= decision.confianza_final <= 1.0
    assert decision.capital_a_usar <= 10.0

    print(f"\n  PIPELINE COMPLETO:")
    print(f"    A (Técnico) → {senal_tecnico['recomendacion']} (conf={senal_tecnico['confianza']:.4f})")
    print(f"    B (Macro)   → {senal_macro['recomendacion']} (conf={senal_macro['confianza']:.4f})")
    print(f"    C (Riesgo)  → {decision.accion_final} (conf={decision.confianza_final:.4f})")
    print(f"    Capital a usar: ${decision.capital_a_usar:.2f}")
    if decision.stop_loss:
        print(f"    SL: {decision.stop_loss} | TP: {decision.take_profit}")


# ── Test de persistencia en DB ───────────────────────────────────────────────

def test_db_operation_persist():
    """Inserta una operación de prueba y verifica que se guardó correctamente."""
    import psycopg2
    DB = "postgresql://neondb_owner:npg_HpqvWm94yaLr@ep-crimson-heart-amtwwmvf.c-5.us-east-1.aws.neon.tech/inversion_evolutiva?channel_binding=require&sslmode=require"
    conn = psycopg2.connect(DB)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO operaciones (
            agente_id, timestamp_entrada, par, accion,
            precio_entrada, capital_usado, estado,
            senal_tecnico, senal_macro, decision_riesgo
        ) VALUES (
            '2026-05-03_01', NOW(), 'EUR/USD', 'BUY',
            1.08500, 5.0000, 'abierta',
            '{"rsi": 25.0, "recomendacion": "BUY", "confianza": 0.82}'::jsonb,
            '{"recomendacion": "HOLD", "confianza": 0.35}'::jsonb,
            '{"accion_final": "BUY", "confianza_final": 0.72, "stop_loss": 1.0633, "take_profit": 1.1254}'::jsonb
        ) RETURNING id
    """)
    op_id = cur.fetchone()[0]
    conn.commit()

    cur.execute("SELECT accion, precio_entrada, estado FROM operaciones WHERE id = %s", (op_id,))
    row = cur.fetchone()
    assert row[0] == "BUY"
    assert float(row[1]) == 1.08500
    assert row[2] == "abierta"

    # Cleanup
    cur.execute("DELETE FROM operaciones WHERE id = %s", (op_id,))
    conn.commit()
    cur.close()
    conn.close()
    print(f"  ✓ DB PERSIST: operación #{op_id} insertada y verificada correctamente")


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("Sub-agente A: señal BUY", test_technical_buy_signal),
        ("Sub-agente A: señal SELL", test_technical_sell_signal),
        ("Sub-agente A: señal HOLD", test_technical_hold_signal),
        ("Sub-agente B: HOLD sin eventos", test_macro_hold_no_events),
        ("Sub-agente C: BUY concordante", test_risk_concordant_buy),
        ("Sub-agente C: HOLD en conflicto", test_risk_conflicting_signals_hold),
        ("Sub-agente C: HOLD por baja confianza", test_risk_low_confidence_blocked),
        ("Pipeline completo A→B→C", test_full_pipeline_dry_run),
        ("Persistencia DB (operaciones)", test_db_operation_persist),
    ]

    passed = 0
    failed = 0
    print("\n" + "="*60)
    print("  INVERSIÓN EVOLUTIVA — Test Suite Fase 2")
    print("="*60)
    for name, fn in tests:
        try:
            print(f"\n[TEST] {name}")
            fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            failed += 1

    print("\n" + "="*60)
    print(f"  RESULTADO: {passed}/{len(tests)} tests pasados | {failed} fallidos")
    print("="*60)
    sys.exit(0 if failed == 0 else 1)
