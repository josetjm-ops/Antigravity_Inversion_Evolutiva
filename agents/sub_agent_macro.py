"""
Sub-agente B (Macro): Procesa el calendario económico y noticias de alto impacto.
Usa NLP vía DeepSeek para calcular un score de sentimiento agregado y emitir
una señal macro (BUY/SELL/HOLD) para EUR/USD.
"""

from __future__ import annotations

import json
from typing import Any

from agents.base_agent import BaseAgent
from data.macro_scraper import MacroSnapshot, fetch_macro_snapshot

_SYSTEM_PROMPT = """Eres el Sub-agente Macro de un sistema de trading evolutivo EUR/USD.
Tu rol es analizar eventos del calendario económico y titulares de noticias para determinar
el sesgo macroeconómico del mercado en el par EUR/USD.

Reglas de respuesta:
- Responde ÚNICAMENTE con un JSON válido, sin texto adicional.
- Formato exacto:
  {"recomendacion": "BUY"|"SELL"|"HOLD",
   "confianza": 0.0-1.0,
   "sentimiento_score": -1.0 a 1.0,
   "eventos_clave": ["evento1", "evento2"],
   "razon": "string breve"}
- sentimiento_score: positivo = bullish EUR, negativo = bearish EUR.
- Si no hay eventos de alto impacto, emite HOLD con confianza baja (0.3-0.4)."""


class SubAgentMacro(BaseAgent):
    role = "macro"
    system_prompt = _SYSTEM_PROMPT

    def _sesgo_tendencia(self, htf_trend: dict) -> tuple[str, float]:
        """
        Convierte la dirección HTF (1h) en un sesgo macro implícito.

        Cuando no hay eventos de alto impacto en el calendario, la tendencia
        de precio multi-día es la señal macro dominante. Este método la traduce
        a una recomendación con confianza moderada (máx 0.55), suficiente para
        confirmar al técnico pero no para abrir solo.

        El gen `peso_sesgo_tendencia` (0.20–0.65) controla la intensidad.
        """
        direccion = htf_trend.get("direccion", "NEUTRAL")
        peso = float(self.params.get("peso_sesgo_tendencia", 0.40))
        if direccion == "BULL":
            return "BUY",  round(min(0.55, peso), 4)
        if direccion == "BEAR":
            return "SELL", round(min(0.55, peso), 4)
        return "HOLD", 0.35

    def _build_prompt(self, snapshot: MacroSnapshot) -> str:
        eventos_alto = snapshot.eventos_alto_impacto()
        eventos_str = "\n".join(
            f"- [{e.moneda}] {e.titulo} | Impacto: {e.impacto} | "
            f"Actual: {e.actual or 'N/A'} | Previo: {e.previo or 'N/A'} | Est: {e.estimado or 'N/A'}"
            for e in eventos_alto[:8]
        ) or "Sin eventos de alto impacto disponibles."

        titulares_str = "\n".join(
            f"- {t}" for t in snapshot.titulares[:6]
        ) or "Sin titulares disponibles."

        ventana = self.params.get("ventana_noticias_horas", 4)

        return (
            f"CALENDARIO ECONÓMICO (próximas {ventana}h, alto impacto):\n{eventos_str}\n\n"
            f"TITULARES RECIENTES EUR/USD:\n{titulares_str}\n\n"
            f"Analiza el impacto neto sobre EUR/USD y emite tu señal en JSON."
        )

    def _fallback_score(self, snapshot: MacroSnapshot, htf_trend: dict | None = None) -> dict:
        """
        Score heurístico cuando el LLM no está disponible o la API falla.

        Sin eventos de alto impacto: usa el sesgo de tendencia HTF si está disponible,
        en lugar de retornar siempre HOLD plano.
        Con eventos de alto impacto: mantiene HOLD (incertidumbre prevalece sobre tendencia).
        """
        peso_alto = self.params.get("peso_noticias_alto", 0.60)
        alto_count = len(snapshot.eventos_alto_impacto())

        if alto_count == 0:
            if htf_trend:
                rec, conf = self._sesgo_tendencia(htf_trend)
                return {
                    "recomendacion":  rec,
                    "confianza":      conf,
                    "sentimiento_score": 0.0,
                    "eventos_clave":  [],
                    "razon": f"Sin eventos — sesgo HTF({htf_trend.get('direccion','NEUTRAL')})",
                }
            return {"recomendacion": "HOLD", "confianza": 0.35, "sentimiento_score": 0.0,
                    "eventos_clave": [], "razon": "Sin eventos de alto impacto"}

        # Eventos presentes → incertidumbre: HOLD
        confianza = min(0.55, peso_alto * 0.6)
        return {
            "recomendacion": "HOLD",
            "confianza": confianza,
            "sentimiento_score": 0.0,
            "eventos_clave": [e.titulo for e in snapshot.eventos_alto_impacto()[:3]],
            "razon": f"{alto_count} eventos de alto impacto detectados — incertidumbre elevada",
        }

    def analyze(
        self,
        snapshot: MacroSnapshot | None = None,
        htf_trend: dict | None = None,
    ) -> dict[str, Any]:
        """
        Analiza el entorno macro para EUR/USD.

        htf_trend: dict {"direccion", "ema_rapida", "ema_lenta"} del timeframe 1h.
                   Cuando no hay eventos de alto impacto, la tendencia multi-día
                   es la señal macro más relevante disponible.
        """
        if snapshot is None:
            ventana = int(self.params.get("ventana_noticias_horas", 4))
            snapshot = fetch_macro_snapshot(ventana)

        prompt = self._build_prompt(snapshot)
        alto_count = len(snapshot.eventos_alto_impacto())
        peso_macro = float(self.params.get("peso_total_macro", 0.40))

        try:
            raw = self.reason(prompt)
            result = json.loads(raw)

            rec = result.get("recomendacion", "HOLD")
            if rec not in ("BUY", "SELL", "HOLD"):
                rec = "HOLD"
            confianza = float(result.get("confianza", 0.4))
            sentimiento = float(result.get("sentimiento_score", 0.0))

            umbral_compra = float(self.params.get("umbral_sentimiento_compra", 0.65))
            umbral_venta  = float(self.params.get("umbral_sentimiento_venta",  0.35))

            sentimiento_norm = (sentimiento + 1) / 2
            if sentimiento_norm >= umbral_compra:
                rec, confianza = "BUY",  max(confianza, sentimiento_norm)
            elif sentimiento_norm <= umbral_venta:
                rec, confianza = "SELL", max(confianza, 1 - sentimiento_norm)

            # Si el LLM no tomó posición (HOLD con baja confianza) y no hay eventos
            # relevantes, el sesgo de tendencia HTF es la mejor señal macro disponible.
            if rec == "HOLD" and confianza < 0.50 and alto_count == 0 and htf_trend:
                rec, confianza = self._sesgo_tendencia(htf_trend)

            return {
                "agente_id":           self.agent_id,
                "recomendacion":       rec,
                "confianza":           round(min(confianza, 0.95), 4),
                "sentimiento_score":   round(sentimiento, 4),
                "peso_macro_aplicado": peso_macro,
                "eventos_clave":       result.get("eventos_clave", [])[:5],
                "total_eventos_alto":  alto_count,
                "total_titulares":     len(snapshot.titulares),
                "razon":               result.get("razon", ""),
                "htf_sesgo":           htf_trend.get("direccion", "N/A") if htf_trend else "N/A",
            }

        except Exception:
            fallback = self._fallback_score(snapshot, htf_trend=htf_trend)
            fallback["agente_id"]           = self.agent_id
            fallback["peso_macro_aplicado"] = peso_macro
            fallback["total_eventos_alto"]  = alto_count
            fallback["total_titulares"]     = len(snapshot.titulares)
            fallback["htf_sesgo"]           = htf_trend.get("direccion", "N/A") if htf_trend else "N/A"
            return fallback
