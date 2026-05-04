"""
Cliente OANDA v20 REST API para ejecución de órdenes EUR/USD.

Una única cuenta OANDA (practice o live) ejecuta las órdenes de todos los agentes.
Cada orden se identifica con el agent_id vía clientExtensions.id.
OANDA gestiona SL/TP nativamente — el monitor de trades sincroniza
las operaciones cerradas de vuelta a PostgreSQL.

Variables de entorno requeridas:
  OANDA_ACCOUNT_ID      — ID de la cuenta (e.g., 101-001-12345678-001)
  OANDA_API_TOKEN       — Bearer token de la API
  OANDA_ENVIRONMENT     — "practice" (default) | "live"
  OANDA_UNITS_PER_TRADE — unidades EUR por operación (default: 1000)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────

_ENV = os.getenv("OANDA_ENVIRONMENT", "practice")
_BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if _ENV != "live"
    else "https://api-fxtrade.oanda.com"
)
_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
_TOKEN      = os.getenv("OANDA_API_TOKEN", "")
_UNITS      = int(os.getenv("OANDA_UNITS_PER_TRADE", "1000"))

_INSTRUMENT = "EUR_USD"

_HEADERS = {
    "Authorization":         f"Bearer {_TOKEN}",
    "Content-Type":          "application/json",
    "Accept-Datetime-Format": "RFC3339",
}


class OandaError(Exception):
    pass


class OandaMarketClosedError(OandaError):
    """Mercado cerrado — orden no pudo ejecutarse."""


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _req(method: str, path: str, **kwargs) -> dict:
    url = f"{_BASE_URL}{path}"
    resp = requests.request(method, url, headers=_HEADERS, timeout=15, **kwargs)
    if not resp.ok:
        body = resp.text[:400]
        if "Market is halted" in body or "MARKET_HALTED" in body:
            raise OandaMarketClosedError(f"Mercado cerrado: {body}")
        raise OandaError(f"OANDA {method} {path} → {resp.status_code}: {body}")
    return resp.json()


# ── Precio actual ─────────────────────────────────────────────────────────────

def get_price() -> dict[str, float]:
    """Retorna {'bid': float, 'ask': float, 'mid': float} para EUR_USD."""
    data = _req(
        "GET",
        f"/v3/accounts/{_ACCOUNT_ID}/pricing",
        params={"instruments": _INSTRUMENT},
    )
    p   = data["prices"][0]
    bid = float(p["bids"][0]["price"])
    ask = float(p["asks"][0]["price"])
    return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 5)}


# ── Colocación de órdenes ─────────────────────────────────────────────────────

def place_order(
    agent_id: str,
    action: str,
    stop_loss: float,
    take_profit: float,
    comment: str = "",
) -> dict[str, Any]:
    """
    Coloca una orden de mercado EUR_USD con SL y TP nativos de OANDA.
    OANDA cierra el trade automáticamente cuando se alcanza SL o TP.

    Retorna dict con: oanda_trade_id, units, entry_price, stop_loss, take_profit.
    Lanza OandaMarketClosedError si el mercado está cerrado.
    Lanza OandaError para cualquier otro error de la API.
    """
    if action not in ("BUY", "SELL"):
        raise ValueError(f"Acción inválida: {action}")

    units = str(_UNITS) if action == "BUY" else str(-_UNITS)

    body = {
        "order": {
            "type":       "MARKET",
            "instrument": _INSTRUMENT,
            "units":      units,
            "stopLossOnFill":   {"price": f"{stop_loss:.5f}"},
            "takeProfitOnFill": {"price": f"{take_profit:.5f}"},
            "timeInForce":      "FOK",
            "clientExtensions": {
                "id":      agent_id,
                "tag":     "inversion-evolutiva",
                "comment": comment[:120],
            },
        }
    }

    data = _req("POST", f"/v3/accounts/{_ACCOUNT_ID}/orders", json=body)

    # FOK cancelado (mercado cerrado o sin liquidez)
    if "orderCancelTransaction" in data:
        reason = data["orderCancelTransaction"].get("reason", "UNKNOWN")
        raise OandaMarketClosedError(f"Orden FOK cancelada: {reason}")

    fill       = data.get("orderFillTransaction", {})
    trade_id   = fill.get("tradeOpened", {}).get("tradeID", "")
    entry_price = float(fill.get("price", 0))

    log.info(
        "[OANDA] Orden %s ejecutada: agent=%s trade_id=%s precio=%.5f",
        action, agent_id, trade_id, entry_price,
    )
    return {
        "oanda_trade_id": trade_id,
        "units":          int(units),
        "entry_price":    entry_price,
        "stop_loss":      stop_loss,
        "take_profit":    take_profit,
    }


# ── Consulta de trades ────────────────────────────────────────────────────────

def get_trade(trade_id: str) -> dict:
    """Retorna el estado completo de un trade individual."""
    return _req("GET", f"/v3/accounts/{_ACCOUNT_ID}/trades/{trade_id}")


def get_open_trades() -> list[dict]:
    """Lista todos los trades EUR_USD actualmente abiertos en la cuenta."""
    data = _req(
        "GET",
        f"/v3/accounts/{_ACCOUNT_ID}/trades",
        params={"state": "OPEN", "instrument": _INSTRUMENT},
    )
    return data.get("trades", [])


def has_open_trade(agent_id: str) -> bool:
    """True si el agente ya tiene un trade abierto en OANDA."""
    try:
        return any(
            t.get("clientExtensions", {}).get("id") == agent_id
            for t in get_open_trades()
        )
    except OandaError:
        return False


# ── Cierre manual ─────────────────────────────────────────────────────────────

def close_trade(trade_id: str) -> dict[str, Any]:
    """Cierra manualmente un trade a precio de mercado."""
    data = _req(
        "PUT",
        f"/v3/accounts/{_ACCOUNT_ID}/trades/{trade_id}/close",
        json={"units": "ALL"},
    )
    fill = data.get("orderFillTransaction", {})
    return {
        "oanda_trade_id": trade_id,
        "close_price":    float(fill.get("price", 0)),
        "realized_pl":    float(fill.get("pl", 0)),
    }


# ── Health check ──────────────────────────────────────────────────────────────

def health_check() -> bool:
    """Verifica que las credenciales OANDA son válidas y la cuenta existe."""
    try:
        _req("GET", f"/v3/accounts/{_ACCOUNT_ID}")
        return True
    except Exception as exc:
        log.error("[OANDA] Health check fallido: %s", exc)
        return False
