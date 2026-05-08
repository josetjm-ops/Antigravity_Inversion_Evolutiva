"""
Agente Juez: Orquesta el ciclo evolutivo diario y genera razonamiento
con DeepSeek sobre qué estrategias se descartan y qué se espera de las nuevas.

Flujo diario:
  1. Invoca EvolutionEngine.run() para ejecutar selección + mutación.
  2. Llama a DeepSeek con el contexto de rendimiento para obtener el razonamiento.
  3. Persiste los logs en la tabla logs_juez con justificaciones por agente.
  4. Retorna un dict de auditoría completo.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from agents.base_agent import BaseAgent
from db.connection import get_conn
from evolution.evolution_engine import EvolutionEngine, EvolutionResult, calc_fitness_scores

log = logging.getLogger("JudgeAgent")


class _SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

_JUDGE_SYSTEM_PROMPT = """Eres el Agente Juez de un sistema de trading evolutivo EUR/USD basado en algoritmos genéticos.
Tu rol es analizar el rendimiento de agentes de trading y emitir un veredicto razonado sobre:
1. Por qué los agentes eliminados tuvieron bajo rendimiento (analiza sus parámetros).
2. Qué mutaciones de los supervivientes tienen mayor potencial.
3. Qué condiciones de mercado favorecieron a los mejores agentes.

Responde en JSON con el siguiente formato:
{
  "veredicto_general": "string de 2-3 oraciones",
  "eliminados": [{"id": "YYYY-MM-DD_NN", "razon": "por qué falló"}],
  "nuevos_agentes": [{"id": "YYYY-MM-DD_NN", "expectativa": "qué se espera de esta mutación"}],
  "insight_mercado": "string sobre condiciones del mercado observadas",
  "recomendacion_parametros": "string sobre qué tipo de parámetros deberían favorecer las próximas generaciones"
}"""


class JudgeAgent(BaseAgent):
    role = "judge"
    system_prompt = _JUDGE_SYSTEM_PROMPT

    def __init__(self):
        super().__init__("JUDGE", {})
        self.today = date.today()

    # ── Fitness Calmar Ratio ──────────────────────────────────────────────────

    def _calcular_fitness_calmar(self, agente_ids: list[str]) -> dict[str, float]:
        """Calcula el Calmar Ratio Proxy para los agentes indicados."""
        with get_conn() as conn:
            return calc_fitness_scores(conn, agente_ids)

    # ── Construcción del contexto para DeepSeek ───────────────────────────────

    def _build_analysis_prompt(self, result: EvolutionResult) -> str:
        # Pre-calcular fitness para mostrar en el prompt
        all_ids = [a["id"] for a in result.survivors + result.eliminated]
        fitness_map = self._calcular_fitness_calmar(all_ids) if all_ids else {}

        def fmt_agent(a: dict) -> str:
            ops     = int(a.get("operaciones_total", 0))
            won     = int(a.get("operaciones_ganadoras", 0))
            wr      = f"{round(won/ops*100, 1)}%" if ops > 0 else "N/A"
            tec     = a.get("params_tecnicos", {})
            risk    = a.get("params_riesgo", {})
            smc     = a.get("params_smc", {})
            fitness = fitness_map.get(a["id"], a.get("fitness_score", 0))
            return (
                f"  ID={a['id']} | fitness={fitness:.4f} | ROI={a.get('roi_total', 0):.4f}% | "
                f"WinRate={wr} | Ops={ops} | Gen={a.get('generacion', '?')}\n"
                f"    Técnico: RSI={tec.get('rsi_periodo','?')}, "
                f"EMA={tec.get('ema_rapida','?')}/{tec.get('ema_lenta','?')}, "
                f"pesos RSI/EMA/MACD={tec.get('peso_rsi',0):.2f}/"
                f"{tec.get('peso_ema',0):.2f}/{tec.get('peso_macd',0):.2f}\n"
                f"    SMC: FVG_min={smc.get('fvg_min_pips','?')}pips, "
                f"R:R={smc.get('risk_reward_target','?')}, "
                f"quarantine={smc.get('macro_quarantine_minutes','?')}min, "
                f"peso_fvg={smc.get('peso_fvg',0):.2f}/peso_ob={smc.get('peso_ob',0):.2f}\n"
                f"    Riesgo: SL={risk.get('stop_loss_pct',0):.3f}, "
                f"conf_min={risk.get('umbral_confianza_minima',0):.2f}"
            )

        def fmt_new(a: dict) -> str:
            tec = a.get("params_tecnicos", {})
            smc = a.get("params_smc", {})
            return (
                f"  ID={a['id']} | Padres: {a.get('padre_1_id','?')} x {a.get('padre_2_id','?')}\n"
                f"    Técnico: RSI={tec.get('rsi_periodo','?')}, "
                f"EMA={tec.get('ema_rapida','?')}/{tec.get('ema_lenta','?')}, "
                f"pesos={tec.get('peso_rsi',0):.2f}/{tec.get('peso_ema',0):.2f}/{tec.get('peso_macd',0):.2f}\n"
                f"    SMC: FVG_min={smc.get('fvg_min_pips','?')}pips, "
                f"R:R={smc.get('risk_reward_target','?')}, "
                f"peso_fvg={smc.get('peso_fvg',0):.2f}/peso_ob={smc.get('peso_ob',0):.2f}"
            )

        survivors_str  = "\n".join(fmt_agent(a) for a in result.survivors)
        eliminated_str = "\n".join(fmt_agent(a) for a in result.eliminated)
        new_agents_str = "\n".join(fmt_new(a) for a in result.new_agents)

        return (
            f"FECHA DE EVALUACIÓN: {result.fecha}\n\n"
            f"AGENTES SUPERVIVIENTES (Top {len(result.survivors)} por Calmar Ratio):\n"
            f"{survivors_str or '  (ninguno)'}\n\n"
            f"AGENTES ELIMINADOS (Bottom {len(result.eliminated)} por fitness):\n"
            f"{eliminated_str or '  (ninguno)'}\n\n"
            f"NUEVOS AGENTES CREADOS ({len(result.new_agents)} mutaciones):\n"
            f"{new_agents_str or '  (ninguno)'}\n\n"
            f"Emite tu veredicto en JSON. "
            f"IDs de eliminados: {[a['id'] for a in result.eliminated]}.\n"
            f"IDs de nuevos: {[a['id'] for a in result.new_agents]}."
        )

    # ── Persistencia de logs ──────────────────────────────────────────────────

    def _log(
        self,
        conn,
        tipo: str,
        agente_id: str | None,
        descripcion: str,
        datos: dict | None = None,
        razonamiento: str | None = None,
    ) -> None:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO logs_juez (
                fecha, tipo_evento, agente_afectado_id,
                descripcion, datos_json, razonamiento_llm
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                self.today,
                tipo,
                agente_id,
                descripcion,
                json.dumps(datos, cls=_SafeEncoder) if datos else None,
                razonamiento,
            ),
        )

    def _persist_logs(
        self,
        result: EvolutionResult,
        llm_verdict: dict,
    ) -> None:
        veredicto_general = llm_verdict.get("veredicto_general", "")
        insight = llm_verdict.get("insight_mercado", "")
        rec_params = llm_verdict.get("recomendacion_parametros", "")

        # Mapa id → razon/expectativa del LLM
        llm_eliminados = {
            e["id"]: e.get("razon", "")
            for e in llm_verdict.get("eliminados", [])
        }
        llm_nuevos = {
            n["id"]: n.get("expectativa", "")
            for n in llm_verdict.get("nuevos_agentes", [])
        }

        with get_conn() as conn:
            # Log de evaluación diaria global
            self._log(
                conn,
                tipo="evaluacion_diaria",
                agente_id=None,
                descripcion=(
                    f"Ciclo evolutivo {self.today}: "
                    f"{len(result.survivors)} supervivientes, "
                    f"{len(result.eliminated)} eliminados, "
                    f"{len(result.new_agents)} nuevos agentes."
                ),
                datos={
                    "survivors":  [a["id"] for a in result.survivors],
                    "eliminated": [a["id"] for a in result.eliminated],
                    "new_agents": [a["id"] for a in result.new_agents],
                    "insight_mercado": insight,
                    "recomendacion_parametros": rec_params,
                },
                razonamiento=veredicto_general,
            )

            # Log por cada eliminado — incluye fitness_score del ciclo
            fitness_map = self._calcular_fitness_calmar([a["id"] for a in result.eliminated])
            for agent in result.eliminated:
                razon_llm   = llm_eliminados.get(agent["id"], "")
                fitness_val = fitness_map.get(agent["id"], agent.get("fitness_score", 0))
                self._log(
                    conn,
                    tipo="eliminacion",
                    agente_id=agent["id"],
                    descripcion=(
                        f"Agente {agent['id']} eliminado — "
                        f"fitness={fitness_val:.4f} ROI={agent.get('roi_total', 0):.4f}%"
                    ),
                    datos={
                        "roi_total":             agent.get("roi_total", 0),
                        "fitness_score":         fitness_val,
                        "operaciones_total":     agent.get("operaciones_total", 0),
                        "operaciones_ganadoras": agent.get("operaciones_ganadoras", 0),
                        "params_tecnicos":       agent.get("params_tecnicos", {}),
                        "params_riesgo":         agent.get("params_riesgo", {}),
                        "params_smc":            agent.get("params_smc", {}),
                    },
                    razonamiento=razon_llm or f"Bottom {len(result.eliminated)} por Calmar Ratio fitness.",
                )

            # Log de selección de padres + reproducción por cada nuevo agente
            for agent in result.new_agents:
                self._log(
                    conn,
                    tipo="seleccion_padres",
                    agente_id=agent["id"],
                    descripcion=(
                        f"Padres seleccionados: {agent['padre_1_id']} x {agent['padre_2_id']}"
                    ),
                    datos={
                        "padre_1_id": agent["padre_1_id"],
                        "padre_2_id": agent["padre_2_id"],
                        "generacion": agent["generacion"],
                    },
                )
                self._log(
                    conn,
                    tipo="nuevo_agente",
                    agente_id=agent["id"],
                    descripcion=f"Nuevo agente creado por mutación — Generación {agent['generacion']}",
                    datos={
                        "params_tecnicos": agent["params_tecnicos"],
                        "params_macro":    agent["params_macro"],
                        "params_riesgo":   agent["params_riesgo"],
                    },
                    razonamiento=llm_nuevos.get(agent["id"], "Mutación gaussiana de padres supervivientes."),
                )

    # ── Ciclo principal del Juez ──────────────────────────────────────────────

    def run_daily_cycle(self) -> dict[str, Any]:
        """
        Punto de entrada del cron diario.
        Retorna un dict de auditoría completo para logging externo.
        """
        started_at = datetime.now(timezone.utc)
        log.info("Iniciando ciclo evolutivo %s", self.today)

        # 1. Ejecutar el motor evolutivo
        engine = EvolutionEngine(self.today)
        result = engine.run()

        if result.errors:
            log.error("Errores en ciclo: %s", result.errors)
            return {"status": "error", "errors": result.errors, "fecha": str(self.today)}

        log.info(
            "Supervivientes: %d | Eliminados: %d | Nuevos: %d",
            len(result.survivors), len(result.eliminated), len(result.new_agents),
        )

        # 2. Obtener razonamiento de DeepSeek
        llm_verdict: dict = {}
        if result.eliminated or result.new_agents:
            prompt = self._build_analysis_prompt(result)
            try:
                raw = self.reason(prompt)
                # Extrae el bloque JSON aunque DeepSeek lo envuelva en ```json ... ```
                start = raw.find("{")
                end   = raw.rfind("}")
                clean = raw[start : end + 1] if start != -1 else raw.strip()
                llm_verdict = json.loads(clean)
                log.info("Razonamiento LLM obtenido.")
            except Exception as e:
                log.warning("LLM no disponible, usando fallback: %s", e)
                llm_verdict = self._fallback_verdict(result)

        # 3. Persistir logs
        self._persist_logs(result, llm_verdict)

        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        summary = {
            "status":       "success",
            "fecha":        str(self.today),
            "survivors":    [a["id"] for a in result.survivors],
            "eliminated":   [a["id"] for a in result.eliminated],
            "new_agents":   [a["id"] for a in result.new_agents],
            "llm_verdict":  llm_verdict,
            "elapsed_sec":  round(elapsed, 2),
        }
        log.info("Ciclo completado en %.2fs.", elapsed)
        return summary

    # ── Fallback sin LLM ──────────────────────────────────────────────────────

    def _fallback_verdict(self, result: EvolutionResult) -> dict:
        fitness_map = self._calcular_fitness_calmar(
            [a["id"] for a in result.eliminated + result.survivors]
        )
        return {
            "veredicto_general": (
                f"Ciclo {self.today}: {len(result.eliminated)} agentes eliminados por Calmar Ratio inferior. "
                f"{len(result.new_agents)} nuevos agentes creados mediante mutación gaussiana."
            ),
            "eliminados": [
                {
                    "id": a["id"],
                    "razon": (
                        f"fitness={fitness_map.get(a['id'], 0):.4f} "
                        f"ROI={a.get('roi_total', 0):.4f}% — bajo rendimiento ajustado al riesgo"
                    ),
                }
                for a in result.eliminated
            ],
            "nuevos_agentes": [
                {
                    "id": a["id"],
                    "expectativa": (
                        f"Hereda de {a['padre_1_id']} y {a['padre_2_id']} con variación estocástica "
                        f"en genes SMC (FVG, OB, R:R, quarantine)"
                    ),
                }
                for a in result.new_agents
            ],
            "insight_mercado": "Análisis LLM no disponible en este ciclo.",
            "recomendacion_parametros": (
                "Priorizar agentes con Calmar Ratio alto: ROI sostenido con drawdown controlado. "
                "Explorar variaciones en risk_reward_target y fvg_min_pips."
            ),
        }
