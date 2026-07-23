"""
Tests de la Fase A del plan de rentabilidad (aprobado 2026-07-23) —
ATRIBUCIÓN de salidas y embudo de decisión.

Motivación: la auditoría del 2026-07-23 encontró el payoff INVERTIDO
(avg_loss ≈ 2× avg_win) sin poder explicar por qué, porque la DB no
registraba qué mecanismo cerraba cada trade. El backfill de la migración
016 reveló de inmediato que el 44% de las operaciones muere en break-even
(-0.033R medio) mientras solo el 6% alcanza el take profit (+1.69R).

Estos tests protegen la instrumentación que produce ese dato.

Puros — sin DB ni red (salvo los de esquema, que usan la sandbox).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from cron.trade_monitor import _clasificar_razon_salida, _FRICTION_PIPS


def _op(accion="BUY", entrada=1.10000, sl_original=1.09800):
    return {
        "id": 1, "agente_id": "TEST", "accion": accion,
        "precio_entrada": entrada, "stop_loss_original": sl_original,
    }


# ─── Clasificación de la razón de salida ────────────────────────────────────

def test_take_profit():
    assert _clasificar_razon_salida(_op(), 1.10400, "HIT_TP") == "TP"


def test_reversal():
    assert _clasificar_razon_salida(_op(), 1.10050, "REVERSAL") == "REV"


def test_stop_loss_original_es_SL():
    """El stop nunca se movió: pérdida completa (~-1R)."""
    assert _clasificar_razon_salida(_op(), 1.09800, "HIT_SL") == "SL"


def test_stop_en_entrada_es_BE():
    """
    El stop se movió a la entrada (break-even). Es el caso que la auditoría
    mostró como el 44% del histórico — NO debe confundirse con un SL.
    """
    assert _clasificar_razon_salida(_op(), 1.10000, "HIT_SL") == "BE"


def test_stop_en_entrada_mas_friccion_es_BE():
    """BE real = entrada ± fricción (el SL de break-even se coloca ahí para
    que el cierre no termine en pérdida tras descontar el spread)."""
    entrada = 1.10000
    fr = _FRICTION_PIPS * 0.0001
    assert _clasificar_razon_salida(_op(entrada=entrada), entrada + fr, "HIT_SL") == "BE"


def test_stop_movido_a_ganancia_es_TRAILING():
    """El trailing subió el stop bien por encima de la entrada: el trade
    cerró EN GANANCIA aunque el evento fue 'HIT_SL'."""
    assert _clasificar_razon_salida(_op(), 1.10250, "HIT_SL") == "TRAILING"


def test_sell_simetrico():
    """En SELL la ganancia va hacia abajo — la clasificación debe invertirse."""
    op = _op(accion="SELL", entrada=1.10000, sl_original=1.10200)
    assert _clasificar_razon_salida(op, 1.10200, "HIT_SL") == "SL"        # stop original
    assert _clasificar_razon_salida(op, 1.10000, "HIT_SL") == "BE"        # en la entrada
    assert _clasificar_razon_salida(op, 1.09750, "HIT_SL") == "TRAILING"  # ganancia


def test_sin_stop_original_no_inventa_categoria_favorable():
    """
    Sin el nivel original registrado no se puede distinguir — debe reportar
    el caso base (SL), nunca asumir BE/TRAILING, que harían ver el sistema
    mejor de lo que es.
    """
    op = _op()
    op["stop_loss_original"] = None
    assert _clasificar_razon_salida(op, 1.10000, "HIT_SL") == "SL"


def test_resultado_desconocido_cae_a_EOD():
    assert _clasificar_razon_salida(_op(), 1.10010, "OPEN") == "EOD"


# ─── Esquema de la migración 016 (sandbox) ──────────────────────────────────

def test_migracion_016_columna_y_tabla_existen():
    from db.connection import get_conn, get_dict_cursor
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='operaciones' AND column_name='razon_salida'"
        )
        assert cur.fetchone() is not None, "Falta operaciones.razon_salida"
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name='embudo_decision'"
        )
        assert cur.fetchone() is not None, "Falta la tabla embudo_decision"


def test_embudo_decision_acepta_insert_y_suma_coherente():
    """El embudo debe cuadrar: los bloqueados + hold + abiertos no pueden
    exceder los candidatos (un agente cae en un solo gate)."""
    from db.connection import get_conn, get_dict_cursor
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO embudo_decision (
                candidatos, bloqueado_regimen, bloqueado_sesion,
                bloqueado_cuarentena, hold_por_senal, abiertos, errores,
                regimen_estado, adx
            ) VALUES (10, 4, 1, 1, 3, 1, 0, 'RANGO', 17.5)
            RETURNING id
            """
        )
        row_id = cur.fetchone()[0]
        conn.commit()

        cur2 = get_dict_cursor(conn)
        cur2.execute("SELECT * FROM embudo_decision WHERE id=%s", (row_id,))
        r = cur2.fetchone()
        suma = (r["bloqueado_regimen"] + r["bloqueado_sesion"]
                + r["bloqueado_cuarentena"] + r["hold_por_senal"] + r["abiertos"])
        assert suma <= r["candidatos"]

        cur.execute("DELETE FROM embudo_decision WHERE id=%s", (row_id,))
        conn.commit()


def test_close_operation_acepta_razon_salida():
    """La firma debe aceptar razon_salida — si alguien la quita, los cierres
    nuevos volverían a quedar sin atribución silenciosamente."""
    import inspect
    from agents.investor_agent import InvestorAgent
    sig = inspect.signature(InvestorAgent.close_operation)
    assert "razon_salida" in sig.parameters
    assert sig.parameters["razon_salida"].default is None


def test_force_close_all_distingue_eod_de_guardia():
    import inspect
    from cron.trade_monitor import force_close_all
    sig = inspect.signature(force_close_all)
    assert "razon" in sig.parameters
    assert sig.parameters["razon"].default == "EOD"
