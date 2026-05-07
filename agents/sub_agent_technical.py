"""
Sub-agente A (Técnico): Analiza indicadores RSI, EMA, MACD para EUR/USD.
Produce una señal de recomendación (BUY/SELL/HOLD) con nivel de confianza
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
Tu rol es interpretar indicadores técnicos (RSI, EMA, MACD) y emitir una señal de trading.

Reglas de respuesta:
- Responde ÚNICAMENTE con un JSON válido, sin texto adicional.
- Formato exacto: {"recomendacion": "BUY"|"SELL"|"HOLD", "confianza": 0.0-1.0, "razon": "string breve"}
- confianza debe reflejar la convergencia de señales (3/3 = alta, 2/3 = media, 1/3 = baja)."""


class SubAgentTechnical(BaseAgent):
    role = "technical"
    system_prompt = _SYSTEM_PROMPT

    def _score_rsi(self, rsi: float) -> tuple[str, float]:
        sobrecompra = self.params.get("rsi_sobrecompra", 70)
        sobreventa = self.params.get("rsi_sobreventa", 30)
        if rsi <= sobreventa:
            strength = (sobreventa - rsi) / sobreventa
            return "BUY", min(0.95, 0.5 + strength)
        if rsi >= sobrecompra:
            strength = (rsi - sobrecompra) / (100 - sobrecompra)
            return "SELL", min(0.95, 0.5 + strength)
        return "HOLD", 0.4

    def _score_ema(self, ema_rapida: float, ema_lenta: float) -> tuple[str, float]:
        if ema_lenta == 0:
            return "HOLD", 0.35
        diff_pct = (ema_rapida - ema_lenta) / ema_lenta
        if diff_pct > 0.0001:
            return "BUY", min(0.90, 0.55 + abs(diff_pct) * 100)
        if diff_pct < -0.0001:
            return "SELL", min(0.90, 0.55 + abs(diff_pct) * 100)
        return "HOLD", 0.35

    def _score_macd(self, macd_hist: float) -> tuple[str, float]:
        if macd_hist > 0.00005:
            return "BUY", min(0.85, 0.5 + macd_hist * 200)
        if macd_hist < -0.00005:
            return "SELL", min(0.85, 0.5 + abs(macd_hist) * 200)
        return "HOLD", 0.30

    def _weighted_signal(self, signals: list[tuple[str, float, float]]) -> tuple[str, float]:
        """
        Calcula la señal ponderada.
        signals: list of (recomendacion, confianza, peso)
        """
        score_buy = sum(c * w for r, c, w in signals if r == "BUY")
        score_sell = sum(c * w for r, c, w in signals if r == "SELL")
        total_weight = sum(w for _, _, w in signals)

        if total_weight == 0:
            return "HOLD", 0.30
        score_buy /= total_weight
        score_sell /= total_weight

        if score_buy > score_sell and score_buy > 0.45:
            return "BUY", round(score_buy, 4)
        if score_sell > score_buy and score_sell > 0.45:
            return "SELL", round(score_sell, 4)
        return "HOLD", round(max(score_buy, score_sell, 0.30), 4)

    def analyze(self, signals: TechnicalSignals | None = None) -> dict[str, Any]:
        if signals is None:
            signals = fetch_signals(self.params)

        rsi_rec, rsi_conf = self._score_rsi(signals.rsi)
        ema_rec, ema_conf = self._score_ema(signals.ema_rapida, signals.ema_lenta)
        macd_rec, macd_conf = self._score_macd(signals.macd_hist)

        w_rsi = self.params.get("peso_rsi", 0.35)
        w_ema = self.params.get("peso_ema", 0.35)
        w_macd = self.params.get("peso_macd", 0.30)

        rec, conf = self._weighted_signal([
            (rsi_rec, rsi_conf, w_rsi),
            (ema_rec, ema_conf, w_ema),
            (macd_rec, macd_conf, w_macd),
        ])

        # Validate with LLM only when confidence is ambiguous (0.45–0.65)
        llm_razon = None
        if 0.45 <= conf <= 0.65:
            prompt = (
                f"RSI={signals.rsi:.2f} ({rsi_rec}, conf={rsi_conf:.2f}), "
                f"EMA_rápida={signals.ema_rapida:.5f} EMA_lenta={signals.ema_lenta:.5f} ({ema_rec}), "
                f"MACD_hist={signals.macd_hist:.5f} ({macd_rec}). "
                f"Señal ponderada preliminar: {rec} (confianza={conf:.2f}). "
                f"¿Confirmas o ajustas? Responde solo JSON."
            )
            try:
                raw = self.reason(prompt)
                parsed = json.loads(raw)
                rec = parsed.get("recomendacion", rec)
                conf = float(parsed.get("confianza", conf))
                llm_razon = parsed.get("razon", "")
            except Exception as e:
                log.warning("[SubAgentTechnical] LLM no disponible: %s — usando heurística.", e)

        return {
            "agente_id": self.agent_id,
            "recomendacion": rec,
            "confianza": conf,
            "indicadores": {
                "rsi": round(signals.rsi, 4),
                "ema_rapida": round(signals.ema_rapida, 5),
                "ema_lenta": round(signals.ema_lenta, 5),
                "ema_cross_alcista": signals.ema_cross_alcista,
                "macd": round(signals.macd, 5),
                "macd_signal": round(signals.macd_signal, 5),
                "macd_hist": round(signals.macd_hist, 5),
                "precio_actual": round(signals.precio_actual, 5),
            },
            "scores_individuales": {
                "rsi": {"señal": rsi_rec, "confianza": round(rsi_conf, 4)},
                "ema": {"señal": ema_rec, "confianza": round(ema_conf, 4)},
                "macd": {"señal": macd_rec, "confianza": round(macd_conf, 4)},
            },
            "llm_ajuste": llm_razon,
        }
