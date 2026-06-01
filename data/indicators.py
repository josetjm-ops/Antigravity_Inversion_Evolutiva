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
# FILTRO DE TEMPORALIDAD SUPERIOR (HTF 1h)
# ══════════════════════════════════════════════════════════════════════════════

def calc_htf_trend_series(
    df_1h: pd.DataFrame,
    ema_rapida: int = 50,
    ema_lenta: int = 200,
) -> pd.DataFrame:
    """
    Calcula la serie temporal del sesgo direccional en el timeframe de 1h.

    Usa EMA50 y EMA200 sobre el cierre horario. La dirección se clasifica como:
      BULL : precio > EMA50 Y EMA50 > EMA200  (tendencia alcista confirmada)
      BEAR : precio < EMA50 Y EMA50 < EMA200  (tendencia bajista confirmada)
      NEUTRAL: cualquier estado mixto (permite operar en ambas direcciones)

    Devuelve el DataFrame original con columnas adicionales:
      htf_ema_rapida, htf_ema_lenta, htf_direccion

    Nota: con EMA200 se necesitan ≥200 barras de 1h para que el suavizado
    converge (≈8 días). Con range_str="3mo" el warmup es más que suficiente.
    """
    close = df_1h["close"]
    ema_r = close.ewm(span=ema_rapida,  adjust=False).mean()
    ema_l = close.ewm(span=ema_lenta,   adjust=False).mean()

    def _dir(row: pd.Series) -> str:
        p, r, l = row["close"], row["htf_ema_rapida"], row["htf_ema_lenta"]
        if p > r and r > l:
            return "BULL"
        if p < r and r < l:
            return "BEAR"
        return "NEUTRAL"

    df_out = df_1h.copy()
    df_out["htf_ema_rapida"] = ema_r.round(5)
    df_out["htf_ema_lenta"]  = ema_l.round(5)
    df_out["htf_direccion"]  = df_out.apply(_dir, axis=1)
    return df_out[["timestamp", "close", "htf_ema_rapida", "htf_ema_lenta", "htf_direccion"]]


def fetch_htf_trend(ema_rapida: int = 50, ema_lenta: int = 200) -> dict:
    """
    Descarga velas 1h (3 meses) y retorna el sesgo direccional actual como dict:
      {"direccion": "BULL"|"BEAR"|"NEUTRAL", "ema_rapida": float, "ema_lenta": float}

    Se llama UNA VEZ por ciclo en trade_monitor y se comparte entre todos los
    agentes, igual que el OHLCV de 15m.

    Ante cualquier fallo de descarga devuelve NEUTRAL para no bloquear el ciclo.
    """
    try:
        df_1h = fetch_ohlcv(interval="1h", range_str="3mo")
        serie = calc_htf_trend_series(df_1h, ema_rapida=ema_rapida, ema_lenta=ema_lenta)
        ultima = serie.iloc[-1]
        result = {
            "direccion":   ultima["htf_direccion"],
            "ema_rapida":  float(ultima["htf_ema_rapida"]),
            "ema_lenta":   float(ultima["htf_ema_lenta"]),
        }
        log.info(
            "[Indicators] HTF(1h) EMA%d=%.5f EMA%d=%.5f → %s",
            ema_rapida, result["ema_rapida"],
            ema_lenta,  result["ema_lenta"],
            result["direccion"],
        )
        return result
    except Exception as exc:
        log.warning("[Indicators] fetch_htf_trend falló (%s) — usando NEUTRAL.", exc)
        return {"direccion": "NEUTRAL", "ema_rapida": 0.0, "ema_lenta": 0.0}


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


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Average True Range (Wilder) sobre velas OHLCV de 15 min.

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = media exponencial del TR con suavizado Wilder (com=period-1).

    Para EUR/USD en 15min el ATR típico es 0.0008–0.0015 (8–15 pips).
    Retorna el valor en precio (no en pips); multiplica × 10 000 para pips.
    """
    if len(df) < period + 1:
        return round(float((df["high"] - df["low"]).mean()), 6)

    high       = df["high"]
    low        = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_val = float(tr.ewm(com=period - 1, adjust=False).mean().iloc[-1])
    return round(atr_val, 6)


def calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    Average Directional Index (Wilder) sobre velas OHLCV de 15 min.

    ADX mide la FUERZA de la tendencia (no su dirección):
      ADX >= 25 → mercado en tendencia
      ADX <  25 → mercado en rango / sin dirección clara

    Retorna un escalar 0-100. Con pocos datos retorna 0 (seguro = NEUTRAL).
    """
    if len(df) < period + 2:
        return 0.0

    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    plus_dm  = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)
    both     = (high - prev_high) < (prev_low - low)
    plus_dm[both]  = 0.0
    neither  = (high - prev_high) <= 0
    plus_dm[neither] = 0.0
    only_minus = (prev_low - low) <= 0
    minus_dm[only_minus] = 0.0

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr14      = tr.ewm(com=period - 1, adjust=False).mean()
    plus_di    = 100.0 * plus_dm.ewm(com=period - 1, adjust=False).mean() / atr14.clip(lower=1e-10)
    minus_di   = 100.0 * minus_dm.ewm(com=period - 1, adjust=False).mean() / atr14.clip(lower=1e-10)
    di_sum     = (plus_di + minus_di).clip(lower=1e-10)
    dx         = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_series = dx.ewm(com=period - 1, adjust=False).mean()

    return round(float(adx_series.iloc[-1]), 2)


def calc_regime(df: pd.DataFrame, adx_period: int = 14, adx_threshold: float = 25.0) -> dict:
    """
    Clasifica el régimen actual del mercado en función del ADX.

    Retorna:
      {"estado": "TENDENCIA"|"RANGO"|"NEUTRAL", "adx": float}

    TENDENCIA : ADX >= adx_threshold (el precio sigue una dirección fuerte)
    RANGO     : ADX <  adx_threshold (el precio oscila sin dirección)
    NEUTRAL   : no hay suficientes datos para calcularlo (ADX = 0)
    """
    adx = calc_adx(df, period=adx_period)
    if adx == 0.0:
        estado = "NEUTRAL"
    elif adx >= adx_threshold:
        estado = "TENDENCIA"
    else:
        estado = "RANGO"
    return {"estado": estado, "adx": adx}


def detect_breakout(df: pd.DataFrame, lookback: int = 20, min_pips: float = 5.0) -> dict:
    """
    Detecta si el precio actual está rompiendo la estructura de rango reciente.

    Ruptura alcista: cierre actual > máximo de las últimas `lookback` velas
                     (excluida la vela actual) por al menos min_pips.
    Ruptura bajista: cierre actual < mínimo de las últimas `lookback` velas
                     (excluida la vela actual) por al menos min_pips.

    Se usa el cierre (no el máximo/mínimo intra-vela) para confirmar que el
    precio CERRÓ fuera del rango: evita falsos positivos por mechas.
    """
    empty = {"activo": False, "direccion": "NONE", "pips": 0.0}

    if len(df) < lookback + 2:
        return empty

    prev_slice  = df["close"].iloc[-(lookback + 1):-1]
    precio_act  = float(df["close"].iloc[-1])

    struct_high = float(prev_slice.max())
    struct_low  = float(prev_slice.min())

    bull_pips = (precio_act - struct_high) * 10_000
    bear_pips = (struct_low  - precio_act) * 10_000

    if bull_pips >= min_pips:
        return {"activo": True, "direccion": "BULL", "pips": round(bull_pips, 2)}
    if bear_pips >= min_pips:
        return {"activo": True, "direccion": "BEAR", "pips": round(bear_pips, 2)}
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
    htf_trend: dict | None = None,
) -> TechnicalSignals:
    """
    Calcula RSI, EMA, MACD (parámetros genéticos del agente) y los indicadores
    SMC: FVG, Order Blocks y Range Proxy.

    params      : params_tecnicos del agente (genes clásicos)
    params_smc  : params_smc del agente (genes SMC). Si es None usa defaults.
    htf_trend   : dict {"direccion", "ema_rapida", "ema_lenta"} del timeframe 1h.
                  Si es None los campos HTF quedan en sus defaults seguros (NEUTRAL).

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
    fvg_min_pips         = float(params_smc.get("fvg_min_pips",              5.0))
    ob_impulse_pips      = float(params_smc.get("ob_impulse_pips",           10.0))
    range_multiplier     = float(params_smc.get("range_spike_multiplier",     1.5))
    atr_period           = int(params_smc.get("atr_period",                  14))
    breakout_lookback    = int(params_smc.get("breakout_lookback_bars",       20))
    breakout_min_pips    = float(params_smc.get("breakout_min_pips",          5.0))
    adx_period           = int(params_smc.get("adx_period",                  14))
    adx_threshold        = float(params_smc.get("adx_threshold",             25.0))

    # ── RSI ───────────────────────────────────────────────────────────────────
    # Calculamos la serie completa para obtener también rsi_prev (vela anterior),
    # necesario para detectar el cruce del nivel 50 en modo momentum.
    delta      = close.diff()
    avg_gains  = delta.clip(lower=0).ewm(com=rsi_p - 1, adjust=False).mean()
    avg_losses = (-delta).clip(lower=0).ewm(com=rsi_p - 1, adjust=False).mean()
    rsi_series = 100.0 - 100.0 / (1.0 + avg_gains / avg_losses.clip(lower=1e-10))
    rsi_val    = round(float(rsi_series.iloc[-1]), 4)
    rsi_prev   = round(float(rsi_series.iloc[-2]), 4) if len(rsi_series) >= 2 else rsi_val

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

    # ── SMC + Régimen + Ruptura ───────────────────────────────────────────────
    fvg  = detect_fvg(df, min_pips=fvg_min_pips)
    ob   = detect_order_blocks(df, impulse_pips=ob_impulse_pips)
    rng_actual, rng_ma20, rng_spike = calc_range_proxy(df, multiplier=range_multiplier)
    atr_val  = calc_atr(df, period=atr_period)
    regime   = calc_regime(df, adx_period=adx_period, adx_threshold=adx_threshold)
    breakout = detect_breakout(df, lookback=breakout_lookback, min_pips=breakout_min_pips)

    # Dirección de la última vela (para condicionar la amplificación de range spike)
    _last_open  = float(df["open"].iloc[-1])
    _last_close = float(df["close"].iloc[-1])
    _doji_thr   = 0.5 / 10_000   # menos de 0.5 pips de cuerpo → doji (NEUTRAL)
    if _last_close - _last_open > _doji_thr:
        candle_dir = "BULL"
    elif _last_open - _last_close > _doji_thr:
        candle_dir = "BEAR"
    else:
        candle_dir = "NEUTRAL"

    log.debug(
        "[Indicators] RSI(%d)=%.2f(prev=%.2f) EMA%d=%.5f EMA%d=%.5f MACD_hist=%.5f precio=%.5f "
        "FVG=%s(%s %.1fpips) OB=%s(%s) Range=%.1f/%.1f spike=%s ATR(%d)=%.1fpips",
        rsi_p, rsi_val, rsi_prev, ema_r, ema_r_val, ema_l, ema_l_val,
        float(hist.iloc[-1]), precio,
        fvg["activo"], fvg["direccion"], fvg["pips"],
        ob["activo"], ob["direccion"],
        rng_actual, rng_ma20, rng_spike,
        atr_period, atr_val * 10_000,
    )

    _htf = htf_trend or {}
    return TechnicalSignals(
        # Clásicos
        rsi=rsi_val,
        rsi_prev=rsi_prev,
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
        candle_direccion=candle_dir,
        # ATR dinámico
        atr=atr_val,
        # HTF (1h) — poblado desde fetch_htf_trend(); NEUTRAL si no se pasa
        htf_direccion=str(_htf.get("direccion", "NEUTRAL")),
        htf_ema_rapida=float(_htf.get("ema_rapida", 0.0)),
        htf_ema_lenta=float(_htf.get("ema_lenta", 0.0)),
        # Régimen (Fase 2)
        adx=regime["adx"],
        regime_estado=regime["estado"],
        # Ruptura S3 (Fase 2)
        breakout_activo=breakout["activo"],
        breakout_direccion=breakout["direccion"],
        breakout_pips=breakout["pips"],
    )


def fetch_signals(params: dict, params_smc: dict | None = None) -> TechnicalSignals:
    """Convenience: descarga OHLCV + HTF y calcula todas las señales en un paso."""
    return calc_signals(fetch_ohlcv(), params, params_smc, htf_trend=fetch_htf_trend())
