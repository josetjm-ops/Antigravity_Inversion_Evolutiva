"""
Agente Inversionista: Coordina el pipeline serial A → B → C.
Persiste la operación resultante en PostgreSQL.

Pipeline:
  1. Sub-agente A (Técnico)  → senal_tecnico
  2. Sub-agente B (Macro)    → senal_macro
  3. Sub-agente C (Riesgo)   → decision_final
  4. Persistencia en DB      → tabla operaciones + agentes

El seguimiento de SL/TP y cierre de posiciones lo realiza TradeMonitor
usando precios reales de Yahoo Finance (broker simulado).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from agents.sub_agent_macro import SubAgentMacro
from agents.sub_agent_risk import RiskDecision, SubAgentRisk
from agents.sub_agent_technical import SubAgentTechnical
from data.alpha_vantage_client import TechnicalSignals
from data.macro_scraper import MacroSnapshot
from db.connection import get_conn, get_dict_cursor

log = logging.getLogger(__name__)


class InvestorAgent:
    """
    Instancia de un Agente Inversionista identificado por su ID YYYY-MM-DD_NN.
    Coordina los tres sub-agentes en pipeline serial y persiste cada ciclo en DB.
    El cierre de posiciones (SL/TP/EOD) lo gestiona TradeMonitor externamente.
    """

    def __init__(self, agent_id: str, params: dict):
        self.agent_id = agent_id
        self.params   = params
        self.sub_technical = SubAgentTechnical(
            agent_id,
            params.get("params_tecnicos", {}),
            params.get("params_smc", None),
        )
        self.sub_macro     = SubAgentMacro(agent_id, params.get("params_macro", {}))
        self.sub_risk      = SubAgentRisk(
            agent_id,
            params.get("params_riesgo", {}),
            params.get("params_smc", None),
        )

    @classmethod
    def from_db(cls, agent_id: str) -> "InvestorAgent":
        """Carga un agente activo desde la base de datos."""
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            cur.execute(
                """
                SELECT params_tecnicos, params_macro, params_riesgo, capital_actual
                FROM agentes
                WHERE id = %s AND estado = 'activo'
                """,
                (agent_id,),
            )
            row = cur.fetchone()
        if not row:
            raise ValueError(f"Agente {agent_id} no encontrado o no activo.")
        return cls(agent_id, {
            "params_tecnicos": row["params_tecnicos"],
            "params_macro":    row["params_macro"],
            "params_riesgo":   row["params_riesgo"],
            "capital_actual":  float(row["capital_actual"]),
        })

    # ── Verificación de posición abierta ─────────────────────────────────────

    def _has_open_position(self) -> bool:
        """True si el agente ya tiene una operación BUY/SELL abierta."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT 1 FROM operaciones
                WHERE agente_id = %s AND estado = 'abierta' AND accion IN ('BUY', 'SELL')
                LIMIT 1
                """,
                (self.agent_id,),
            )
            return cur.fetchone() is not None

    # ── Pipeline principal ────────────────────────────────────────────────────

    def run_cycle(
        self,
        tech_signals: TechnicalSignals | None = None,
        macro_snapshot: MacroSnapshot | None = None,
    ) -> dict[str, Any]:
        """
        Ejecuta un ciclo completo: Técnico → Macro → Riesgo → DB.
        Las posiciones abiertas (BUY/SELL) quedan registradas con precio de
        entrada real y serán monitoreadas por TradeMonitor para cierre por SL/TP.
        Si el agente ya tiene una posición abierta, el ciclo se omite.
        """
        if self._has_open_position():
            log.info("[InvestorAgent] %s ya tiene posición abierta — ciclo omitido.", self.agent_id)
            return {
                "agente_id": self.agent_id,
                "skipped":   True,
                "reason":    "open_position",
                "decision":  {"accion_final": "SKIP", "confianza_final": 0},
            }

        capital = float(self.params.get("capital_actual", 10.0))

        senal_tecnico = self.sub_technical.analyze(tech_signals)
        senal_macro   = self.sub_macro.analyze(macro_snapshot)
        decision: RiskDecision = self.sub_risk.analyze(senal_tecnico, senal_macro, capital)

        precio_entrada = senal_tecnico.get("indicadores", {}).get("precio_actual")
        op_id = self._persist_operation(decision, precio_entrada)

        return {
            "agente_id":       self.agent_id,
            "ciclo_timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": {
                "accion_final":    decision.accion_final,
                "confianza_final": decision.confianza_final,
                "stop_loss":       decision.stop_loss,
                "take_profit":     decision.take_profit,
                "capital_a_usar":  decision.capital_a_usar,
                "razonamiento":    decision.razonamiento,
            },
            "senal_tecnico": senal_tecnico,
            "senal_macro":   senal_macro,
            "operacion_id":  op_id,
        }

    # ── Persistencia en DB ────────────────────────────────────────────────────

    def _persist_operation(
        self, decision: RiskDecision, precio_entrada: float | None
    ) -> int | None:
        """Inserta la operación en `operaciones` y actualiza el contador del agente."""
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO operaciones (
                        agente_id, timestamp_entrada, par, accion,
                        precio_entrada, capital_usado,
                        senal_tecnico, senal_macro, decision_riesgo, estado
                    ) VALUES (
                        %s, %s, 'EUR/USD', %s,
                        %s, %s, %s, %s, %s,
                        CASE WHEN %s = 'HOLD' THEN 'cancelada' ELSE 'abierta' END
                    ) RETURNING id
                    """,
                    (
                        decision.agente_id,
                        datetime.now(timezone.utc),
                        decision.accion_final,
                        precio_entrada,
                        decision.capital_a_usar if decision.accion_final != "HOLD" else 0,
                        json.dumps(decision.senal_tecnico),
                        json.dumps(decision.senal_macro),
                        json.dumps({
                            "accion_final":    decision.accion_final,
                            "confianza_final": decision.confianza_final,
                            "stop_loss":       decision.stop_loss,
                            "take_profit":     decision.take_profit,
                            "razonamiento":    decision.razonamiento,
                        }),
                        decision.accion_final,
                    ),
                )
                op_id = cur.fetchone()[0]

                if decision.accion_final != "HOLD":
                    cur.execute(
                        "UPDATE agentes SET operaciones_total = operaciones_total + 1 WHERE id = %s",
                        (self.agent_id,),
                    )
                return op_id
        except Exception as exc:
            log.error("[InvestorAgent] Error persistiendo operación: %s", exc)
            return None

    # ── Cierre de posición ────────────────────────────────────────────────────

    def close_operation(
        self,
        op_id: int,
        precio_salida: float,
        capital_disponible: float,
    ) -> dict[str, Any]:
        """
        Cierra una operación: calcula P&L desde precios reales de mercado
        y actualiza el capital y ROI del agente en la base de datos.
        Llamado por TradeMonitor cuando el precio toca SL, TP, o al EOD.
        """
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            cur.execute(
                "SELECT precio_entrada, capital_usado, accion FROM operaciones WHERE id = %s",
                (op_id,),
            )
            row = cur.fetchone()

        if not row or not row["precio_entrada"]:
            return {"error": f"Operación {op_id} no encontrada o sin precio de entrada"}

        precio_entrada = float(row["precio_entrada"])
        capital_usado  = float(row["capital_usado"])
        accion         = row["accion"]

        if accion == "BUY":
            pnl = round((precio_salida - precio_entrada) / precio_entrada * capital_usado, 4)
        elif accion == "SELL":
            pnl = round((precio_entrada - precio_salida) / precio_entrada * capital_usado, 4)
        else:
            pnl = 0.0

        pnl_pct       = round(pnl / capital_usado * 100, 4) if capital_usado > 0 else 0.0
        nuevo_capital = round(capital_disponible + pnl, 4)
        roi_delta     = round(pnl / capital_disponible * 100, 4) if capital_disponible > 0 else 0.0

        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE operaciones SET
                    timestamp_salida = %s,
                    precio_salida    = %s,
                    pnl              = %s,
                    pnl_porcentaje   = %s,
                    estado           = 'cerrada'
                WHERE id = %s
                """,
                (datetime.now(timezone.utc), precio_salida, pnl, pnl_pct, op_id),
            )
            cur.execute(
                """
                UPDATE agentes SET
                    capital_actual        = %s,
                    roi_total             = roi_total + %s,
                    operaciones_ganadoras = operaciones_ganadoras
                                           + CASE WHEN %s > 0 THEN 1 ELSE 0 END
                WHERE id = %s
                """,
                (nuevo_capital, roi_delta, pnl, self.agent_id),
            )

        log.info(
            "[InvestorAgent] Op %d cerrada: accion=%s entrada=%.5f salida=%.5f pnl=%.4f",
            op_id, accion, precio_entrada, precio_salida, pnl,
        )
        return {"op_id": op_id, "pnl": pnl, "pnl_pct": pnl_pct, "nuevo_capital": nuevo_capital}
