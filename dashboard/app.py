"""
INVERSIÓN EVOLUTIVA — Command Center
Streamlit dashboard: dark luxury theme · gold / emerald palette.
"""

from __future__ import annotations

import base64
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


def _fmt_bogota(dt, fmt: str = "%m/%d %H:%M") -> str:
    """
    Formatea un timestamp en hora Bogotá. Acepta:
      • datetime.datetime (aware o naive — naive se asume UTC)
      • pandas.Timestamp (aware o naive — naive se asume UTC)
      • None / NaT → devuelve "—"
    Por convención, la DB Neon almacena timestamps en UTC.
    """
    if dt is None:
        return "—"
    try:
        if pd.isna(dt):
            return "—"
    except (TypeError, ValueError):
        pass
    ts = pd.Timestamp(dt)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(_BOGOTA_TZ).strftime(fmt)


# Asegura que el directorio raíz del proyecto esté en sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dashboard import data as D
from dashboard import charts as C

# ── Logo ──────────────────────────────────────────────────────────────────────
def _logo_b64() -> str:
    _logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    with open(_logo_path, "rb") as f:
        return base64.b64encode(f.read()).decode()

_LOGO = _logo_b64()

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
        <div style="padding:16px 0 20px 0; border-bottom:1px solid {BORDER}; margin-bottom:22px;
                    display:flex; align-items:center; gap:16px;">
          <img src="data:image/png;base64,{_LOGO}"
               style="width:130px;height:130px;border-radius:20px;flex-shrink:0;">
          <div>
            <div style="font-size:20px;font-weight:800;letter-spacing:3px;color:{GOLD};">
              INVERSIÓN EVOLUTIVA
            </div>
            <div style="font-size:10px;color:{DIM};letter-spacing:3.5px;
                        text-transform:uppercase;margin-top:4px;">
              Command Center · EUR/USD · Algorithmic Evolution
            </div>
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
        <div style="padding:10px 0 14px; border-bottom:1px solid {BORDER}; margin-bottom:16px;
                    display:flex; align-items:center; gap:10px;">
          <img src="data:image/png;base64,{_LOGO}"
               style="width:36px;height:36px;border-radius:8px;flex-shrink:0;">
          <div>
            <div style="font-size:11px;font-weight:800;letter-spacing:1.5px;color:{GOLD};">
              INV. EVOLUTIVA
            </div>
            <div style="font-size:9px;letter-spacing:2px;color:{DIM};text-transform:uppercase;">
              Filtros &amp; Control
            </div>
          </div>
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
            last_str = _fmt_bogota(last_j, "%m/%d %H:%M")
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
          INVERSIÓN EVOLUTIVA v1.2<br>
          Cache TTL: 60 s · Par: EUR/USD<br>
          Motor: DeepSeek + Supabase PG · cron-job.org
        </div>
        """, unsafe_allow_html=True)

    return estados or ["activo"], gens or avail_gens, n_logs


# ═══════════════════════════════════════════════════════════════════════════════
# KPIs
# ═══════════════════════════════════════════════════════════════════════════════
def _kpis(df_active: pd.DataFrame, df_all: pd.DataFrame) -> None:
    n_act        = len(df_active)
    best_roi     = float(df_active["roi_total"].max()) if not df_active.empty else 0.0
    avg_roi      = float(df_active["roi_total"].mean()) if not df_active.empty else 0.0
    max_gen      = int(df_all["generacion"].max()) if not df_all.empty else 1
    best_fitness = (
        float(df_active["fitness_score"].max())
        if not df_active.empty and "fitness_score" in df_active.columns
        else 0.0
    )

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

    # Fila 2: 3 métricas
    c4, c5, c6 = st.columns(3)
    with c4:
        st.metric("Generación Actual", f"Gen {max_gen}",
                  delta=f"{max_gen - 1} ciclos" if max_gen > 1 else "Génesis")
    with c5:
        d, dc = _delta(wr - 50)
        st.metric("Win Rate Global", f"{wr:.1f}%", delta=d, delta_color=dc)
    with c6:
        st.metric("Mejor Fitness (Calmar)", f"{best_fitness:.4f}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — POBLACIÓN
# ═══════════════════════════════════════════════════════════════════════════════
_ESPECIE_META = {
    "tendencia": {"emoji": "📈", "color": "#d4af37", "label": "Tendencia"},
    "reversion": {"emoji": "↔️",  "color": "#00c878", "label": "Reversión"},
    "ruptura":   {"emoji": "💥", "color": "#e8a020", "label": "Ruptura"},
}


def _tab_population(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No hay agentes con los filtros seleccionados.")
        return

    st.markdown('<span class="ie-label">Mapa de Rentabilidad</span>', unsafe_allow_html=True)
    st.plotly_chart(C.roi_heatmap(df), use_container_width=True,
                    config={"displayModeBar": False})

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Toggle de vista ──────────────────────────────────────────────────────
    col_label, col_toggle = st.columns([4, 1])
    with col_label:
        st.markdown('<span class="ie-label">Rankings de Agentes</span>',
                    unsafe_allow_html=True)
    with col_toggle:
        agrupar = st.toggle("Por especie", value=False, key="pop_group_by_especie")

    _fitness_col = ["fitness_score"] if "fitness_score" in df.columns else []
    has_especie  = "especie" in df.columns

    col_cfg = {
        "Fitness":     st.column_config.NumberColumn(format="%.4f"),
        "ROI %":       st.column_config.NumberColumn(format="%.4f %%"),
        "Capital ($)": st.column_config.NumberColumn(format="$%.4f"),
        "Win Rate %":  st.column_config.ProgressColumn(
            format="%.1f %%", min_value=0, max_value=100),
    }

    if agrupar and has_especie:
        # ── Vista agrupada por especie ────────────────────────────────────
        for especie in ["tendencia", "reversion", "ruptura"]:
            meta  = _ESPECIE_META.get(especie, {"emoji": "•", "color": GOLD, "label": especie})
            grupo = df[df["especie"] == especie].copy()
            if grupo.empty:
                continue

            n_ag   = len(grupo)
            fit_m  = grupo["fitness_score"].mean() if "fitness_score" in grupo.columns else 0.0
            roi_m  = grupo["roi_total"].mean()
            cap_m  = grupo["capital_actual"].mean()

            st.markdown(f"""
            <div style="margin:18px 0 8px 0;padding:10px 16px;
                        border-left:3px solid {meta['color']};
                        background:rgba(255,255,255,0.03);border-radius:0 8px 8px 0;">
              <span style="font-size:15px;font-weight:800;color:{meta['color']};
                           letter-spacing:1px;">
                {meta['emoji']} {meta['label'].upper()}
              </span>
              <span style="font-size:11px;color:{DIM};margin-left:14px;">
                {n_ag} agentes &nbsp;·&nbsp;
                Fitness medio: <b style="color:{meta['color']};">{fit_m:+.4f}</b> &nbsp;·&nbsp;
                ROI medio: <b style="color:{meta['color']};">{roi_m:+.4f}%</b> &nbsp;·&nbsp;
                Capital medio: <b style="color:{meta['color']};">${cap_m:.4f}</b>
              </span>
            </div>
            """, unsafe_allow_html=True)

            disp = grupo[[
                "id", "generacion", "estado", *_fitness_col, "roi_total",
                "capital_actual", "operaciones_total", "win_rate_pct",
                "padre_1_id", "fecha_nacimiento",
            ]].copy()
            disp.columns = [
                "ID Agente", "Gen", "Estado",
                *(["Fitness"] if _fitness_col else []),
                "ROI %", "Capital ($)", "Ops", "Win Rate %",
                "Padre Principal", "Nacimiento",
            ]
            disp["ROI %"]       = disp["ROI %"].round(4)
            disp["Capital ($)"] = disp["Capital ($)"].round(4)
            if "Fitness" in disp.columns:
                disp["Fitness"] = disp["Fitness"].round(4)

            st.dataframe(disp, use_container_width=True,
                         height=min(280, len(disp) * 36 + 60),
                         column_config=col_cfg, hide_index=True)
    else:
        # ── Vista plana original ──────────────────────────────────────────
        base_cols = ["id", "generacion", "estado", *_fitness_col,
                     "roi_total", "capital_actual", "operaciones_total",
                     "win_rate_pct", "padre_1_id", "fecha_nacimiento"]
        if has_especie:
            base_cols = ["id", "generacion", "especie", "estado", *_fitness_col,
                         "roi_total", "capital_actual", "operaciones_total",
                         "win_rate_pct", "padre_1_id", "fecha_nacimiento"]
        disp = df[base_cols].copy()
        disp.columns = [
            "ID Agente", "Gen",
            *( ["Especie"] if has_especie else []),
            "Estado",
            *(["Fitness"] if _fitness_col else []),
            "ROI %", "Capital ($)", "Ops", "Win Rate %",
            "Padre Principal", "Nacimiento",
        ]
        disp["ROI %"]       = disp["ROI %"].round(4)
        disp["Capital ($)"] = disp["Capital ($)"].round(4)
        if "Fitness" in disp.columns:
            disp["Fitness"] = disp["Fitness"].round(4)

        st.dataframe(
            disp,
            use_container_width=True,
            height=min(600, len(disp) * 36 + 60),
            column_config=col_cfg,
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
        _fit_col = ["fitness_score"] if "fitness_score" in children.columns else []
        gen_df = children[[
            "id", "generacion", "padre_1_id", "padre_2_id", *_fit_col, "roi_total", "estado",
        ]].copy()
        gen_df.columns = [
            "Agente Hijo", "Gen", "Padre 1", "Padre 2",
            *(["Fitness"] if _fit_col else []), "ROI %", "Estado",
        ]
        gen_df["ROI %"] = gen_df["ROI %"].round(4)
        if "Fitness" in gen_df.columns:
            gen_df["Fitness"] = gen_df["Fitness"].round(4)
        st.dataframe(
            gen_df, use_container_width=True,
            height=min(320, len(gen_df) * 36 + 60),
            column_config={
                "Fitness": st.column_config.NumberColumn(format="%.4f"),
                "ROI %":   st.column_config.NumberColumn(format="%.4f %%"),
            },
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
          En producción, GitHub Actions lo ejecuta automáticamente cada día a las 23:00 Bogotá.
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
        ts_str = _fmt_bogota(row["created_at"], "%m/%d %H:%M")
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
          completo de trading (proximo dia habil a las 23:00 Bogota).
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
        disp["timestamp_entrada"] = (
            pd.to_datetime(disp["timestamp_entrada"], utc=True)
            .dt.tz_convert("America/Bogota")
            .dt.tz_localize(None)
            .dt.strftime("%m/%d %H:%M")
        )
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
# TAB 5 — PRECIO EUR/USD
# ═══════════════════════════════════════════════════════════════════════════════

_RANGE_MAP = {
    "Hoy (5 min)":  ("5m",  "1d"),
    "5 Días (1 h)": ("1h",  "5d"),
    "1 Mes (1 día)": ("1d", "1mo"),
}


def _tab_price(df_all: pd.DataFrame) -> None:
    col_rng, col_agt, _ = st.columns([1, 2, 3])

    with col_rng:
        st.markdown(
            f'<span style="font-size:9px;color:{DIM};letter-spacing:1px;'
            'text-transform:uppercase;">Rango</span>',
            unsafe_allow_html=True,
        )
        range_label = st.selectbox(
            "Rango", options=list(_RANGE_MAP.keys()),
            index=1, label_visibility="collapsed",
        )

    interval, range_str = _RANGE_MAP[range_label]

    agent_ids = ["— Sin agente —"] + sorted(df_all["id"].tolist())
    with col_agt:
        st.markdown(
            f'<span style="font-size:9px;color:{DIM};letter-spacing:1px;'
            'text-transform:uppercase;">Agente</span>',
            unsafe_allow_html=True,
        )
        selected_agent = st.selectbox(
            "Agente", options=agent_ids, index=0,
            label_visibility="collapsed",
        )

    agent_id = selected_agent if selected_agent != "— Sin agente —" else ""

    with st.spinner("Cargando datos de precio..."):
        df_prices = D.fetch_price_history(interval=interval, range_str=range_str)

    df_agent_ops = pd.DataFrame()
    if agent_id:
        with st.spinner(f"Cargando operaciones de {agent_id}..."):
            df_agent_ops = D.fetch_operations_by_agent(agent_id)

    st.plotly_chart(
        C.price_chart_with_operations(df_prices, df_agent_ops, agent_id),
        use_container_width=True,
        config={"displayModeBar": True, "scrollZoom": True},
    )

    # Detalle de operaciones del agente seleccionado
    if agent_id and not df_agent_ops.empty:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f'<span class="ie-label">Operaciones de {agent_id}</span>',
            unsafe_allow_html=True,
        )
        disp = df_agent_ops.copy()
        disp["timestamp_entrada"] = (
            disp["timestamp_entrada"]
            .dt.tz_convert("America/Bogota").dt.tz_localize(None)
            .dt.strftime("%m/%d %H:%M")
        )
        _salida_notna = disp["timestamp_salida"].notna()
        disp["timestamp_salida"] = (
            disp["timestamp_salida"]
            .dt.tz_convert("America/Bogota").dt.tz_localize(None)
            .dt.strftime("%m/%d %H:%M")
            .where(_salida_notna, other="abierta")
        )
        disp = disp[[
            "id", "accion", "precio_entrada", "precio_salida",
            "timestamp_entrada", "timestamp_salida", "pnl", "estado",
        ]]
        disp.columns = [
            "ID", "Acción", "P. Entrada", "P. Salida",
            "Entrada", "Salida", "PnL $", "Estado",
        ]
        st.dataframe(
            disp, use_container_width=True,
            height=min(320, len(disp) * 36 + 60),
            column_config={
                "PnL $":      st.column_config.NumberColumn(format="$%.4f"),
                "P. Entrada": st.column_config.NumberColumn(format="%.5f"),
                "P. Salida":  st.column_config.NumberColumn(format="%.5f"),
            },
            hide_index=True,
        )
    elif agent_id:
        st.markdown(
            f'<div class="ie-card ie-card-amber" style="font-size:12px;color:{DIM};">'
            f'No hay operaciones registradas para <b>{agent_id}</b>.</div>',
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — INSTRUCCIONES
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_instructions() -> None:
    st.markdown(f"""
    <style>
      .ins-section {{
        margin-bottom: 28px;
      }}
      .ins-title {{
        font-size: 11px;
        font-weight: 800;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: {GOLD};
        border-bottom: 1px solid {BORDER};
        padding-bottom: 8px;
        margin-bottom: 16px;
      }}
      .ins-card {{
        background: {CARD};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 18px 22px;
        margin-bottom: 12px;
      }}
      .ins-card-left-gold    {{ border-left: 3px solid {GOLD};    }}
      .ins-card-left-emerald {{ border-left: 3px solid {EMERALD}; }}
      .ins-card-left-red     {{ border-left: 3px solid {RED};     }}
      .ins-card-left-amber   {{ border-left: 3px solid {AMBER};   }}
      .ins-card-left-dim     {{ border-left: 3px solid {BORDER};  }}
      .ins-body {{
        font-size: 13px;
        color: {TEXT};
        line-height: 1.8;
      }}
      .ins-body b {{ color: {GOLD}; }}
      .ins-body em {{ color: {EMERALD}; font-style: normal; }}
      .ins-body s {{ color: {RED}; text-decoration: none; font-weight: 700; }}
      .ins-step {{
        display: flex;
        align-items: flex-start;
        gap: 14px;
        margin-bottom: 10px;
      }}
      .ins-step-num {{
        min-width: 26px;
        height: 26px;
        border-radius: 50%;
        background: {GOLD};
        color: #000;
        font-size: 11px;
        font-weight: 800;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        margin-top: 2px;
      }}
      .ins-step-text {{
        font-size: 13px;
        color: {TEXT};
        line-height: 1.7;
      }}
      .ins-step-text b {{ color: {GOLD}; }}
      .ins-pill {{
        display: inline-block;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.5px;
        margin-right: 6px;
      }}
      .ins-pill-buy  {{ background: rgba(0,200,120,0.15); color: {EMERALD}; border: 1px solid {EMERALD}; }}
      .ins-pill-sell {{ background: rgba(224,82,82,0.15);  color: {RED};     border: 1px solid {RED}; }}
      .ins-pill-hold {{ background: rgba(212,175,55,0.15); color: {GOLD};    border: 1px solid {GOLD}; }}
      .ins-timeline {{
        position: relative;
        padding-left: 20px;
        border-left: 2px solid {BORDER};
        margin: 8px 0;
      }}
      .ins-timeline-item {{
        margin-bottom: 14px;
        position: relative;
      }}
      .ins-timeline-item::before {{
        content: '';
        position: absolute;
        left: -25px;
        top: 5px;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: {GOLD};
      }}
      .ins-time {{
        font-size: 11px;
        color: {GOLD};
        font-family: monospace;
        font-weight: 700;
      }}
      .ins-timeline-text {{
        font-size: 12px;
        color: {TEXT};
        line-height: 1.6;
        margin-top: 2px;
      }}
      .ins-param-grid {{
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 10px;
        margin-top: 10px;
      }}
      .ins-param-box {{
        background: {CARD2};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 10px 14px;
      }}
      .ins-param-label {{
        font-size: 9px;
        color: {GOLD};
        letter-spacing: 1.5px;
        text-transform: uppercase;
        font-weight: 700;
        margin-bottom: 6px;
      }}
      .ins-param-items {{
        font-size: 11px;
        color: {DIM};
        line-height: 1.8;
      }}
    </style>

    <!-- ══ ENCABEZADO ══ -->
    <div style="margin-bottom:28px;">
      <div style="font-size:18px;font-weight:800;color:{GOLD};letter-spacing:2px;margin-bottom:6px;">
        ⚡ INVERSIÓN EVOLUTIVA — Cómo funciona
      </div>
      <div style="font-size:12px;color:{DIM};line-height:1.7;max-width:800px;">
        Un sistema de trading algorítmico multi-agente que aplica <b style="color:{GOLD};">
        algoritmos genéticos</b> para encontrar estrategias rentables en EUR/USD.
        Los agentes compiten entre sí: los mejores sobreviven y se reproducen, los peores
        son eliminados. La única herramienta de supervivencia de cada agente es su estrategia de inversión.
      </div>
    </div>

    <!-- ══ 1. LOS AGENTES ══ -->
    <div class="ins-section">
      <div class="ins-title">1 · Los Agentes Inversionistas — 3 Especies</div>

      <!-- Especies -->
      <div class="ins-card ins-card-left-gold" style="margin-bottom:12px;">
        <div style="font-size:10px;color:{GOLD};letter-spacing:1.5px;
                    text-transform:uppercase;font-weight:700;margin-bottom:10px;">
          🧬 Tres arquetipos decorrelacionados (desde Sesión 16)
        </div>
        <div class="ins-body">
          Los 10 agentes ya <b>no son copias del mismo sistema</b>. Pertenecen a tres
          <b>especies estratégicas</b> genuinamente distintas, cada una diseñada para
          ganar en un régimen de mercado diferente:
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px;">
          <div class="ins-param-box" style="border-left:3px solid {GOLD};">
            <div class="ins-param-label">📈 Tendencia (4 agentes)</div>
            <div class="ins-param-items">
              Momentum RSI + EMA + MACD<br>
              Filtro HTF activo<br>
              Opera cuando <b style="color:{GOLD};">ADX ≥ 25</b><br>
              (mercado con dirección clara)
            </div>
          </div>
          <div class="ins-param-box" style="border-left:3px solid {EMERALD};">
            <div class="ins-param-label">↔️ Reversión (3 agentes)</div>
            <div class="ins-param-items">
              RSI en extremos + OB/FVG<br>
              HTF desactivado<br>
              Opera cuando <b style="color:{EMERALD};">ADX &lt; 25</b><br>
              (mercado en rango)
            </div>
          </div>
          <div class="ins-param-box" style="border-left:3px solid {AMBER};">
            <div class="ins-param-label">💥 Ruptura (3 agentes)</div>
            <div class="ins-param-items">
              Breakout de estructura N velas<br>
              Confirmado por range_spike<br>
              Opera en <b style="color:{AMBER};">ambos regímenes</b><br>
              (busca explosión de volatilidad)
            </div>
          </div>
        </div>
        <div class="ins-body" style="margin-top:12px;">
          <b>Clasificador ADX:</b> cada ciclo se calcula el ADX(14) sobre las velas de 15m.
          ADX ≥ 25 = <em>TENDENCIA</em> (S1 + S3 activos, S2 bloqueado).
          ADX &lt; 25 = <em>RANGO</em> (S2 activo, S1 bloqueado). NEUTRAL = todos operan.<br><br>
          <b>Resultado práctico:</b> en días de mercado lateral (como el 01-jun-2026 con ADX=17.7),
          los 4 agentes tendencia quedan en HOLD automáticamente y los 3 de reversión operan
          en la dirección <em>correcta</em> para ese régimen. <s>Nunca más "todos pierden a la vez".</s>
        </div>
      </div>

      <div class="ins-card ins-card-left-gold">
        <div class="ins-body">
          Cada agente tiene <b>$10.00 USD de capital virtual</b> y un conjunto único de parámetros
          que constituyen su "ADN". Se heredan, cruzan y mutan entre generaciones.
        </div>
        <div class="ins-param-grid" style="margin-top:14px;">
          <div class="ins-param-box">
            <div class="ins-param-label">📊 Parámetros Técnicos</div>
            <div class="ins-param-items">
              Período RSI (5–50)<br>
              RSI modo (momentum / reversion)<br>
              Zona muerta RSI (1.0–15.0)<br>
              EMA rápida y lenta<br>
              Períodos MACD<br>
              Pesos RSI / EMA / MACD
            </div>
          </div>
          <div class="ins-param-box">
            <div class="ins-param-label">📰 Parámetros Macro</div>
            <div class="ins-param-items">
              Pesos por impacto de noticias<br>
              Umbrales de sentimiento<br>
              Ventana temporal (1–8 h)<br>
              Peso macro vs técnico total<br>
              Sesgo tendencia HTF (0.20–0.65)
            </div>
          </div>
          <div class="ins-param-box">
            <div class="ins-param-label">🛡️ Parámetros de Riesgo</div>
            <div class="ins-param-items">
              Riesgo por trade (1–2% equity)<br>
              Ratio riesgo/beneficio (1.5–4.0)<br>
              Confianza mínima para operar<br>
              Máx. drawdown diario
            </div>
          </div>
          <div class="ins-param-box">
            <div class="ins-param-label">🔮 Genes SMC + Régimen</div>
            <div class="ins-param-items">
              FVG mínimo en pips (2–15)<br>
              OB impulso mínimo (5–20)<br>
              Spike de rango (1.2–3.0×)<br>
              Cuarentena macro (30–120 min)<br>
              Pesos FVG / OB (0.05–0.50)<br>
              ATR factor SL (0.8–3.0)<br>
              Trailing activation pips (5–40)<br>
              Trailing distance pips (5–25)<br>
              <b>Breakout lookback bars (10–50)</b> S3<br>
              <b>Breakout min pips (3–15)</b> S3<br>
              <b>Peso breakout (0.20–0.70)</b> S3<br>
              Filtro HTF habilitado (0 / 1)
            </div>
          </div>
        </div>
      </div>

      <!-- Capital compartido -->
      <div class="ins-card ins-card-left-amber" style="margin-top:12px;">
        <div style="font-size:10px;color:{AMBER};letter-spacing:1.5px;
                    text-transform:uppercase;font-weight:700;margin-bottom:10px;">
          💰 Pool de capital compartido — Igualdad de condiciones cada día
        </div>
        <div class="ins-body">
          El sistema arrancó con <b>$100 USD virtuales</b> divididos en partes iguales:
          <b>$10 por agente</b>. A partir del primer día, ese pool fluctúa únicamente por
          las ganancias y pérdidas reales de trading — no se inyecta ni retira capital externo.<br><br>
          Al cierre de cada jornada (<b>11:00 pm Bogotá, lunes a viernes</b>), tras el ciclo
          evolutivo, el sistema suma el <code>capital_actual</code> de los 10 agentes activos
          resultantes y lo divide en partes iguales. <b>Todos los agentes inician el día
          siguiente con exactamente el mismo capital</b>, independientemente de si ganaron,
          perdieron o fueron recién creados.<br><br>
          Esto garantiza que la competencia sea <em>puramente estratégica</em>: ningún agente
          llega al día siguiente con ventaja de capital por haber tenido suerte el día anterior.
          El único mérito que importa es la calidad de la estrategia, medida día a día.
        </div>
      </div>
    </div>

    <!-- ══ 2. CÓMO DECIDE UN AGENTE ══ -->
    <div class="ins-section">
      <div class="ins-title">2 · Cómo toma decisiones un agente — Pipeline A → B → C</div>
      <div class="ins-card ins-card-left-emerald">
        <div class="ins-body" style="margin-bottom:14px;">
          Dentro del horario de trading (<b>1:30 am – 11:00 pm Bogotá</b>), cada agente
          sin posición abierta ejecuta el pipeline de tres sub-agentes en serie.
          Los indicadores técnicos (RSI, EMA, MACD) se calculan <b>en el momento exacto
          de cada evaluación</b> con los precios más recientes de Yahoo Finance —
          no se reutilizan los datos de la mañana. El resultado final es una de tres acciones:
          <span class="ins-pill ins-pill-buy">BUY</span>
          <span class="ins-pill ins-pill-sell">SELL</span>
          <span class="ins-pill ins-pill-hold">HOLD</span>
        </div>
        <div class="ins-step">
          <div class="ins-step-num">A</div>
          <div class="ins-step-text">
            <b>Sub-agente Técnico</b> — Detecta señales SMC: <b>Fair Value Gaps</b> (FVG)
            alcistas y bajistas en las últimas 50 velas, <b>Order Blocks</b> (OB) en las
            últimas 80 velas, y un <b>Range Proxy</b> (high–low en pips) como sustituto de
            volumen. Complementa con <b>RSI momentum</b> (cruce del nivel 50 — el RSI señala
            la dirección del mercado en lugar de contradecirla), EMA y MACD. Calcula una
            señal ponderada con pesos genéticos individuales (FVG, OB, RSI, EMA, MACD).<br>
            Un <b>spike de rango</b> amplifica la confianza ×1.15 <em>solo si la última vela
            confirma la dirección de la señal</em> (condicionado desde Sesión 15 — un spike
            contrario no atenúa ni amplifica).<br>
            El <b>filtro HTF</b> (<code>htf_filter_enabled</code>) veta la señal si contradice
            la tendencia EMA50/EMA200 en velas de 1h: señal BUY cuando HTF = BEAR → HOLD.
            Si la confianza cae en zona ambigua (0.45–0.65), consulta a DeepSeek para confirmar.
          </div>
        </div>
        <div class="ins-step">
          <div class="ins-step-num">B</div>
          <div class="ins-step-text">
            <b>Sub-agente Macro</b> — Analiza el calendario económico y titulares de noticias
            Forex en tiempo real vía <b>Finnhub API</b>. DeepSeek devuelve un score de
            sentimiento (−1.0 a +1.0). Los eventos de alto impacto (USD/EUR) tienen más peso
            que los de bajo impacto, según los parámetros del agente.<br>
            Cuando no hay eventos de alto impacto y el LLM devolvería HOLD plano, aplica el
            <b>sesgo de tendencia HTF</b>: usa la dirección EMA50/EMA200 en 1h para emitir
            BUY/SELL con confianza = <code>min(0.55, peso_sesgo_tendencia)</code>. Evita que
            el sub-agente B sea siempre neutral en días sin noticias.
          </div>
        </div>
        <div class="ins-step">
          <div class="ins-step-num">C</div>
          <div class="ins-step-text">
            <b>Sub-agente de Riesgo (decisión final)</b> — Combina las señales A y B con
            pesos configurables. Si las señales coinciden, promedia. Si hay conflicto,
            gana la que supere 0.75 de confianza; si ninguna lo hace, emite <b>HOLD</b>.
            Si la confianza final no alcanza el umbral mínimo del agente, también emite
            <b>HOLD</b>. Si la acción es BUY o SELL, calcula el <b>Stop Loss</b> siguiendo
            una jerarquía de 4 niveles: <b>① Order Block</b> activo no mitigado →
            <b>② Fair Value Gap</b> activo no rellenado → <b>③ ATR × atr_factor</b>
            (distancia real de mercado, ~12–22 pips típico, gen mutable) →
            <b>④ porcentaje fijo</b> (fallback legacy si ATR = 0).
            El Take Profit se calcula como <code>SL × risk_reward_target</code> (gen mutable,
            default 2.0×). El sizing es dinámico: <code>lotes = (equity × risk_pct) / (sl_pips × 0.10)</code>, convertido a <b>nocional USD</b> con techo de <b>50:1 de apalancamiento</b> (máx. <code>equity × 50</code>).
            El <b>trailing stop</b> (genes <code>trailing_activation_pips</code> y
            <code>trailing_distance_pips</code>) queda registrado en la operación para que
            el Trade Monitor lo aplique <b>vela a vela</b> dentro del verificador intra-bar.
            Luego consulta a DeepSeek para validación final.
          </div>
        </div>
      </div>
    </div>

    <!-- ══ 3. HOLD ══ -->
    <div class="ins-section">
      <div class="ins-title">3 · HOLD — Por qué no operar también es una estrategia</div>
      <div class="ins-card ins-card-left-amber">
        <div class="ins-body">
          <span class="ins-pill ins-pill-hold">HOLD</span> significa que el agente decidió
          <b>no abrir ninguna posición ese día</b>. Esto no es una falla — es una decisión
          estratégica válida y en muchos casos la más inteligente.<br><br>
          <b>¿Cuándo emite HOLD un agente?</b><br>
          &nbsp;• Cuando las señales técnica y macro están en conflicto y ninguna tiene confianza suficiente.<br>
          &nbsp;• Cuando la confianza combinada está por debajo del umbral mínimo del agente.<br>
          &nbsp;• Cuando ya tiene una posición abierta de un ciclo anterior (no abre un segundo trade).<br>
          &nbsp;• Cuando un evento macroeconómico crítico (NFP, CPI, FOMC, ECB, GDP) cae dentro de la ventana de cuarentena del agente (gen <code>macro_quarantine_minutes</code>, rango 30–120 min). El agente espera en silencio hasta que la ventana expire.<br><br>
          <b>¿Por qué es válido como estrategia de supervivencia?</b><br>
          Si el mercado está muy volátil e incierto y otros agentes abren posiciones perdedoras,
          el agente que hizo HOLD conserva su capital intacto. Con el tiempo, un agente que
          sabe cuándo <em>no operar</em> puede acumular una ventaja competitiva real frente
          a agentes agresivos que pierden capital en señales débiles.
        </div>
      </div>
    </div>

        <!-- ══ 4. MONITOREO Y CIERRE ══ -->
    <div class="ins-section">
      <div class="ins-title">4 · Monitor cada 15 minutos — Verificación intra-vela OHLC 1m + Nuevas posiciones</div>
      <div class="ins-card ins-card-left-dim">
        <div class="ins-body">
          El <b>Trade Monitor</b> corre cada 15 minutos dentro del horario 1:30 am–10:30 pm
          Bogotá y realiza <b>dos tareas en cada ciclo</b>:<br><br>
          <b>① Verificación intra-vela de SL/TP + Trailing stop</b><br>
          Para cada agente con posición abierta, el monitor descarga las <b>velas OHLC
          de 1 minuto</b> de Yahoo Finance desde la última verificación hasta ahora
          (≥15 velas por ciclo) y las recorre en orden cronológico. Por cada vela:<br>
          &nbsp;• <b>Primero</b> verifica si el <code>high</code> o <code>low</code>
          tocó el Stop Loss o el Take Profit con el SL <em>antes</em> de aplicar trailing.
          Si <b>ambos</b> se tocan en la misma vela → <s>SL gana</s>
          (convención conservadora: ante ambigüedad intra-vela, asumir el peor caso).<br>
          &nbsp;• <b>Luego</b>, si no hubo hit, aplica el trailing usando el extremo
          favorable de la vela (<code>low</code> para SELL, <code>high</code> para BUY).
          El SL dinámico <b>nunca empeora</b> — solo se mueve a favor del agente.<br><br>
          Si SL o TP se activa, la operación cierra al <b>precio exacto del nivel</b>
          y el <code>timestamp_salida</code> refleja el instante real de la mecha:<br>
          &nbsp;• <b style="color:{EMERALD};">&#10003; Take Profit alcanzado</b> — ganancia al precio exacto del TP.<br>
          &nbsp;• <b style="color:{RED};">&#10007; Stop Loss alcanzado</b> — pérdida controlada al precio del SL (puede ser el SL trailing apretado).<br>
          &nbsp;• <b style="color:{DIM};">&#9677; Cierre EOD (10:45 pm Bogotá)</b> — posiciones aún abiertas cierran al precio de mercado.<br>
          &nbsp;• <b style="color:{DIM};">&#9888; Fallback automático:</b> si Yahoo no devuelve velas
          (fin de semana, error API), cae al check por snapshot único para no bloquear el ciclo.<br><br>
          <b>Por qué importa para la evolución:</b> antes, el monitor comparaba un snapshot
          cada 15 min. Una mecha que tocara el TP y rebotara era invisible: el trade
          seguía abierto y terminaba cerrando en SL trailing. El fitness medía un mundo
          ficticio. Con OHLC 1 min el simulador refleja lo que haría un broker real con
          órdenes stop/limit, y el ADN que evoluciona es portable a producción real.<br><br>
          <b>② Evaluación de nuevas posiciones</b><br>
          Para cada agente libre (sin posición abierta y con capital suficiente),
          descarga velas OHLCV actualizadas, recalcula SMC + RSI/EMA/MACD con los precios
          del momento y ejecuta el pipeline A→B→C completo. Un agente puede operar
          varias veces al día de forma <b>secuencial</b> (una posición abierta a la vez).
        </div>
      </div>
    </div>

    <!-- ══ 5. AGENTE JUEZ ══ -->
    <div class="ins-section">
      <div class="ins-title">5 · El Agente Juez — Evaluación diaria a las 11:00 pm Bogotá</div>
      <div class="ins-card ins-card-left-red">
        <div class="ins-body">
          Al cierre del mercado, el <b>Agente Juez</b> ejecuta el ciclo evolutivo:<br><br>
        </div>
        <div class="ins-step">
          <div class="ins-step-num">1</div>
          <div class="ins-step-text">
            <b>Fitness por Expectancy neta (desde Sesión 16)</b> — Los agentes se ordenan
            por <code>fitness = (expectancy/trade / (max_drawdown+1)) × confianza_estadística</code>.
            La <b>expectancy</b> es <code>win_rate × ganancia_media − loss_rate × pérdida_media</code>
            (ya neta de spread+slippage). La <b>confianza estadística</b> escala de 0 a 1
            según cuántos trades cerrados tiene el agente vs. la muestra mínima de 15.
            Un agente con ROI 5% en 2 trades no supera a uno con ROI 3% en 30 trades.
          </div>
        </div>
        <div class="ins-step">
          <div class="ins-step-num">2</div>
          <div class="ins-step-text">
            <b>Cuota dinámica + muestra mínima + protección de especies</b> —
            Son <b>inmunes</b> (no elegibles para eliminación): agentes con &lt; 15 trades
            cerrados (muestra insuficiente) o recién nacidos sin operaciones.
            Solo son eliminables los elegibles con <b>fitness ≤ 0</b>. Máximo 5 por día.
            <b>Protección de diversidad:</b> nunca se elimina un agente si bajaría su
            especie (tendencia/reversion/ruptura) por debajo de 2 agentes activos.
          </div>
        </div>
        <div class="ins-step">
          <div class="ins-step-num">3</div>
          <div class="ins-step-text">
            <b>Razonamiento con DeepSeek</b> — El LLM analiza el contexto: parámetros
            de los eliminados, condiciones de mercado del día, y qué estrategias
            funcionaron mejor. Genera un veredicto con justificaciones individuales.
          </div>
        </div>
        <div class="ins-step">
          <div class="ins-step-num">4</div>
          <div class="ins-step-text">
            <b>Torneo de candidatos + backtest OOS (desde Sesión 16)</b> — Para cada slot
            vacante se generan <b>3 candidatos</b> con genes distintos. Cada uno se
            backtestea sobre los <b>últimos 20 días de historia</b> (el mismo pipeline
            de producción: señales → régimen → SL/TP → fricción, sin LLM).
            Solo se despliega el candidato con mayor fitness out-of-sample.
            Los otros 2 se descartan antes de operar en vivo.
          </div>
        </div>
        <div class="ins-step">
          <div class="ins-step-num">5</div>
          <div class="ins-step-text">
            <b>Redistribución igualitaria de capital</b> — Se suma el <code>capital_actual</code>
            de los 10 agentes activos resultantes y se divide en partes iguales.
            Todos inician el día siguiente con <b>exactamente el mismo capital</b>.
          </div>
        </div>
      </div>
    </div>

    <!-- ══ 6. EVOLUCIÓN GENÉTICA ══ -->
    <div class="ins-section">
      <div class="ins-title">6 · Reproducción y Mutación — Cómo nacen los nuevos agentes</div>
      <div class="ins-card ins-card-left-gold">
        <div class="ins-body">
          Cada nuevo agente se crea en dos pasos:<br><br>
          <b>① Cruce (Crossover)</b> — Se seleccionan 2 padres del pool de supervivientes.
          La probabilidad de ser padre es proporcional al <b>fitness score</b> (expectancy neta,
          no ROI crudo — desde Sesión 16). Se prefieren padres de la misma especie para cruzar
          genes coherentes. Cada parámetro del hijo se hereda del Padre 1 con 60%, del Padre 2 con 40%.
          El hijo hereda la <b>especie del eliminado</b> que reemplaza.<br><br>
          <b>② Mutación Gaussiana</b> — Cada parámetro heredado se perturba con ruido aleatorio:
          <code style="font-size:11px;color:{EMERALD};">
            param_hijo = param_padre × (1 + N(0, σ))
          </code><br>
          Los niveles de ruido (σ) son distintos por tipo:<br>
          &nbsp;• Pesos de decisión: σ = 5%<br>
          &nbsp;• Períodos enteros (RSI, EMA): σ = 8%<br>
          &nbsp;• Parámetros de riesgo (SL, TP): σ = 10%<br><br>
          <b>Restricciones de seguridad post-mutación:</b><br>
          &nbsp;• Los pesos RSI/EMA/MACD/FVG/OB se renormalizan para sumar 1.0.<br>
          &nbsp;• La EMA rápida siempre es menor que la EMA lenta.<br>
          &nbsp;• Los genes SMC se recortan a sus rangos evolutivos:
          <code>fvg_min_pips</code> (2–15), <code>ob_impulse_pips</code> (5–20),
          <code>risk_reward_target</code> (1.5–4.0), <code>macro_quarantine_minutes</code> (30–120),
          <code>risk_pct_per_trade</code> (1–2%), <code>peso_fvg/peso_ob</code> (0.05–0.50),
          <code>atr_factor</code> (0.8–3.0), <code>trailing_activation_pips</code> (5–40),
          <code>trailing_distance_pips</code> (5–25), <code>atr_period</code> (7–21).<br>
          &nbsp;• Genes nuevos (Sesión 15): <code>rsi_zona_muerta</code> (1.0–15.0, técnico),
          <code>peso_sesgo_tendencia</code> (0.20–0.65, macro). El gen <code>htf_filter_enabled</code>
          es entero 0/1 — decisión estratégica, no mutable gaussianamente.
          El gen <code>rsi_modo</code> es string — solo cambia por crossover.<br>
          &nbsp;• El riesgo por operación se fuerza dentro del rango 1–2% del equity (hard limits no mutables).
        </div>
      </div>
    </div>

    <!-- ══ 7. HALL OF FAME ══ -->
    <div class="ins-section">
      <div class="ins-title">7 · Hall of Fame — Estrategias que demuestran valor real</div>
      <div class="ins-card ins-card-left-emerald">
        <div class="ins-body">
          Todo agente superviviente con <b>ROI &gt; 0.05%</b> tiene sus parámetros
          guardados en la tabla <em>estrategias_exitosas</em>. Este repositorio acumula
          las configuraciones que han demostrado rentabilidad real con datos de mercado
          reales. En el futuro, puede usarse como fuente preferencial de herencia
          para nuevas generaciones, acelerando la convergencia evolutiva.
        </div>
      </div>
    </div>

    <!-- ══ 8. CICLO DIARIO ══ -->
    <div class="ins-section">
      <div class="ins-title">8 · Ciclo Diario Completo — Cronograma (hora Bogotá)</div>
      <div class="ins-card ins-card-left-dim">
        <div class="ins-timeline">
          <div class="ins-timeline-item">
            <div class="ins-time">1:30 am – 10:30 pm · cada 15 minutos</div>
            <div class="ins-timeline-text">
              <b style="color:{TEXT};">Monitor intraday (doble función)</b><br>
              <span style="color:{DIM};">① Verificación intra-vela:</span> descarga OHLC 1m
              desde la última verificación, recorre cada vela y cierra al precio exacto del SL/TP
              si <code>high/low</code> lo toca. Trailing se aplica vela a vela.<br>
              <span style="color:{DIM};">② Nuevas posiciones:</span> para cada agente libre
              (sin posición y con capital suficiente), descarga OHLCV actualizado, recalcula
              SMC (FVG, OB, Range Proxy) + RSI/EMA/MACD con los precios del momento y ejecuta el pipeline completo.
              Un agente puede operar varias veces al día de forma secuencial.
            </div>
          </div>
          <div class="ins-timeline-item">
            <div class="ins-time">10:45 pm – 11:00 pm · lunes a viernes</div>
            <div class="ins-timeline-text">
              <b style="color:{TEXT};">Cierre EOD + Ciclo Evolutivo + Redistribución</b><br>
              <span style="color:{DIM};">① 10:45 pm — Cierre forzado:</span> todas las posiciones abiertas
              se cierran al precio de mercado del momento.<br>
              <span style="color:{DIM};">② 11:00 pm — Selección natural:</span> el Agente Juez clasifica
              los 10 agentes por Calmar Ratio (fitness), elimina hasta 5 (cuota dinámica, solo
              fitness ≤ 0) y crea el mismo número de hijos por mutación de los supervivientes.<br>
              <span style="color:{DIM};">③ Redistribución de capital:</span> se suma el capital
              de los 10 agentes activos resultantes y se divide en partes iguales.
              Todos inician el día siguiente con el mismo capital.
              Todo queda registrado en el log de auditoría.<br>
              <span style="color:{DIM};">Los sábados y domingos este ciclo no corre — no hay trading.</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ 9. INDEPENDENCIA E INTEGRIDAD ══ -->
    <div class="ins-section">
      <div class="ins-title">9 · Independencia e Integridad — Por qué no existe conflicto de interés</div>
      <div class="ins-card ins-card-left-gold">
        <div class="ins-body">
          Aunque la meta de cada agente es <b>sobrevivir</b>, el sistema está diseñado para
          que ningún agente pueda influir sobre las decisiones del Agente Juez, manipular
          los resultados de otros agentes ni alterar su propio historial de rendimiento.
          A continuación, los mecanismos estructurales que lo garantizan:
        </div>
      </div>

      <!-- Separación de responsabilidades -->
      <div class="ins-card ins-card-left-emerald" style="margin-top:12px;">
        <div style="font-size:10px;color:{EMERALD};letter-spacing:1.5px;
                    text-transform:uppercase;font-weight:700;margin-bottom:10px;">
          ① Separación total de responsabilidades (código)
        </div>
        <div class="ins-body">
          Los agentes inversionistas (<code>InvestorAgent</code>) y el Agente Juez
          (<code>JudgeAgent</code>) viven en módulos completamente separados y nunca
          se invocan mutuamente. Un agente inversionista solo puede:
          <br><br>
          &nbsp;• Leer datos de mercado públicos (Yahoo Finance / scraping).<br>
          &nbsp;• Insertar o actualizar <b>sus propias</b> filas en la tabla <code>operaciones</code>.<br>
          &nbsp;• Leer su propio capital de la tabla <code>agentes</code>.<br><br>
          No tiene acceso, visibilidad ni referencias al código del Juez, al motor genético,
          ni a los parámetros o resultados de otros agentes.
        </div>
      </div>

      <!-- Métrica de fitness inmanipulable -->
      <div class="ins-card ins-card-left-emerald" style="margin-top:12px;">
        <div style="font-size:10px;color:{EMERALD};letter-spacing:1.5px;
                    text-transform:uppercase;font-weight:700;margin-bottom:10px;">
          ② El Calmar Ratio (fitness) es un hecho de mercado, no una opinión del agente
        </div>
        <div class="ins-body">
          La métrica que el Juez usa para clasificar y eliminar es el <b>Calmar Ratio (fitness)</b>,
          calculado exclusivamente a partir de precios reales — ROI y max drawdown
          derivados de las operaciones cerradas en la base de datos:<br><br>
          &nbsp;• Los precios de entrada y salida provienen de <b>Yahoo Finance</b>
            (fuente externa, no controlada por ningún agente).<br>
          &nbsp;• El P&L lo calcula el <b>Trade Monitor</b>, no el agente mismo.<br>
          &nbsp;• El cierre de posiciones (SL/TP/EOD) lo ejecuta el Trade Monitor de forma autónoma.<br><br>
          Un agente no puede declarar su propio P&L ni alterar precios de mercado.
          Su fitness (Calmar Ratio) es un dato objetivo derivado del historial de operaciones cerradas — no de lo que el agente afirma sobre sí mismo.
        </div>
      </div>

      <!-- Aislamiento LLM -->
      <div class="ins-card ins-card-left-emerald" style="margin-top:12px;">
        <div style="font-size:10px;color:{EMERALD};letter-spacing:1.5px;
                    text-transform:uppercase;font-weight:700;margin-bottom:10px;">
          ③ Aislamiento de los LLMs — cada agente habla con DeepSeek por separado
        </div>
        <div class="ins-body">
          Cada sub-agente invoca a DeepSeek de forma independiente con solo la información
          de mercado que le corresponde (RSI, noticias, señales). El prompt del sub-agente
          <b>nunca contiene</b> información sobre otros agentes, sobre el Juez, ni sobre
          criterios de eliminación. El LLM del Juez recibe únicamente métricas objetivas
          de rendimiento (ROI, win rate, drawdown) y no puede ser influenciado por el
          razonamiento de ningún sub-agente inversionista — son llamadas totalmente
          independientes al mismo modelo.
        </div>
      </div>

      <!-- No hay comunicación entre agentes -->
      <div class="ins-card ins-card-left-emerald" style="margin-top:12px;">
        <div style="font-size:10px;color:{EMERALD};letter-spacing:1.5px;
                    text-transform:uppercase;font-weight:700;margin-bottom:10px;">
          ④ Cero comunicación entre agentes inversionistas
        </div>
        <div class="ins-body">
          Los 10 agentes no se conocen entre sí. Cada uno toma sus decisiones de forma
          completamente independiente a partir de los mismos datos de mercado públicos.
          No existe ningún canal de comunicación, memoria compartida ni mecanismo de
          coordinación entre agentes. No pueden coaligarse, imitar estrategias ajenas
          en tiempo real ni sabotear las posiciones de sus competidores.
        </div>
      </div>

      <!-- Una posición a la vez -->
      <div class="ins-card ins-card-left-emerald" style="margin-top:12px;">
        <div style="font-size:10px;color:{EMERALD};letter-spacing:1.5px;
                    text-transform:uppercase;font-weight:700;margin-bottom:10px;">
          ⑤ Restricción secuencial — una posición abierta a la vez
        </div>
        <div class="ins-body">
          Cada agente solo puede tener <b>una posición BUY o SELL abierta simultáneamente</b>.
          Esta restricción es verificada en la base de datos antes de cada apertura.
          Un agente no puede acumular múltiples posiciones para multiplicar artificialmente
          su exposición ni distorsionar su P&L. La competencia es puramente sobre la calidad
          de cada decisión individual, no sobre el volumen de operaciones abiertas a la vez.
        </div>
      </div>

      <!-- Conclusión -->
      <div class="ins-card" style="margin-top:12px;border-left:3px solid {GOLD};">
        <div class="ins-body">
          <b>En resumen:</b> los agentes compiten en un entorno cerrado donde las únicas
          palancas disponibles son <em>cuándo entrar</em>, <em>en qué dirección</em> y
          <em>cuánto arriesgar</em>. El mercado es el árbitro imparcial. El Juez evalúa
          hechos, no intenciones. Ningún agente puede corromper el proceso evolutivo
          porque el proceso no depende de lo que los agentes afirman sobre sí mismos,
          sino de lo que el mercado hizo con sus posiciones.
        </div>
      </div>
    </div>

    <!-- ══ NOTA TÉCNICA ══ -->
    <div style="background:{CARD2};border:1px solid {BORDER};border-radius:8px;
                padding:14px 18px;font-size:11px;color:{DIM};line-height:1.8;">
      <b style="color:{GOLD};">Nota técnica:</b>
      Las velas OHLCV (5.600+ velas de 15min, 60 días) se obtienen de
      <b style="color:{TEXT};">Yahoo Finance</b> (gratuito, sin API key) — 1 descarga compartida
      por ciclo, cada agente calcula sus señales en memoria con sus propios genes.
      El volumen EUR/USD es siempre 0 (par OTC), por eso se usa el
      <b style="color:{TEXT};">Range Proxy</b> <code>(high−low) × 10 000</code> en pips
      como sustituto de VSA. El <b style="color:{TEXT};">ATR</b> (Wilder 14 períodos)
      se calcula sobre las mismas velas y sirve como base del SL dinámico
      (piso <b style="color:{TEXT};">10 pips</b> desde Sesión 16 — evita stops barridos por ruido de 1m).
      El <b style="color:{TEXT};">clasificador ADX</b> calcula el régimen del mercado
      (TENDENCIA / RANGO) una vez por ciclo y habilita/bloquea cada especie según corresponde.
      El <b style="color:{TEXT};">filtro HTF</b> descarga velas de <b style="color:{TEXT};">1h / 3 meses</b>
      y calcula EMA50/EMA200 para bloquear señales contra la tendencia principal.
      El razonamiento de los agentes y del Juez usa <b style="color:{TEXT};">DeepSeek</b>
      (<code>deepseek-reasoner</code>). El fitness (Expectancy ajustada por riesgo × confianza
      estadística) se calcula vía SQL sobre operaciones cerradas en
      <b style="color:{TEXT};">PostgreSQL — Supabase</b>.
      La verificación SL/TP usa <b style="color:{TEXT};">velas OHLC de 1 minuto</b>
      (≥15 velas/ciclo, cierre = nivel exacto SL/TP).
      <b style="color:{TEXT};">Fricción 1.4 pips round-trip</b> (spread + slippage) descontada
      de cada operación al cerrar — el P&L en la DB es siempre neto de costos.
      El <b style="color:{TEXT};">torneo de candidatos</b> backtestea 3 mutaciones por slot
      sobre los últimos 20 días de historia antes de desplegar al ganador (Sesión 16).
      Los workflows (monitor cada 15 min y juez diario lunes–viernes) corren en
      <b style="color:{TEXT};">GitHub Actions</b>, disparados externamente por
      <b style="color:{TEXT};">cron-job.org</b> con precisión ±5 segundos.
      Todos los timestamps están en hora <b style="color:{TEXT};">America/Bogota (UTC−5)</b>.
    </div>
    """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    _css()
    _header()

    estados_f, gens_f, n_logs = _sidebar()

    # Cargar datos
    with st.spinner("Sincronizando con Supabase..."):
        df_filtered = D.fetch_agents(estados=estados_f, gens=gens_f)
        df_active   = D.fetch_agents(estados=["activo"])
        df_all      = D.fetch_agents()
        df_logs     = D.fetch_judge_logs(limit=n_logs)
        df_ops      = D.fetch_operations()
        df_hist     = D.fetch_ranking_history()

    # KPIs globales
    _kpis(df_active, df_all)
    st.markdown("<br>", unsafe_allow_html=True)

    # Tabs principales
    tab_pop, tab_evo, tab_judge, tab_ops, tab_price, tab_ins = st.tabs([
        "📊  Población",
        "🧬  Evolución",
        "⚖️  Agente Juez",
        "💹  Operaciones",
        "📈  Precio",
        "📖  Instrucciones",
    ])

    with tab_pop:
        _tab_population(df_filtered)

    with tab_evo:
        _tab_evolution(df_all, df_hist)

    with tab_judge:
        _tab_judge(df_logs)

    with tab_ops:
        _tab_operations(df_ops)

    with tab_price:
        _tab_price(df_all)

    with tab_ins:
        _tab_instructions()


main()
