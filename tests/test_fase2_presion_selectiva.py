"""
Tests de la Fase 2 del rediseño de rentabilidad (PLAN_REDISENO_RENTABILIDAD.md,
evaluación de arquitectura 2026-07-02) — "presión selectiva real":

  1. Regla de bleeder crónico: elimina siempre, sin importar cuota ni piso
     de especie.
  2. Capital ∝ fitness: pesos clamp(1+fitness, floor, cap), conservación
     exacta del capital total, newborns con peso 1.0.
  3. Objetivo de población por especie: ruptura reducido (TARGET_AGENTS_RUPTURA).

Puros donde es posible (select_survivors_and_eliminated no toca DB);
_redistribute_capital usa la sandbox Neon (conftest.py fuerza DATABASE_URL).
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from evolution.evolution_engine import (
    EvolutionEngine,
    BLEEDER_FITNESS_THRESHOLD,
    BLEEDER_MIN_TRADES,
    CAPITAL_WEIGHT_FLOOR,
    CAPITAL_WEIGHT_CAP,
    TARGET_AGENTS_PER_ESPECIE,
    TARGET_AGENTS_RUPTURA,
)


def _agent(id_: str, fitness: float, n_trades: int = 30,
           especie: str = "tendencia", fecha: date | None = None) -> dict:
    return {
        "id": id_,
        "fitness_score": fitness,
        "n_trades": n_trades,
        "especie": especie,
        "fecha_nacimiento": fecha or date(2026, 5, 1),
    }


# ─── (1) Bleeder crónico ───────────────────────────────────────────────────

def test_bleeder_eliminado_incondicionalmente():
    engine = EvolutionEngine(date(2026, 7, 2))
    agents = [
        _agent("BLEEDER", fitness=BLEEDER_FITNESS_THRESHOLD - 0.1, n_trades=BLEEDER_MIN_TRADES + 5),
        _agent("SANO_1", fitness=0.2),
        _agent("SANO_2", fitness=0.1),
    ]
    survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    eliminated_ids = {a["id"] for a in eliminated}
    assert "BLEEDER" in eliminated_ids
    assert "BLEEDER" not in {a["id"] for a in survivors}


def test_bleeder_no_eliminado_con_muestra_insuficiente():
    """Fitness catastrófico pero pocos trades → NO es bleeder (podría ser racha corta)."""
    engine = EvolutionEngine(date(2026, 7, 2))
    agents = [
        _agent("POCA_MUESTRA", fitness=BLEEDER_FITNESS_THRESHOLD - 0.1, n_trades=BLEEDER_MIN_TRADES - 5),
        _agent("SANO", fitness=0.2),
    ]
    survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    assert "POCA_MUESTRA" not in {a["id"] for a in eliminated}


def test_bleeder_respeta_piso_de_padres():
    """
    Decisión de diseño 2026-07-25: el bleeder YA NO bypassa el piso de especie.

    Revierte a propósito el comportamiento de la Fase 2. La población debe
    volver siempre a 15 con 5 por especie, y para eso hacen falta 2 padres
    vivos de los que nazcan los reemplazos. Un bleeder en una especie que ya
    está en el piso sobrevive este ciclo y encabeza la fila del siguiente.
    """
    engine = EvolutionEngine(date(2026, 7, 2))
    # Especie "ruptura" con exactamente 2 miembros (el piso _MIN_AGENTS_PER_ESPECIE)
    agents = [
        _agent("RUPTURA_BLEEDER", fitness=BLEEDER_FITNESS_THRESHOLD - 0.2,
               n_trades=BLEEDER_MIN_TRADES + 10, especie="ruptura"),
        _agent("RUPTURA_OK", fitness=0.05, especie="ruptura"),
    ]
    survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    assert eliminated == [], \
        "Con la especie en el piso de 2 padres no puede salir nadie, ni el bleeder"
    assert len(survivors) == 2


def test_tope_de_tres_bajas_por_especie():
    """
    De 5 miembros salen como máximo 3, dejando 2 padres — aunque los 5 sean
    elegibles para eliminación (fitness <= 0).
    """
    engine = EvolutionEngine(date(2026, 7, 2))
    agents = [
        _agent(f"REV_{i}", fitness=-0.05 * (i + 1), especie="reversion")
        for i in range(5)
    ]
    survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    assert len(eliminated) == 3, f"debe eliminar exactamente 3, eliminó {len(eliminated)}"
    assert len(survivors) == 2, "deben quedar 2 padres"
    # Los que salen son los 3 peores por fitness.
    assert {a["id"] for a in eliminated} == {"REV_4", "REV_3", "REV_2"}


def test_tope_por_especie_es_independiente_entre_especies():
    """
    El cupo de 3 se cuenta por especie: 3 especies malas => 9 bajas, no 3.

    Se fija N_ELIMINATE=9 (el valor de producción, judge_daily.yml) para aislar
    la lógica por especie del tope global, que en .env local está en 5.
    """
    engine = EvolutionEngine(date(2026, 7, 2))
    agents = [
        _agent(f"{esp[:3].upper()}_{i}", fitness=-0.05 * (i + 1), especie=esp)
        for esp in ("tendencia", "reversion", "ruptura")
        for i in range(5)
    ]
    with patch("evolution.evolution_engine.N_ELIMINATE", 9):
        survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    por_especie: dict[str, int] = {}
    for a in eliminated:
        por_especie[a["especie"]] = por_especie.get(a["especie"], 0) + 1
    assert por_especie == {"tendencia": 3, "reversion": 3, "ruptura": 3}
    assert len(survivors) == 6, "2 padres por especie"


def test_tope_global_sigue_acotando_por_encima_del_cupo_por_especie():
    """
    AGENTS_ELIMINATE_PER_CYCLE sigue siendo una red de seguridad: si es menor
    que 3 x n_especies, acota el total aunque quede cupo por especie.
    """
    engine = EvolutionEngine(date(2026, 7, 2))
    agents = [
        _agent(f"{esp[:3].upper()}_{i}", fitness=-0.05 * (i + 1), especie=esp)
        for esp in ("tendencia", "reversion", "ruptura")
        for i in range(5)
    ]
    with patch("evolution.evolution_engine.N_ELIMINATE", 4):
        _survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    assert len(eliminated) == 4


def test_bleeder_consume_cupo_de_su_especie():
    """
    Un bleeder cuenta DENTRO de las 3 bajas de su especie (antes era adicional).
    Con 1 bleeder + 4 elegibles, salen 3 en total, no 4.
    """
    engine = EvolutionEngine(date(2026, 7, 2))
    agents = [
        _agent("REV_BLEEDER", fitness=BLEEDER_FITNESS_THRESHOLD - 0.2,
               n_trades=BLEEDER_MIN_TRADES + 10, especie="reversion"),
    ] + [
        _agent(f"REV_{i}", fitness=-0.01 * (i + 1), especie="reversion")
        for i in range(4)
    ]
    survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    eliminated_ids = {a["id"] for a in eliminated}
    assert "REV_BLEEDER" in eliminated_ids, "el bleeder sale primero"
    assert len(eliminated) == 3, "el bleeder consume 1 de los 3 cupos"
    assert len(survivors) == 2


def test_bleeder_no_cuenta_contra_cuota_n_eliminate():
    """Un bleeder es ADICIONAL a la cuota dinámica normal, no compite por ella."""
    from evolution.evolution_engine import N_ELIMINATE

    engine = EvolutionEngine(date(2026, 7, 2))
    agents = [
        _agent("BLEEDER", fitness=BLEEDER_FITNESS_THRESHOLD - 0.1, n_trades=BLEEDER_MIN_TRADES + 5),
    ]
    # N_ELIMINATE agentes con fitness <=0 "normales" (no bleeders) de distintas especies
    # variadas para no chocar con el piso de especie.
    especies = ["tendencia", "reversion", "ruptura"]
    for i in range(N_ELIMINATE):
        agents.append(_agent(f"NEG_{i}", fitness=-0.01, especie=especies[i % 3],
                              fecha=date(2026, 4, 1)))
        # 5 sanos por especie para no chocar con el piso al eliminar los NEG_i
        for j in range(3):
            agents.append(_agent(f"SANO_{especies[i % 3]}_{i}_{j}", fitness=0.1,
                                  especie=especies[i % 3]))

    survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    eliminated_ids = {a["id"] for a in eliminated}
    assert "BLEEDER" in eliminated_ids
    # Los NEG_i (fitness<=0, cuota dinámica) también deben poder eliminarse,
    # SUMADOS al bleeder — el total no se recorta a N_ELIMINATE.
    assert len(eliminated) > N_ELIMINATE


# ─── (2) Capital ∝ fitness ─────────────────────────────────────────────────

def test_redistribute_capital_conserva_el_total():
    """
    _redistribute_capital reparte entre TODOS los agentes activos de la DB,
    no solo los del test (la sandbox puede tener otros agentes de fixtures
    de otros tests) — por eso la conservación se verifica sobre el total
    real de agentes activos, no sobre un subconjunto asumido.
    """
    from db.connection import get_conn
    engine = EvolutionEngine(date(2026, 7, 2))

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM agentes WHERE id LIKE 'TEST_CAP_%'")
        for i, fit in enumerate([0.5, -0.5, 0.0, 1.5, -1.5]):
            cur.execute(
                """
                INSERT INTO agentes (
                    id, fecha_nacimiento, generacion, capital_inicial,
                    capital_actual, especie, estado
                ) VALUES (%s, '2026-05-01', 1, 10.0, 10.0, 'tendencia', 'activo')
                """,
                (f"TEST_CAP_{i}",),
            )
        conn.commit()

        cur.execute("SELECT COALESCE(SUM(capital_actual), 0) FROM agentes WHERE estado = 'activo'")
        pool_real = float(cur.fetchone()[0])

        fitness_map = {f"TEST_CAP_{i}": fit for i, fit in enumerate([0.5, -0.5, 0.0, 1.5, -1.5])}
        pool_total, cuota_base = engine._redistribute_capital(
            conn, new_agent_ids=[], pool_override=pool_real, fitness_map=fitness_map,
        )
        conn.commit()

        cur.execute("SELECT COALESCE(SUM(capital_actual), 0) FROM agentes WHERE estado = 'activo'")
        pool_after = float(cur.fetchone()[0])

        cur.execute(
            "SELECT id, capital_actual FROM agentes WHERE id LIKE 'TEST_CAP_%' ORDER BY id"
        )
        rows = {r[0]: float(r[1]) for r in cur.fetchall()}
        cur.execute("DELETE FROM agentes WHERE id LIKE 'TEST_CAP_%'")
        conn.commit()

    assert abs(pool_after - pool_real) < 0.01, \
        "La suma de capital_actual de TODOS los agentes activos debe conservarse exacta"
    # El agente con mejor fitness (1.5, clamp a CAPITAL_WEIGHT_CAP=2.0) debe
    # terminar con más capital que el de peor fitness (-1.5, clamp a FLOOR=0.5).
    assert rows["TEST_CAP_3"] > rows["TEST_CAP_4"]
    assert rows["TEST_CAP_3"] > cuota_base
    assert rows["TEST_CAP_4"] < cuota_base


def test_redistribute_capital_newborn_recibe_cuota_estandar():
    from db.connection import get_conn
    engine = EvolutionEngine(date(2026, 7, 2))

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM agentes WHERE id LIKE 'TEST_CAP2_%'")
        cur.execute(
            """
            INSERT INTO agentes (
                id, fecha_nacimiento, generacion, capital_inicial,
                capital_actual, especie, estado
            ) VALUES
                ('TEST_CAP2_VETERAN', '2026-05-01', 1, 10.0, 10.0, 'tendencia', 'activo'),
                ('TEST_CAP2_NEWBORN', '2026-07-02', 2, 10.0, 10.0, 'tendencia', 'activo')
            """
        )
        conn.commit()

        # Veterano con fitness muy negativo (clamp a floor); si el newborn
        # heredara ese peso por error, terminaría con capital anormalmente bajo.
        fitness_map = {"TEST_CAP2_VETERAN": -5.0}
        pool_total, cuota_base = engine._redistribute_capital(
            conn, new_agent_ids=["TEST_CAP2_NEWBORN"], pool_override=20.0,
            fitness_map=fitness_map,
        )
        conn.commit()

        cur.execute(
            "SELECT id, capital_actual FROM agentes WHERE id LIKE 'TEST_CAP2_%' ORDER BY id"
        )
        rows = {r[0]: float(r[1]) for r in cur.fetchall()}
        cur.execute("DELETE FROM agentes WHERE id LIKE 'TEST_CAP2_%'")
        conn.commit()

    # newborn con peso 1.0 debe recibir más que el veterano en floor (peso 0.5)
    assert rows["TEST_CAP2_NEWBORN"] > rows["TEST_CAP2_VETERAN"]


# ─── (3) Objetivo de población por especie ──────────────────────────────────

def test_poblacion_objetivo_es_15_paritaria():
    """
    Decisión de diseño 2026-07-25: población fija de 15 agentes, 5 por especie.

    Revierte a propósito la reducción del cupo de ruptura de la Fase 2
    (auditoría 2026-07-01). El propietario prioriza conservar diversidad de
    régimen; la presión selectiva sigue viva por el bleeder crónico, el gate
    OOS y la cuota dinámica, no por el tamaño del cupo.
    """
    assert TARGET_AGENTS_PER_ESPECIE == 5
    assert TARGET_AGENTS_RUPTURA == 5, \
        "ruptura comparte el cupo general: 3 especies x 5 = 15 agentes"


def test_capital_weight_bounds_sane():
    assert CAPITAL_WEIGHT_FLOOR < 1.0 < CAPITAL_WEIGHT_CAP


# ─── (4) Gate OOS sin bypass forzado — comportamiento DEFAULT ──────────────

def test_repopulation_vacante_por_default_sin_bypass():
    """
    Fase 2 (rediseño 2026-07-02): con REPOBLACION_PERMITE_VACANTES=true
    (default), si ningún candidato supera el umbral OOS tras agotar las
    rondas de torneo/HoF, el cupo queda VACANTE — ya NO se despliega el
    mejor candidato sin evidencia de edge ("mejor_candidato_oos" legacy).
    Réplica del escenario de test_repopulation_best_candidate_when_no_one_passes
    en test_sesion18_repopulacion.py, pero sin forzar el kill-switch a False.
    """
    from evolution.evolution_engine import REPOBLACION_PERMITE_VACANTES

    assert REPOBLACION_PERMITE_VACANTES is True, \
        "El default de Fase 2 debe ser sin bypass forzado"

    engine = EvolutionEngine(date(2026, 6, 9))
    current = (
        [_agent(f"T_{i}", 0.05, especie="tendencia") for i in range(4)]  # déficit de 1
        + [_agent(f"R_{i}", 0.05, especie="reversion") for i in range(5)]
        + [_agent(f"B_{i}", 0.05, especie="ruptura") for i in range(3)]
    )
    bad_bt = {"fitness": 0.02, "n_trades": 3}  # positivo pero muestra corta: no pasa el gate

    def _mock_breed(p1, p2, child_id, today, gen, **kw):
        return _agent(child_id, 0.0, especie=kw.get("especie", "tendencia"))

    hof_pool = [_agent(f"HOF_{i}", 0.10, especie="tendencia") for i in range(2)]

    with patch("evolution.evolution_engine.breed_agent", side_effect=_mock_breed), \
         patch("evolution.backtester.run_backtest", return_value=bad_bt), \
         patch.object(engine, "_get_hof_parents", return_value=hof_pool):

        recovered, slots_rec_log, deficit_restante = engine._try_repopulate(
            current_population=current,
            parent_pool=current,
            backtest_data={"df_15m": None, "df_1h": None},
            start_idx=10,
            max_gen=1,
            sw=0.05, sp=0.08, sr=0.10,
        )

    assert recovered == [], "Sin bypass, ningún candidato sin edge debe desplegarse"
    assert deficit_restante.get("tendencia", 0) == 1, \
        "El déficit de tendencia debe quedar registrado, no encubierto por un clon forzado"
