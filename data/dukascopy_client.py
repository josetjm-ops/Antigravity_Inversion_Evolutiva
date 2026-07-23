"""
Cliente de históricos de Dukascopy — Fase B del plan de rentabilidad
(aprobado 2026-07-23).

POR QUÉ EXISTE
--------------
El cuello de botella #1 del sistema es el hambre de datos: la evolución
aprende a ~10 trades/día en producción, así que una búsqueda genética seria
tomaría años. Yahoo Finance solo sirve ~8 días de velas de 1 minuto y 60 de
15 minutos — suficiente para el torneo OOS de cada noche, insuficiente para
evolucionar offline sobre múltiples regímenes de mercado.

Dukascopy publica su feed de ticks histórico (desde ~2003) sin API key:

    https://datafeed.dukascopy.com/datafeed/{PAR}/{YYYY}/{MM-1}/{DD}/{HH}h_ticks.bi5

Formato: LZMA sobre registros de 20 bytes big-endian
    uint32 ms_desde_la_hora | uint32 ask | uint32 bid | float32 vol_ask | float32 vol_bid
Los precios vienen como enteros y hay que dividirlos por 10^point (5 para EUR/USD).

OJO CON EL MES: Dukascopy indexa los meses desde CERO (enero = 00,
diciembre = 11). Es la fuente de error más común al usar este feed.

DISEÑO: descarga reanudable
---------------------------
Descargar años de ticks son decenas de miles de peticiones HTTP. El proceso
guarda cada día ya consolidado en Parquet y SALTA los que existen, así que
puede interrumpirse y reanudarse sin perder trabajo — no depende de que una
sola sesión dure horas.
"""

from __future__ import annotations

import logging
import lzma
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
_TICK_STRUCT = struct.Struct(">3I2f")  # ms, ask, bid, vol_ask, vol_bid
_TICK_SIZE = _TICK_STRUCT.size          # 20 bytes
_POINT_DIVISOR = 100_000.0              # EUR/USD cotiza con 5 decimales

# Directorio de caché (fuera del repo: son cientos de MB, no versionables)
CACHE_DIR = Path(
    __import__("os").getenv(
        "DUKASCOPY_CACHE_DIR",
        str(Path.home() / ".inversion_evolutiva" / "historico"),
    )
)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; InversionEvolutiva/1.0)"}

# Hilos concurrentes por día (las 24 horas son peticiones independientes).
# 8 es un compromiso: acelera ~12× sin saturar el feed público.
_MAX_WORKERS = int(__import__("os").getenv("DUKASCOPY_MAX_WORKERS", "8"))


# ── Descarga de una hora de ticks ───────────────────────────────────────────

def fetch_hour_ticks(
    simbolo: str, dia: date, hora: int, timeout: int = 30, reintentos: int = 3
) -> pd.DataFrame:
    """
    Descarga y decodifica una hora de ticks. Devuelve un DataFrame con
    columnas [timestamp, ask, bid, mid] (vacío si no hay datos — normal en
    fines de semana y feriados, cuando Dukascopy responde 404 o 0 bytes).
    """
    url = (
        f"{_BASE_URL}/{simbolo}/{dia.year}/{dia.month - 1:02d}/{dia.day:02d}"
        f"/{hora:02d}h_ticks.bi5"
    )

    contenido = None
    for intento in range(reintentos):
        try:
            resp = requests.get(url, timeout=timeout, headers=_HEADERS)
            if resp.status_code == 404:
                return _empty_ticks()          # hora sin mercado: no es error
            resp.raise_for_status()
            contenido = resp.content
            break
        except requests.RequestException as exc:
            if intento == reintentos - 1:
                log.warning("[Dukascopy] %s falló tras %d intentos: %s",
                            url, reintentos, exc)
                return _empty_ticks()

    if not contenido:
        return _empty_ticks()

    try:
        crudo = lzma.LZMADecompressor().decompress(contenido)
    except lzma.LZMAError as exc:
        log.warning("[Dukascopy] LZMA inválido en %s: %s", url, exc)
        return _empty_ticks()

    base = datetime(dia.year, dia.month, dia.day, hora, tzinfo=timezone.utc)
    filas = []
    for off in range(0, len(crudo) - _TICK_SIZE + 1, _TICK_SIZE):
        ms, ask, bid, _va, _vb = _TICK_STRUCT.unpack_from(crudo, off)
        filas.append((
            base + timedelta(milliseconds=ms),
            ask / _POINT_DIVISOR,
            bid / _POINT_DIVISOR,
        ))

    if not filas:
        return _empty_ticks()

    df = pd.DataFrame(filas, columns=["timestamp", "ask", "bid"])
    df["mid"] = (df["ask"] + df["bid"]) / 2.0
    return df


def _empty_ticks() -> pd.DataFrame:
    return pd.DataFrame(columns=["timestamp", "ask", "bid", "mid"])


# ── Agregación a velas OHLC ─────────────────────────────────────────────────

def ticks_a_velas(df_ticks: pd.DataFrame, regla: str = "1min") -> pd.DataFrame:
    """
    Agrega ticks a velas OHLC usando el precio MID (mitad del spread).

    Usar mid y no bid/ask es deliberado: el sistema modela los costos de
    transacción por separado, vía TRADE_FRICTION_PIPS (1.4 pips round-trip).
    Construir las velas sobre bid o ask cobraría el spread dos veces.
    """
    if df_ticks.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])

    df = df_ticks.set_index("timestamp")
    ohlc = df["mid"].resample(regla).ohlc().dropna()
    return ohlc.reset_index()


# ── Descarga de un día completo, con caché ──────────────────────────────────

def _ruta_dia(simbolo: str, dia: date) -> Path:
    return CACHE_DIR / simbolo / f"{dia.isoformat()}.parquet"


def descargar_dia(
    simbolo: str, dia: date, regla: str = "1min", forzar: bool = False
) -> pd.DataFrame:
    """
    Descarga las 24 horas de un día y las consolida en velas, cacheando el
    resultado en Parquet. Si el archivo ya existe y `forzar` es False, lo
    lee del disco — así el proceso completo es reanudable.
    """
    destino = _ruta_dia(simbolo, dia)
    if destino.exists() and not forzar:
        return pd.read_parquet(destino)

    # Las 24 horas son peticiones independientes y la descarga es I/O puro:
    # en serie tardaba ~73 s/día (≈10 h para 2 años), en paralelo ~6 s/día.
    # Se limita a 8 hilos para no maltratar el feed público de Dukascopy.
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        partes = list(pool.map(lambda h: fetch_hour_ticks(simbolo, dia, h), range(24)))
    partes = [p for p in partes if not p.empty]

    if not partes:
        velas = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    else:
        velas = ticks_a_velas(pd.concat(partes, ignore_index=True), regla=regla)

    destino.parent.mkdir(parents=True, exist_ok=True)
    velas.to_parquet(destino, index=False)
    return velas


def descargar_rango(
    simbolo: str,
    desde: date,
    hasta: date,
    regla: str = "1min",
    on_progress=None,
) -> dict:
    """
    Descarga un rango de fechas día a día (saltando fines de semana, sin
    mercado FX). Reanudable: los días ya cacheados no se vuelven a pedir.

    Devuelve un resumen {dias_nuevos, dias_cacheados, dias_vacios, velas}.
    """
    resumen = {"dias_nuevos": 0, "dias_cacheados": 0, "dias_vacios": 0, "velas": 0}
    actual = desde

    while actual <= hasta:
        if actual.weekday() >= 5:            # sábado/domingo: sin mercado
            actual += timedelta(days=1)
            continue

        cacheado = _ruta_dia(simbolo, actual).exists()
        velas = descargar_dia(simbolo, actual, regla=regla)

        resumen["dias_cacheados" if cacheado else "dias_nuevos"] += 1
        if velas.empty:
            resumen["dias_vacios"] += 1
        resumen["velas"] += len(velas)

        if on_progress:
            on_progress(actual, len(velas), resumen)

        actual += timedelta(days=1)

    return resumen


def cargar_historico(
    simbolo: str, desde: date, hasta: date, regla: str | None = None
) -> pd.DataFrame:
    """
    Carga desde la caché local el rango pedido y lo devuelve como un único
    DataFrame ordenado. Si `regla` se especifica (p. ej. "15min"), re-agrega
    las velas de 1 minuto a esa temporalidad.

    Nunca descarga: usar `descargar_rango` primero. Así el motor evolutivo
    offline corre sin depender de la red.
    """
    marcos = []
    actual = desde
    while actual <= hasta:
        ruta = _ruta_dia(simbolo, actual)
        if ruta.exists():
            df = pd.read_parquet(ruta)
            if not df.empty:
                marcos.append(df)
        actual += timedelta(days=1)

    if not marcos:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])

    df = pd.concat(marcos, ignore_index=True).sort_values("timestamp")
    df = df.drop_duplicates(subset="timestamp").reset_index(drop=True)

    if regla:
        idx = df.set_index("timestamp")
        df = (
            idx.resample(regla)
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
            .reset_index()
        )

    return df
