"""
Broker simulado para INVERSIÓN EVOLUTIVA.

Ejecuta órdenes EUR/USD contra precios reales de mercado (Yahoo Finance)
sin necesitar un broker externo ni credenciales adicionales.

Los 10 agentes compiten bajo condiciones idénticas usando el mismo precio
de mercado para abrir y cerrar posiciones. El P&L se calcula sobre el
capital virtual de cada agente ($10 USD) usando las variaciones reales
del tipo de cambio EUR/USD.

Flujo operativo:
  1. InvestorAgent.run_cycle() decide BUY/SELL y persiste la operación
     con el precio de Alpha Vantage como precio de entrada.
  2. TradeMonitor llama a sync_once() cada 15 min: obtiene el precio
     actual de Yahoo Finance y verifica si alguna posición alcanzó su SL o TP.
  3. Si se activa SL o TP, la posición se cierra al precio exacto del nivel.
  4. Al EOD (16:45 Bogotá), force_close_all() cierra todo al precio de mercado.
"""

from __future__ import annotations

import logging
from typing import Literal

import requests

log = logging.getLogger(__name__)

_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
_HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; InversionEvolutiva/1.0)"}

PositionResult = Literal["HIT_TP", "HIT_SL", "OPEN"]


# ── Precio de mercado ─────────────────────────────────────────────────────────

def get_current_price() -> float:
    """
    Obtiene el precio actual de EUR/USD desde Yahoo Finance.
    Gratuito, sin API key, sin límite de llamadas.
    """
    resp = requests.get(
        _YAHOO_URL,
        headers=_HEADERS,
        params={"interval": "1m", "range": "1d"},
        timeout=10,
    )
    resp.raise_for_status()
    data  = resp.json()
    price = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
    log.debug("[SimBroker] EUR/USD = %.5f", price)
    return price


# ── Lógica SL/TP ─────────────────────────────────────────────────────────────

def check_sl_tp(
    action: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    current_price: float,
) -> PositionResult:
    """
    Verifica si el precio actual activó el SL o el TP de una posición abierta.

    BUY:  precio sube → TP | precio baja → SL
    SELL: precio baja → TP | precio sube → SL
    """
    if action == "BUY":
        if current_price >= take_profit:
            return "HIT_TP"
        if current_price <= stop_loss:
            return "HIT_SL"
    elif action == "SELL":
        if current_price <= take_profit:
            return "HIT_TP"
        if current_price >= stop_loss:
            return "HIT_SL"
    return "OPEN"


def exit_price_for(result: PositionResult, stop_loss: float, take_profit: float,
                   current_price: float) -> float:
    """
    Precio de salida de una posición cerrada.
    Si tocó SL/TP se usa ese nivel exacto; si es cierre manual (EOD) se usa el mercado.
    """
    if result == "HIT_TP":
        return take_profit
    if result == "HIT_SL":
        return stop_loss
    return current_price  # cierre forzado EOD
