"""
Cliente de noticias y calendario económico para EUR/USD.
Fuente: Finnhub API (REST, sin scraping de HTML).

Endpoints usados:
  GET /news?category=forex          → titulares de noticias Forex en tiempo real
  GET /calendar/economic            → calendario de eventos económicos (USD/EUR)

Finnhub free tier: 60 llamadas/min, sin restricción comercial.
Requiere: variable de entorno FINNHUB_API_KEY
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"

_EUR_COUNTRIES = {"EU", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "FI", "PT", "GR", "IE"}
_USD_COUNTRIES = {"US"}

_IMPACT_MAP = {"high": "alto", "medium": "medio", "low": "bajo"}


# ── Estructuras de datos (sin cambio de interfaz para los sub-agentes) ─────────

@dataclass
class EconomicEvent:
    titulo: str
    moneda: str
    impacto: str
    hora_utc: Optional[datetime]
    actual: Optional[str]
    previo: Optional[str]
    estimado: Optional[str]
    fuente: str = "finnhub"


@dataclass
class MacroSnapshot:
    timestamp: datetime
    eventos: list[EconomicEvent] = field(default_factory=list)
    titulares: list[str] = field(default_factory=list)

    def eventos_alto_impacto(self) -> list[EconomicEvent]:
        return [e for e in self.eventos if e.impacto == "alto"]

    def tiene_riesgo_evento(self) -> bool:
        return len(self.eventos_alto_impacto()) > 0


# ── Helpers ────────────────────────────────────────────────────────────────────

def _api_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "")


def _get(endpoint: str, params: dict, timeout: int = 10) -> dict | list:
    key = _api_key()
    if not key:
        raise RuntimeError("FINNHUB_API_KEY no configurada")
    params["token"] = key
    resp = requests.get(f"{_BASE_URL}{endpoint}", params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ── Noticias Forex ─────────────────────────────────────────────────────────────

def fetch_forex_news(limit: int = 10) -> list[str]:
    """Titulares de noticias Forex en tiempo real desde Finnhub."""
    try:
        articles = _get("/news", {"category": "forex"})
        headlines = [a["headline"] for a in articles[:limit] if a.get("headline")]
        log.info("[MacroScraper] %d titulares forex obtenidos desde Finnhub", len(headlines))
        return headlines
    except RuntimeError:
        log.warning("[MacroScraper] FINNHUB_API_KEY no configurada — titulares vacíos")
        return []
    except Exception as e:
        log.warning("[MacroScraper] Finnhub /news no disponible: %s", e)
        return []


# ── Calendario económico ───────────────────────────────────────────────────────

def fetch_economic_calendar(ventana_horas: int = 4) -> list[EconomicEvent]:
    """
    Eventos del calendario económico filtrados para USD y EUR (últimas 1h + próximas ventana_horas).
    """
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(hours=1)).strftime("%Y-%m-%d")
    date_to   = (now + timedelta(hours=ventana_horas)).strftime("%Y-%m-%d")

    try:
        data = _get("/calendar/economic", {"from": date_from, "to": date_to})
        items = data.get("economicCalendar", []) if isinstance(data, dict) else []
    except RuntimeError:
        log.warning("[MacroScraper] FINNHUB_API_KEY no configurada — calendario vacío")
        return []
    except Exception as e:
        log.warning("[MacroScraper] Finnhub /calendar/economic no disponible: %s", e)
        return []

    events: list[EconomicEvent] = []
    for item in items:
        country = (item.get("country") or "").upper()
        if country not in _EUR_COUNTRIES and country not in _USD_COUNTRIES:
            continue

        impact = _IMPACT_MAP.get((item.get("impact") or "low").lower(), "bajo")
        moneda = "USD" if country in _USD_COUNTRIES else "EUR"

        hora_utc: Optional[datetime] = None
        time_str = item.get("time", "")
        if time_str:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    hora_utc = datetime.strptime(time_str[:19], fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue

        events.append(EconomicEvent(
            titulo=item.get("event", "N/A"),
            moneda=moneda,
            impacto=impact,
            hora_utc=hora_utc,
            actual=item.get("actual"),
            previo=item.get("prev"),
            estimado=item.get("estimate"),
        ))

    log.info("[MacroScraper] %d eventos económicos EUR/USD obtenidos (ventana=%dh)", len(events), ventana_horas)
    return events


# ── Punto de entrada principal ─────────────────────────────────────────────────

def fetch_macro_snapshot(ventana_horas: int = 4) -> MacroSnapshot:
    """
    Retorna MacroSnapshot con noticias y calendario económico para los sub-agentes.
    Si FINNHUB_API_KEY no está configurada, retorna snapshot vacío (agentes emiten HOLD macro).
    """
    snapshot = MacroSnapshot(timestamp=datetime.now(timezone.utc))
    snapshot.titulares = fetch_forex_news(limit=10)
    snapshot.eventos   = fetch_economic_calendar(ventana_horas=ventana_horas)
    return snapshot
