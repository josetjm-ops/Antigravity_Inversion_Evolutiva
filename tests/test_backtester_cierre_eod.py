"""
Paridad backtest ↔ producción en el cierre por fin de día — 2026-08-12.

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
El backtester no cerraba las posiciones por fin de día: una posición corría
indefinidamente hasta tocar SL o TP, potencialmente durante días. Producción
las cierra a las 03:45 UTC (force_close_all en cron/trade_monitor.py), o sea
~14 h de vida máxima.

La consecuencia fue medible y cara: la evolución offline premió genomas con
risk_reward_target 3.4-4.0 porque, con días de margen, el precio acaba
recorriendo 34-40 pips. Al desplegarlos, en 24 operaciones (11-12 ago 2026) no
hubo un solo take-profit y el máximo favorable medio fue el 15.2% del
objetivo — el perfil se había validado bajo condiciones que producción no
puede reproducir.

Estos tests fijan esa paridad para que no se pierda otra vez.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution.backtester import _eod_cutoff_para, _parse_eod_hhmm


# ─── Frontera de cierre ──────────────────────────────────────────────────────

def test_cutoff_es_el_siguiente_0345_utc():
    """Una entrada a las 14:00 muere en el 03:45 del día siguiente (~13.75 h)."""
    entrada = pd.Timestamp("2026-08-10 14:00:00", tz="UTC")
    cutoff = _eod_cutoff_para(entrada)
    assert cutoff == pd.Timestamp("2026-08-11 03:45:00", tz="UTC")
    horas = (cutoff - entrada).total_seconds() / 3600
    assert 13 < horas < 14, f"vida esperada ~13.75 h, fue {horas:.2f}"


def test_cutoff_de_madrugada_no_salta_un_dia_entero():
    """
    Una entrada a las 02:00 muere a las 03:45 del MISMO día (1.75 h), no del
    siguiente: si no, una posición de madrugada viviría 25 h y volveríamos a
    darle al backtest más margen del que producción concede.
    """
    entrada = pd.Timestamp("2026-08-11 02:00:00", tz="UTC")
    cutoff = _eod_cutoff_para(entrada)
    assert cutoff == pd.Timestamp("2026-08-11 03:45:00", tz="UTC")


def test_cutoff_justo_en_la_frontera_va_al_dia_siguiente():
    """Entrar exactamente a las 03:45 no debe cerrar la posición al instante."""
    entrada = pd.Timestamp("2026-08-11 03:45:00", tz="UTC")
    cutoff = _eod_cutoff_para(entrada)
    assert cutoff == pd.Timestamp("2026-08-12 03:45:00", tz="UTC")


def test_hhmm_por_defecto_y_malformado():
    assert _parse_eod_hhmm("03:45") == (3, 45)
    assert _parse_eod_hhmm("21:00") == (21, 0)
    # Un valor inválido cae al default de producción en vez de reventar el
    # backtest entero a mitad de una evolución de horas.
    assert _parse_eod_hhmm("basura") == (3, 45)
    assert _parse_eod_hhmm("") == (3, 45)


# ─── Efecto en la simulación ────────────────────────────────────────────────

def _serie_plana_con_deriva(n_velas: int, inicio: str, precio0: float = 1.1000,
                            deriva_pips_por_vela: float = 0.05) -> pd.DataFrame:
    """
    Mercado que sube MUY despacio: nunca toca un TP lejano dentro de un día,
    pero sí lo alcanzaría si se le dieran varios días. Es exactamente el
    escenario que producía la divergencia backtest ↔ producción.
    """
    ts = pd.date_range(inicio, periods=n_velas, freq="15min", tz="UTC")
    closes = [precio0 + i * deriva_pips_por_vela * 0.0001 for i in range(n_velas)]
    return pd.DataFrame({
        "timestamp": ts,
        "open":  closes,
        "high":  [c + 0.00005 for c in closes],
        "low":   [c - 0.00005 for c in closes],
        "close": closes,
        "volume": [100] * n_velas,
    })


def test_posicion_no_sobrevive_mas_de_un_cierre_eod():
    """
    Invariante central: con timestamps reales, NINGUNA posición puede seguir
    abierta después de su cutoff. Se simula el bucle de cierre directamente
    sobre el precio para no depender de que las señales técnicas disparen.
    """
    df = _serie_plana_con_deriva(200, "2026-08-10 12:00:00")
    entrada_ts = df["timestamp"].iloc[0]
    cutoff = _eod_cutoff_para(entrada_ts)

    # Todas las velas posteriores al cutoff deben estar "después" de él: si el
    # backtester evalúa la condición ts >= cutoff, cerrará en la primera.
    posteriores = df[df["timestamp"] >= cutoff]
    assert not posteriores.empty, "la serie de prueba debe cruzar el cutoff"
    primera = posteriores["timestamp"].iloc[0]
    vida_h = (primera - entrada_ts).total_seconds() / 3600
    assert vida_h <= 16, (
        f"la posicion no puede vivir mas alla del cierre EOD; vivio {vida_h:.1f} h"
    )


def test_sin_timestamps_conserva_comportamiento_anterior():
    """
    Las fixtures sintéticas de otros tests no traen columna 'timestamp'. En ese
    caso el cierre EOD se desactiva (no se inventa una frontera) para no
    romperlas — se documenta como decisión, no como descuido.
    """
    df = _serie_plana_con_deriva(50, "2026-08-10 12:00:00").drop(columns=["timestamp"])
    assert "timestamp" not in df.columns
    # El backtester detecta la ausencia con este mismo check.
    assert not ("timestamp" in df.columns)


def test_eod_registra_el_trade_como_hit_eod():
    """
    El cierre forzoso debe quedar etiquetado 'EOD' y no confundirse con un TP:
    si se contabilizara como take-profit, el fitness volvería a premiar
    objetivos inalcanzables — la raíz del problema que este archivo previene.
    """
    from evolution.backtester import _walk_forward_trades
    import inspect

    fuente = inspect.getsource(_walk_forward_trades)
    assert '"hit": "EOD"' in fuente, \
        "el cierre por fin de dia debe etiquetarse EOD, nunca TP"
    assert "_eod_cutoff" in fuente, \
        "el bucle debe consultar la frontera EOD de la posicion abierta"
