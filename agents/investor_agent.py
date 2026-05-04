"""
Agente Inversionista: Coordina el pipeline serial A → B → C.
Persiste la operación resultante en PostgreSQL, coloca la orden en OANDA
y actualiza las métricas del agente cuando OANDA cierra el trade.

Pipeline:
  1. Sub-agente A (Técnico)  → senal_tecnico
  2. Sub-agente B (Macro)    → senal_macro
  3. Sub-agente C (Riesgo)   → decision_final
  4. Persistencia en DB      → tabla operaciones + agentes
  5. Ejecución en OANDA      → orden de mercado con SL/TP nativos
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
    Coordina los tres sub-agentes en pipeline serial, persiste cada ciclo en DB
    y ejecuta la orden resultante en OANDA Practice.
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

    # ── Pipeline principal ────────────────────────────────────────────────────

    def run_cycle(
        self,
        tech_signals: TechnicalSignals | None = None,
        macro_snapshot: MacroSnapshot | None = None,
    ) -> dict[str, Any]:
        """
        Ejecuta un ciclo completo:
        Técnico → Macro → Riesgo/Decisión → DB → OANDA
        """
        capital = float(self.params.get("capital_actual", 10.0))

        senal_tecnico = self.sub_technical.analyze(tech_signals)
        senal_macro   = self.sub_macro.analyze(macro_snapshot)
        decision: RiskDecision = self.sub_risk.analyze(senal_tecnico, senal_macro, capital)

        precio_entrada = senal_tecnico.get("indicadores", {}).get("precio_actual")
        op_id = self._persist_operation(decision, precio_entrada)

        resultado: dict[str, Any] = {
            "agente_id":        self.agent_id,
            "ciclo_timestamp":  datetime.now(timezone.utc).isoformat(),
            "decision": {
                "accion_final":    decision.accion_final,
                "confianza_final": decision.confianza_final,
                "stop_loss":       decision.stop_loss,
                "take_profit":     decision.take_profit,
                "capital_a_usar":  decision.capital_a_usar,
                "razonamiento":    decision.razonamiento,
            },
            "senal_tecnico":  senal_tecnico,
            "senal_macro":    senal_macro,
            "operacion_id":   op_id,
        }

        # ── Ejecución en OANDA ────────────────────────────────────────────────
        if decision.accion_final != "HOLD" and precio_entrada and op_id:
            self._place_oanda_order(decision, op_id, senal_tecnico)

        return resultado

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
                        %s, %s,
                        %s, %s, %s,
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

    # ── Integración OANDA ─────────────────────────────────────────────────────

    def _place_oanda_order(
        self, decision: RiskDecision, op_id: int, senal_tecnico: dict
    ) -> None:
        """Coloca la orden en OANDA y guarda el trade_id en la operación."""
        try:
            from data import oanda_client

            if oanda_client.has_open_trade(self.agent_id):
                log.info(
                    "[InvestorAgent] Agente %s ya tiene trade abierto en OANDA, skip.",
                    self.agent_id,
                )
                return

            rsi = senal_tecnico.get("indicadores", {}).get("rsi", "?")
            conf = decision.confianza_final
            comment = f"RSI={rsi}|conf={conf:.2f}"

            oanda_result = oanda_client.place_order(
                agent_id=self.agent_id,
                action=decision.accion_final,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                comment=comment,
            )

            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE operaciones
                    SET oanda_trade_id = %s,
                        oanda_units    = %s
                    WHERE id = %s
                    """,
                    (oanda_result["oanda_trade_id"], abs(oanda_result["units"]), op_id),
                )

            log.info(
                "[InvestorAgent] OANDA trade_id=%s registrado para op_id=%d",
                oanda_result["oanda_trade_id"], op_id,
            )

        except Exception as exc:
            log.warning("[InvestorAgent] No se pudo colocar orden OANDA: %s", exc)

    # ── Cierre desde OANDA (llamado por trade_monitor) ────────────────────────

    def close_operation_from_oanda(
        self,
        op_id: int,
        oanda_realized_pl: float,
        close_price: float,
        capital_disponible: float,
    ) -> dict[str, Any]:
        """
        Sincroniza el cierre de un trade (SL/TP tocado en OANDA) con nuestra DB.

        El P&L de OANDA (en USD reales sobre OANDA_UNITS_PER_TRADE EUR) se escala
        proporcionalmente al capital virtual que el agente tenía asignado al trade.
        Esto convierte el resultado real de OANDA en una variación del capital
        virtual del agente, manteniendo la evolución económicamente coherente.
        """
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            cur.execute(
                "SELECT capital_usado, oanda_units, precio_entrada FROM operaciones WHERE id = %s",
                (op_id,),
            )
            row = cur.fetchone()

        if not row:
            return {"error": f"Operación {op_id} no encontrada"}

        capital_usado   = float(row["capital_usado"] or 0)
        oanda_units     = int(row["oanda_units"] or 1000)
        precio_entrada  = float(row["precio_entrada"] or close_price)

        # Escalar P&L de OANDA al capital virtual del agente
        trade_value = oanda_units * precio_entrada
        if trade_value > 0 and capital_usado > 0:
            scaling = capital_usado / trade_value
            pnl = round(oanda_realized_pl * scaling, 4)
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
                    timestamp_salida  = %s,
                    precio_salida     = %s,
                    pnl               = %s,
                    pnl_porcentaje    = %s,
                    oanda_realized_pl = %s,
                    estado            = 'cerrada'
                WHERE id = %s
                """,
                (
                    datetime.now(timezone.utc),
                    close_price, pnl, pnl_pct,
                    oanda_realized_pl, op_id,
                ),
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
            "[InvestorAgent] Op %d cerrada: pnl=%.4f pnl_pct=%.4f%% capital=%.4f",
            op_id, pnl, pnl_pct, nuevo_capital,
        )
        return {
            "op_id":         op_id,
            "pnl":           pnl,
            "pnl_pct":       pnl_pct,
            "nuevo_capital": nuevo_capital,
        }

    # ── Cierre manual (fallback sin OANDA) ────────────────────────────────────

    def close_operation(
        self, op_id: int, precio_salida: float, capital_disponible: float
    ) -> dict[str, Any]:
        """
        Cierra una operación calculando el P&L desde precios de entrada/salida.
        Fallback para uso manual o cuando OANDA no está disponible.
        """
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            cur.execute(
                "SELECT precio_entrada, capital_usado, accion FROM operaciones WHERE id = %s",
                (op_id,),
            )
            row = cur.fetchone()

        if not row or not row["precio_entrada"]:
            return {"error": "Operación no encontrada o sin precio de entrada"}

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

        return {"op_id": op_id, "pnl": pnl, "pnl_pct": pnl_pct, "nuevo_capital": nuevo_capital}
