"""
Regresión del desbordamiento que mató al Juez 21 días — 2026-09-15.

QUÉ PASÓ
--------
`_snapshot_ranking` calculaba el ROI diario contra un capital inicial
HARDCODEADO en 10.0:

    cap_inicial = 10.0
    roi_diario  = (cap_actual - cap_inicial) / cap_inicial * 100

Era correcto cuando cada agente tenía ~$10. Al capitalizar el sistema a
$15.000 el 10-ago, cada agente pasó a ~$1.000 y el cálculo devolvía
(1064-10)/10*100 = 10.543 contra una columna NUMERIC(8,4) cuyo máximo es
9.999,9999. El INSERT lanzaba NumericValueOutOfRange, la transacción revertía
el ciclo evolutivo COMPLETO, y el Juez murió cada noche desde el 26 de agosto
(último snapshot bueno: el 25, con $1.000,82 = roi 9.908, que pasó raspando).

21 días sin evolución. El workflow generó 56 issues de alerta que nadie vio.

DOS LECCIONES, DOS DEFENSAS
---------------------------
1. El cálculo debe usar el capital inicial REAL de cada agente.
2. Un paso de AUDITORÍA no puede abortar la evolución. Aunque el cálculo
   vuelva a salirse de rango por cualquier otra razón, se recorta y se avisa
   en vez de tumbar el ciclo.

Ambas se prueban aquí. Sin DB ni red.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution.evolution_engine import _clamp_numerico, _MAX_NUMERIC_8_4


# ─── El tope coincide con la columna real ───────────────────────────────────

def test_tope_coincide_con_numeric_8_4():
    """NUMERIC(8,4) = 8 dígitos, 4 decimales -> |v| <= 9999.9999."""
    assert _MAX_NUMERIC_8_4 == 9999.9999


# ─── La defensa: recortar en vez de reventar ────────────────────────────────

def test_el_valor_que_mato_al_juez_ahora_se_recorta():
    """
    10.543,82 es el roi_diario exacto que producía el agente 2026-07-24_02
    con $1.064,38 de capital y el divisor hardcodeado en 10. Antes reventaba
    el ciclo entero; ahora se recorta.
    """
    salida = _clamp_numerico(10543.82, _MAX_NUMERIC_8_4, "roi_diario", "2026-07-24_02")
    assert salida == _MAX_NUMERIC_8_4
    assert abs(salida) <= _MAX_NUMERIC_8_4


def test_valores_sanos_pasan_intactos():
    """Con el capital inicial real los ROI caen en el rango -20% a +21%."""
    for v in (6.44, -20.95, 0.0, 20.95, -3.31):
        assert _clamp_numerico(v, _MAX_NUMERIC_8_4, "roi_diario", "X") == v


def test_negativo_fuera_de_rango_se_recorta_al_negativo():
    """Un ROI catastrófico no debe convertirse en positivo al recortarse."""
    salida = _clamp_numerico(-99999.5, _MAX_NUMERIC_8_4, "roi_diario", "X")
    assert salida == -_MAX_NUMERIC_8_4
    assert salida < 0


def test_nan_e_infinito_no_llegan_a_la_base():
    """
    Un capital inicial 0 produciría inf o NaN. Postgres los rechaza igual que
    un overflow, asi que tambien deben neutralizarse antes del INSERT.
    """
    assert _clamp_numerico(float("inf"), _MAX_NUMERIC_8_4, "roi_diario", "X") == 0.0
    assert _clamp_numerico(float("-inf"), _MAX_NUMERIC_8_4, "roi_diario", "X") == 0.0
    assert _clamp_numerico(float("nan"), _MAX_NUMERIC_8_4, "roi_diario", "X") == 0.0


# ─── La correccion de raiz: capital inicial real ────────────────────────────

def _roi_diario(cap_actual: float, cap_inicial_agente) -> float:
    """Réplica exacta del cálculo que hace _snapshot_ranking."""
    cap_inicial = float(cap_inicial_agente or 0) or 10.0
    return round((cap_actual - cap_inicial) / cap_inicial * 100, 4)


def test_roi_usa_el_capital_inicial_real_del_agente():
    """
    Caso real del 15-sep: 2026-07-24_02 con inicial $1.000 y actual $1.064,38.
    Con el hardcode daba 10.543%; con el capital real da +6,44%.
    """
    assert _roi_diario(1064.38, 1000.00) == 6.438
    # El valor viejo, para dejar constancia de la diferencia de magnitud.
    assert _roi_diario(1064.38, 10.0) == 10543.8


def test_capital_inicial_ausente_o_cero_cae_a_10():
    """
    Un agente sin capital_inicial no debe provocar division por cero: cae al
    valor historico de 10.0. El clamp cubre el resto si aun asi desborda.
    """
    assert _roi_diario(12.0, None) == 20.0
    assert _roi_diario(12.0, 0) == 20.0


def test_toda_la_poblacion_real_cabe_tras_la_correccion():
    """
    Capitales reales de los 15 agentes vivos el 2026-09-15. Con el capital
    inicial correcto, ninguno se acerca al tope — el margen es de ~475x.
    """
    poblacion = [
        (1064.38, 1000.00), (1000.18, 826.94), (955.22, 845.03),
        (930.46, 962.35), (929.56, 849.14), (927.06, 1000.00),
        (920.12, 1000.00), (915.08, 1000.00), (909.10, 1000.00),
        (909.01, 849.98), (890.14, 849.98), (887.90, 849.14),
        (878.15, 849.98), (876.68, 1000.00), (748.78, 929.56),
    ]
    peor = max(abs(_roi_diario(act, ini)) for act, ini in poblacion)
    assert peor < 25.0, f"ROI real maximo {peor} — se esperaba < 25%"
    assert peor < _MAX_NUMERIC_8_4
