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
import os
from datetime import datetime, timezone
from typing import Any

# Fricción de mercado (Fase 0 — realismo): costo round-trip en pips que agrupa
# spread + slippage. Se descuenta del P&L de toda operación BUY/SELL al cerrar.
# Sin esto el simulador es optimista y cualquier micro-edge parece rentable.
# Default 1.4 pips ≈ 0.8 spread retail EUR/USD + 0.6 slippage en stops de mercado.
_FRICTION_PIPS = float(os.getenv("TRADE_FRICTION_PIPS", "1.4"))

from agents.sub_agent_macro import SubAgentMacro
from agents.sub_agent_risk import RiskDecision, SubAgentRisk
from agents.sub_agent_technical import SubAgentTechnical
from data.alpha_vantage_client import TechnicalSignals
from data.macro_scraper import MacroSnapshot
from db.connection import get_conn, get_dict_cursor
from utils.sheets_logger import SheetsLogger

log = logging.getLogger(__name__)


class InvestorAgent:
    """
    Instancia de un Agente Inversionista identificado por su ID YYYY-MM-DD_NN.
    Coordina los tres sub-agentes en pipeline serial y persiste cada ciclo en DB.
    El cierre de posiciones (SL/TP/EOD) lo gestiona TradeMonitor externamente.
    """

    def __init__(self, agent_id: str, params: dict):
        self.agent_id   = agent_id
        self.params     = params
        self.generacion = str(params.get("generacion", ""))
        self.especie    = str(params.get("especie", "tendencia"))
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
                SELECT params_tecnicos, params_macro, params_riesgo, capital_actual,
                       generacion, especie
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
            "generacion":      str(row["generacion"]),
            "especie":         str(row["especie"] or "tendencia"),
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
        htf_trend: dict | None = None,
    ) -> dict[str, Any]:
        """
        Ejecuta un ciclo completo: Técnico → Macro → Riesgo → DB.
        Las posiciones abiertas (BUY/SELL) quedan registradas con precio de
        entrada real y serán monitoreadas por TradeMonitor para cierre por SL/TP.
        Si el agente ya tiene una posición abierta, el ciclo se omite.

        htf_trend: sesgo direccional del 1h, calculado una vez por ciclo en
                   trade_monitor y pasado a ambos sub-agentes que lo consumen.
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

        senal_tecnico = self.sub_technical.analyze(tech_signals, especie=self.especie)
        senal_macro   = self.sub_macro.analyze(macro_snapshot, htf_trend=htf_trend)
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
        op_id = None
        ts_entrada = datetime.now(timezone.utc)

        # Calcula pips_sl si la operación es BUY/SELL con SL válido y precio de entrada
        pips_sl: float | None = None
        if (
            decision.accion_final in ("BUY", "SELL")
            and decision.stop_loss is not None
            and precio_entrada is not None
            and precio_entrada > 0
        ):
            pips_sl = round(abs(float(precio_entrada) - float(decision.stop_loss)) * 10_000, 2)

        try:
            with get_conn() as conn:
                cur = conn.cursor()
                is_trade = decision.accion_final in ("BUY", "SELL")
                if is_trade:
                    # Atomic conditional INSERT: only if no open BUY/SELL exists for this agent.
                    # Prevents TOCTOU race conditions when two workflow runs overlap.
                    cur.execute(
                        """
                        INSERT INTO operaciones (
                            agente_id, timestamp_entrada, par, accion,
                            precio_entrada, capital_usado, pips_sl,
                            senal_tecnico, senal_macro, decision_riesgo, estado,
                            sl_dinamico, precio_extremo_favorable
                        )
                        SELECT %s, %s, 'EUR/USD', %s,
                               %s, %s, %s, %s, %s, %s,
                               'abierta',
                               %s, %s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM operaciones
                            WHERE agente_id = %s
                              AND estado = 'abierta'
                              AND accion IN ('BUY', 'SELL')
                        )
                        RETURNING id
                        """,
                        (
                            decision.agente_id,
                            ts_entrada,
                            decision.accion_final,
                            precio_entrada,
                            decision.capital_a_usar,
                            pips_sl,
                            json.dumps(decision.senal_tecnico),
                            json.dumps(decision.senal_macro),
                            json.dumps({
                                "accion_final":             decision.accion_final,
                                "confianza_final":          decision.confianza_final,
                                "confianza_tecnica":        decision.confianza_tecnica,
                                "confianza_macro":          decision.confianza_macro,
                                "stop_loss":                decision.stop_loss,
                                "take_profit":              decision.take_profit,
                                "razonamiento":             decision.razonamiento,
                                "sl_fuente":                decision.sl_fuente,
                                "atr_valor":                decision.atr_valor,
                                "trailing_activation_pips": decision.trailing_activation_pips,
                                "trailing_distance_pips":   decision.trailing_distance_pips,
                            }),
                            decision.stop_loss,
                            precio_entrada,
                            decision.agente_id,
                        ),
                    )
                    row = cur.fetchone()
                    if row is None:
                        log.warning(
                            "[InvestorAgent] %s ya tiene posición abierta — INSERT bloqueado atómicamente.",
                            self.agent_id,
                        )
                        return None
                    op_id = row[0]
                else:
                    # HOLD: insert as 'cancelada' unconditionally
                    cur.execute(
                        """
                        INSERT INTO operaciones (
                            agente_id, timestamp_entrada, par, accion,
                            precio_entrada, capital_usado, pips_sl,
                            senal_tecnico, senal_macro, decision_riesgo, estado,
                            sl_dinamico, precio_extremo_favorable
                        ) VALUES (
                            %s, %s, 'EUR/USD', %s,
                            %s, %s, %s, %s, %s, %s,
                            'cancelada',
                            %s, %s
                        ) RETURNING id
                        """,
                        (
                            decision.agente_id,
                            ts_entrada,
                            decision.accion_final,
                            precio_entrada,
                            0,
                            pips_sl,
                            json.dumps(decision.senal_tecnico),
                            json.dumps(decision.senal_macro),
                            json.dumps({
                                "accion_final":             decision.accion_final,
                                "confianza_final":          decision.confianza_final,
                                "confianza_tecnica":        decision.confianza_tecnica,
                                "confianza_macro":          decision.confianza_macro,
                                "stop_loss":                decision.stop_loss,
                                "take_profit":              decision.take_profit,
                                "razonamiento":             decision.razonamiento,
                                "sl_fuente":                decision.sl_fuente,
                                "atr_valor":                decision.atr_valor,
                                "trailing_activation_pips": decision.trailing_activation_pips,
                                "trailing_distance_pips":   decision.trailing_distance_pips,
                            }),
                            None,
                            None,
                        ),
                    )
                    op_id = cur.fetchone()[0]

                if decision.accion_final != "HOLD":
                    cur.execute(
                        "UPDATE agentes SET operaciones_total = operaciones_total + 1 WHERE id = %s",
                        (self.agent_id,),
                    )
            # DB committed — registrar en Sheets fuera de la transacción
            try:
                SheetsLogger().log_operation(
                    op_id=op_id,
                    decision={
                        "agente_id":         decision.agente_id,
                        "accion_final":      decision.accion_final,
                        "confianza_final":   decision.confianza_final,
                        "confianza_tecnica": decision.confianza_tecnica,
                        "confianza_macro":   decision.confianza_macro,
                        "stop_loss":         decision.stop_loss,
                        "take_profit":       decision.take_profit,
                        "razonamiento":      decision.razonamiento,
                        "senal_tecnico":     decision.senal_tecnico,
                        "senal_macro":       decision.senal_macro,
                    },
                    precio_entrada=precio_entrada,
                    timestamp_entrada=ts_entrada,
                    capital_usado=decision.capital_a_usar if decision.accion_final != "HOLD" else 0,
                    generacion=self.generacion,
                )
            except Exception as e:
                log.error("[InvestorAgent] Error registrando operación %s en Sheets: %s", op_id, e)
            return op_id
        except Exception as exc:
            # Captura explícita de violación de unicidad (dos workers simultáneos):
            # el índice parcial idx_unique_open_position_per_agent garantiza que
            # solo uno de los dos INSERTs concurrentes tenga éxito.
            try:
                import psycopg2.errors
                if isinstance(exc, psycopg2.errors.UniqueViolation):
                    log.warning(
                        "[InvestorAgent] %s — posición abierta detectada por índice único "
                        "(concurrencia). INSERT ignorado.",
                        self.agent_id,
                    )
                    return None
            except ImportError:
                pass
            log.error("[InvestorAgent] Error persistiendo operación: %s", exc)
            return None

    # ── Cierre de posición ────────────────────────────────────────────────────

    def close_operation(
        self,
        op_id: int,
        precio_salida: float,
        capital_disponible: float,
        timestamp_salida: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Cierra una operación: calcula P&L desde precios reales de mercado
        y actualiza el capital y ROI del agente en la base de datos.
        Llamado por TradeMonitor cuando el precio toca SL, TP, o al EOD.

        `timestamp_salida` (opcional, UTC-aware): permite registrar el
        instante real en que la vela tocó SL/TP cuando el cierre proviene
        del verificador intra-vela. Si se omite, se usa `datetime.now(UTC)`
        (comportamiento legacy para EOD/snapshot).
        """
        ts_salida = timestamp_salida or datetime.now(timezone.utc)
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            cur.execute(
                "SELECT precio_entrada, capital_usado, accion FROM operaciones WHERE id = %s",
                (op_id,),
            )
            row = cur.fetchone()

        if not row:
            return {"error": f"Operación {op_id} no encontrada"}

        precio_entrada_raw = row["precio_entrada"]
        capital_usado      = float(row["capital_usado"])
        accion             = row["accion"]

        # Asignar precio_entrada antes del if/else para que siempre esté definida
        # (incluso en la rama None) y no provoque NameError en el log posterior.
        precio_entrada = float(precio_entrada_raw) if precio_entrada_raw is not None else 0.0

        if precio_entrada_raw is None:
            # Sin precio de entrada registrado — cerrar con pnl=0 (breakeven)
            log.warning("[InvestorAgent] Op %d sin precio_entrada — cerrando con pnl=0.", op_id)
            pnl = 0.0
        else:
            if accion == "BUY":
                pnl = round((precio_salida - precio_entrada) / precio_entrada * capital_usado, 4)
            elif accion == "SELL":
                pnl = round((precio_entrada - precio_salida) / precio_entrada * capital_usado, 4)
            else:
                pnl = 0.0

            # Fricción de mercado (spread + slippage) round-trip sobre el nocional.
            # Se aplica a BUY/SELL ganadoras y perdedoras por igual: es el costo
            # ineludible de entrar y salir del mercado en condiciones reales.
            if accion in ("BUY", "SELL"):
                friccion = round(_FRICTION_PIPS * 0.0001 / precio_entrada * capital_usado, 4)
                pnl = round(pnl - friccion, 4)

        # pnl_pct = retorno sobre el capital del agente (cuánto movió la cuenta este trade)
        pnl_pct       = round(pnl / capital_disponible * 100, 4) if capital_disponible > 0 else 0.0
        nuevo_capital = round(capital_disponible + pnl, 4)
        roi_delta     = pnl_pct

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
                (ts_salida, precio_salida, pnl, pnl_pct, op_id),
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

        try:
            SheetsLogger().update_operation(
                op_id, precio_salida, pnl,
                timestamp_salida=ts_salida,
            )
        except Exception as e:
            log.error(f"[InvestorAgent] Error updating sheets (operation): {e}")

        # Actualizar capital y ROI del agente en la pestaña Agentes
        try:
            with get_conn() as conn:
                cur = get_dict_cursor(conn)
                cur.execute(
                    "SELECT roi_total, operaciones_total, operaciones_ganadoras FROM agentes WHERE id = %s",
                    (self.agent_id,),
                )
                ag = cur.fetchone()
            if ag:
                SheetsLogger().update_agent_live(
                    agent_id=self.agent_id,
                    capital=nuevo_capital,
                    roi=float(ag["roi_total"]),
                    ops=int(ag["operaciones_total"]),
                    ops_ganadoras=int(ag["operaciones_ganadoras"]),
                )
        except Exception as e:
            log.error("[InvestorAgent] Error updating agent live stats in sheets: %s", e)

        log.info(
            "[InvestorAgent] Op %d cerrada: accion=%s entrada=%.5f salida=%.5f pnl=%.4f",
            op_id, accion, precio_entrada, precio_salida, pnl,
        )
        return {"op_id": op_id, "pnl": pnl, "pnl_pct": pnl_pct, "nuevo_capital": nuevo_capital}
