"""
Cliente HTTP compartido para Yahoo Finance (chart EUR/USD).

Centraliza URL, headers y — sobre todo — la política de REINTENTOS con backoff.
Yahoo Finance es un endpoint gratuito que esporádicamente responde lento o
resetea la conexión; un solo intento tumbaba el run completo del Trade Monitor.

  Incidente 2026-06-15 17:15 UTC: 'Read timed out. (read timeout=15)' al bajar
  OHLCV → el workflow falló → se creó un Issue y se envió un correo de alerta
  por un simple parpadeo de red (el run siguiente, 15 min después, salió OK).

Es el MISMO patrón que db/connection.py ya resolvió para los timeouts del pooler
de Supabase. Aquí lo aplicamos a la otra dependencia externa flaky del sistema.

Solo se reintenta ante errores TRANSITORIOS (timeout, fallo de conexión, HTTP
5xx). Un 4xx o un JSON inválido se propaga de inmediato: no es transitorio y
reintentar no ayuda.
"""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; InversionEvolutiva/1.0)"}

# 3 intentos; 0 espera en el camino feliz. Peor caso: (2+5)=7s de backoff +
# 3×timeout de request. Holgado dentro del timeout-minutes:12 del workflow y por
# debajo de la cadencia de 15 min (sin solape entre ciclos).
_ATTEMPTS = 3
_BACKOFF_S = (2, 5)  # espera tras el intento 1 y 2 (el último no espera)


def _is_retryable(exc: Exception) -> bool:
    """Solo timeouts, fallos de conexión y HTTP 5xx cuentan como transitorios."""
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500
    return False


def fetch_chart(params: dict, timeout: int = 15) -> dict:
    """
    GET al chart de Yahoo Finance con reintentos ante fallos transitorios.

    Devuelve el JSON ya parseado (dict completo). El llamador conserva su propio
    parseo de `chart.result` y sus mensajes de error específicos.

    Lanza la última excepción si agota los reintentos, o de inmediato si el error
    no es transitorio (4xx, JSON inválido).
    """
    last_exc: Exception | None = None
    for attempt in range(_ATTEMPTS):
        try:
            resp = requests.get(
                YAHOO_CHART_URL, headers=HEADERS, params=params, timeout=timeout
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            if not _is_retryable(exc) or attempt == _ATTEMPTS - 1:
                raise
            last_exc = exc
            wait = _BACKOFF_S[attempt]
            log.warning(
                "[Yahoo] Petición falló (intento %d/%d): %s — reintento en %ds",
                attempt + 1, _ATTEMPTS, exc, wait,
            )
            time.sleep(wait)
    raise last_exc  # defensivo: el bucle siempre retorna o lanza antes
