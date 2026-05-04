"""
INVERSIÓN EVOLUTIVA — Command Center
Streamlit dashboard: dark luxury theme · gold / emerald palette.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

try:
    from zoneinfo import ZoneInfo
    _BOGOTA_TZ = ZoneInfo("America/Bogota")
except Exception:
    from datetime import timedelta
    _BOGOTA_TZ = timezone(timedelta(hours=-5))

# Asegura que el directorio raíz del proyecto esté en sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dashboard import data as D
from dashboard import charts as C

# ═══════════════════════════════════════════════════════════════════════════════
# PALETA Y CONSTANTES
# ═══════════════════════════════════════════════════════════════════════════════
GOLD    = "#d4af37"
EMERALD = "#00c878"
RED     = "#e05252"
AMBER   = "#f59e0b"
BG      = "#07070f"
CARD    = "#10101a"
CARD2   = "#14141f"
BORDER  = "#22223a"
TEXT    = "#e2e2e2"
DIM     = "#6a6a8a"

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG — debe ser la primera llamada a Streamlit
# ═══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="INVERSIÓN EVOLUTIVA · Command Center",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CSS GLOBAL
# ═══════════════════════════════════════════════════════════════════════════════
def _css() -> None:
    st.markdown(f"""
    <style>
      /* ── Fondo y texto base ───────────────────────────────── */
      .stApp,
      [data-testid="stAppViewContainer"],
      [data-testid="stMain"] {{
          background-color: {BG};
          color: {TEXT};
      }}
      [data-testid="stSidebar"] {{
          background-color: {CARD};
          border-right: 1px solid {BORDER};
      }}
      [data-testid="stHeader"]  {{ display: none; }}
      [data-testid="stToolbar"] {{ display: none; }}

      /* ── Métricas ─────────────────────────────────────────── */
      [data-testid="stMetric"] {{
          background: {CARD};
          border: 1px solid {BORDER};
          border-radius: 10px;
          padding: 18px 22px 14px;
      }}
      [data-testid="stMetricLabel"] p {{
          color: {DIM} !important;
          font-size: 10px !important;
          text-transform: uppercase;
          letter-spacing: 1.4px;
      }}
      [data-testid="stMetricValue"] {{
          color: {TEXT} !important;
          font-size: 26px !important;
          font-weight: 700;
      }}

      /* ── Tabs ─────────────────────────────────────────────── */
      [data-testid="stTabs"] [role="tablist"] {{
          border-bottom: 1px solid {BORDER};
          gap: 0;
      }}
      [data-testid="stTabs"] [role="tab"] {{
          color: {DIM};
          border: none;
          background: transparent;
          font-size: 12px;
          letter-spacing: 0.6px;
          padding: 10px 22px;
          border-radius: 0;
      }}
      [data-testid="stTabs"] [role="tab"]:hover {{
          color: {TEXT};
          background: rgba(255,255,255,0.03);
      }}
      [data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
          color: {GOLD} !important;
          border-bottom: 2px solid {GOLD} !important;
          font-weight: 600;
      }}

      /* ── Botones ──────────────────────────────────────────── */
      [data-testid="baseButton-primary"] {{
          background: {GOLD} !important;
          color: #000 !important;
          border: none !important;
          font-weight: 700 !important;
          letter-spacing: 0.5px;
      }}
      [data-testid="baseButton-primary"]:hover {{
          background: #c9a430 !important;
      }}
      [data-testid="baseButton-secondary"] {{
          background: transparent !important;
          border: 1px solid {BORDER} !important;
          color: {TEXT} !important;
      }}

      /* ── DataFrames ───────────────────────────────────────── */
      [data-testid="stDataFrameContainer"] {{
          border: 1px solid {BORDER};
          border-radius: 8px;
          overflow: hidden;
      }}
      .stDataFrame thead tr th {{
          background: {CARD2} !important;
          color: {DIM} !important;
          font-size: 10px;
          text-transform: uppercase;
          letter-spacing: 0.8px;
      }}

      /* ── Selectbox / Multiselect ──────────────────────────── */
      [data-baseweb="select"] {{
          background-color: {CARD2} !important;
          border-color: {BORDER} !important;
      }}
      [data-baseweb="select"] * {{ color: {TEXT} !important; }}

      /* ── Divider ──────────────────────────────────────────── */
      hr {{ border-color: {BORDER}; margin: 20px 0; }}

      /* ── Mobile responsive ───────────────────────────────── */
      @media screen and (max-width: 768px) {{
        /* Columnas apiladas verticalmente en móvil */
        [data-testid="stHorizontalBlock"] {{
          flex-wrap: wrap !important;
        }}
        [data-testid="column"] {{
          min-width: 45% !important;
          flex: 1 1 45% !important;
        }}
        /* Métricas KPI más compactas */
        [data-testid="stMetricValue"] {{
          font-size: 18px !important;
        }}
        [data-testid="stMetricLabel"] p {{
          font-size: 8px !important;
          letter-spacing: 0.8px !important;
        }}
        [data-testid="stMetric"] {{
          padding: 10px 12px 8px !important;
        }}
        /* Tabs más compactos */
        [data-testid="stTabs"] [role="tab"] {{
          font-size: 10px !important;
          padding: 8px 10px !important;
          letter-spacing: 0px !important;
        }}
        /* Reducir padding general */
        .main .block-container {{
          padding-left: 0.8rem !important;
          padding-right: 0.8rem !important;
          padding-top: 0.5rem !important;
        }}
        /* Cards más compactas */
        .ie-card {{ padding: 12px 14px !important; }}
        /* Sidebar botón full width */
        [data-testid="stSidebar"] .stButton button {{
          width: 100% !important;
        }}
      }}

      @media screen and (max-width: 480px) {{
        /* Teléfono vertical: columnas apiladas al 100% */
        [data-testid="column"] {{
          min-width: 100% !important;
          flex: 1 1 100% !important;
        }}
        /* Header más pequeño */
        [data-testid="stTabs"] [role="tab"] {{
          font-size: 9px !important;
          padding: 6px 8px !important;
        }}
        [data-testid="stMetricValue"] {{
          font-size: 16px !important;
        }}
      }}

      /* ── Clases utilitarias ───────────────────────────────── */
      .ie-label {{
          font-size: 9px;
          font-weight: 700;
          letter-spacing: 2px;
          text-transform: uppercase;
          color: {GOLD};
          margin-bottom: 10px;
          display: block;
      }}
      .ie-card {{
          background: {CARD};
          border: 1px solid {BORDER};
          border-radius: 10px;
          padding: 18px 22px;
          margin-bottom: 14px;
      }}
      .ie-card-gold    {{ border-left: 3px solid {GOLD}; }}
      .ie-card-emerald {{ border-left: 3px solid {EMERALD}; }}
      .ie-card-red     {{ border-left: 3px solid {RED}; }}
      .ie-card-amber   {{ border-left: 3px solid {AMBER}; }}

      /* Badges del Juez */
      .jbadge {{
          display: inline-block;
          padding: 2px 9px;
          border-radius: 20px;
          font-size: 9px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.8px;
      }}
      .jb-eval {{ background:#1a3a5c; color:#6ab0f5; }}
      .jb-elim {{ background:#3a1414; color:#f07070; }}
      .jb-new  {{ background:#143a22; color:#60d888; }}
      .jb-mut  {{ background:#2a2614; color:{GOLD}; }}
      .jb-par  {{ background:#24142a; color:#c080f0; }}

      .jlog {{
          background: {CARD2};
          border: 1px solid {BORDER};
          border-left: 3px solid {GOLD};
          border-radius: 8px;
          padding: 14px 18px;
          margin-bottom: 10px;
      }}
      .jlog-head {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 7px;
      }}
    </style>
    """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════════════════
def _header() -> None:
    left, right = st.columns([5, 2])
    with left:
        st.markdown(f"""
        <div style="padding:16px 0 20px 0; border-bottom:1px solid {BORDER}; margin-bottom:22px;">
          <div style="font-size:20px;font-weight:800;letter-spacing:3px;color:{GOLD};">
            ⚡&nbsp; INVERSIÓN EVOLUTIVA
          </div>
          <div style="font-size:10px;color:{DIM};letter-spacing:3.5px;
                      text-transform:uppercase;margin-top:4px;">
            Command Center · EUR/USD · Algorithmic Evolution
          </div>
        </div>
        """, unsafe_allow_html=True)
    with right:
        now_bog = datetime.now(_BOGOTA_TZ)
        st.markdown(f"""
        <div style="padding:20px 0 0 0;text-align:right;
                    font-size:10px;color:{DIM};font-family:monospace;">
          🕐 {now_bog.strftime('%Y-%m-%d %H:%M')} <span style="color:{GOLD};">Bogotá</span>
        </div>
        """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
def _sidebar() -> tuple[list[str], list[int]]:
    with st.sidebar:
        st.markdown(f"""
        <div style="padding:14px 0 10px; border-bottom:1px solid {BORDER}; margin-bottom:16px;">
          <span style="font-size:9px;letter-spacing:2px;color:{GOLD};text-transform:uppercase;">
            Filtros &amp; Control
          </span>
        </div>
        """, unsafe_allow_html=True)

        # Estado
        st.markdown(f'<span style="font-size:9px;color:{DIM};letter-spacing:1px;text-transform:uppercase;">Estado</span>', unsafe_allow_html=True)
        estados = st.multiselect(
            "Estado", options=["activo", "eliminado"],
            default=["activo"], label_visibility="collapsed",
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # Generación
        st.markdown(f'<span style="font-size:9px;color:{DIM};letter-spacing:1px;text-transform:uppercase;">Generación</span>', unsafe_allow_html=True)
        avail_gens = D.fetch_available_generations()
        gens = st.multiselect(
            "Gen", options=avail_gens,
            default=avail_gens, label_visibility="collapsed",
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # Número de logs del Juez
        st.markdown(f'<span style="font-size:9px;color:{DIM};letter-spacing:1px;text-transform:uppercase;">Logs del Juez</span>', unsafe_allow_html=True)
        n_logs = st.slider("Logs", 5, 80, 30, label_visibility="collapsed")

        st.divider()

        # Refresh
        if st.button("⟳  Actualizar", use_container_width=True, type="primary"):
            st.cache_data.clear()
            st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        # Status del sistema
        status = D.fetch_system_status()
        if status["ok"]:
            last_j = status["last_judge"]
            last_str = last_j.strftime("%m/%d %H:%M") if last_j else "—"
            st.markdown(f"""
            <div style="font-size:11px;">
              <span style="color:{EMERALD};">● DB online</span>
              <div style="color:{DIM};font-size:10px;margin-top:6px;line-height:1.8;">
                Agentes activos: <b style="color:{TEXT};">{status['n_active']}</b><br>
                Generación max: <b style="color:{TEXT};">Gen {status['max_gen']}</b><br>
                Ops abiertas: <b style="color:{TEXT};">{status['ops_open']}</b><br>
                Último ciclo: <b style="color:{TEXT};">{last_str}</b>
              </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f'<span style="color:{RED};font-size:11px;">● DB error</span>', unsafe_allow_html=True)
            st.caption(status.get("error", ""))

        st.markdown(f"""
        <div style="font-size:8px;color:{DIM};border-top:1px solid {BORDER};
                    padding-top:10px;margin-top:16px;line-height:1.8;">
          INVERSIÓN EVOLUTIVA v1.0<br>
          Cache TTL: 60 s · Par: EUR/USD<br>
          Motor: DeepSeek + Neon PG 17
        </div>
        """, unsafe_allow_html=True)

    return estados or ["activo"], gens or avail_gens


# ═══════════════════════════════════════════════════════════════════════════════
# KPIs
# ═══════════════════════════════════════════════════════════════════════════════
def _kpis(df_active: pd.DataFrame, df_all: pd.DataFrame) -> None:
    n_act    = len(df_active)
    best_roi = float(df_active["roi_total"].max()) if not df_active.empty else 0.0
    avg_roi  = float(df_active["roi_total"].mean()) if not df_active.empty else 0.0
    max_gen  = int(df_all["generacion"].max()) if not df_all.empty else 1

    if not df_active.empty:
        t_ops = int(df_active["operaciones_total"].sum())
        t_won = int(df_active["operaciones_ganadoras"].sum())
        wr    = round(t_won / t_ops * 100, 2) if t_ops > 0 else 0.0
    else:
        wr = 0.0

    def _delta(v: float, sfx: str = "%") -> tuple[str, str]:
        return f"{v:+.2f}{sfx}", ("normal" if v >= 0 else "inverse")

    # Fila 1: 3 métricas (se adaptan mejor en móvil)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Agentes Activos", n_act)
    with c2:
        d, dc = _delta(best_roi)
        st.metric("Mejor ROI", f"{best_roi:.2f}%", delta=d, delta_color=dc)
    with c3:
        d, dc = _delta(avg_roi)
        st.metric("ROI Promedio", f"{avg_roi:.2f}%", delta=d, delta_color=dc)

    # Fila 2: 2 métricas
    c4, c5 = st.columns(2)
    with c4:
        st.metric("Generación Actual", f"Gen {max_gen}",
                  delta=f"{max_gen - 1} ciclos" if max_gen > 1 else "Génesis")
    with c5:
        d, dc = _delta(wr - 50)
        st.metric("Win Rate Global", f"{wr:.1f}%", delta=d, delta_color=dc)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — POBLACIÓN
# ═══════════════════════════════════════════════════════════════════════════════
def _tab_population(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No hay agentes con los filtros seleccionados.")
        return

    st.markdown('<span class="ie-label">Mapa de Rentabilidad</span>', unsafe_allow_html=True)
    st.plotly_chart(C.roi_heatmap(df), use_container_width=True,
                    config={"displayModeBar": False})

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<span class="ie-label">Rankings de Agentes</span>', unsafe_allow_html=True)

    disp = df[[
        "id", "generacion", "estado", "roi_total", "capital_actual",
        "operaciones_total", "win_rate_pct", "padre_1_id", "fecha_nacimiento",
    ]].copy()
    disp.columns = [
        "ID Agente", "Gen", "Estado", "ROI %", "Capital ($)",
        "Ops", "Win Rate %", "Padre Principal", "Nacimiento",
    ]
    disp["ROI %"]      = disp["ROI %"].round(4)
    disp["Capital ($)"] = disp["Capital ($)"].round(4)

    st.dataframe(
        disp,
        use_container_width=True,
        height=min(400, len(disp) * 36 + 60),
        column_config={
            "ROI %": st.column_config.NumberColumn(format="%.4f %%"),
            "Capital ($)": st.column_config.NumberColumn(format="$%.4f"),
            "Win Rate %":  st.column_config.ProgressColumn(
                format="%.1f %%", min_value=0, max_value=100,
            ),
        },
        hide_index=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — EVOLUCIÓN
# ═══════════════════════════════════════════════════════════════════════════════
def _tab_evolution(df_all: pd.DataFrame, df_hist: pd.DataFrame) -> None:
    # Fila 1: survival curve + barras por generación
    col_l, col_r = st.columns([3, 2])
    with col_l:
        st.markdown('<span class="ie-label">Curva de Supervivencia</span>', unsafe_allow_html=True)
        st.plotly_chart(C.survival_curve(df_all), use_container_width=True,
                        config={"displayModeBar": False})
    with col_r:
        st.markdown('<span class="ie-label">Rendimiento por Generación</span>', unsafe_allow_html=True)
        st.plotly_chart(C.generation_bars(df_all), use_container_width=True,
                        config={"displayModeBar": False})

    # Fila 2: capital timeline (si hay historial)
    if not df_hist.empty:
        st.markdown('<span class="ie-label">Evolución de Capital</span>', unsafe_allow_html=True)
        st.plotly_chart(C.capital_timeline(df_hist), use_container_width=True,
                        config={"displayModeBar": False})

    # Árbol genealógico
    children = df_all[df_all["padre_1_id"].notna()].copy()
    if not children.empty:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<span class="ie-label">Árbol Genealógico</span>', unsafe_allow_html=True)
        gen_df = children[[
            "id", "generacion", "padre_1_id", "padre_2_id", "roi_total", "estado",
        ]].copy()
        gen_df.columns = ["Agente Hijo", "Gen", "Padre 1", "Padre 2", "ROI %", "Estado"]
        gen_df["ROI %"] = gen_df["ROI %"].round(4)
        st.dataframe(
            gen_df, use_container_width=True,
            height=min(320, len(gen_df) * 36 + 60),
            column_config={"ROI %": st.column_config.NumberColumn(format="%.4f %%")},
            hide_index=True,
        )
    else:
        st.markdown(f"""
        <div class="ie-card ie-card-amber" style="font-size:12px;color:{DIM};">
          No hay agentes de generaciones posteriores aún.
          Ejecuta el Agente Juez para iniciar el ciclo evolutivo.<br><br>
          <code>python -m cron.judge_scheduler --run-now</code>
        </div>
        """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — AGENTE JUEZ
# ═══════════════════════════════════════════════════════════════════════════════
_BADGE = {
    "evaluacion_diaria": ("jb-eval", "Evaluación Diaria"),
    "eliminacion":       ("jb-elim", "Eliminación"),
    "nuevo_agente":      ("jb-new",  "Nuevo Agente"),
    "mutacion":          ("jb-mut",  "Mutación"),
    "seleccion_padres":  ("jb-par",  "Selección Padres"),
    "reproduccion":      ("jb-new",  "Reproducción"),
}


def _tab_judge(df_logs: pd.DataFrame) -> None:
    if df_logs.empty:
        st.markdown(f"""
        <div class="ie-card ie-card-amber" style="font-size:13px;">
          <b style="color:{AMBER};">Sin registros del Agente Juez</b><br><br>
          El ciclo evolutivo aún no ha sido ejecutado. Actívalo con:<br>
          <code style="font-size:12px;">python -m cron.judge_scheduler --run-now</code><br><br>
          En producción, GitHub Actions lo ejecuta automáticamente cada día a las 17:00 Bogotá.
        </div>
        """, unsafe_allow_html=True)
        return

    # Último veredicto general
    evals = df_logs[df_logs["tipo_evento"] == "evaluacion_diaria"]
    if not evals.empty:
        last = evals.iloc[0]
        fecha_str = last["fecha"].strftime("%Y-%m-%d") if pd.notna(last["fecha"]) else "—"
        razon = last["razonamiento_llm"] or last["descripcion"]
        st.markdown(f"""
        <div class="ie-card ie-card-gold">
          <div style="font-size:9px;color:{GOLD};letter-spacing:1.5px;
                      text-transform:uppercase;margin-bottom:10px;">
            ⚖️ Último Veredicto del Juez · {fecha_str}
          </div>
          <div style="font-size:13px;color:{TEXT};line-height:1.7;">
            {razon}
          </div>
        </div>
        """, unsafe_allow_html=True)

    # Resumen numérico del día
    hoy = df_logs[df_logs["fecha"] == df_logs["fecha"].max()]
    if not hoy.empty:
        col_a, col_b, col_c = st.columns(3)
        n_elim = int((hoy["tipo_evento"] == "eliminacion").sum())
        n_new  = int((hoy["tipo_evento"] == "nuevo_agente").sum())
        n_par  = int((hoy["tipo_evento"] == "seleccion_padres").sum())
        col_a.metric("Eliminados", n_elim)
        col_b.metric("Nuevos agentes", n_new)
        col_c.metric("Cruces realizados", n_par)
        st.markdown("<br>", unsafe_allow_html=True)

    # Filtro de tipo de evento
    all_tipos = df_logs["tipo_evento"].unique().tolist()
    col_f, _ = st.columns([2, 3])
    with col_f:
        tipo_filter = st.multiselect(
            "Filtrar por tipo de evento",
            options=all_tipos, default=all_tipos,
            label_visibility="visible",
        )

    logs_show = df_logs[df_logs["tipo_evento"].isin(tipo_filter)] if tipo_filter else df_logs
    st.markdown('<span class="ie-label">Log de Actividad</span>', unsafe_allow_html=True)

    for _, row in logs_show.head(40).iterrows():
        cls, label = _BADGE.get(row["tipo_evento"], ("jb-eval", row["tipo_evento"]))
        agente_str = f'&nbsp;·&nbsp;<span style="color:{DIM};font-size:10px;">{row["agente_afectado_id"]}</span>' if row["agente_afectado_id"] else ""
        ts_str = row["created_at"].strftime("%m/%d %H:%M") if pd.notna(row["created_at"]) else ""
        razon  = row["razonamiento_llm"] or ""
        razon_html = (
            f'<div style="margin-top:8px;color:{DIM};font-size:11px;'
            f'font-style:italic;line-height:1.6;">{razon}</div>'
            if razon else ""
        )
        st.markdown(f"""
        <div class="jlog">
          <div class="jlog-head">
            <div>
              <span class="jbadge {cls}">{label}</span>{agente_str}
            </div>
            <span style="color:{DIM};font-size:9px;font-family:monospace;">{ts_str}</span>
          </div>
          <div style="font-size:12px;color:{TEXT};">{row['descripcion']}</div>
          {razon_html}
        </div>
        """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — OPERACIONES
# ═══════════════════════════════════════════════════════════════════════════════
def _tab_operations(df_ops: pd.DataFrame) -> None:
    if df_ops.empty:
        st.markdown(f"""
        <div class="ie-card ie-card-amber" style="font-size:13px;">
          <b style="color:{AMBER};">Sin operaciones registradas aun</b><br><br>
          Las APIs estan configuradas correctamente. Las operaciones apareceran
          aqui automaticamente cuando el Agente Juez ejecute el primer ciclo
          completo de trading (proximo dia habil a las 17:00 Bogota).
        </div>
        """, unsafe_allow_html=True)
        return

    # Sub-métricas
    total  = len(df_ops)
    pnl_total = float(df_ops["pnl"].sum()) if "pnl" in df_ops else 0.0
    wins   = int((df_ops["pnl"] > 0).sum()) if "pnl" in df_ops else 0
    wr_ops = round(wins / total * 100, 1) if total > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Operaciones", total)
    c2.metric("PnL Total", f"${pnl_total:.4f}",
              delta=f"{pnl_total:+.4f} USD",
              delta_color="normal" if pnl_total >= 0 else "inverse")
    c3.metric("Operaciones Ganadoras", wins)
    c4.metric("Win Rate", f"{wr_ops:.1f}%",
              delta=f"{wr_ops - 50:+.1f}% vs 50%",
              delta_color="normal" if wr_ops >= 50 else "inverse")

    st.markdown("<br>", unsafe_allow_html=True)

    # Tabla + distribución en columnas
    col_t, col_ch = st.columns([3, 2])

    with col_t:
        st.markdown('<span class="ie-label">Historial EUR/USD</span>', unsafe_allow_html=True)
        disp = df_ops[[
            "timestamp_entrada", "agente_id", "generacion", "par",
            "accion", "precio_entrada", "precio_salida",
            "pnl", "pnl_porcentaje", "estado",
        ]].copy()
        disp["timestamp_entrada"] = pd.to_datetime(
            disp["timestamp_entrada"]
        ).dt.strftime("%m/%d %H:%M")
        disp.columns = [
            "Entrada", "Agente", "Gen", "Par",
            "Acción", "P. Entrada", "P. Salida",
            "PnL $", "PnL %", "Estado",
        ]
        st.dataframe(
            disp, use_container_width=True,
            height=min(420, len(disp) * 36 + 60),
            column_config={
                "PnL $": st.column_config.NumberColumn(format="$%.4f"),
                "PnL %": st.column_config.NumberColumn(format="%.4f %%"),
                "P. Entrada": st.column_config.NumberColumn(format="%.5f"),
                "P. Salida":  st.column_config.NumberColumn(format="%.5f"),
            },
            hide_index=True,
        )

    with col_ch:
        st.markdown('<span class="ie-label">Distribución de PnL</span>', unsafe_allow_html=True)
        st.plotly_chart(
            C.pnl_distribution(df_ops), use_container_width=True,
            config={"displayModeBar": False},
        )
        st.markdown("<br>", unsafe_allow_html=True)

        # Desglose BUY / SELL / HOLD
        if "accion" in df_ops.columns:
            st.markdown('<span class="ie-label">Por Tipo de Acción</span>', unsafe_allow_html=True)
            action_df = (
                df_ops.groupby("accion")
                .agg(count=("id", "count"), pnl_sum=("pnl", "sum"))
                .reset_index()
            )
            action_df["pnl_sum"] = action_df["pnl_sum"].round(4)
            action_df.columns = ["Acción", "Cantidad", "PnL Total $"]
            st.dataframe(action_df, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    _css()
    _header()

    estados_f, gens_f = _sidebar()

    # Cargar datos
    with st.spinner("Sincronizando con Neon..."):
        df_filtered = D.fetch_agents(estados=estados_f, gens=gens_f)
        df_active   = D.fetch_agents(estados=["activo"])
        df_all      = D.fetch_agents()
        df_logs     = D.fetch_judge_logs()
        df_ops      = D.fetch_operations()
        df_hist     = D.fetch_ranking_history()

    # KPIs globales
    _kpis(df_active, df_all)
    st.markdown("<br>", unsafe_allow_html=True)

    # Tabs principales
    tab_pop, tab_evo, tab_judge, tab_ops = st.tabs([
        "📊  Población",
        "🧬  Evolución",
        "⚖️  Agente Juez",
        "💹  Operaciones",
    ])

    with tab_pop:
        _tab_population(df_filtered)

    with tab_evo:
        _tab_evolution(df_all, df_hist)

    with tab_judge:
        _tab_judge(df_logs)

    with tab_ops:
        _tab_operations(df_ops)


main()
