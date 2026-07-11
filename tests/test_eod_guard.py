"""
Tests del fix de la guardia EOD y de la guardia de fin de semana
(auditoría 2026-07-11).

BUG ORIGINAL: _eod_guard usaba "hoy a las 06:30 UTC" como frontera sin
manejar que la ventana de trading cruza la medianoche UTC. Entre las 00:00
y las 04:00 UTC (7–11 pm Bogotá) esa frontera quedaba en el futuro →
cualquier posición recién abierta parecía "del día anterior" →
force_close_all() en cada ciclo de 15 min + reapertura inmediata (la
ventana sigue activa hasta las 04:00 UTC) = churn pagando fricción cada
15 minutos. Observado en prod: 16 ops consecutivas de un solo agente el
2026-07-10, ~-$1.32 acumulados en esa ventana horaria.

Además, el viernes desde ~21:00 UTC el mercado FX está cerrado y Yahoo
congela el precio — el sistema abría posiciones sin movimiento posible.

Puros — sin DB ni red.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cron.trade_monitor import _eod_guard_cutoff, _forex_market_closed


def _utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# ─── _eod_guard_cutoff: la frontera es el último 03:45 UTC que YA pasó ───────

def test_cutoff_mediodia_no_toca_la_sesion_de_hoy():
    """A las 12:00 UTC la frontera es hoy 03:45 — una posición abierta hoy
    a las 07:00 UTC (sesión vigente) queda DESPUÉS de la frontera (viva)."""
    # 2026-07-08 fue miércoles
    cutoff = _eod_guard_cutoff(_utc(2026, 7, 8, 12, 0))
    assert cutoff == _utc(2026, 7, 8, 3, 45)
    assert _utc(2026, 7, 8, 7, 0) > cutoff      # posición de hoy: viva
    assert _utc(2026, 7, 7, 20, 0) < cutoff     # sobra de ayer: huérfana


def test_cutoff_madrugada_utc_NO_mata_la_sesion_vigente():
    """EL CASO DEL BUG: a las 00:15 UTC (7:15 pm Bogotá) la frontera debe ser
    AYER 03:45 — una posición abierta hace 10 minutos (23:59 UTC de ayer o
    00:05 de hoy) pertenece a la sesión vigente y NO es huérfana."""
    cutoff = _eod_guard_cutoff(_utc(2026, 7, 9, 0, 15))
    assert cutoff == _utc(2026, 7, 8, 3, 45), \
        "La frontera debe retroceder al día anterior cuando aún no pasó el 03:45 de hoy"
    # Posición abierta hace minutos (dentro de la sesión que empezó ayer 06:30):
    assert _utc(2026, 7, 9, 0, 5) > cutoff, "Recién abierta: NO huérfana"
    assert _utc(2026, 7, 8, 20, 0) > cutoff, "De esta tarde: NO huérfana"
    # Sobra de la sesión de ANTES de ayer (sobrevivió el cierre de ayer 03:45):
    assert _utc(2026, 7, 8, 2, 0) < cutoff, "De la sesión anterior: huérfana"


def test_cutoff_ventana_ciega_si_cierra_la_sesion_terminada():
    """El propósito ORIGINAL de la guardia sigue intacto: a las 05:00 UTC
    (ventana ciega, después del cierre EOD de las 03:45), las posiciones de
    la sesión que acaba de terminar SÍ son huérfanas si el Juez se retrasó."""
    cutoff = _eod_guard_cutoff(_utc(2026, 7, 9, 5, 0))
    assert cutoff == _utc(2026, 7, 9, 3, 45)
    assert _utc(2026, 7, 8, 20, 0) < cutoff, \
        "Posición de la sesión terminada: huérfana (el Juez debió cerrarla a las 03:45)"


def test_cutoff_justo_despues_del_cierre():
    """A las 03:50 UTC la frontera es hoy 03:45 (recién pasada)."""
    cutoff = _eod_guard_cutoff(_utc(2026, 7, 9, 3, 50))
    assert cutoff == _utc(2026, 7, 9, 3, 45)


def test_cutoff_justo_antes_del_cierre():
    """A las 03:40 UTC el cierre de hoy aún no pasa → frontera = ayer 03:45.
    Las posiciones de la sesión vigente (aún sin cerrar por el Juez) viven."""
    cutoff = _eod_guard_cutoff(_utc(2026, 7, 9, 3, 40))
    assert cutoff == _utc(2026, 7, 8, 3, 45)
    assert _utc(2026, 7, 9, 2, 0) > cutoff, "Posición de la sesión vigente: viva"


# ─── _forex_market_closed: viernes 21:00 UTC → domingo 21:00 UTC ─────────────

def test_mercado_abierto_entre_semana():
    # 2026-07-08 = miércoles; 2026-07-10 = viernes (mediodía)
    assert _forex_market_closed(_utc(2026, 7, 8, 12, 0)) is False
    assert _forex_market_closed(_utc(2026, 7, 10, 12, 0)) is False
    assert _forex_market_closed(_utc(2026, 7, 10, 20, 59)) is False


def test_mercado_cerrado_viernes_noche_utc():
    """El caso observado en prod (2026-07-10 = viernes): a las 00:xx UTC del
    sábado (7-11 pm Bogotá del viernes) el mercado está cerrado."""
    assert _forex_market_closed(_utc(2026, 7, 10, 21, 0)) is True   # viernes 21:00
    assert _forex_market_closed(_utc(2026, 7, 10, 23, 30)) is True
    assert _forex_market_closed(_utc(2026, 7, 11, 0, 30)) is True   # sábado UTC
    assert _forex_market_closed(_utc(2026, 7, 11, 12, 0)) is True   # sábado
    assert _forex_market_closed(_utc(2026, 7, 12, 12, 0)) is True   # domingo mediodía


def test_mercado_reabre_domingo_noche_utc():
    assert _forex_market_closed(_utc(2026, 7, 12, 21, 0)) is False  # domingo 21:00
    assert _forex_market_closed(_utc(2026, 7, 13, 7, 0)) is False   # lunes


def test_weekend_guard_activo_por_defecto():
    from cron.trade_monitor import _FOREX_WEEKEND_GUARD
    assert _FOREX_WEEKEND_GUARD is True
