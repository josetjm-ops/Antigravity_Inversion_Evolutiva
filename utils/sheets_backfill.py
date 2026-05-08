"""
Sincronización histórica de DB → Google Sheets.

Uso:
    python -m utils.sheets_backfill

Flujo:
    1. Limpia pestaña "Agentes" (conserva headers fila 1)
    2. Escribe todos los agentes de la DB con genes SMC y fitness
    3. Limpia pestaña "Operaciones" (conserva headers fila 1)
    4. Escribe todas las operaciones con indicadores técnicos extraídos del JSONB
       - Ops cerradas → P&G numérico (ya calculado)
       - Ops abiertas → fórmula GOOGLEFINANCE
"""
from __future__ import annotations

import json
import logging
import os
import sys

# Permitir ejecución directa desde la raíz del proyecto
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from dotenv import load_dotenv
load_dotenv()

from db.connection import get_conn, get_dict_cursor
from utils.sheets_logger import (
    SheetsLogger,
    _col_letter,
    _COL_OPS,
    _pnl_formula,
    _pnl_pct_formula,
    _safe_float,
    _to_bogota,
    _HEADERS_OPS,
    _HEADERS_AGENTS,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

_BATCH = 50


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clear_sheet(ws, headers: list[str]) -> None:
    """Borra todas las filas excepto la cabecera."""
    total = ws.row_count
    if total > 1:
        ws.delete_rows(2, total)
    # Re-escribir headers por si acaso
    end_col = _col_letter(len(headers))
    ws.update(f"A1:{end_col}1", [headers], value_input_option="USER_ENTERED")
    log.info("Pestaña '%s' limpiada (%d filas borradas).", ws.title, total - 1)


def _write_batches(ws, rows: list[list]) -> None:
    for i in range(0, len(rows), _BATCH):
        batch = rows[i : i + _BATCH]
        ws.append_rows(batch, value_input_option="USER_ENTERED")
        log.info("  Escritas filas %d–%d.", i + 1, i + len(batch))


# ── Agentes ────────────────────────────────────────────────────────────────────

def _fetch_agents() -> list[dict]:
    sql = """
        SELECT
            a.id, a.generacion, a.fecha_nacimiento,
            a.padre_1_id, a.padre_2_id,
            a.estado, a.fecha_eliminacion, a.razon_eliminacion,
            a.roi_total, a.operaciones_total, a.operaciones_ganadoras,
            a.capital_inicial, a.capital_actual,
            a.params_smc,
            COALESCE(rh_latest.fitness_score, 0) AS fitness_score
        FROM agentes a
        LEFT JOIN LATERAL (
            SELECT fitness_score FROM ranking_historico
            WHERE agente_id = a.id
            ORDER BY fecha DESC LIMIT 1
        ) rh_latest ON true
        ORDER BY a.generacion ASC, a.created_at ASC
    """
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(sql)
        return cur.fetchall()


def _agent_row(a: dict) -> list:
    smc     = a.get("params_smc") or {}
    ops_t   = int(a.get("operaciones_total", 0) or 0)
    ops_w   = int(a.get("operaciones_ganadoras", 0) or 0)
    wr      = round(ops_w / ops_t * 100, 2) if ops_t > 0 else 0.0
    tipo    = "Génesis" if not a.get("padre_1_id") else f"Mutante Gen-{a.get('generacion', '?')}"
    fecha_n = a.get("fecha_nacimiento")
    fecha_str = fecha_n.isoformat() if hasattr(fecha_n, "isoformat") else str(fecha_n or "")
    return [
        a.get("id", ""),
        a.get("generacion", ""),
        tipo,
        fecha_str,
        a.get("padre_1_id") or "",
        a.get("padre_2_id") or "",
        a.get("estado", "activo"),
        str(a.get("fecha_eliminacion") or ""),
        a.get("razon_eliminacion") or "",
        _safe_float(a.get("roi_total"), decimals=4),
        _safe_float(a.get("fitness_score"), decimals=6),
        wr,
        ops_t,
        _safe_float(a.get("capital_inicial", 10.0), decimals=4),
        _safe_float(a.get("capital_actual", 10.0), decimals=4),
        smc.get("fvg_min_pips", ""),
        smc.get("ob_impulse_pips", ""),
        smc.get("risk_reward_target", ""),
        smc.get("macro_quarantine_minutes", ""),
        smc.get("peso_fvg", ""),
        smc.get("peso_ob", ""),
    ]


# ── Operaciones ────────────────────────────────────────────────────────────────

def _fetch_operations() -> list[dict]:
    sql = """
        SELECT
            o.id, o.agente_id, o.timestamp_entrada, o.timestamp_salida,
            o.accion,
            o.precio_entrada::float  AS precio_entrada,
            o.precio_salida::float   AS precio_salida,
            o.capital_usado::float   AS capital_usado,
            o.pnl::float             AS pnl,
            o.pnl_porcentaje::float  AS pnl_porcentaje,
            o.estado,
            o.senal_tecnico,
            o.decision_riesgo,
            a.generacion
        FROM operaciones o
        JOIN agentes a ON a.id = o.agente_id
        ORDER BY o.timestamp_entrada ASC
    """
    with get_conn() as conn:
        cur = get_dict_cursor(conn)
        cur.execute(sql)
        return cur.fetchall()


def _op_row(op: dict, row_n: int) -> list:
    """Construye la fila de Operaciones para el backfill."""
    dr   = op.get("decision_riesgo") or {}
    st   = op.get("senal_tecnico") or {}
    ind  = st.get("indicadores", {}) if isinstance(st, dict) else {}
    if isinstance(dr, str):
        try:
            dr = json.loads(dr)
        except Exception:
            dr = {}
    if isinstance(st, str):
        try:
            st = json.loads(st)
            ind = st.get("indicadores", {})
        except Exception:
            ind = {}

    accion        = op.get("accion", "HOLD")
    estado        = op.get("estado", "cancelada")
    precio_ent    = op.get("precio_entrada")
    precio_sal    = op.get("precio_salida")
    capital_usado = op.get("capital_usado", 0)

    sl = dr.get("stop_loss")  or ""
    tp = dr.get("take_profit") or ""

    # Pips SL
    pips_sl = ""
    if sl and precio_ent:
        try:
            pips_sl = round(abs(float(precio_ent) - float(sl)) * 10000, 1)
        except Exception:
            pass

    # R:R
    rr = ""
    if sl and tp and precio_ent:
        try:
            sl_d = abs(float(precio_ent) - float(sl))
            tp_d = abs(float(tp) - float(precio_ent))
            rr = round(tp_d / sl_d, 2) if sl_d > 0 else ""
        except Exception:
            pass

    ts_ent_str = _to_bogota(op.get("timestamp_entrada"))
    ts_sal_str = _to_bogota(op.get("timestamp_salida"))

    # P&G: numérico para cerradas, fórmula GOOGLEFINANCE para abiertas
    if estado == "cerrada":
        pnl_val    = _safe_float(op.get("pnl"), decimals=4)
        pnl_pct_val = _safe_float(op.get("pnl_porcentaje"), decimals=4)
    elif estado == "cancelada":
        pnl_val    = 0
        pnl_pct_val = 0
    else:
        pnl_val    = _pnl_formula(row_n)
        pnl_pct_val = _pnl_pct_formula(row_n)

    confianza_tec = dr.get("confianza_tecnica") or ind.get("confianza_tecnica") or ""
    confianza_mac = dr.get("confianza_macro")   or ind.get("confianza_macro")   or ""

    return [
        op.get("id", ""),
        op.get("agente_id", ""),
        op.get("generacion", ""),
        ts_ent_str,
        accion,
        _safe_float(precio_ent, decimals=5) if precio_ent else "",
        _safe_float(sl, decimals=5) if sl else "",
        _safe_float(tp, decimals=5) if tp else "",
        pips_sl,
        rr,
        _safe_float(capital_usado, decimals=4),
        estado,
        ts_sal_str,
        _safe_float(precio_sal, decimals=5) if precio_sal else "",
        pnl_val,
        pnl_pct_val,
        _safe_float(dr.get("confianza_final"), decimals=4),
        confianza_tec,
        confianza_mac,
        _safe_float(ind.get("rsi"), decimals=2),
        str(ind.get("fvg_activo", False)),
        ind.get("fvg_direccion", "NONE"),
        _safe_float(ind.get("fvg_pips"), decimals=1),
        str(ind.get("ob_activo", False)),
        ind.get("ob_direccion", "NONE"),
        str(ind.get("range_spike", False)),
        dr.get("razonamiento") or "",
    ]


# ── Main ───────────────────────────────────────────────────────────────────────

def run_backfill() -> None:
    sl = SheetsLogger()
    if not sl.client:
        log.error("SheetsLogger no inicializado. Verifica GOOGLE_SHEET_ID y GOOGLE_CREDENTIALS_JSON.")
        sys.exit(1)

    # ── Agentes ──────────────────────────────────────────────────────────────
    log.info("=== BACKFILL AGENTES ===")
    agents = _fetch_agents()
    log.info("Agentes en DB: %d", len(agents))
    _clear_sheet(sl.ws_agents, _HEADERS_AGENTS)
    rows_agents = [_agent_row(a) for a in agents]
    _write_batches(sl.ws_agents, rows_agents)
    log.info("Agentes escritos: %d", len(rows_agents))

    # ── Operaciones ───────────────────────────────────────────────────────────
    log.info("=== BACKFILL OPERACIONES ===")
    ops = _fetch_operations()
    log.info("Operaciones en DB: %d", len(ops))
    _clear_sheet(sl.ws_ops, _HEADERS_OPS)
    # Las filas empiezan en 2 (fila 1 = headers)
    rows_ops = [_op_row(op, i + 2) for i, op in enumerate(ops)]
    _write_batches(sl.ws_ops, rows_ops)
    log.info("Operaciones escritas: %d", len(rows_ops))

    log.info("=== BACKFILL COMPLETADO ===")


if __name__ == "__main__":
    run_backfill()
