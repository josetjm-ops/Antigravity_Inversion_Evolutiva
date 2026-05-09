import os
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

load_dotenv()

_BASE_URL = os.getenv("ALPHA_VANTAGE_BASE_URL", "https://www.alphavantage.co/query")
_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
_SYMBOL = "EURUSD"


@dataclass
class TechnicalSignals:
    # ── Indicadores clásicos (requeridos) ─────────────────────────────────────
    rsi: float
    ema_rapida: float
    ema_lenta: float
    macd: float
    macd_signal: float
    macd_hist: float
    precio_actual: float
    ema_cross_alcista: bool

    # ── Smart Money Concepts (opcionales — defaults seguros) ──────────────────
    fvg_activo:      bool  = field(default=False)   # FVG detectado y no rellenado
    fvg_direccion:   str   = field(default="NONE")  # "BULL" | "BEAR" | "NONE"
    fvg_pips:        float = field(default=0.0)     # tamaño del FVG en pips
    fvg_nivel_sup:   float = field(default=0.0)     # precio techo del gap
    fvg_nivel_inf:   float = field(default=0.0)     # precio piso del gap
    ob_activo:       bool  = field(default=False)   # Order Block válido y no mitigado
    ob_direccion:    str   = field(default="NONE")  # "BULL" | "BEAR" | "NONE"
    ob_nivel_sup:    float = field(default=0.0)     # precio techo del OB
    ob_nivel_inf:    float = field(default=0.0)     # precio piso del OB
    range_proxy:     float = field(default=0.0)     # (high-low) última vela en pips
    range_ma20:      float = field(default=0.0)     # media móvil 20p del range en pips
    range_spike:     bool  = field(default=False)   # range_proxy > range_ma20 * multiplier
    atr:             float = field(default=0.0)     # ATR(14) Wilder en precio, ej. 0.0012 = 12 pips


def _get(params: dict) -> dict:
    params["apikey"] = _API_KEY
    resp = requests.get(_BASE_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "Error Message" in data or "Note" in data:
        raise RuntimeError(f"Alpha Vantage error: {data}")
    return data


def _latest_value(series: dict) -> tuple[str, dict]:
    latest_ts = sorted(series.keys(), reverse=True)[0]
    return latest_ts, series[latest_ts]


def get_rsi(period: int = 14) -> float:
    data = _get({
        "function": "RSI",
        "symbol": _SYMBOL,
        "interval": "15min",
        "time_period": period,
        "series_type": "close",
    })
    _, values = _latest_value(data["Technical Analysis: RSI"])
    return float(values["RSI"])


def get_ema(period: int) -> float:
    data = _get({
        "function": "EMA",
        "symbol": _SYMBOL,
        "interval": "15min",
        "time_period": period,
        "series_type": "close",
    })
    _, values = _latest_value(data["Technical Analysis: EMA"])
    return float(values["EMA"])


def get_macd(fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float]:
    data = _get({
        "function": "MACD",
        "symbol": _SYMBOL,
        "interval": "15min",
        "series_type": "close",
        "fastperiod": fast,
        "slowperiod": slow,
        "signalperiod": signal,
    })
    _, values = _latest_value(data["Technical Analysis: MACD"])
    return (
        float(values["MACD"]),
        float(values["MACD_Signal"]),
        float(values["MACD_Hist"]),
    )


def get_current_price() -> float:
    data = _get({
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": "EUR",
        "to_currency": "USD",
    })
    return float(data["Realtime Currency Exchange Rate"]["5. Exchange Rate"])


def fetch_signals(params_tecnicos: dict) -> TechnicalSignals:
    rsi_periodo = int(params_tecnicos["rsi_periodo"])
    ema_r = int(params_tecnicos["ema_rapida"])
    ema_l = int(params_tecnicos["ema_lenta"])
    macd_f = int(params_tecnicos["macd_rapida"])
    macd_sl = int(params_tecnicos["macd_lenta"])
    macd_sg = int(params_tecnicos["macd_senal"])

    rsi = get_rsi(rsi_periodo)
    ema_rapida = get_ema(ema_r)
    ema_lenta = get_ema(ema_l)
    macd, macd_signal, macd_hist = get_macd(macd_f, macd_sl, macd_sg)
    precio = get_current_price()

    return TechnicalSignals(
        rsi=rsi,
        ema_rapida=ema_rapida,
        ema_lenta=ema_lenta,
        macd=macd,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        precio_actual=precio,
        ema_cross_alcista=ema_rapida > ema_lenta,
    )
