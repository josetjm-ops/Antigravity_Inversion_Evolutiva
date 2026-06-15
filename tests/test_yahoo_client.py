"""
Tests del cliente HTTP de Yahoo Finance con reintentos (data/yahoo_client.py).

Cubre la política de resiliencia añadida tras el incidente 2026-06-15 17:15 UTC
(un 'Read timed out' aislado tumbaba el workflow y disparaba un correo de alerta):
  - Éxito al primer intento (sin reintentos ni esperas en el camino feliz).
  - Reintento ante timeout transitorio y éxito posterior.
  - Agotar reintentos ante timeout persistente → propaga la excepción.
  - NO reintentar ante HTTP 4xx (error de cliente, no transitorio).
  - Reintentar ante HTTP 5xx (error de servidor, transitorio).

requests.get y time.sleep están mockeados: el test no toca red ni espera.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import yahoo_client


def _ok_response(payload):
    """Respuesta HTTP simulada exitosa (raise_for_status no lanza)."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _http_error_response(status_code):
    """Respuesta cuyo raise_for_status lanza HTTPError con el status dado."""
    resp = MagicMock()
    err = requests.exceptions.HTTPError(response=MagicMock(status_code=status_code))
    resp.raise_for_status.side_effect = err
    return resp


class TestFetchChartRetry:

    def test_exito_primer_intento_sin_reintentos(self):
        payload = {"chart": {"result": [{"meta": {"regularMarketPrice": 1.085}}]}}
        with patch("data.yahoo_client.requests.get",
                   return_value=_ok_response(payload)) as mock_get, \
             patch("data.yahoo_client.time.sleep") as mock_sleep:
            out = yahoo_client.fetch_chart({"interval": "1m", "range": "1d"})
        assert out == payload
        assert mock_get.call_count == 1
        mock_sleep.assert_not_called()

    def test_reintenta_tras_timeout_y_luego_exito(self):
        payload = {"chart": {"result": [1]}}
        responses = [requests.exceptions.Timeout("read timed out"), _ok_response(payload)]
        with patch("data.yahoo_client.requests.get", side_effect=responses) as mock_get, \
             patch("data.yahoo_client.time.sleep") as mock_sleep:
            out = yahoo_client.fetch_chart({"interval": "15m", "range": "5d"})
        assert out == payload
        assert mock_get.call_count == 2
        assert mock_sleep.call_count == 1

    def test_agota_reintentos_y_propaga(self):
        with patch("data.yahoo_client.requests.get",
                   side_effect=requests.exceptions.Timeout("persistente")) as mock_get, \
             patch("data.yahoo_client.time.sleep") as mock_sleep:
            with pytest.raises(requests.exceptions.Timeout):
                yahoo_client.fetch_chart({"interval": "1m", "range": "1d"})
        assert mock_get.call_count == yahoo_client._ATTEMPTS
        assert mock_sleep.call_count == yahoo_client._ATTEMPTS - 1

    def test_no_reintenta_ante_4xx(self):
        with patch("data.yahoo_client.requests.get",
                   return_value=_http_error_response(404)) as mock_get, \
             patch("data.yahoo_client.time.sleep") as mock_sleep:
            with pytest.raises(requests.exceptions.HTTPError):
                yahoo_client.fetch_chart({"interval": "1m", "range": "1d"})
        assert mock_get.call_count == 1
        mock_sleep.assert_not_called()

    def test_reintenta_ante_5xx_y_luego_exito(self):
        payload = {"chart": {"result": [2]}}
        responses = [_http_error_response(503), _ok_response(payload)]
        with patch("data.yahoo_client.requests.get", side_effect=responses) as mock_get, \
             patch("data.yahoo_client.time.sleep") as mock_sleep:
            out = yahoo_client.fetch_chart({"interval": "1m", "range": "1d"})
        assert out == payload
        assert mock_get.call_count == 2
        assert mock_sleep.call_count == 1
