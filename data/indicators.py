"""
Indicadores técnicos EUR/USD calculados desde velas OHLCV de Yahoo Finance.

Ventajas sobre Alpha Vantage:
  - Sin límite de llamadas (Yahoo Finance es gratuito e ilimitado)
  - Sin API key
  - Cada agente obtiene indicadores calculados con SUS propios parámetros genéticos
    (RSI_periodo, EMA_rapida, EMA_lenta, etc.) a partir de los mismos precios base.
  - Un solo request HTTP por ciclo de 15 min — luego se calculan N sets de indicadores
    en memoria, uno por agente, sin costo adicional.

Flujo recomendado:
  df = fetch_ohlcv()                        # 1 request HTTP compartido
  signals_a = calc_signals(df, params_a)   # cálculo en memoria por agente
  signals_b = calc_signals(df, params_b)
  ...
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from data.alpha_vantage_client import TechnicalSignals

log = logging.getLogger(__name__)

_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
_HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; InversionEvolutiva/1.0)"}


def fetch_ohlcv(interval: str = "15m", range_str: str = "5d") -> pd.DataFrame:
    """
    Descarga velas OHLCV de EUR/USD desde Yahoo Finance.

    5 días de velas de 15 min ≈ 480 velas — suficiente para MACD(12,26,9) y RSI(50).
    Retorna DataFrame con columnas: timestamp, open, high, low, close.
    """
    resp = requests.get(
        _YAHOO_URL, headers=_HEADERS,
        params={"interval": interval, "range": range_str},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    quote  = result["indicators"]["quote"][0]

    df = pd.DataFrame({
        "timestamp": [datetime.fromtimestamp(ts, tz=timezone.utc) for ts in result["timestamp"]],
        "open":  [float(v) if v is not None else None for v in quote["open"]],
        "high":  [float(v) if v is not None else None for v in quote["high"]],
        "low":   [float(v) if v is not None else None for v in quote["low"]],
        "close": [float(v) if v is not None else None for v in quote["close"]],
    }).dropna().reset_index(drop=True)

    log.info("[Indicators] %d velas OHLCV cargadas (interval=%s range=%s)", len(df), interval, range_str)
    return df


def calc_signals(df: pd.DataFrame, params: dict) -> TechnicalSignals:
    """
    Calcula RSI, EMA y MACD usando los parámetros genéticos específicos del agente.

    Recibe el DataFrame OHLCV ya descargado — así 10 agentes comparten 1 request HTTP
    pero cada uno obtiene indicadores calculados con sus propios períodos evolutivos.
    """
    close = df["close"]

    rsi_p   = int(params.get("rsi_periodo", 14))
    ema_r   = int(params.get("ema_rapida",   9))
    ema_l   = int(params.get("ema_lenta",   21))
    macd_f  = int(params.get("macd_rapida", 12))
    macd_sl = int(params.get("macd_lenta",  26))
    macd_sg = int(params.get("macd_senal",   9))

    # ── RSI ───────────────────────────────────────────────────────────────────
    delta    = close.diff()
    avg_gain = delta.clip(lower=0).ewm(com=rsi_p - 1, adjust=False).mean().iloc[-1]
    avg_loss = (-delta).clip(lower=0).ewm(com=rsi_p - 1, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        rsi_val = 100.0
    else:
        rsi_val = round(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 4)

    # ── EMA ───────────────────────────────────────────────────────────────────
    ema_r_val = round(float(close.ewm(span=ema_r, adjust=False).mean().iloc[-1]), 5)
    ema_l_val = round(float(close.ewm(span=ema_l, adjust=False).mean().iloc[-1]), 5)

    # ── MACD ──────────────────────────────────────────────────────────────────
    ema_fast  = close.ewm(span=macd_f,  adjust=False).mean()
    ema_slow  = close.ewm(span=macd_sl, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line  = macd_line.ewm(span=macd_sg, adjust=False).mean()
    hist      = macd_line - sig_line

    precio = round(float(close.iloc[-1]), 5)

    log.debug(
        "[Indicators] RSI(%d)=%.2f EMA%d=%.5f EMA%d=%.5f MACD_hist=%.5f precio=%.5f",
        rsi_p, rsi_val, ema_r, ema_r_val, ema_l, ema_l_val, float(hist.iloc[-1]), precio,
    )

    return TechnicalSignals(
        rsi=rsi_val,
        ema_rapida=ema_r_val,
        ema_lenta=ema_l_val,
        macd=round(float(macd_line.iloc[-1]), 5),
        macd_signal=round(float(sig_line.iloc[-1]), 5),
        macd_hist=round(float(hist.iloc[-1]), 5),
        precio_actual=precio,
        ema_cross_alcista=ema_r_val > ema_l_val,
    )


def fetch_signals(params: dict) -> TechnicalSignals:
    """Convenience: descarga OHLCV y calcula señales en un solo paso."""
    return calc_signals(fetch_ohlcv(), params)
