"""
Agente Inversionista: Coordina el pipeline serial A → B → C.
Persiste la operación resultante en PostgreSQL y actualiza las métricas del agente.

Pipeline:
  1. Sub-agente A (Técnico)  → senal_tecnico
  2. Sub-agente B (Macro)    → senal_macro
  3. Sub-agente C (Riesgo)   → decision_final
  4. Persistencia en DB      → tabla operaciones + agentes
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from agents.sub_agent_macro import SubAgentMacro
from agents.sub_agent_risk import RiskDecision, SubAgentRisk
from agents.sub_agent_technical import SubAgentTechnical
from data.alpha_vantage_client import TechnicalSignals
from data.macro_scraper import MacroSnapshot
from db.connection import get_session


class InvestorAgent:
    """
    Instancia de un Agente Inversionista identificado por su ID YYYY-MM-DD_NN.
    Coordina los tres sub-agentes en pipeline serial y persiste cada ciclo en DB.
    """

    def __init__(self, agent_id: str, params: dict):
        self.agent_id = agent_id
        self.params = params
        self.sub_technical = SubAgentTechnical(agent_id, params.get("params_tecnicos", {}))
        self.sub_macro = SubAgentMacro(agent_id, params.get("params_macro", {}))
        self.sub_risk = SubAgentRisk(agent_id, params.get("params_riesgo", {}))

    @classmethod
    def from_db(cls, agent_id: str) -> "InvestorAgent":
        """Carga un agente activo desde la base de datos."""
        with get_session() as session:
            row = session.execute(
                "SELECT params_tecnicos, params_macro, params_riesgo, capital_actual "
                "FROM agentes WHERE id = :id AND estado = 'activo'",
                {"id": agent_id},
            ).fetchone()
            if not row:
                raise ValueError(f"Agente {agent_id} no encontrado o no activo.")
            params = {
                "params_tecnicos": row[0],
                "params_macro": row[1],
                "params_riesgo": row[2],
                "capital_actual": float(row[3]),
            }
        return cls(agent_id, params)

    def run_cycle(
        self,
        tech_signals: TechnicalSignals | None = None,
        macro_snapshot: MacroSnapshot | None = None,
    ) -> dict[str, Any]:
        """
        Ejecuta un ciclo completo del pipeline:
        Técnico → Macro → Riesgo/Decisión → Persistencia
        """
        capital = float(self.params.get("capital_actual", 10.0))

        # ── PASO 1: Sub-agente Técnico ──────────────────────────────────────
        senal_tecnico = self.sub_technical.analyze(tech_signals)

        # ── PASO 2: Sub-agente Macro ────────────────────────────────────────
        senal_macro = self.sub_macro.analyze(macro_snapshot)

        # ── PASO 3: Sub-agente Riesgo/Decisión ─────────────────────────────
        decision: RiskDecision = self.sub_risk.analyze(
            senal_tecnico, senal_macro, capital
        )

        # ── PASO 4: Persistencia en DB ──────────────────────────────────────
        precio_entrada = senal_tecnico.get("indicadores", {}).get("precio_actual")
        op_id = self._persist_operation(decision, precio_entrada)

        resultado = {
            "agente_id": self.agent_id,
            "ciclo_timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": {
                "accion_final": decision.accion_final,
                "confianza_final": decision.confianza_final,
                "stop_loss": decision.stop_loss,
                "take_profit": decision.take_profit,
                "capital_a_usar": decision.capital_a_usar,
                "razonamiento": decision.razonamiento,
            },
            "senal_tecnico": senal_tecnico,
            "senal_macro": senal_macro,
            "operacion_id": op_id,
        }

        return resultado

    def _persist_operation(
        self, decision: RiskDecision, precio_entrada: float | None
    ) -> int | None:
        """Inserta la operación en la tabla `operaciones` y actualiza `agentes`."""
        try:
            with get_session() as session:
                # Insertar operación
                result = session.execute(
                    """
                    INSERT INTO operaciones (
                        agente_id, timestamp_entrada, par, accion,
                        precio_entrada, capital_usado,
                        senal_tecnico, senal_macro, decision_riesgo, estado
                    ) VALUES (
                        :agente_id, :ts, 'EUR/USD', :accion,
                        :precio_entrada, :capital_usado,
                        :senal_tecnico, :senal_macro, :decision_riesgo,
                        CASE WHEN :accion = 'HOLD' THEN 'cancelada' ELSE 'abierta' END
                    ) RETURNING id
                    """,
                    {
                        "agente_id": decision.agente_id,
                        "ts": datetime.now(timezone.utc),
                        "accion": decision.accion_final,
                        "precio_entrada": precio_entrada,
                        "capital_usado": decision.capital_a_usar if decision.accion_final != "HOLD" else 0,
                        "senal_tecnico": json.dumps(decision.senal_tecnico),
                        "senal_macro": json.dumps(decision.senal_macro),
                        "decision_riesgo": json.dumps({
                            "accion_final": decision.accion_final,
                            "confianza_final": decision.confianza_final,
                            "stop_loss": decision.stop_loss,
                            "take_profit": decision.take_profit,
                            "razonamiento": decision.razonamiento,
                        }),
                    },
                )
                op_id = result.fetchone()[0]

                # Actualizar contador de operaciones del agente
                if decision.accion_final != "HOLD":
                    session.execute(
                        "UPDATE agentes SET operaciones_total = operaciones_total + 1 WHERE id = :id",
                        {"id": self.agent_id},
                    )

                return op_id
        except Exception as e:
            print(f"[InvestorAgent] Error persistiendo operación: {e}")
            return None

    def close_operation(
        self, op_id: int, precio_salida: float, capital_disponible: float
    ) -> dict[str, Any]:
        """
        Cierra una operación abierta: calcula PnL y actualiza capital del agente.
        Llamado por el sistema de monitoreo cuando se alcanza SL o TP.
        """
        with get_session() as session:
            row = session.execute(
                "SELECT precio_entrada, capital_usado, accion FROM operaciones WHERE id = :id",
                {"id": op_id},
            ).fetchone()

            if not row or not row[0]:
                return {"error": "Operación no encontrada o sin precio de entrada"}

            precio_entrada, capital_usado, accion = float(row[0]), float(row[1]), row[2]

            if accion == "BUY":
                pnl = round((precio_salida - precio_entrada) / precio_entrada * capital_usado, 4)
            elif accion == "SELL":
                pnl = round((precio_entrada - precio_salida) / precio_entrada * capital_usado, 4)
            else:
                pnl = 0.0

            pnl_pct = round(pnl / capital_usado * 100, 4) if capital_usado > 0 else 0.0
            nuevo_capital = round(capital_disponible + pnl, 4)
            roi_delta = round(pnl / capital_disponible * 100, 4) if capital_disponible > 0 else 0.0

            session.execute(
                """
                UPDATE operaciones SET
                    timestamp_salida = :ts,
                    precio_salida = :ps,
                    pnl = :pnl,
                    pnl_porcentaje = :pnl_pct,
                    estado = 'cerrada'
                WHERE id = :id
                """,
                {"ts": datetime.now(timezone.utc), "ps": precio_salida,
                 "pnl": pnl, "pnl_pct": pnl_pct, "id": op_id},
            )

            session.execute(
                """
                UPDATE agentes SET
                    capital_actual = :capital,
                    roi_total = roi_total + :roi_delta,
                    operaciones_ganadoras = operaciones_ganadoras + CASE WHEN :pnl > 0 THEN 1 ELSE 0 END
                WHERE id = :id
                """,
                {"capital": nuevo_capital, "roi_delta": roi_delta,
                 "pnl": pnl, "id": self.agent_id},
            )

        return {"op_id": op_id, "pnl": pnl, "pnl_pct": pnl_pct, "nuevo_capital": nuevo_capital}
