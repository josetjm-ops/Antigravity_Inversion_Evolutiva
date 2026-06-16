"""
Tests de PUREZA DE ESPECIE (Sesión 25).

Política aprobada por el usuario: pureza dura — todo hijo lleva ≥1 padre de su
especie (dominante al 60%) mientras la especie no esté extinta; se permiten
padres jóvenes de la especie. Cross-species total solo si la especie tiene 0
genomas.

Unitarios puros (con mocks) — sin DB ni red.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _agent(id_: str, especie: str, fitness: float = 0.05) -> dict:
    return {
        "id": id_, "especie": especie, "estado": "activo", "generacion": 1,
        "fitness_score": fitness, "roi_total": fitness * 10,
    }


# ─── _species_dominant_pair: los 4 casos ──────────────────────────────────────

def test_pair_both_species_unchanged():
    from evolution.evolution_engine import EvolutionEngine
    a, b = _agent("A", "reversion"), _agent("B", "reversion")
    p1, p2, pw = EvolutionEngine._species_dominant_pair(a, b, "reversion", [])
    assert (p1, p2) == (a, b) and pw is None  # 60/40 por ROI, sin tocar


def test_pair_one_species_dominates():
    from evolution.evolution_engine import EvolutionEngine
    esp_, cross = _agent("S", "reversion"), _agent("X", "tendencia")
    # especie en segunda posición → debe pasar a dominante (primero) con 0.6
    p1, p2, pw = EvolutionEngine._species_dominant_pair(cross, esp_, "reversion", [])
    assert p1 is esp_ and p2 is cross and pw == 0.6


def test_pair_none_species_substitutes_from_pool():
    from evolution.evolution_engine import EvolutionEngine
    x, y = _agent("X", "tendencia"), _agent("Y", "ruptura")
    pool = [_agent("R", "reversion")]
    p1, p2, pw = EvolutionEngine._species_dominant_pair(x, y, "reversion", pool)
    assert p1["especie"] == "reversion" and pw == 0.6
    assert p2 in (x, y), "el segundo padre debe ser uno de los originales (diversidad)"


def test_pair_extinct_species_leaves_cross():
    from evolution.evolution_engine import EvolutionEngine
    x, y = _agent("X", "tendencia"), _agent("Y", "ruptura")
    p1, p2, pw = EvolutionEngine._species_dominant_pair(x, y, "reversion", [])
    assert (p1, p2) == (x, y) and pw is None  # especie extinta: cross inevitable


# ─── _species_genome_pool: filtra a la especie y mezcla HoF ───────────────────

def test_genome_pool_filters_and_dedups():
    from evolution.evolution_engine import EvolutionEngine
    engine = EvolutionEngine(date(2026, 6, 16))
    lista_a = [_agent("R1", "reversion"), _agent("T1", "tendencia")]
    lista_b = [_agent("R1", "reversion"), _agent("R2", "reversion")]  # R1 duplicado
    hof = [_agent("R3", "reversion"), _agent("B1", "ruptura")]  # solo R3 cuenta
    with patch.object(engine, "_get_hof_parents", return_value=hof):
        pool = engine._species_genome_pool("reversion", lista_a, lista_b)
    ids = sorted(a["id"] for a in pool)
    assert ids == ["R1", "R2", "R3"], f"esperado R1,R2,R3 (sin cross, sin dup); fue {ids}"


# ─── Cableado: un hijo reversion de pool cross-species obtiene padre reversion ─

def test_repopulation_cross_pool_still_gets_species_parent():
    """tourn_pool es 100% tendencia, pero existe un genoma reversion joven en la
    población → todo cruce del slot reversion debe llevar ≥1 padre reversion."""
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 6, 9))
    current = (
        [_agent(f"R{i}", "reversion") for i in range(4)]   # déficit reversion = 1
        + [_agent(f"T{i}", "tendencia") for i in range(5)]
        + [_agent(f"B{i}", "ruptura")  for i in range(5)]
    )
    parent_pool = [_agent(f"PT{i}", "tendencia") for i in range(5)]  # solo tendencia

    parejas: list[tuple[str, str]] = []

    def _mock_breed(p1, p2, child_id, today, gen, **kw):
        parejas.append((p1["especie"], p2["especie"]))
        return _agent(child_id, kw.get("especie", "tendencia"))

    good_bt = {"fitness": 1.0, "n_trades": 999, "expectancy": 0.5}

    with patch("evolution.evolution_engine.breed_agent", side_effect=_mock_breed), \
         patch("evolution.backtester.run_backtest", return_value=good_bt), \
         patch.object(engine, "_get_hof_parents", return_value=[]):

        recovered, slots_rec_log, deficit = engine._try_repopulate(
            current_population=current,
            parent_pool=parent_pool,
            backtest_data={"df_15m": None, "df_1h": None},
            start_idx=10, max_gen=1, sw=0.05, sp=0.08, sr=0.10,
        )

    assert len(recovered) == 1
    assert recovered[0]["especie"] == "reversion"
    assert parejas, "no se crió ningún candidato"
    assert all("reversion" in par for par in parejas), \
        f"hubo cruces sin padre reversion: {parejas}"
