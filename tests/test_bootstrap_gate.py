"""
Tests de la Fase 2 (PLAN_DE_MEJORA.md) — validación estadística real en el
torneo mediante bootstrap.

Reproducen el hallazgo de la investigación: 2026-06-27_01 (fitness=0.0105,
n_trades=5) pasaba el umbral legacy pero su IC80 inferior era -0.062 —
indistinguible de azar. El bootstrap debe rechazarlo; el legacy lo acepta
(documentando la diferencia de comportamiento que motiva la mejora).

Puros — sin DB ni red (solo ejercitan bootstrap_edge_ok y _passes_oos_gate).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution.backtester import bootstrap_edge_ok


def _trades(pnls: list[float]) -> list[dict]:
    return [{"pnl": p} for p in pnls]


# ─── (1) Menos de min_trades → rechazo directo, sin computar bootstrap ────────

def test_bootstrap_rechaza_muestra_insuficiente():
    passes, lower = bootstrap_edge_ok(_trades([0.1, -0.05, 0.2]), min_trades=8)
    assert passes is False
    assert lower is None


# ─── (2) Caso real documentado: 5 trades con expectancy positiva débil ────────
# 2026-06-27_01: fitness=0.0105 con n=5. Aun si tuviera 8+ trades del mismo
# perfil (mayoría ganancias pequeñas, alguna pérdida grande ocasional), el
# límite inferior IC80 debe quedar bajo cero.

def test_bootstrap_rechaza_edge_debil_con_alta_dispersion():
    # 8 trades: 6 ganancias pequeñas + 2 pérdidas grandes → expectancy media
    # positiva pero muy dispersa (perfil típico de "suerte" con muestra corta).
    pnls = [0.05, 0.04, 0.06, 0.03, 0.05, 0.04, -0.25, -0.20]
    passes, lower = bootstrap_edge_ok(_trades(pnls), iters=2000, ci=0.80, min_trades=8, seed=42)
    assert passes is False
    assert lower is not None and lower < 0


# ─── (3) Edge claro y consistente → el bootstrap SÍ lo aprueba ────────────────

def test_bootstrap_aprueba_edge_consistente():
    # 15 trades con expectancy positiva estable (ganancias > pérdidas de forma
    # consistente, baja dispersión relativa) → IC80 inferior > 0.
    pnls = [0.06, 0.05, 0.07, -0.03, 0.06, 0.05, -0.02, 0.06,
            0.05, 0.07, -0.03, 0.06, 0.05, 0.06, -0.02]
    passes, lower = bootstrap_edge_ok(_trades(pnls), iters=2000, ci=0.80, min_trades=8, seed=42)
    assert passes is True
    assert lower is not None and lower > 0


# ─── (4) Determinismo: misma seed → mismo resultado (reproducibilidad en tests) ─

def test_bootstrap_es_determinista_con_seed():
    pnls = [0.05, -0.04, 0.06, 0.03, -0.05, 0.04, 0.05, -0.02, 0.06]
    r1 = bootstrap_edge_ok(_trades(pnls), seed=7)
    r2 = bootstrap_edge_ok(_trades(pnls), seed=7)
    assert r1 == r2


# ─── (5) Integración: _passes_oos_gate respeta TOURNAMENT_GATE_MODE ───────────

def test_passes_oos_gate_legacy_vs_bootstrap(monkeypatch):
    from evolution import evolution_engine as ee
    import evolution.backtester as bt_mod

    # Caso documentado: fitness apenas positivo, n=5 (pasa legacy, min_trades
    # de bootstrap=8 lo rechaza directo por muestra insuficiente).
    bt = {
        "fitness": 0.0105,
        "n_trades": 5,
        "oos_trades": _trades([0.05, 0.04, -0.03, 0.05, -0.06]),
    }

    monkeypatch.setattr(bt_mod, "TOURNAMENT_GATE_MODE", "legacy")
    assert ee._passes_oos_gate(bt) is True  # umbral legacy: fitness>0 & n>=5

    monkeypatch.setattr(bt_mod, "TOURNAMENT_GATE_MODE", "bootstrap")
    assert ee._passes_oos_gate(bt) is False  # bootstrap: n=5 < BOOTSTRAP_MIN_TRADES=8
