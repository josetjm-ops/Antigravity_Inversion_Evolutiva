"""
Sub-agente C (Riesgo/Decisión): Orquestador final del pipeline.
Recibe las señales de los Sub-agentes A (Técnico) y B (Macro),
evalúa la gestión de riesgo y emite la decisión final: BUY, SELL o HOLD.
Calcula stop-loss, take-profit y tamaño de posición.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agents.base_agent import BaseAgent

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Eres el Sub-agente de Riesgo y Decisión Final de un sistema de trading evolutivo EUR/USD.
Recibes señales de dos analistas (Técnico y Macro) y debes tomar la decisión óptima de trading.

Reglas de respuesta:
- Responde ÚNICAMENTE con un JSON válido, sin texto adicional.
- Formato exacto:
  {"accion_final": "BUY"|"SELL"|"HOLD",
   "confianza_final": 0.0-1.0,
   "stop_loss": precio_float,
   "take_profit": precio_float,
   "capital_a_usar": monto_float,
   "razonamiento": "string explicando la decisión"}
- Si las señales están en conflicto (una BUY, otra SELL), emite HOLD salvo que una tenga confianza > 0.75.
- Siempre respeta los umbrales de riesgo máximo."""


@dataclass
class RiskDecision:
    agente_id: str
    accion_final: str
    confianza_final: float
    stop_loss: float | None
    take_profit: float | None
    capital_a_usar: float
    razonamiento: str
    senal_tecnico: dict
    senal_macro: dict
    confianza_tecnica: float
    confianza_macro: float


class SubAgentRisk(BaseAgent):
    role = "risk"
    system_prompt = _SYSTEM_PROMPT

    def _compute_levels(
        self,
        precio: float,
        accion: str,
        capital: float,
    ) -> tuple[float | None, float | None]:
        sl_pct = float(self.params.get("stop_loss_pct", 0.02))
        tp_pct = float(self.params.get("take_profit_pct", 0.04))
        if accion == "BUY":
            return round(precio * (1 - sl_pct), 5), round(precio * (1 + tp_pct), 5)
        if accion == "SELL":
            return round(precio * (1 + sl_pct), 5), round(precio * (1 - tp_pct), 5)
        return None, None

    def _blend_confidence(
        self,
        conf_tecnica: float,
        conf_macro: float,
        rec_tec: str,
        rec_mac: str,
    ) -> tuple[str, float]:
        peso_tec = float(self.params.get("peso_tecnico_vs_macro", 0.55))
        peso_mac = 1.0 - peso_tec

        # Si las señales concuerdan: promediar ponderado
        if rec_tec == rec_mac:
            conf = conf_tecnica * peso_tec + conf_macro * peso_mac
            return rec_tec, round(conf, 4)

        # HOLD de un agente = abstención, no conflicto
        if rec_mac == "HOLD" and rec_tec in ("BUY", "SELL"):
            return rec_tec, round(conf_tecnica, 4)
        if rec_tec == "HOLD" and rec_mac in ("BUY", "SELL"):
            return rec_mac, round(conf_macro, 4)

        # Conflicto real (BUY vs SELL): la señal más segura gana si supera 0.75
        conf_tec_w = conf_tecnica * peso_tec
        conf_mac_w = conf_macro * peso_mac
        if conf_tec_w > conf_mac_w and conf_tecnica > 0.75:
            return rec_tec, round(conf_tec_w, 4)
        if conf_mac_w > conf_tec_w and conf_macro > 0.75:
            return rec_mac, round(conf_mac_w, 4)

        return "HOLD", round(max(conf_tec_w, conf_mac_w), 4)

    def analyze(
        self,
        senal_tecnico: dict,
        senal_macro: dict,
        capital_disponible: float = 10.0,
    ) -> RiskDecision:
        rec_tec = senal_tecnico.get("recomendacion", "HOLD")
        rec_mac = senal_macro.get("recomendacion", "HOLD")
        conf_tec = float(senal_tecnico.get("confianza", 0.5))
        conf_mac = float(senal_macro.get("confianza", 0.5))
        precio_actual = float(senal_tecnico.get("indicadores", {}).get("precio_actual", 0))

        if precio_actual <= 0:
            log.warning("[SubAgentRisk] precio_actual=%s inválido — emitiendo HOLD preventivo.", precio_actual)
            return RiskDecision(
                agente_id=self.agent_id,
                accion_final="HOLD",
                confianza_final=0.30,
                stop_loss=None,
                take_profit=None,
                capital_a_usar=0.0,
                razonamiento="precio_actual inválido o cero — HOLD preventivo",
                senal_tecnico=senal_tecnico,
                senal_macro=senal_macro,
                confianza_tecnica=conf_tec,
                confianza_macro=conf_mac,
            )

        accion_prelim, conf_prelim = self._blend_confidence(conf_tec, conf_mac, rec_tec, rec_mac)

        umbral_min = float(self.params.get("umbral_confianza_minima", 0.50))
        capital_pct = float(self.params.get("capital_por_operacion_pct", 0.50))
        capital_uso = round(capital_disponible * capital_pct, 4)

        # Bloquear si la confianza no alcanza el umbral mínimo del agente
        if conf_prelim < umbral_min:
            accion_prelim = "HOLD"

        # Enriquecer con LLM para decisiones no triviales
        accion_final = accion_prelim
        conf_final = conf_prelim
        razonamiento = f"Señal técnica: {rec_tec} ({conf_tec:.2f}), Macro: {rec_mac} ({conf_mac:.2f})"
        stop_loss, take_profit = self._compute_levels(precio_actual, accion_final, capital_uso)

        if accion_final != "HOLD":
            prompt = (
                f"SEÑAL TÉCNICA: {rec_tec} (confianza={conf_tec:.2f})\n"
                f"Indicadores: RSI={senal_tecnico.get('indicadores',{}).get('rsi','N/A')}, "
                f"EMA_cross={senal_tecnico.get('indicadores',{}).get('ema_cross_alcista','N/A')}, "
                f"MACD_hist={senal_tecnico.get('indicadores',{}).get('macd_hist','N/A')}\n\n"
                f"SEÑAL MACRO: {rec_mac} (confianza={conf_mac:.2f})\n"
                f"Sentimiento={senal_macro.get('sentimiento_score','N/A')}, "
                f"Eventos clave: {senal_macro.get('eventos_clave',[])}\n\n"
                f"Precio EUR/USD: {precio_actual}\n"
                f"Capital disponible: ${capital_disponible:.2f}\n"
                f"Stop-loss calculado: {stop_loss}, Take-profit: {take_profit}\n\n"
                f"Señal combinada preliminar: {accion_prelim} (confianza={conf_prelim:.2f})\n"
                f"Confirma o ajusta. Responde solo JSON."
            )
            try:
                raw = self.reason(prompt)
                parsed = json.loads(raw)
                accion_final = parsed.get("accion_final", accion_final)
                conf_final = float(parsed.get("confianza_final", conf_final))
                razonamiento = parsed.get("razonamiento", razonamiento)
                if parsed.get("stop_loss"):
                    stop_loss = float(parsed["stop_loss"])
                if parsed.get("take_profit"):
                    take_profit = float(parsed["take_profit"])
                if parsed.get("capital_a_usar"):
                    capital_uso = float(parsed["capital_a_usar"])
            except Exception as e:
                log.warning("[SubAgentRisk] LLM no disponible: %s — usando heurística.", e)

        return RiskDecision(
            agente_id=self.agent_id,
            accion_final=accion_final,
            confianza_final=round(conf_final, 4),
            stop_loss=stop_loss,
            take_profit=take_profit,
            capital_a_usar=capital_uso,
            razonamiento=razonamiento,
            senal_tecnico=senal_tecnico,
            senal_macro=senal_macro,
            confianza_tecnica=conf_tec,
            confianza_macro=conf_mac,
        )
