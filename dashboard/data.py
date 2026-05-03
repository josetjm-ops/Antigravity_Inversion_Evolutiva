"""
Capa de datos del Command Center.
Todas las funciones usan st.cache_data(ttl=60) para no saturar Neon.
Decimal → float se aplica en _coerce() antes de devolver el DataFrame.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


# ── Conexión ─────────────────────────────────────────────────────────────────

def _db_url() -> str:
    try:
        return st.secrets["DATABASE_URL"]
    except Exception:
        url = os.getenv("DATABASE_URL", "")
        if not url:
            st.error("DATABASE_URL no configurada. Revisa `.streamlit/secrets.toml` o `.env`.")
            st.stop()
        return url


def _conn():
    return psycopg2.connect(_db_url())


# ── Normalización de tipos ────────────────────────────────────────────────────

def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte Decimal → float y normaliza fechas en todo el DataFrame."""
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().iloc[:1]
            if not sample.empty and isinstance(sample.iloc[0], Decimal):
                df[col] = df[col].apply(lambda v: float(v) if isinstance(v, Decimal) else v)
    return df


# ── Queries cacheadas ─────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def fetch_agents(
    estados: list[str] | None = None,
    gens: list[int] | None = None,
) -> pd.DataFrame:
    where, params = [], []
    if estados:
        where.append("estado = ANY(%s)"); params.append(estados)
    if gens:
        where.append("generacion = ANY(%s)"); params.append(gens)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT
            id, generacion, fecha_nacimiento, estado,
            capital_inicial, capital_actual,
            roi_total, operaciones_total, operaciones_ganadoras,
            padre_1_id, padre_2_id,
            fecha_eliminacion, razon_eliminacion,
            CASE WHEN operaciones_total > 0
                 THEN ROUND(operaciones_ganadoras::numeric / operaciones_total * 100, 2)
                 ELSE 0 END AS win_rate_pct,
            created_at
        FROM agentes
        {where_sql}
        ORDER BY generacion ASC, roi_total DESC
    """
    conn = _conn()
    cur  = conn.cursor()
    cur.execute(sql, params or None)
    rows = cur.fetchall()
    conn.close()

    cols = [
        "id", "generacion", "fecha_nacimiento", "estado",
        "capital_inicial", "capital_actual", "roi_total",
        "operaciones_total", "operaciones_ganadoras",
        "padre_1_id", "padre_2_id",
        "fecha_eliminacion", "razon_eliminacion",
        "win_rate_pct", "created_at",
    ]
    df = _coerce(pd.DataFrame(rows, columns=cols))

    for c in ["roi_total", "capital_actual", "capital_inicial", "win_rate_pct"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["operaciones_total"]    = df["operaciones_total"].fillna(0).astype(int)
    df["operaciones_ganadoras"] = df["operaciones_ganadoras"].fillna(0).astype(int)
    df["generacion"]           = df["generacion"].astype(int)
    df["fecha_nacimiento"]     = pd.to_datetime(df["fecha_nacimiento"])
    return df


@st.cache_data(ttl=60, show_spinner=False)
def fetch_judge_logs(limit: int = 40) -> pd.DataFrame:
    sql = """
        SELECT id, fecha, tipo_evento, agente_afectado_id,
               descripcion, razonamiento_llm, created_at
        FROM logs_juez
        ORDER BY created_at DESC
        LIMIT %s
    """
    conn = _conn()
    cur  = conn.cursor()
    cur.execute(sql, (limit,))
    rows = cur.fetchall()
    conn.close()

    cols = ["id", "fecha", "tipo_evento", "agente_afectado_id",
            "descripcion", "razonamiento_llm", "created_at"]
    df = _coerce(pd.DataFrame(rows, columns=cols))
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df


@st.cache_data(ttl=60, show_spinner=False)
def fetch_operations(limit: int = 100) -> pd.DataFrame:
    sql = """
        SELECT
            o.id, o.agente_id, o.timestamp_entrada, o.timestamp_salida,
            o.par, o.accion, o.precio_entrada, o.precio_salida,
            o.capital_usado, o.pnl, o.pnl_porcentaje, o.estado,
            a.generacion
        FROM operaciones o
        JOIN agentes a ON a.id = o.agente_id
        ORDER BY o.timestamp_entrada DESC
        LIMIT %s
    """
    conn = _conn()
    cur  = conn.cursor()
    cur.execute(sql, (limit,))
    rows = cur.fetchall()
    conn.close()

    cols = [
        "id", "agente_id", "timestamp_entrada", "timestamp_salida",
        "par", "accion", "precio_entrada", "precio_salida",
        "capital_usado", "pnl", "pnl_porcentaje", "estado", "generacion",
    ]
    df = _coerce(pd.DataFrame(rows, columns=cols))
    for c in ["precio_entrada", "precio_salida", "capital_usado", "pnl", "pnl_porcentaje"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def fetch_ranking_history() -> pd.DataFrame:
    sql = """
        SELECT rh.fecha, rh.agente_id, rh.posicion_ranking,
               rh.roi_diario, rh.roi_acumulado, rh.capital_fin_dia,
               rh.operaciones_dia, rh.evento, a.generacion
        FROM ranking_historico rh
        JOIN agentes a ON a.id = rh.agente_id
        ORDER BY rh.fecha ASC, rh.posicion_ranking ASC
    """
    conn = _conn()
    cur  = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()

    cols = ["fecha", "agente_id", "posicion_ranking", "roi_diario",
            "roi_acumulado", "capital_fin_dia", "operaciones_dia",
            "evento", "generacion"]
    df = _coerce(pd.DataFrame(rows, columns=cols))
    for c in ["roi_diario", "roi_acumulado", "capital_fin_dia"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df


@st.cache_data(ttl=30, show_spinner=False)
def fetch_system_status() -> dict:
    """Métricas de estado para el sidebar — TTL de 30 s."""
    try:
        conn = _conn()
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM agentes WHERE estado='activo'")
        n_active = cur.fetchone()[0]
        cur.execute("SELECT MAX(generacion) FROM agentes")
        max_gen = cur.fetchone()[0] or 1
        cur.execute("SELECT MAX(created_at) FROM logs_juez")
        last_judge = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM operaciones WHERE estado='abierta'")
        ops_open = cur.fetchone()[0]
        conn.close()
        return {
            "ok": True,
            "n_active": n_active,
            "max_gen": max_gen,
            "last_judge": last_judge,
            "ops_open": ops_open,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=60, show_spinner=False)
def fetch_available_generations() -> list[int]:
    try:
        conn = _conn()
        cur  = conn.cursor()
        cur.execute("SELECT DISTINCT generacion FROM agentes ORDER BY generacion")
        gens = [r[0] for r in cur.fetchall()]
        conn.close()
        return gens
    except Exception:
        return [1]
