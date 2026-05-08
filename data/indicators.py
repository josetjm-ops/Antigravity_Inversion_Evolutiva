"""
Indicadores técnicos EUR/USD calculados desde velas OHLCV de Yahoo Finance.

Indicadores clásicos: RSI, EMA, MACD
Smart Money Concepts: Fair Value Gap (FVG), Order Blocks (OB), Range Proxy

Flujo recomendado:
  df = fetch_ohlcv()                               # 1 request HTTP compartido
  signals_a = calc_signals(df, params_a, smc_a)   # cálculo en memoria por agente
  signals_b = calc_signals(df, params_b, smc_b)
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


# ══════════════════════════════════════════════════════════════════════════════
# DESCARGA OHLCV
# ══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv(interval: str = "15m", range_str: str = "5d") -> pd.DataFrame:
    """
    Descarga velas OHLCV de EUR/USD desde Yahoo Finance.

    5 días de velas de 15 min ≈ 420 velas — suficiente para MACD(12,26,9), RSI(50)
    y detección de FVG / Order Blocks en ventana de 80 velas.

    Nota: el campo 'volume' siempre llega en 0 para EUR/USD (mercado OTC sin
    bolsa centralizada). Se incluye en el DataFrame para mantener estructura
    consistente; el Range Proxy sustituye al volumen como señal de actividad.
    """
    resp = requests.get(
        _YAHOO_URL, headers=_HEADERS,
        params={"interval": interval, "range": range_str},
        timeout=15,
    )
    resp.raise_for_status()
    chart_result = resp.json().get("chart", {}).get("result") or []
    if not chart_result:
        raise RuntimeError(
            "Yahoo Finance: resultado vacío para EURUSD=X "
            "(mercado cerrado, festivo o estructura de API cambiada)."
        )
    result    = chart_result[0]
    quote_list = result.get("indicators", {}).get("quote") or []
    if not quote_list:
        raise RuntimeError("Yahoo Finance: sin datos de quote para EURUSD=X.")
    quote = quote_list[0]
    n     = len(result["timestamp"])

    df = pd.DataFrame({
        "timestamp": [datetime.fromtimestamp(ts, tz=timezone.utc) for ts in result["timestamp"]],
        "open":   [float(v) if v is not None else None for v in quote["open"]],
        "high":   [float(v) if v is not None else None for v in quote["high"]],
        "low":    [float(v) if v is not None else None for v in quote["low"]],
        "close":  [float(v) if v is not None else None for v in quote["close"]],
        "volume": [float(v) if v is not None else 0.0  for v in quote.get("volume", [0.0] * n)],
    }).dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

    log.info("[Indicators] %d velas OHLCV cargadas (interval=%s range=%s)", len(df), interval, range_str)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# SMART MONEY CONCEPTS — DETECCIÓN
# ══════════════════════════════════════════════════════════════════════════════

def detect_fvg(df: pd.DataFrame, min_pips: float = 5.0) -> dict:
    """
    Detecta el Fair Value Gap (FVG) más reciente no rellenado.

    FVG alcista: high[i-2] < low[i]  → ineficiencia por encima de la vela central
    FVG bajista: low[i-2]  > high[i] → ineficiencia por debajo de la vela central

    Solo se reporta el FVG más reciente cuyo tamaño supere min_pips y que el
    precio actual no haya rellenado todavía.
    """
    empty = {"activo": False, "direccion": "NONE", "pips": 0.0,
             "nivel_superior": 0.0, "nivel_inferior": 0.0}

    if len(df) < 3:
        return empty

    precio_actual = float(df["close"].iloc[-1])
    lookback      = min(50, len(df) - 2)

    for i in range(len(df) - 1, len(df) - 1 - lookback, -1):
        if i < 2:
            break

        # FVG alcista: gap entre techo de vela i-2 y piso de vela i
        gap_bull_pips = (df["low"].iloc[i] - df["high"].iloc[i - 2]) * 10_000
        if gap_bull_pips >= min_pips:
            nivel_inf = float(df["high"].iloc[i - 2])
            nivel_sup = float(df["low"].iloc[i])
            if precio_actual >= nivel_inf:          # precio aún por encima → no rellenado
                return {
                    "activo":          True,
                    "direccion":       "BULL",
                    "pips":            round(gap_bull_pips, 2),
                    "nivel_superior":  round(nivel_sup, 5),
                    "nivel_inferior":  round(nivel_inf, 5),
                }

        # FVG bajista: gap entre piso de vela i-2 y techo de vela i
        gap_bear_pips = (df["low"].iloc[i - 2] - df["high"].iloc[i]) * 10_000
        if gap_bear_pips >= min_pips:
            nivel_sup = float(df["low"].iloc[i - 2])
            nivel_inf = float(df["high"].iloc[i])
            if precio_actual <= nivel_sup:          # precio aún por debajo → no rellenado
                return {
                    "activo":          True,
                    "direccion":       "BEAR",
                    "pips":            round(gap_bear_pips, 2),
                    "nivel_superior":  round(nivel_sup, 5),
                    "nivel_inferior":  round(nivel_inf, 5),
                }

    return empty


def detect_order_blocks(df: pd.DataFrame, impulse_pips: float = 10.0) -> dict:
    """
    Detecta el Order Block más reciente no mitigado.

    OB alcista: última vela bajista (close < open) seguida de impulso alcista
                fuerte (siguiente vela sube > impulse_pips). Precio aún sobre el
                piso del OB (no mitigado).

    OB bajista: última vela alcista (close > open) seguida de impulso bajista
                fuerte. Precio aún bajo el techo del OB.
    """
    empty = {"activo": False, "direccion": "NONE",
             "nivel_superior": 0.0, "nivel_inferior": 0.0}

    if len(df) < 5:
        return empty

    precio_actual      = float(df["close"].iloc[-1])
    impulse_threshold  = impulse_pips / 10_000.0
    lookback           = min(80, len(df) - 2)

    for i in range(len(df) - 2, len(df) - 2 - lookback, -1):
        if i < 0:
            break

        cuerpo      = float(df["close"].iloc[i] - df["open"].iloc[i])
        sig_movida  = float(df["close"].iloc[i + 1] - df["open"].iloc[i + 1])

        nivel_sup = float(df["high"].iloc[i])
        nivel_inf = float(df["low"].iloc[i])

        # OB alcista: vela bajista + impulso alcista posterior
        if cuerpo < 0 and sig_movida >= impulse_threshold:
            if precio_actual > nivel_inf:           # no mitigado (precio sobre el piso)
                return {
                    "activo":          True,
                    "direccion":       "BULL",
                    "nivel_superior":  round(nivel_sup, 5),
                    "nivel_inferior":  round(nivel_inf, 5),
                }

        # OB bajista: vela alcista + impulso bajista posterior
        if cuerpo > 0 and sig_movida <= -impulse_threshold:
            if precio_actual < nivel_sup:           # no mitigado (precio bajo el techo)
                return {
                    "activo":          True,
                    "direccion":       "BEAR",
                    "nivel_superior":  round(nivel_sup, 5),
                    "nivel_inferior":  round(nivel_inf, 5),
                }

    return empty


def calc_range_proxy(df: pd.DataFrame, multiplier: float = 1.5) -> tuple[float, float, bool]:
    """
    Calcula el Range Proxy (high - low en pips) como sustituto del volumen.

    En EUR/USD Yahoo Finance siempre reporta volume = 0 (mercado OTC).
    Un rango de vela amplio en relación a su media indica actividad institucional.

    Retorna: (range_actual_pips, range_ma20_pips, range_spike)
    """
    rango_pips  = (df["high"] - df["low"]) * 10_000
    range_ma20  = float(rango_pips.rolling(20, min_periods=5).mean().iloc[-1])
    range_actual = float(rango_pips.iloc[-1])
    range_spike  = range_actual > range_ma20 * multiplier
    return round(range_actual, 2), round(range_ma20, 2), range_spike


# ══════════════════════════════════════════════════════════════════════════════
# CÁLCULO DE SEÑALES — clásico + SMC
# ══════════════════════════════════════════════════════════════════════════════

def calc_signals(
    df: pd.DataFrame,
    params: dict,
    params_smc: dict | None = None,
) -> TechnicalSignals:
    """
    Calcula RSI, EMA, MACD (parámetros genéticos del agente) y los indicadores
    SMC: FVG, Order Blocks y Range Proxy.

    params      : params_tecnicos del agente (genes clásicos)
    params_smc  : params_smc del agente (genes SMC). Si es None usa defaults.

    Un solo DataFrame compartido entre todos los agentes; cada uno obtiene
    sus propios indicadores calculados en memoria.
    """
    if params_smc is None:
        params_smc = {}

    close = df["close"]

    # ── Parámetros genéticos clásicos ─────────────────────────────────────────
    rsi_p   = int(params.get("rsi_periodo",  14))
    ema_r   = int(params.get("ema_rapida",    9))
    ema_l   = int(params.get("ema_lenta",    21))
    macd_f  = int(params.get("macd_rapida",  12))
    macd_sl = int(params.get("macd_lenta",   26))
    macd_sg = int(params.get("macd_senal",    9))

    # ── Parámetros genéticos SMC ──────────────────────────────────────────────
    fvg_min_pips       = float(params_smc.get("fvg_min_pips",            5.0))
    ob_impulse_pips    = float(params_smc.get("ob_impulse_pips",         10.0))
    range_multiplier   = float(params_smc.get("range_spike_multiplier",   1.5))

    # ── RSI ───────────────────────────────────────────────────────────────────
    delta    = close.diff()
    avg_gain = delta.clip(lower=0).ewm(com=rsi_p - 1, adjust=False).mean().iloc[-1]
    avg_loss = (-delta).clip(lower=0).ewm(com=rsi_p - 1, adjust=False).mean().iloc[-1]
    rsi_val  = 100.0 if avg_loss == 0 else round(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 4)

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

    # ── SMC ───────────────────────────────────────────────────────────────────
    fvg  = detect_fvg(df, min_pips=fvg_min_pips)
    ob   = detect_order_blocks(df, impulse_pips=ob_impulse_pips)
    rng_actual, rng_ma20, rng_spike = calc_range_proxy(df, multiplier=range_multiplier)

    log.debug(
        "[Indicators] RSI(%d)=%.2f EMA%d=%.5f EMA%d=%.5f MACD_hist=%.5f precio=%.5f "
        "FVG=%s(%s %.1fpips) OB=%s(%s) Range=%.1f/%.1f spike=%s",
        rsi_p, rsi_val, ema_r, ema_r_val, ema_l, ema_l_val,
        float(hist.iloc[-1]), precio,
        fvg["activo"], fvg["direccion"], fvg["pips"],
        ob["activo"], ob["direccion"],
        rng_actual, rng_ma20, rng_spike,
    )

    return TechnicalSignals(
        # Clásicos
        rsi=rsi_val,
        ema_rapida=ema_r_val,
        ema_lenta=ema_l_val,
        macd=round(float(macd_line.iloc[-1]), 5),
        macd_signal=round(float(sig_line.iloc[-1]), 5),
        macd_hist=round(float(hist.iloc[-1]), 5),
        precio_actual=precio,
        ema_cross_alcista=ema_r_val > ema_l_val,
        # SMC — FVG
        fvg_activo=fvg["activo"],
        fvg_direccion=fvg["direccion"],
        fvg_pips=fvg["pips"],
        fvg_nivel_sup=fvg["nivel_superior"],
        fvg_nivel_inf=fvg["nivel_inferior"],
        # SMC — Order Block
        ob_activo=ob["activo"],
        ob_direccion=ob["direccion"],
        ob_nivel_sup=ob["nivel_superior"],
        ob_nivel_inf=ob["nivel_inferior"],
        # SMC — Range Proxy
        range_proxy=rng_actual,
        range_ma20=rng_ma20,
        range_spike=rng_spike,
    )


def fetch_signals(params: dict, params_smc: dict | None = None) -> TechnicalSignals:
    """Convenience: descarga OHLCV y calcula señales clásicas + SMC en un paso."""
    return calc_signals(fetch_ohlcv(), params, params_smc)
