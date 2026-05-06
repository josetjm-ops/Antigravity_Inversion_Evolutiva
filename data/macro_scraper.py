"""
Scraper de calendario económico y noticias de alto impacto para EUR/USD.
Fuentes: Investing.com (ForexFactory-compatible) y DailyForex.
Devuelve eventos estructurados para que el Sub-agente B los procese con NLP.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_IMPACT_MAP = {"high": "alto", "medium": "medio", "low": "bajo"}


@dataclass
class EconomicEvent:
    titulo: str
    moneda: str
    impacto: str
    hora_utc: Optional[datetime]
    actual: Optional[str]
    previo: Optional[str]
    estimado: Optional[str]
    fuente: str


@dataclass
class MacroSnapshot:
    timestamp: datetime
    eventos: list[EconomicEvent] = field(default_factory=list)
    titulares: list[str] = field(default_factory=list)

    def eventos_alto_impacto(self) -> list[EconomicEvent]:
        return [e for e in self.eventos if e.impacto == "alto"]

    def tiene_riesgo_evento(self) -> bool:
        return len(self.eventos_alto_impacto()) > 0


def _get(url: str, timeout: int = 15) -> BeautifulSoup:
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def scrape_investing_calendar() -> list[EconomicEvent]:
    """
    Parsea el calendario económico de Investing.com vía endpoint público.
    Filtra eventos que afectan EUR o USD.
    """
    url = "https://www.investing.com/economic-calendar/"
    events: list[EconomicEvent] = []
    try:
        soup = _get(url)
        rows = soup.select("tr.js-event-item")
        for row in rows:
            currency = row.get("data-country", "")
            if currency not in ("eu", "us"):
                continue

            impact_el = row.select_one("td.sentiment span")
            impact_raw = impact_el.get("title", "low").lower() if impact_el else "low"
            impact = _IMPACT_MAP.get(impact_raw, "bajo")

            title_el = row.select_one("td.event a")
            title = title_el.get_text(strip=True) if title_el else "N/A"

            time_el = row.select_one("td.time")
            time_str = time_el.get_text(strip=True) if time_el else None

            actual_el = row.select_one("td.actual")
            prev_el = row.select_one("td.prev")
            est_el = row.select_one("td.forecast")

            events.append(EconomicEvent(
                titulo=title,
                moneda="EUR" if currency == "eu" else "USD",
                impacto=impact,
                hora_utc=_parse_time(time_str),
                actual=actual_el.get_text(strip=True) if actual_el else None,
                previo=prev_el.get_text(strip=True) if prev_el else None,
                estimado=est_el.get_text(strip=True) if est_el else None,
                fuente="investing.com",
            ))
    except Exception as e:
        log.warning("[MacroScraper] investing.com no accesible: %s", e)
    return events


def scrape_dailyforex_news() -> list[str]:
    """Extrae titulares recientes de DailyForex relacionados con EUR/USD."""
    url = "https://www.dailyforex.com/forex-news/eurusd"
    headlines: list[str] = []
    try:
        soup = _get(url)
        for el in soup.select("h3.article-title, h2.article-title")[:10]:
            text = el.get_text(strip=True)
            if text:
                headlines.append(text)
    except Exception as e:
        log.warning("[MacroScraper] dailyforex.com no accesible: %s", e)
    return headlines


def _parse_time(time_str: Optional[str]) -> Optional[datetime]:
    if not time_str:
        return None
    try:
        now = datetime.now(timezone.utc)
        t = datetime.strptime(time_str, "%H:%M")
        return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    except ValueError:
        return None


def fetch_macro_snapshot(ventana_horas: int = 4) -> MacroSnapshot:
    """
    Punto de entrada principal para el Sub-agente B.
    Retorna un MacroSnapshot con eventos y titulares actualizados,
    filtrado a los que ocurrieron dentro de la ventana de tiempo indicada.
    """
    snapshot = MacroSnapshot(timestamp=datetime.now(timezone.utc))
    snapshot.eventos = scrape_investing_calendar()
    snapshot.titulares = scrape_dailyforex_news()

    if ventana_horas > 0 and snapshot.eventos:
        window = timedelta(hours=ventana_horas)
        now = snapshot.timestamp
        snapshot.eventos = [
            e for e in snapshot.eventos
            if e.hora_utc is None or abs((now - e.hora_utc).total_seconds()) <= window.total_seconds()
        ]

    return snapshot
