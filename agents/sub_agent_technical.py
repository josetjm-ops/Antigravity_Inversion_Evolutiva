"""
Sub-agente A (Técnico): Analiza indicadores RSI, EMA, MACD + SMC (FVG, OB, Range Proxy)
para EUR/USD. Produce una señal de recomendación (BUY/SELL/HOLD) con nivel de confianza
basado en los parámetros genéticos del agente padre.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base_agent import BaseAgent

log = logging.getLogger(__name__)
from data.alpha_vantage_client import TechnicalSignals
from data.indicators import fetch_signals

_SYSTEM_PROMPT = """Eres el Sub-agente Técnico de un sistema de trading evolutivo EUR/USD.
Tu rol es interpretar indicadores técnicos (RSI, EMA, MACD) y Smart Money Concepts
(Fair Value Gap, Order Blocks, Range Proxy) para emitir una señal de trading.

Reglas de respuesta:
- Responde ÚNICAMENTE con un JSON válido, sin texto adicional.
- Formato exacto: {"recomendacion": "BUY"|"SELL"|"HOLD", "confianza": 0.0-1.0, "razon": "string breve"}
- confianza debe reflejar la convergencia de señales (FVG+OB alineados = alta, señales mixtas = baja)."""


class SubAgentTechnical(BaseAgent):
    role = "technical"
    system_prompt = _SYSTEM_PROMPT

    def __init__(self, agent_id: str, params: dict, params_smc: dict | None = None):
        super().__init__(agent_id, params)
        self.params_smc = params_smc or {}

    # ── Scoring clásico ───────────────────────────────────────────────────────

    def _score_rsi(self, rsi: float, rsi_prev: float = 50.0) -> tuple[str, float]:
        """
        Modo momentum (default): señala la dirección del mercado mediante el cruce
        del nivel 50, alineándose con EMA y MACD en lugar de contradecirlos.

          Cruce al alza   (prev ≤ 50 → actual > 50) : BUY  fuerte (0.60–0.85)
          Cruce a la baja (prev ≥ 50 → actual < 50) : SELL fuerte (0.60–0.85)
          Por encima de 50 sin cruce                 : BUY  débil  (0.40–0.60)
          Por debajo de 50 sin cruce                 : SELL débil  (0.40–0.60)
          Zona muerta (|rsi - 50| ≤ zona_muerta)     : HOLD

        Modo reversion (legacy — params_tecnicos.rsi_modo == "reversion"):
          Mantiene el comportamiento anterior (señal en sobreventa/sobrecompra).
          Solo lo activa la evolución si descubre que funciona mejor.
        """
        modo = self.params.get("rsi_modo", "momentum")

        if modo == "reversion":
            sobrecompra = self.params.get("rsi_sobrecompra", 70)
            sobreventa  = self.params.get("rsi_sobreventa",  30)
            if rsi <= sobreventa:
                return "BUY",  min(0.95, 0.5 + (sobreventa - rsi) / sobreventa)
            if rsi >= sobrecompra:
                return "SELL", min(0.95, 0.5 + (rsi - sobrecompra) / (100 - sobrecompra))
            return "HOLD", 0.40

        # ── Modo momentum: cruce del nivel 50 ────────────────────────────────
        nivel    = 50.0
        zona_muerta = float(self.params.get("rsi_zona_muerta", 5.0))

        cruce_alcista  = rsi_prev <= nivel and rsi > nivel
        cruce_bajista  = rsi_prev >= nivel and rsi < nivel

        if cruce_alcista:
            strength = min(1.0, (rsi - nivel) / 20.0)     # 0→1 en rango 50–70
            return "BUY", round(min(0.85, 0.60 + strength * 0.25), 4)

        if cruce_bajista:
            strength = min(1.0, (nivel - rsi) / 20.0)     # 0→1 en rango 30–50
            return "SELL", round(min(0.85, 0.60 + strength * 0.25), 4)

        # Sin cruce — sesgo suave por posición respecto al nivel 50
        dist = abs(rsi - nivel)
        if dist <= zona_muerta:
            return "HOLD", 0.35

        if rsi > nivel:
            strength = min(1.0, (rsi - nivel) / 30.0)
            return "BUY",  round(min(0.60, 0.40 + strength * 0.15), 4)
        else:
            strength = min(1.0, (nivel - rsi) / 30.0)
            return "SELL", round(min(0.60, 0.40 + strength * 0.15), 4)

    def _score_ema(self, ema_rapida: float, ema_lenta: float) -> tuple[str, float]:
        if ema_lenta == 0:
            return "HOLD", 0.35
        diff_pct = (ema_rapida - ema_lenta) / ema_lenta
        if diff_pct > 0.0001:
            return "BUY",  min(0.90, 0.55 + abs(diff_pct) * 100)
        if diff_pct < -0.0001:
            return "SELL", min(0.90, 0.55 + abs(diff_pct) * 100)
        return "HOLD", 0.35

    def _score_macd(self, macd_hist: float) -> tuple[str, float]:
        if macd_hist > 0.00005:
            return "BUY",  min(0.85, 0.5 + macd_hist * 200)
        if macd_hist < -0.00005:
            return "SELL", min(0.85, 0.5 + abs(macd_hist) * 200)
        return "HOLD", 0.30

    # ── Scoring SMC ───────────────────────────────────────────────────────────

    def _score_fvg(
        self, fvg_activo: bool, fvg_direccion: str, fvg_pips: float
    ) -> tuple[str, float]:
        """FVG activo no rellenado → señal en su dirección. Confianza escala con el tamaño del gap."""
        if not fvg_activo or fvg_direccion == "NONE":
            return "HOLD", 0.30
        min_pips = float(self.params_smc.get("fvg_min_pips", 5.0))
        # confianza: 0.55 base + hasta 0.30 extra según tamaño relativo al umbral
        strength = min(1.0, (float(fvg_pips) - min_pips) / (min_pips * 2))
        conf = round(float(min(0.85, 0.55 + strength * 0.30)), 4)
        rec = "BUY" if fvg_direccion == "BULL" else "SELL"
        return rec, conf

    def _score_ob(self, ob_activo: bool, ob_direccion: str) -> tuple[str, float]:
        """Order Block no mitigado → señal en su dirección con confianza fija alta."""
        if not ob_activo or ob_direccion == "NONE":
            return "HOLD", 0.30
        rec = "BUY" if ob_direccion == "BULL" else "SELL"
        return rec, 0.65

    def _score_breakout(
        self, breakout_activo: bool, breakout_direccion: str, breakout_pips: float,
        range_spike: bool,
    ) -> tuple[str, float]:
        """
        Ruptura de estructura (S3): cierre por encima/debajo del rango de N velas.

        La confianza base es 0.65; se amplifica hasta 0.90 si además hay range_spike
        (expansión de volatilidad que confirma que la ruptura tiene fuerza real).
        Sin breakout activo devuelve HOLD (el agente S3 espera el momento preciso).
        """
        if not breakout_activo or breakout_direccion == "NONE":
            return "HOLD", 0.30
        min_pips = float(self.params_smc.get("breakout_min_pips", 5.0))
        strength = min(1.0, breakout_pips / (min_pips * 3))
        conf     = round(min(0.85, 0.65 + strength * 0.20), 4)
        if range_spike:
            conf = min(0.90, round(conf * 1.10, 4))
        rec = "BUY" if breakout_direccion == "BULL" else "SELL"
        return rec, conf

    # ── Sistema ponderado ─────────────────────────────────────────────────────

    def _weighted_signal(self, signals: list[tuple[str, float, float]]) -> tuple[str, float]:
        """
        Calcula la señal ponderada.
        signals: list of (recomendacion, confianza, peso)
        """
        score_buy  = sum(c * w for r, c, w in signals if r == "BUY")
        score_sell = sum(c * w for r, c, w in signals if r == "SELL")
        # Normalizar solo sobre el peso de los indicadores direccionales (los que sí opinan).
        # Dividir por el peso total diluye la señal con los indicadores neutros.
        dir_weight = sum(w for r, _, w in signals if r in ("BUY", "SELL"))

        if dir_weight == 0:
            return "HOLD", 0.30
        buy_norm  = score_buy  / dir_weight
        sell_norm = score_sell / dir_weight

        # Exige dominancia clara: margen mínimo de 0.15 y convicción base de 0.40.
        if buy_norm > sell_norm and (buy_norm - sell_norm) > 0.15 and buy_norm > 0.40:
            return "BUY",  round(buy_norm, 4)
        if sell_norm > buy_norm and (sell_norm - buy_norm) > 0.15 and sell_norm > 0.40:
            return "SELL", round(sell_norm, 4)
        return "HOLD", round(max(buy_norm, sell_norm, 0.30), 4)

    # ── Análisis principal ────────────────────────────────────────────────────

    def analyze(
        self,
        signals: TechnicalSignals | None = None,
        especie: str = "tendencia",
    ) -> dict[str, Any]:
        """
        Calcula la señal técnica enrutando por especie:

          tendencia  — momentum RSI50 + EMA + MACD + HTF strict (comportamiento original)
          reversion  — RSI en extremos (mode=reversion) + OB/FVG como entrada estructural
                       + HTF desactivado (opera contra-tendencia en rango)
          ruptura    — breakout de estructura como señal primaria, confirmado por
                       range_spike; RSI/EMA como contexto secundario

        La especie se pasa desde InvestorAgent, que la carga de la DB.
        """
        if signals is None:
            signals = fetch_signals(self.params, self.params_smc)

        # ── Scores comunes a las tres especies ──────────────────────────────
        rsi_rec,  rsi_conf  = self._score_rsi(signals.rsi, getattr(signals, "rsi_prev", 50.0))
        ema_rec,  ema_conf  = self._score_ema(signals.ema_rapida, signals.ema_lenta)
        macd_rec, macd_conf = self._score_macd(signals.macd_hist)
        fvg_rec,  fvg_conf  = self._score_fvg(
            signals.fvg_activo, signals.fvg_direccion, signals.fvg_pips
        )
        ob_rec,  ob_conf    = self._score_ob(signals.ob_activo, signals.ob_direccion)
        bo_rec,  bo_conf    = self._score_breakout(
            signals.breakout_activo, signals.breakout_direccion,
            signals.breakout_pips,   signals.range_spike,
        )

        # ── Ensamble ponderado por especie ───────────────────────────────────
        w_rsi  = float(self.params.get("peso_rsi",  0.25))
        w_ema  = float(self.params.get("peso_ema",  0.25))
        w_macd = float(self.params.get("peso_macd", 0.20))
        w_fvg  = float(self.params_smc.get("peso_fvg", 0.15))
        w_ob   = float(self.params_smc.get("peso_ob",  0.15))
        w_bo   = float(self.params_smc.get("peso_breakout", 0.40))  # peso alto para S3

        if especie == "ruptura":
            # S3: breakout domina; RSI y EMA solo como contexto
            rec, conf = self._weighted_signal([
                (bo_rec,   bo_conf,   w_bo),
                (rsi_rec,  rsi_conf,  w_rsi * 0.5),
                (ema_rec,  ema_conf,  w_ema * 0.5),
                (fvg_rec,  fvg_conf,  w_fvg),
                (ob_rec,   ob_conf,   w_ob),
            ])
        elif especie == "reversion":
            # S2: RSI reversion + OB/FVG estructural; EMA/MACD con peso reducido
            rec, conf = self._weighted_signal([
                (rsi_rec,  rsi_conf,  w_rsi * 1.5),
                (ob_rec,   ob_conf,   w_ob  * 1.5),
                (fvg_rec,  fvg_conf,  w_fvg * 1.5),
                (ema_rec,  ema_conf,  w_ema * 0.4),
                (macd_rec, macd_conf, w_macd * 0.4),
            ])
        else:
            # S1 tendencia (default): ensamble original
            rec, conf = self._weighted_signal([
                (rsi_rec,  rsi_conf,  w_rsi),
                (ema_rec,  ema_conf,  w_ema),
                (macd_rec, macd_conf, w_macd),
                (fvg_rec,  fvg_conf,  w_fvg),
                (ob_rec,   ob_conf,   w_ob),
            ])

        # ── Range spike: amplifica si confirma la señal (todas las especies) ─
        if signals.range_spike and rec in ("BUY", "SELL"):
            candle_dir = getattr(signals, "candle_direccion", "NEUTRAL")
            signal_dir = "BULL" if rec == "BUY" else "BEAR"
            if candle_dir == signal_dir:
                conf = min(0.95, round(conf * 1.15, 4))

        # ── Filtro HTF: solo S1 y S3 (S2 opera contra-tendencia) ─────────────
        htf_vetada = False
        htf_filter = bool(self.params_smc.get("htf_filter_enabled", True))
        if htf_filter and rec in ("BUY", "SELL"):
            htf = getattr(signals, "htf_direccion", "NEUTRAL")
            if (rec == "BUY" and htf == "BEAR") or (rec == "SELL" and htf == "BULL"):
                rec, conf, htf_vetada = "HOLD", round(conf, 4), True

        log.debug(
            "[SubAgentTechnical:%s] RSI:%s(%.2f) EMA:%s(%.2f) MACD:%s(%.2f) "
            "FVG:%s(%.2f) OB:%s(%.2f) BO:%s(%.2f) spike=%s ADX=%.1f régimen=%s HTF=%s%s → %s(%.2f)",
            especie,
            rsi_rec, rsi_conf, ema_rec, ema_conf, macd_rec, macd_conf,
            fvg_rec, fvg_conf, ob_rec, ob_conf, bo_rec, bo_conf,
            signals.range_spike,
            getattr(signals, "adx", 0.0),
            getattr(signals, "regime_estado", "N/A"),
            getattr(signals, "htf_direccion", "N/A"),
            "(VETO)" if htf_vetada else "",
            rec, conf,
        )

        # Validate with LLM only when confidence is ambiguous (0.45–0.65)
        llm_razon = None
        if 0.45 <= conf <= 0.65:
            prompt = (
                f"Especie={especie}. "
                f"RSI={signals.rsi:.2f} ({rsi_rec} conf={rsi_conf:.2f}), "
                f"EMA ({ema_rec}), MACD_hist={signals.macd_hist:.5f} ({macd_rec}). "
                f"FVG={signals.fvg_activo} {signals.fvg_direccion} {signals.fvg_pips:.1f}pips, "
                f"OB={signals.ob_activo} {signals.ob_direccion}. "
                f"Breakout={signals.breakout_activo} {signals.breakout_direccion} {signals.breakout_pips:.1f}pips. "
                f"ADX={signals.adx:.1f} régimen={signals.regime_estado}. "
                f"Señal ponderada: {rec} (confianza={conf:.2f}). "
                f"¿Confirmas o ajustas? Responde solo JSON."
            )
            try:
                raw    = self.reason(prompt)
                parsed = json.loads(raw)
                rec    = parsed.get("recomendacion", rec)
                conf   = float(parsed.get("confianza", conf))
                llm_razon = parsed.get("razon", "")
            except Exception as e:
                log.warning("[SubAgentTechnical] LLM no disponible: %s — usando heurística.", e)

        return {
            "agente_id":     self.agent_id,
            "especie":       especie,
            "recomendacion": rec,
            "confianza":     conf,
            "indicadores": {
                # Clásicos
                "rsi":               round(signals.rsi, 4),
                "rsi_prev":          round(getattr(signals, "rsi_prev", 50.0), 4),
                "ema_rapida":        round(signals.ema_rapida, 5),
                "ema_lenta":         round(signals.ema_lenta, 5),
                "ema_cross_alcista": signals.ema_cross_alcista,
                "macd":              round(signals.macd, 5),
                "macd_signal":       round(signals.macd_signal, 5),
                "macd_hist":         round(signals.macd_hist, 5),
                "precio_actual":     round(signals.precio_actual, 5),
                # SMC
                "fvg_activo":        signals.fvg_activo,
                "fvg_direccion":     signals.fvg_direccion,
                "fvg_pips":          signals.fvg_pips,
                "fvg_nivel_sup":     signals.fvg_nivel_sup,
                "fvg_nivel_inf":     signals.fvg_nivel_inf,
                "ob_activo":         signals.ob_activo,
                "ob_direccion":      signals.ob_direccion,
                "ob_nivel_sup":      signals.ob_nivel_sup,
                "ob_nivel_inf":      signals.ob_nivel_inf,
                "range_proxy":       signals.range_proxy,
                "range_ma20":        signals.range_ma20,
                "range_spike":       signals.range_spike,
                "candle_direccion":  getattr(signals, "candle_direccion", "NEUTRAL"),
                "atr":               round(signals.atr, 6),
                "atr_pips":          round(signals.atr * 10_000, 2),
                # Régimen + Ruptura
                "adx":               round(getattr(signals, "adx", 0.0), 2),
                "regime_estado":     getattr(signals, "regime_estado", "NEUTRAL"),
                "breakout_activo":   signals.breakout_activo,
                "breakout_direccion": signals.breakout_direccion,
                "breakout_pips":     signals.breakout_pips,
            },
            "scores_individuales": {
                "rsi":      {"señal": rsi_rec,  "confianza": round(rsi_conf, 4)},
                "ema":      {"señal": ema_rec,  "confianza": round(ema_conf, 4)},
                "macd":     {"señal": macd_rec, "confianza": round(macd_conf, 4)},
                "fvg":      {"señal": fvg_rec,  "confianza": round(fvg_conf, 4)},
                "ob":       {"señal": ob_rec,   "confianza": round(ob_conf, 4)},
                "breakout": {"señal": bo_rec,   "confianza": round(bo_conf, 4)},
                "range_spike": signals.range_spike,
                "htf": {"direccion": getattr(signals, "htf_direccion", "NEUTRAL"),
                        "veto": htf_vetada},
            },
            "llm_ajuste": llm_razon,
        }
