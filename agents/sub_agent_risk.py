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
# Techo de Stop Loss (Sesión 22 — coherencia intradía): un SL mayor produce un
# TP (R:R ≥ 1.5) inalcanzable antes del cierre forzoso EOD, dejando solo dos
# finales posibles (EOD o SL completo) y fitness sin señal. Aplica a TODAS las
# fuentes de SL (estructura OB/FVG y ATR) — el SL de 61 pips del 2026-06-12
# salió de la rama estructural, que solo validaba distancia mínima.
_MAX_SL_PIPS   = float(os.getenv("MAX_SL_PIPS", "35.0"))
_MAX_LEVERAGE  = 50.0    # techo de apalancamiento (nocional ≤ equity × 50)
_UNITS_PER_LOT = 1000.0  # unidades EUR por lote micro (referencia pip_value)

# ── Piso económico duro de R:R (auditoría forex 2026-07-31) ────────────────
# Con fricción de _FRICTION_PIPS por operación, un R:R bajo hace que el coste
# de entrar/salir sea una fracción demasiado grande del objetivo: a 12.4 pips
# de SL medio y R:R 2.0 (población legacy), la fricción representaba 5.5% del
# TP; con el R:R validado por la evolución offline (~3.8, campeones
# 2026-07-24_01/_02, holdout +0.50R a +0.71R) baja a 2.9%. Se aplica como PISO
# EN TIEMPO REAL — no solo como límite de mutación — para corregir de
# inmediato a los agentes YA vivos con genes legacy (R:R 1.5-2.0), sin esperar
# a que la selección natural los reemplace.
# 2026-08-10: subido 2.5 -> 3.5 para alinearlo con el perfil campeon validado.
# 2026-08-12: REVERTIDO a 2.2. El 3.5 fue un error de metodo con consecuencias
# medibles: en 24 operaciones (11-12 ago) NINGUNA llego siquiera a un tercio de
# su objetivo — maximo favorable medio 15.2% del TP, mejor caso 31.2% — y hubo
# cero take-profits. Con el stop clavado en el piso de 10 pips, un R:R de 3.5-4.0
# pone el objetivo en 34-40 pips, por encima del MAXIMO historico de casi todas
# las salidas ganadoras.
#
# CAUSA RAIZ DEL ERROR: el perfil campeon se valido en un backtest que NO cerraba
# por fin de dia (las posiciones corrian dias hasta tocar SL/TP), mientras que
# produccion las cierra a las 03:45 UTC (~14 h de vida maxima). Se valido bajo
# condiciones que produccion no puede reproducir. Ver el cierre EOD anadido al
# backtester en evolution/backtester.py (misma fecha).
#
# 2026-08-12 (segunda revision, mismo dia): 2.2 -> 2.8. La bajada a 2.2 fue una
# reaccion a 24 operaciones y se apoyaba en la hipotesis de que el cierre EOD
# explicaba la perdida. El experimento la REFUTO: sobre el mismo holdout de 6
# meses y con identico filtro de sesion, anadir el cierre EOD solo resta 0.069R
# (+0.435R -> +0.366R). No es la causa. Y bajo esas condiciones realistas el
# backtest prefiere R:R alto: 3.82 -> +0.366R, 2.7 -> +0.315R, 2.2 -> +0.185R.
#
# Por que 2.8 y no volver a 3.8: queda un sesgo conocido a favor del R:R alto en
# el backtester — NO modela el trailing stop, que en produccion cierra ganadores
# antes de llegar al objetivo (69 salidas por trailing en 90 dias, a 11.4 pips
# medios frente a 22.3 de los TP completos). El backtest deja correr al ganador
# hasta un TP lejano que en vivo rara vez se cobra, asi que su optimo esta
# inflado. 2.8 conserva el 86% del edge estimado (+0.315R en la curva medida)
# con objetivos de ~28 pips, dentro del percentil 90 real de las ganadoras
# (27.7 pips) en vez de los 38-40 que no se alcanzaron ni una vez.
MIN_RISK_REWARD_TARGET = float(os.getenv("MIN_RISK_REWARD_TARGET", "2.8"))

# ── Regla de peaje (auditoría forex 2026-07-31) ─────────────────────────────
# Red de seguridad adicional: si por cualquier vía el objetivo en pips queda
# por debajo de MIN_TARGET_TO_FRICTION_RATIO × fricción, la operación se
# rechaza (HOLD). Con el piso de R:R y el piso de SL (_MIN_SL_PIPS) ya activos
# el objetivo mínimo es 10 × 2.5 = 25 pips >> 10 × 1.4 = 14 pips, así que en
# la práctica esta regla no debería disparar — existe como defensa en
# profundidad, no como palanca principal.
MIN_TARGET_TO_FRICTION_RATIO = float(os.getenv("MIN_TARGET_TO_FRICTION_RATIO", "10.0"))
_FRICTION_PIPS = float(os.getenv("TRADE_FRICTION_PIPS", "1.4"))

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

        # Validar que el SL esté del lado correcto y a distancia válida
        if sl_precio is not None:
            wrong_side = (accion == "BUY" and sl_precio >= precio) or \
                         (accion == "SELL" and sl_precio <= precio)
            sl_dist_pips = abs(precio - sl_precio) * 10_000
            too_close  = sl_dist_pips < _MIN_SL_PIPS
            too_far    = sl_dist_pips > _MAX_SL_PIPS   # Sesión 22
            if wrong_side or too_close or too_far:
                sl_precio = None
                sl_fuente = "pct"

        # ── 2. SL basado en ATR (reemplaza fallback % fijo) ───────────────────
        if sl_precio is None:
            atr = float(ind.get("atr", 0.0))
            atr_factor = float(self.params_smc.get("atr_factor",
                               self.params.get("atr_factor", 1.5)))
            if atr > 0:
                dist = max(atr * atr_factor, _MIN_SL_PIPS * 0.0001)  # piso = _MIN_SL_PIPS
                dist = min(dist, _MAX_SL_PIPS * 0.0001)              # techo (Sesión 22)
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
        # Piso económico duro (auditoría 2026-07-31): el gen del agente puede
        # pedir menos, pero nunca se ejecuta un R:R por debajo del piso — se
        # aplica al valor efectivo, no solo al rango de mutación, para corregir
        # también a los agentes ya vivos con genes legacy.
        risk_reward = float(
            self.params_smc.get("risk_reward_target",
            self.params.get("risk_reward_target", 2.0))
        )
        risk_reward = max(risk_reward, MIN_RISK_REWARD_TARGET)
        tp_pips     = sl_pips * risk_reward

        # ── Regla de peaje (defensa en profundidad) ─────────────────────────
        # Si el objetivo no alcanza a cubrir la fricción con margen amplio, la
        # operación no tiene economía viable: se rechaza antes de calcular
        # nocional. Con los pisos de SL y R:R activos no debería dispararse en
        # la práctica; existe para blindar contra rutas de cálculo futuras que
        # no pasen por este piso de R:R.
        if tp_pips < _FRICTION_PIPS * MIN_TARGET_TO_FRICTION_RATIO:
            log.info(
                "[SubAgentRisk] Rechazada por regla de peaje: TP=%.1fpips < "
                "%.1fx friccion (%.1fpips) — HOLD preventivo.",
                tp_pips, MIN_TARGET_TO_FRICTION_RATIO, _FRICTION_PIPS,
            )
            return None, None, 0.0, sl_pips, "peaje", atr_valor

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

        # Trailing on/off (gen trailing_enabled, 2026-08-12). Con el gen en 0 se
        # propaga activación 0, que es lo que _apply_trailing_stop interpreta
        # como "trailing apagado" (`if configured_act <= 0: return`). Se apaga
        # aquí, en el origen, para que quede registrado en decision_riesgo y el
        # monitor lo respete sin lógica adicional.
        # Motivo: al modelar por fin el trailing en el backtester se midió que
        # CUESTA -0.189R sobre 6 meses de holdout (+0.319R sin él vs +0.131R
        # con él) — salta a mitad de camino del objetivo y una retracción normal
        # cierra la posición, convirtiendo ganadores en stops.
        trailing_on = int(self.params_smc.get("trailing_enabled", 1) or 0)
        trailing_activation_pips = (
            float(self.params_smc.get("trailing_activation_pips", 15.0))
            if trailing_on else 0.0
        )
        trailing_distance_pips = float(
            self.params_smc.get("trailing_distance_pips", 10.0)
        )

        accion_final = accion_prelim
        conf_final   = conf_prelim
        ind          = senal_tecnico.get("indicadores", {})
        rr           = max(
            float(self.params_smc.get("risk_reward_target",
                 self.params.get("risk_reward_target", 2.0))),
            MIN_RISK_REWARD_TARGET,
        )

        # La regla de peaje devuelve sl_fuente="peaje" y stop_loss=None: la
        # operación no tiene economía viable y se convierte en HOLD, aunque
        # accion_prelim fuera BUY/SELL.
        if sl_fuente == "peaje":
            accion_final = "HOLD"
            conf_final   = 0.30

        razonamiento = (
            f"Tecnico: {rec_tec} ({conf_tec:.2f}), Macro: {rec_mac} ({conf_mac:.2f}). "
            f"SL={stop_loss} ({sl_pips:.1f}pips, fuente={sl_fuente}), "
            f"TP={take_profit} (R:R {rr:.1f}x)"
        )

        from agents.base_agent import LLM_EXECUTION_ENABLED
        if LLM_EXECUTION_ENABLED and accion_final != "HOLD":
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
