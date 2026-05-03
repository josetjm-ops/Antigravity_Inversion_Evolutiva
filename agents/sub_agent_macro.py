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

    def _fallback_score(self, snapshot: MacroSnapshot) -> dict:
        """Score heurístico cuando el LLM no está disponible o la API falla."""
        peso_alto = self.params.get("peso_noticias_alto", 0.60)
        umbral_compra = self.params.get("umbral_sentimiento_compra", 0.65)
        umbral_venta = self.params.get("umbral_sentimiento_venta", 0.35)

        alto_count = len(snapshot.eventos_alto_impacto())
        if alto_count == 0:
            return {"recomendacion": "HOLD", "confianza": 0.35, "sentimiento_score": 0.0,
                    "eventos_clave": [], "razon": "Sin eventos de alto impacto"}

        # Heurística simple: más eventos = más incertidumbre = HOLD
        confianza = min(0.55, peso_alto * 0.6)
        return {
            "recomendacion": "HOLD",
            "confianza": confianza,
            "sentimiento_score": 0.0,
            "eventos_clave": [e.titulo for e in snapshot.eventos_alto_impacto()[:3]],
            "razon": f"{alto_count} eventos de alto impacto detectados — incertidumbre elevada",
        }

    def analyze(self, snapshot: MacroSnapshot | None = None) -> dict[str, Any]:
        if snapshot is None:
            ventana = int(self.params.get("ventana_noticias_horas", 4))
            snapshot = fetch_macro_snapshot(ventana)

        prompt = self._build_prompt(snapshot)

        try:
            raw = self.reason(prompt)
            result = json.loads(raw)

            # Validate and normalize fields
            rec = result.get("recomendacion", "HOLD")
            if rec not in ("BUY", "SELL", "HOLD"):
                rec = "HOLD"
            confianza = float(result.get("confianza", 0.4))
            sentimiento = float(result.get("sentimiento_score", 0.0))

            # Apply macro weight from agent params
            peso_macro = float(self.params.get("peso_total_macro", 0.40))
            umbral_compra = float(self.params.get("umbral_sentimiento_compra", 0.65))
            umbral_venta = float(self.params.get("umbral_sentimiento_venta", 0.35))

            # Override recommendation based on agent's own thresholds
            sentimiento_norm = (sentimiento + 1) / 2  # normalize -1..1 to 0..1
            if sentimiento_norm >= umbral_compra:
                rec, confianza = "BUY", max(confianza, sentimiento_norm)
            elif sentimiento_norm <= umbral_venta:
                rec, confianza = "SELL", max(confianza, 1 - sentimiento_norm)

            return {
                "agente_id": self.agent_id,
                "recomendacion": rec,
                "confianza": round(min(confianza, 0.95), 4),
                "sentimiento_score": round(sentimiento, 4),
                "peso_macro_aplicado": peso_macro,
                "eventos_clave": result.get("eventos_clave", [])[:5],
                "total_eventos_alto": len(snapshot.eventos_alto_impacto()),
                "total_titulares": len(snapshot.titulares),
                "razon": result.get("razon", ""),
            }

        except Exception:
            fallback = self._fallback_score(snapshot)
            fallback["agente_id"] = self.agent_id
            fallback["peso_macro_aplicado"] = float(self.params.get("peso_total_macro", 0.40))
            fallback["total_eventos_alto"] = len(snapshot.eventos_alto_impacto())
            fallback["total_titulares"] = len(snapshot.titulares)
            return fallback
