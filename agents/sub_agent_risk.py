"""
Sub-agente C (Riesgo/Decisión): Orquestador final del pipeline.
Recibe las señales de los Sub-agentes A (Técnico) y B (Macro),
evalúa la gestión de riesgo y emite la decisión final: BUY, SELL o HOLD.
Calcula stop-loss estructural (OB/FVG), take-profit por R:R y position sizing dinámico.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from agents.base_agent import BaseAgent

log = logging.getLogger(__name__)

# Hard limits inmutables — nunca se mutan genéticamente
_RISK_PCT_MIN  = 0.01    # mínimo 1% del equity en riesgo por operación
_RISK_PCT_MAX  = 0.02    # máximo 2% del equity en riesgo por operación
# Piso de Stop Loss (Fase 0 — realismo): un SL por debajo de este umbral queda
# dentro del ruido normal de las velas de 1m con que el monitor verifica los
# niveles, por lo que es estadísticamente imposible que sobreviva. Subido de
# 5→10 pips. No empeora el riesgo: position sizing escala inverso a sl_pips, así
# que un SL mayor produce un nocional menor con el mismo 1–2% de riesgo.
_MIN_SL_PIPS   = float(os.getenv("MIN_SL_PIPS", "10.0"))  # distancia mínima de SL válido
_MAX_LEVERAGE  = 50.0    # techo de apalancamiento (nocional ≤ equity × 50)
_UNITS_PER_LOT = 1000.0  # unidades EUR por lote micro (referencia pip_value)

_SYSTEM_PROMPT = """Eres el Sub-agente de Riesgo y Decisión Final de un sistema de trading evolutivo EUR/USD.
Recibes señales de dos analistas (Técnico y Macro) y debes tomar la decisión óptima de trading.

Reglas de respuesta:
- Responde ÚNICAMENTE con un JSON válido, sin texto adicional.
- Formato exacto:
  {"accion_final": "BUY"|"SELL"|"HOLD",
   "confianza_final": 0.0-1.0,
   "stop_loss": precio_float,
   "take_profit": precio_float,
   "razonamiento": "string explicando la decisión"}
- Si las señales están en conflicto (una BUY, otra SELL), emite HOLD salvo que una tenga confianza > 0.75.
- Siempre respeta los umbrales de riesgo máximo (1-2% del capital por operación).
- El tamaño de posición (nocional en USD) es calculado automáticamente por el sistema; no lo incluyas."""


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
    sl_fuente: str = "pct"
    atr_valor: float = 0.0
    trailing_activation_pips: float = 0.0
    trailing_distance_pips: float = 0.0


class SubAgentRisk(BaseAgent):
    role = "risk"
    system_prompt = _SYSTEM_PROMPT

    def __init__(self, agent_id: str, params: dict, params_smc: dict | None = None):
        super().__init__(agent_id, params)
        self.params_smc = params_smc or {}

    # ── Position sizing ───────────────────────────────────────────────────────

    def _dynamic_position_size(
        self, equity: float, sl_pips: float, risk_pct: float, precio: float
    ) -> float:
        """
        Position sizing dinámico. Retorna el NOCIONAL EN USD de la posición.

        Lógica:
          1. Calcula número de lotes (×1000 EUR) para que la pérdida máxima al SL
             sea exactamente equity × risk_pct (1–2% inmutable).
          2. Convierte a nocional USD = lotes × 1000 × precio.
          3. Aplica techo de apalancamiento: nocional ≤ equity × _MAX_LEVERAGE (50×).

        El nocional USD se almacena en capital_usado y es lo que multiplica el P&L
        porcentual en close_operation, produciendo dólares correctos.
        """
        risk_pct      = max(_RISK_PCT_MIN, min(_RISK_PCT_MAX, risk_pct))
        pip_value_usd = 0.0001 * _UNITS_PER_LOT          # $0.10 por pip por lote
        lotes         = (equity * risk_pct) / (sl_pips * pip_value_usd)
        nocional_usd  = lotes * _UNITS_PER_LOT * precio   # exposición en USD
        nocional_usd  = min(nocional_usd, equity * _MAX_LEVERAGE)
        return round(nocional_usd, 4)

    # ── Niveles SL/TP ─────────────────────────────────────────────────────────

    def _compute_levels(
        self,
        precio: float,
        accion: str,
        capital: float,
        senal_tecnico: dict | None = None,
    ) -> tuple[float | None, float | None, float, float, str, float]:
        """
        Calcula (stop_loss, take_profit, capital_uso, sl_pips, sl_fuente, atr_valor).

        Jerarquía de SL:
          1. OB activo no mitigado — nivel estructural más fuerte
          2. FVG activo no rellenado — nivel estructural secundario
          3. ATR × atr_factor — SL dinámico realista (reemplaza % fijo)
          4. Porcentaje fijo (stop_loss_pct) — fallback si ATR no disponible

        TP = SL × risk_reward_target (gen mutable, default 2.0).
        """
        if accion == "HOLD":
            return None, None, 0.0, 0.0, "hold", 0.0

        ind = (senal_tecnico or {}).get("indicadores", {})

        ob_activo    = bool(ind.get("ob_activo",    False))
        fvg_activo   = bool(ind.get("fvg_activo",   False))
        ob_nivel_inf  = float(ind.get("ob_nivel_inf",  0.0))
        ob_nivel_sup  = float(ind.get("ob_nivel_sup",  0.0))
        fvg_nivel_inf = float(ind.get("fvg_nivel_inf", 0.0))
        fvg_nivel_sup = float(ind.get("fvg_nivel_sup", 0.0))

        # ── 1. SL estructural ─────────────────────────────────────────────────
        sl_precio: float | None = None
        sl_fuente = "pct"
        atr_valor = 0.0

        if ob_activo:
            candidate = ob_nivel_inf if accion == "BUY" else ob_nivel_sup
            if candidate > 0:
                sl_precio = candidate
                sl_fuente = "OB"
        if sl_precio is None and fvg_activo:
            candidate = fvg_nivel_inf if accion == "BUY" else fvg_nivel_sup
            if candidate > 0:
                sl_precio = candidate
                sl_fuente = "FVG"

        # Validar que el SL esté del lado correcto y a distancia mínima
        if sl_precio is not None:
            wrong_side = (accion == "BUY" and sl_precio >= precio) or \
                         (accion == "SELL" and sl_precio <= precio)
            too_close  = abs(precio - sl_precio) * 10_000 < _MIN_SL_PIPS
            if wrong_side or too_close:
                sl_precio = None
                sl_fuente = "pct"

        # ── 2. SL basado en ATR (reemplaza fallback % fijo) ───────────────────
        if sl_precio is None:
            atr = float(ind.get("atr", 0.0))
            atr_factor = float(self.params_smc.get("atr_factor",
                               self.params.get("atr_factor", 1.5)))
            if atr > 0:
                dist = max(atr * atr_factor, _MIN_SL_PIPS * 0.0001)  # piso = _MIN_SL_PIPS
                dist = min(dist, 0.0050)                             # máx 50 pips
                sl_precio = (
                    round(precio - dist, 5) if accion == "BUY"
                    else round(precio + dist, 5)
                )
                sl_fuente = "ATR"
                atr_valor = atr
            else:
                # ── 3. SL porcentual (fallback legacy si ATR = 0) ─────────────
                sl_pct = float(self.params.get("stop_loss_pct", 0.02))
                sl_precio = (
                    round(precio * (1 - sl_pct), 5) if accion == "BUY"
                    else round(precio * (1 + sl_pct), 5)
                )
                sl_fuente = "pct"

        sl_pips = round(abs(precio - sl_precio) * 10_000, 2)

        # ── Take profit por R:R ────────────────────────────────────────────────
        risk_reward = float(
            self.params_smc.get("risk_reward_target",
            self.params.get("risk_reward_target", 2.0))
        )
        tp_pips     = sl_pips * risk_reward
        take_profit = (
            round(precio + tp_pips * 0.0001, 5) if accion == "BUY"
            else round(precio - tp_pips * 0.0001, 5)
        )

        # ── Position sizing dinámico ───────────────────────────────────────────
        risk_pct = float(
            self.params_smc.get("risk_pct_per_trade",
            self.params.get("risk_pct_per_trade", 0.015))
        )
        capital_uso = self._dynamic_position_size(capital, sl_pips, risk_pct, precio)

        log.debug(
            "[SubAgentRisk] SL=%s (fuente=%s, %.1fpips) TP=%s R:R=%.1f nocional=$%.2f",
            round(sl_precio, 5), sl_fuente, sl_pips,
            round(take_profit, 5), risk_reward, capital_uso,
        )

        return round(sl_precio, 5), take_profit, capital_uso, sl_pips, sl_fuente, atr_valor

    # ── Blend confidence ──────────────────────────────────────────────────────

    def _blend_confidence(
        self,
        conf_tecnica: float,
        conf_macro: float,
        rec_tec: str,
        rec_mac: str,
    ) -> tuple[str, float]:
        peso_tec = float(self.params.get("peso_tecnico_vs_macro", 0.55))
        peso_mac = 1.0 - peso_tec

        # Señales iguales: promediar ponderado
        if rec_tec == rec_mac:
            conf = conf_tecnica * peso_tec + conf_macro * peso_mac
            return rec_tec, round(conf, 4)

        # El técnico abre con su propia señal aunque el macro se abstenga.
        if rec_mac == "HOLD" and rec_tec in ("BUY", "SELL"):
            return rec_tec, round(conf_tecnica, 4)
        # El macro nunca abre por sí solo: sin confirmación técnica no hay entrada.
        if rec_tec == "HOLD" and rec_mac in ("BUY", "SELL"):
            return "HOLD", round(conf_macro, 4)

        # Conflicto real (BUY vs SELL): el técnico decide la dirección; el macro puede vetar a HOLD.
        conf_tec_w = conf_tecnica * peso_tec
        conf_mac_w = conf_macro * peso_mac
        if conf_tec_w > conf_mac_w and conf_tecnica > 0.75:
            return rec_tec, round(conf_tec_w, 4)
        # El macro no impone su dirección en conflicto — solo veta.
        return "HOLD", round(max(conf_tec_w, conf_mac_w), 4)

    # ── Análisis principal ────────────────────────────────────────────────────

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
        precio_actual = float(
            senal_tecnico.get("indicadores", {}).get("precio_actual", 0)
        )

        if precio_actual <= 0:
            log.warning(
                "[SubAgentRisk] precio_actual=%s invalido — HOLD preventivo.", precio_actual
            )
            return RiskDecision(
                agente_id=self.agent_id,
                accion_final="HOLD",
                confianza_final=0.30,
                stop_loss=None,
                take_profit=None,
                capital_a_usar=0.0,
                razonamiento="precio_actual invalido o cero — HOLD preventivo",
                senal_tecnico=senal_tecnico,
                senal_macro=senal_macro,
                confianza_tecnica=conf_tec,
                confianza_macro=conf_mac,
            )

        accion_prelim, conf_prelim = self._blend_confidence(
            conf_tec, conf_mac, rec_tec, rec_mac
        )

        umbral_min = float(self.params.get("umbral_confianza_minima", 0.50))
        if conf_prelim < umbral_min:
            accion_prelim = "HOLD"

        stop_loss, take_profit, capital_uso, sl_pips, sl_fuente, atr_valor = self._compute_levels(
            precio_actual, accion_prelim, capital_disponible, senal_tecnico
        )

        trailing_activation_pips = float(
            self.params_smc.get("trailing_activation_pips", 15.0)
        )
        trailing_distance_pips = float(
            self.params_smc.get("trailing_distance_pips", 10.0)
        )

        accion_final = accion_prelim
        conf_final   = conf_prelim
        ind          = senal_tecnico.get("indicadores", {})
        rr           = float(self.params_smc.get("risk_reward_target",
                             self.params.get("risk_reward_target", 2.0)))

        razonamiento = (
            f"Tecnico: {rec_tec} ({conf_tec:.2f}), Macro: {rec_mac} ({conf_mac:.2f}). "
            f"SL={stop_loss} ({sl_pips:.1f}pips, fuente={sl_fuente}), "
            f"TP={take_profit} (R:R {rr:.1f}x)"
        )

        if accion_final != "HOLD":
            prompt = (
                f"SEÑAL TÉCNICA: {rec_tec} (confianza={conf_tec:.2f})\n"
                f"RSI={ind.get('rsi','N/A')}, EMA_cross={ind.get('ema_cross_alcista','N/A')}, "
                f"MACD_hist={ind.get('macd_hist','N/A')}\n"
                f"FVG={ind.get('fvg_activo',False)} {ind.get('fvg_direccion','NONE')} "
                f"{ind.get('fvg_pips',0):.1f}pips | "
                f"OB={ind.get('ob_activo',False)} {ind.get('ob_direccion','NONE')}\n\n"
                f"SEÑAL MACRO: {rec_mac} (confianza={conf_mac:.2f})\n"
                f"Sentimiento={senal_macro.get('sentimiento_score','N/A')}, "
                f"Eventos: {senal_macro.get('eventos_clave',[])}\n\n"
                f"Precio EUR/USD: {precio_actual}\n"
                f"Capital disponible: ${capital_disponible:.2f}\n"
                f"SL ({sl_fuente}): {stop_loss} ({sl_pips:.1f} pips) | "
                f"TP: {take_profit} | Nocional USD: ${capital_uso:.2f}\n\n"
                f"Señal combinada: {accion_prelim} (conf={conf_prelim:.2f}). "
                f"Confirma o ajusta. Responde solo JSON."
            )
            try:
                raw    = self.reason(prompt)
                parsed = json.loads(raw)
                accion_final = parsed.get("accion_final", accion_final)
                conf_final   = float(parsed.get("confianza_final", conf_final))
                razonamiento = parsed.get("razonamiento", razonamiento)
                if parsed.get("stop_loss"):
                    stop_loss = float(parsed["stop_loss"])
                if parsed.get("take_profit"):
                    take_profit = float(parsed["take_profit"])
                # capital_uso no se overridea con el LLM: el sizer ya lo calculó correctamente
            except Exception as e:
                log.warning("[SubAgentRisk] LLM no disponible: %s — usando heuristica.", e)

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
            sl_fuente=sl_fuente,
            atr_valor=atr_valor,
            trailing_activation_pips=trailing_activation_pips,
            trailing_distance_pips=trailing_distance_pips,
        )
