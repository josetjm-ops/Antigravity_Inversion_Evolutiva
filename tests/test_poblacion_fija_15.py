"""
Decisión de diseño 2026-07-25 — población fija y capital con gate de muestra.

Reglas que fija el propietario del sistema:
  1. Siempre 15 agentes: 5 por especie (test en test_fase2_presion_selectiva).
  2. De cada especie salen máximo 3 por ciclo, quedando 2 padres de los que
     nacen los 3 reemplazos (test en test_fase2_presion_selectiva).
  3. Todos los agentes amanecen con el MISMO capital... salvo quien ya
     demostró edge con muestra suficiente. Eso es lo que cubre este archivo.

El punto medio de la regla 3: igualar el capital SIEMPRE apagaría la selección
natural (auditoría 2026-07-01, hallazgo P0-2), pero ponderar con muestras de 3
operaciones es premiar suerte. El gate CAPITAL_WEIGHT_MIN_TRADES resuelve las
dos cosas: cuota exactamente equitativa mientras no haya evidencia, sobrepeso
solo cuando la hay.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from db.connection import get_conn
from evolution.evolution_engine import (
    EvolutionEngine,
    CAPITAL_WEIGHT_MIN_TRADES,
    _fitness_y_muestra,
)

_PREFIJO = "TEST_GATE_"

# _redistribute_capital hace que el ÚLTIMO agente absorba el remanente de
# redondeo para que sum(capitales) == pool_total exacto (no se crea ni destruye
# capital). Por eso "cuota equitativa" se verifica con tolerancia de un centavo,
# no con igualdad estricta: una ponderación real mueve dólares, no centésimas.
_EQUITATIVO = dict(abs=0.01)


def _sembrar(conn, ids: list[str]) -> None:
    cur = conn.cursor()
    cur.execute(f"DELETE FROM agentes WHERE id LIKE '{_PREFIJO}%'")
    for aid in ids:
        cur.execute(
            """
            INSERT INTO agentes (
                id, fecha_nacimiento, generacion, capital_inicial,
                capital_actual, especie, estado
            ) VALUES (%s, '2026-05-01', 1, 10.0, 10.0, 'tendencia', 'activo')
            """,
            (aid,),
        )
    conn.commit()


def _capitales(conn, engine, fitness_map, new_ids=None):
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(capital_actual), 0) FROM agentes WHERE estado = 'activo'")
    pool = float(cur.fetchone()[0])
    pool_total, cuota_base = engine._redistribute_capital(
        conn, new_agent_ids=new_ids or [], pool_override=pool, fitness_map=fitness_map,
    )
    conn.commit()
    cur.execute(
        f"SELECT id, capital_actual FROM agentes WHERE id LIKE '{_PREFIJO}%' ORDER BY id"
    )
    rows = {r[0]: float(r[1]) for r in cur.fetchall()}
    return rows, pool_total, cuota_base


# ─── Normalización del fitness_map ──────────────────────────────────────────

def test_fitness_y_muestra_acepta_formato_nuevo_y_antiguo():
    assert _fitness_y_muestra({"fitness": 0.4, "n_trades": 30}) == (0.4, 30)
    # Formato antiguo (float suelto): muestra desconocida -> None.
    assert _fitness_y_muestra(0.4) == (0.4, None)
    assert _fitness_y_muestra(None) == (0.0, None)


# ─── El gate de muestra ─────────────────────────────────────────────────────

def test_muestra_corta_recibe_cuota_exactamente_equitativa():
    """
    Dos agentes con fitness opuestos pero ambos con muestra corta deben
    terminar con EXACTAMENTE el mismo capital: sin evidencia no hay premio
    ni castigo.
    """
    engine = EvolutionEngine(date(2026, 7, 25))
    corta = max(0, CAPITAL_WEIGHT_MIN_TRADES - 1)
    ids = [f"{_PREFIJO}BUENO", f"{_PREFIJO}MALO"]

    with get_conn() as conn:
        _sembrar(conn, ids)
        fitness_map = {
            f"{_PREFIJO}BUENO": {"fitness": 1.5, "n_trades": corta},
            f"{_PREFIJO}MALO":  {"fitness": -1.5, "n_trades": corta},
        }
        rows, _pool, _cuota = _capitales(conn, engine, fitness_map)
        cur = conn.cursor()
        cur.execute(f"DELETE FROM agentes WHERE id LIKE '{_PREFIJO}%'")
        conn.commit()

    assert rows[f"{_PREFIJO}BUENO"] == pytest.approx(rows[f"{_PREFIJO}MALO"], **_EQUITATIVO), \
        "con muestra insuficiente el fitness es ruido: reparto equitativo"


def test_muestra_suficiente_si_diferencia_el_capital():
    """Con evidencia real, el buen agente se gana su sobrepeso."""
    engine = EvolutionEngine(date(2026, 7, 25))
    larga = CAPITAL_WEIGHT_MIN_TRADES + 10
    ids = [f"{_PREFIJO}BUENO", f"{_PREFIJO}MALO"]

    with get_conn() as conn:
        _sembrar(conn, ids)
        fitness_map = {
            f"{_PREFIJO}BUENO": {"fitness": 1.5, "n_trades": larga},
            f"{_PREFIJO}MALO":  {"fitness": -1.5, "n_trades": larga},
        }
        rows, _pool, _cuota = _capitales(conn, engine, fitness_map)
        cur = conn.cursor()
        cur.execute(f"DELETE FROM agentes WHERE id LIKE '{_PREFIJO}%'")
        conn.commit()

    assert rows[f"{_PREFIJO}BUENO"] > rows[f"{_PREFIJO}MALO"], \
        "con muestra suficiente la ponderación por fitness debe activarse"


def test_umbral_alto_colapsa_a_reparto_equitativo_puro():
    """
    CAPITAL_WEIGHT_MIN_TRADES muy alto = kill-switch hacia el reparto
    equitativo puro (la regla literal del propietario), sin tocar código.
    """
    engine = EvolutionEngine(date(2026, 7, 25))
    ids = [f"{_PREFIJO}A", f"{_PREFIJO}B"]

    with get_conn() as conn:
        _sembrar(conn, ids)
        fitness_map = {
            f"{_PREFIJO}A": {"fitness": 1.5, "n_trades": 500},
            f"{_PREFIJO}B": {"fitness": -1.5, "n_trades": 500},
        }
        with patch("evolution.evolution_engine.CAPITAL_WEIGHT_MIN_TRADES", 99999):
            rows, _pool, _cuota = _capitales(conn, engine, fitness_map)
        cur = conn.cursor()
        cur.execute(f"DELETE FROM agentes WHERE id LIKE '{_PREFIJO}%'")
        conn.commit()

    assert rows[f"{_PREFIJO}A"] == pytest.approx(rows[f"{_PREFIJO}B"], **_EQUITATIVO)


def test_el_pool_se_conserva_exacto_con_el_gate_activo():
    """
    Invariante irrenunciable: la redistribución no crea ni destruye capital,
    con o sin gate. El pool solo cambia por P&L real de trading.
    """
    engine = EvolutionEngine(date(2026, 7, 25))
    ids = [f"{_PREFIJO}{i}" for i in range(5)]

    with get_conn() as conn:
        _sembrar(conn, ids)
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(capital_actual), 0) FROM agentes WHERE estado = 'activo'")
        pool_antes = float(cur.fetchone()[0])

        # Mezcla deliberada: unos con muestra, otros sin ella.
        fitness_map = {
            f"{_PREFIJO}0": {"fitness": 0.8,  "n_trades": CAPITAL_WEIGHT_MIN_TRADES + 5},
            f"{_PREFIJO}1": {"fitness": -0.8, "n_trades": CAPITAL_WEIGHT_MIN_TRADES + 5},
            f"{_PREFIJO}2": {"fitness": 1.2,  "n_trades": 1},
            f"{_PREFIJO}3": {"fitness": -1.2, "n_trades": 0},
            f"{_PREFIJO}4": {"fitness": 0.0,  "n_trades": CAPITAL_WEIGHT_MIN_TRADES},
        }
        _rows, pool_total, _cuota = _capitales(conn, engine, fitness_map)

        cur.execute("SELECT COALESCE(SUM(capital_actual), 0) FROM agentes WHERE estado = 'activo'")
        pool_despues = float(cur.fetchone()[0])
        cur.execute(f"DELETE FROM agentes WHERE id LIKE '{_PREFIJO}%'")
        conn.commit()

    assert abs(pool_despues - pool_antes) < 0.01, "el pool debe conservarse exacto"
    assert abs(pool_total - pool_antes) < 0.01


def test_recien_nacido_recibe_cuota_equitativa_aunque_tenga_fitness():
    """Un agente nacido este ciclo nunca se pondera, tenga lo que tenga."""
    engine = EvolutionEngine(date(2026, 7, 25))
    ids = [f"{_PREFIJO}NUEVO", f"{_PREFIJO}VIEJO"]

    with get_conn() as conn:
        _sembrar(conn, ids)
        fitness_map = {
            # fitness alto y muestra larga, pero es recién nacido -> peso 1.0
            f"{_PREFIJO}NUEVO": {"fitness": 1.5, "n_trades": 999},
            f"{_PREFIJO}VIEJO": {"fitness": 0.0, "n_trades": CAPITAL_WEIGHT_MIN_TRADES + 1},
        }
        rows, _pool, _cuota = _capitales(
            conn, engine, fitness_map, new_ids=[f"{_PREFIJO}NUEVO"],
        )
        cur = conn.cursor()
        cur.execute(f"DELETE FROM agentes WHERE id LIKE '{_PREFIJO}%'")
        conn.commit()

    # fitness 0.0 -> peso clamp(1+0)=1.0, igual que el newborn.
    assert rows[f"{_PREFIJO}NUEVO"] == pytest.approx(rows[f"{_PREFIJO}VIEJO"], **_EQUITATIVO)
