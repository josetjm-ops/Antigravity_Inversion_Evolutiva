"""
Tests de la numeración consecutiva real (Cambio A).

Reproducen el incidente del 2026-06-16: el slot _01 se crió, el backtest lo
rechazó (3 trades) y quedó vacante; la repoblación insertó el agente real como
_02. Tras renumerar, el único agente insertado debe llamarse _01.

Todos son puros — sin DB ni red (solo ejercitan _renumber_contiguous).
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _born(id_: str) -> dict:
    return {"id": id_}


# ─── (1) El caso real: _02 sin _01 → renumera a _01 ───────────────────────────

def test_renumber_fills_gap_from_rejected_slot():
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 16))
    # next_idx = 1; el slot _01 se rechazó (vacante), la repoblación creó _02.
    born = [_born("2026-06-16_02")]
    remap = engine._renumber_contiguous(born, start_idx=1)

    assert born[0]["id"] == "2026-06-16_01", "El único agente insertado debe ser _01"
    assert remap == {"2026-06-16_02": "2026-06-16_01"}


# ─── (2) Varios nacidos con huecos → quedan contiguos ─────────────────────────

def test_renumber_multiple_with_gaps():
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 16))
    born = [_born("2026-06-16_03"), _born("2026-06-16_05"), _born("2026-06-16_08")]
    remap = engine._renumber_contiguous(born, start_idx=3)

    assert [a["id"] for a in born] == [
        "2026-06-16_03", "2026-06-16_04", "2026-06-16_05",
    ]
    # _03 no cambia (ya estaba bien); _05→_04, _08→_05.
    assert remap == {
        "2026-06-16_05": "2026-06-16_04",
        "2026-06-16_08": "2026-06-16_05",
    }


# ─── (3) start_idx respeta agentes ya nacidos hoy (re-run) ────────────────────

def test_renumber_respects_start_idx():
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 16))
    born = [_born("X"), _born("Y")]
    engine._renumber_contiguous(born, start_idx=3)  # ya existían _01 y _02

    assert [a["id"] for a in born] == ["2026-06-16_03", "2026-06-16_04"]


# ─── (4) Lista vacía → no-op ──────────────────────────────────────────────────

def test_renumber_empty_is_noop():
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 16))
    assert engine._renumber_contiguous([], start_idx=1) == {}


# ─── (5) Sin huecos → sin cambios ni remap ────────────────────────────────────

def test_renumber_already_contiguous_no_remap():
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 16))
    born = [_born("2026-06-16_01"), _born("2026-06-16_02")]
    remap = engine._renumber_contiguous(born, start_idx=1)

    assert remap == {}
    assert [a["id"] for a in born] == ["2026-06-16_01", "2026-06-16_02"]
