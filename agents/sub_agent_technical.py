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

    def _score_rsi(self, rsi: float) -> tuple[str, float]:
        sobrecompra = self.params.get("rsi_sobrecompra", 70)
        sobreventa  = self.params.get("rsi_sobreventa",  30)
        if rsi <= sobreventa:
            strength = (sobreventa - rsi) / sobreventa
            return "BUY", min(0.95, 0.5 + strength)
        if rsi >= sobrecompra:
            strength = (rsi - sobrecompra) / (100 - sobrecompra)
            return "SELL", min(0.95, 0.5 + strength)
        return "HOLD", 0.40

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

    # ── Sistema ponderado ─────────────────────────────────────────────────────

    def _weighted_signal(self, signals: list[tuple[str, float, float]]) -> tuple[str, float]:
        """
        Calcula la señal ponderada.
        signals: list of (recomendacion, confianza, peso)
        """
        score_buy  = sum(c * w for r, c, w in signals if r == "BUY")
        score_sell = sum(c * w for r, c, w in signals if r == "SELL")
        total_weight = sum(w for _, _, w in signals)

        if total_weight == 0:
            return "HOLD", 0.30
        score_buy  /= total_weight
        score_sell /= total_weight

        if score_buy > score_sell and score_buy > 0.45:
            return "BUY",  round(score_buy, 4)
        if score_sell > score_buy and score_sell > 0.45:
            return "SELL", round(score_sell, 4)
        return "HOLD", round(max(score_buy, score_sell, 0.30), 4)

    # ── Análisis principal ────────────────────────────────────────────────────

    def analyze(self, signals: TechnicalSignals | None = None) -> dict[str, Any]:
        if signals is None:
            signals = fetch_signals(self.params, self.params_smc)

        # Scores clásicos
        rsi_rec,  rsi_conf  = self._score_rsi(signals.rsi)
        ema_rec,  ema_conf  = self._score_ema(signals.ema_rapida, signals.ema_lenta)
        macd_rec, macd_conf = self._score_macd(signals.macd_hist)

        # Scores SMC
        fvg_rec, fvg_conf = self._score_fvg(
            signals.fvg_activo, signals.fvg_direccion, signals.fvg_pips
        )
        ob_rec, ob_conf = self._score_ob(signals.ob_activo, signals.ob_direccion)

        # Pesos (clásicos desde params_tecnicos, SMC desde params_smc)
        w_rsi  = float(self.params.get("peso_rsi",  0.25))
        w_ema  = float(self.params.get("peso_ema",  0.25))
        w_macd = float(self.params.get("peso_macd", 0.20))
        w_fvg  = float(self.params_smc.get("peso_fvg", 0.15))
        w_ob   = float(self.params_smc.get("peso_ob",  0.15))

        rec, conf = self._weighted_signal([
            (rsi_rec,  rsi_conf,  w_rsi),
            (ema_rec,  ema_conf,  w_ema),
            (macd_rec, macd_conf, w_macd),
            (fvg_rec,  fvg_conf,  w_fvg),
            (ob_rec,   ob_conf,   w_ob),
        ])

        # Range spike amplifica la confianza de la señal dominante
        if signals.range_spike:
            conf = min(0.95, round(conf * 1.15, 4))

        log.debug(
            "[SubAgentTechnical] RSI:%s(%.2f) EMA:%s(%.2f) MACD:%s(%.2f) "
            "FVG:%s(%.2f) OB:%s(%.2f) range=%.1fpips spike=%s → %s(%.2f)",
            rsi_rec, rsi_conf, ema_rec, ema_conf, macd_rec, macd_conf,
            fvg_rec, fvg_conf, ob_rec, ob_conf,
            signals.range_proxy, signals.range_spike, rec, conf,
        )

        # Validate with LLM only when confidence is ambiguous (0.45–0.65)
        llm_razon = None
        if 0.45 <= conf <= 0.65:
            prompt = (
                f"RSI={signals.rsi:.2f} ({rsi_rec} conf={rsi_conf:.2f}), "
                f"EMA ({ema_rec}), MACD_hist={signals.macd_hist:.5f} ({macd_rec}). "
                f"FVG={signals.fvg_activo} {signals.fvg_direccion} {signals.fvg_pips:.1f}pips ({fvg_rec} conf={fvg_conf:.2f}), "
                f"OB={signals.ob_activo} {signals.ob_direccion} ({ob_rec} conf={ob_conf:.2f}). "
                f"Range={signals.range_proxy:.1f}pips ma20={signals.range_ma20:.1f} spike={signals.range_spike}. "
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
            "recomendacion": rec,
            "confianza":     conf,
            "indicadores": {
                # Clásicos
                "rsi":             round(signals.rsi, 4),
                "ema_rapida":      round(signals.ema_rapida, 5),
                "ema_lenta":       round(signals.ema_lenta, 5),
                "ema_cross_alcista": signals.ema_cross_alcista,
                "macd":            round(signals.macd, 5),
                "macd_signal":     round(signals.macd_signal, 5),
                "macd_hist":       round(signals.macd_hist, 5),
                "precio_actual":   round(signals.precio_actual, 5),
                # SMC
                "fvg_activo":      signals.fvg_activo,
                "fvg_direccion":   signals.fvg_direccion,
                "fvg_pips":        signals.fvg_pips,
                "fvg_nivel_sup":   signals.fvg_nivel_sup,
                "fvg_nivel_inf":   signals.fvg_nivel_inf,
                "ob_activo":       signals.ob_activo,
                "ob_direccion":    signals.ob_direccion,
                "ob_nivel_sup":    signals.ob_nivel_sup,
                "ob_nivel_inf":    signals.ob_nivel_inf,
                "range_proxy":     signals.range_proxy,
                "range_ma20":      signals.range_ma20,
                "range_spike":     signals.range_spike,
            },
            "scores_individuales": {
                "rsi":         {"señal": rsi_rec,  "confianza": round(rsi_conf, 4)},
                "ema":         {"señal": ema_rec,  "confianza": round(ema_conf, 4)},
                "macd":        {"señal": macd_rec, "confianza": round(macd_conf, 4)},
                "fvg":         {"señal": fvg_rec,  "confianza": round(fvg_conf, 4)},
                "ob":          {"señal": ob_rec,   "confianza": round(ob_conf, 4)},
                "range_spike": signals.range_spike,
            },
            "llm_ajuste": llm_razon,
        }
