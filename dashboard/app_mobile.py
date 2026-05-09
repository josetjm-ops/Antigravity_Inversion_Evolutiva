"""
INVERSIÓN EVOLUTIVA — Mobile Dashboard
Streamlit dashboard: mobile-first, dark luxury theme · gold / emerald palette.
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

# ═══════════════════════════════════════════════════════════════════════════════
# PALETA Y CONSTANTES (Algorithmic Prestige)
# ═══════════════════════════════════════════════════════════════════════════════
GOLD    = "#D4AF37"
EMERALD = "#50C878"
RED     = "#E05252"
BG      = "#0A0A0A"
CARD    = "#141414"
BORDER  = "#2A2A2A"
TEXT    = "#FFFFFF"
DIM     = "#888888"

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Inversion Evolutiva",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CSS GLOBAL MOBILE-FIRST
# ═══════════════════════════════════════════════════════════════════════════════
def _css() -> None:
    st.markdown(f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Hanken+Grotesk:wght@400;700;800&display=swap');

      /* ── Fondo y texto base ───────────────────────────────── */
      .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
          background-color: {BG};
          color: {TEXT};
          font-family: 'Inter', sans-serif;
      }}
      [data-testid="stHeader"]  {{ display: none; }}
      [data-testid="stToolbar"] {{ display: none; }}
      [data-testid="stSidebar"] {{ display: none; }} /* Ocultamos sidebar en móvil */

      /* Reducir padding general para aprovechar pantalla móvil */
      .main .block-container {{
          padding: 1rem 0.8rem 4rem 0.8rem !important;
          max-width: 500px !important;
          margin: 0 auto;
      }}

      h1, h2, h3, .hanken {{
          font-family: 'Hanken Grotesk', sans-serif !important;
      }}

      /* ── Tabs (Main Navigation) ────────────────────────────── */
      [data-testid="stTabs"] [role="tablist"] {{
          border-bottom: 1px solid {BORDER};
          gap: 0;
          justify-content: center;
      }}
      [data-testid="stTabs"] [role="tab"] {{
          color: {DIM};
          border: none;
          background: transparent;
          font-size: 14px;
          font-weight: 600;
          letter-spacing: 0.5px;
          padding: 14px 20px;
          border-radius: 0;
          flex: 1;
          text-align: center;
      }}
      [data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
          color: {GOLD} !important;
          border-bottom: 3px solid {GOLD} !important;
      }}

      /* ── Tarjetas y Componentes Custom ──────────────────────── */
      .m-card {{
          background: {CARD};
          border: 1px solid {BORDER};
          border-radius: 12px;
          padding: 16px;
          margin-bottom: 16px;
      }}
      .m-card-gold {{ border-left: 4px solid {GOLD}; }}
      .m-card-emerald {{ border-left: 4px solid {EMERALD}; }}
      .m-card-red {{ border-left: 4px solid {RED}; }}

      .m-label {{
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 1.5px;
          text-transform: uppercase;
          color: {DIM};
          margin-bottom: 4px;
      }}
      .m-value {{
          font-size: 24px;
          font-weight: 800;
          font-family: 'Hanken Grotesk', sans-serif;
          color: {TEXT};
      }}
      .m-value-gold {{ color: {GOLD}; }}
      .m-value-emerald {{ color: {EMERALD}; }}
      .m-value-red {{ color: {RED}; }}

      /* ── DataFrames ───────────────────────────────────────── */
      [data-testid="stDataFrameContainer"] {{
          border: 1px solid {BORDER};
          border-radius: 8px;
      }}
      .stDataFrame thead tr th {{
          background: {CARD} !important;
          color: {DIM} !important;
          font-size: 11px;
      }}
    </style>
    """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# VISTA: INICIO (COMMAND CENTER)
# ═══════════════════════════════════════════════════════════════════════════════
def _view_inicio(df_active: pd.DataFrame, df_ops: pd.DataFrame, status: dict) -> None:
    # --- Cálculos ---
    # Capital Total = suma del capital actual de los agentes activos
    cap_total = float(df_active["capital_actual"].sum()) if not df_active.empty else 0.0
    
    # Operaciones Abiertas
    ops_abiertas = df_ops[df_ops["estado"] == "abierta"].copy() if not df_ops.empty and "estado" in df_ops.columns else pd.DataFrame()
    pnl_abierto = float(ops_abiertas["pnl"].sum()) if not ops_abiertas.empty else 0.0

    # Mejores métricas
    best_fitness = float(df_active["fitness_score"].max()) if not df_active.empty and "fitness_score" in df_active.columns else 0.0
    
    t_ops = int(df_active["operaciones_total"].sum()) if not df_active.empty else 0
    t_won = int(df_active["operaciones_ganadoras"].sum()) if not df_active.empty else 0
    wr_global = (t_won / t_ops * 100) if t_ops > 0 else 0.0

    # --- Header App ---
    st.markdown(f"""
    <div style="text-align:center; padding: 10px 0 20px 0;">
        <div style="font-size:12px; color:{DIM}; letter-spacing:3px; text-transform:uppercase; margin-bottom:4px;">
            Inversión Evolutiva
        </div>
        <div class="hanken" style="font-size:32px; font-weight:800; color:{GOLD}; line-height:1;">
            ${cap_total:,.2f}
        </div>
        <div style="font-size:11px; color:{DIM}; margin-top:4px;">
            CAPITAL TOTAL PORTAFOLIO
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- Grid de Métricas ---
    c1, c2 = st.columns(2)
    pnl_color_class = "m-value-emerald" if pnl_abierto >= 0 else "m-value-red"
    pnl_sign = "+" if pnl_abierto >= 0 else ""
    
    with c1:
        st.markdown(f"""
        <div class="m-card">
            <div class="m-label">PnL Abierto</div>
            <div class="{pnl_color_class}" style="font-size:20px; font-weight:700;">{pnl_sign}${pnl_abierto:.2f}</div>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="m-card">
            <div class="m-label">Operaciones</div>
            <div class="m-value" style="font-size:20px;">{len(ops_abiertas)} <span style="font-size:12px;color:{DIM};font-weight:400;">Activas</span></div>
        </div>
        """, unsafe_allow_html=True)

    c3, c4 = st.columns(2)
    with c3:
        st.markdown(f"""
        <div class="m-card">
            <div class="m-label">Win Rate</div>
            <div class="m-value" style="font-size:20px;">{wr_global:.1f}%</div>
        </div>
        """, unsafe_allow_html=True)
    with c4:
        st.markdown(f"""
        <div class="m-card">
            <div class="m-label">Top Fitness</div>
            <div class="m-value-gold" style="font-size:20px;">{best_fitness:.4f}</div>
        </div>
        """, unsafe_allow_html=True)

    # --- Tabla de Operaciones Activas ---
    st.markdown('<div style="font-size:14px; font-weight:700; color:#FFF; margin: 24px 0 12px 0;">Operaciones Activas</div>', unsafe_allow_html=True)

    if ops_abiertas.empty:
        st.markdown(f"""
        <div class="m-card" style="text-align:center; padding: 30px 16px;">
            <div style="color:{DIM}; font-size:14px;">No hay operaciones abiertas en este momento.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Preparar dataframe para mostrar
        cols_disp = ["id", "accion", "precio_entrada", "stop_loss", "take_profit", "pnl"]
        
        # Filtrar columnas que existen
        exist_cols = [c for c in cols_disp if c in ops_abiertas.columns]
        disp_ops = ops_abiertas[exist_cols].copy()
        
        # Renombrar para display
        rename_map = {
            "id": "ID", "accion": "Tipo", "precio_entrada": "Entrada",
            "stop_loss": "SL", "take_profit": "TP", "pnl": "PnL $"
        }
        disp_ops.rename(columns=rename_map, inplace=True)
        
        if "PnL $" in disp_ops.columns:
            disp_ops["PnL $"] = disp_ops["PnL $"].round(2)

        st.dataframe(
            disp_ops,
            use_container_width=True,
            hide_index=True,
            column_config={{
                "PnL $": st.column_config.NumberColumn(format="$%.2f"),
                "Entrada": st.column_config.NumberColumn(format="%.4f"),
                "SL": st.column_config.NumberColumn(format="%.4f"),
                "TP": st.column_config.NumberColumn(format="%.4f")
            }}
        )

# ═══════════════════════════════════════════════════════════════════════════════
# VISTA: AGENTES (POBLACIÓN / GENEALOGÍA)
# ═══════════════════════════════════════════════════════════════════════════════
def _view_agentes(df_active: pd.DataFrame, df_all: pd.DataFrame) -> None:
    st.markdown('<div style="font-size:18px; font-weight:800; color:#FFF; margin-bottom: 16px; text-align:center;">Agentes Activos</div>', unsafe_allow_html=True)
    
    tab_rank, tab_gen = st.tabs(["🏆 Ranking", "🧬 Genealogía"])

    with tab_rank:
        if df_active.empty:
            st.info("No hay agentes activos.")
        else:
            # Ordenar por fitness
            df_rank = df_active.copy()
            if "fitness_score" in df_rank.columns:
                df_rank = df_rank.sort_values(by="fitness_score", ascending=False)
            
            for _, agt in df_rank.iterrows():
                fit = agt.get("fitness_score", 0.0)
                roi = agt.get("roi_total", 0.0)
                wr = agt.get("win_rate_pct", 0.0)
                gen = agt.get("generacion", 1)
                
                roi_color = EMERALD if roi >= 0 else RED
                roi_sign = "+" if roi >= 0 else ""

                st.markdown(f"""
                <div class="m-card" style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div style="font-size:12px; color:{DIM}; font-weight:600; margin-bottom:2px;">GEN {gen}</div>
                        <div style="font-size:16px; font-weight:700; color:{TEXT}; margin-bottom:6px;">{agt['id']}</div>
                        <div style="display:flex; gap:12px; font-size:12px;">
                            <div><span style="color:{DIM};">ROI:</span> <span style="color:{roi_color}; font-weight:600;">{roi_sign}{roi:.2f}%</span></div>
                            <div><span style="color:{DIM};">WR:</span> <span style="color:{TEXT};">{wr:.1f}%</span></div>
                        </div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:10px; color:{GOLD}; letter-spacing:1px; text-transform:uppercase;">Fitness</div>
                        <div class="hanken" style="font-size:22px; font-weight:800; color:{GOLD};">{fit:.4f}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

    with tab_gen:
        children = df_all[df_all["padre_1_id"].notna()].copy()
        if children.empty:
            st.markdown(f"""
            <div class="m-card" style="text-align:center;">
                <div style="color:{DIM}; font-size:14px;">Aún no hay cruces genéticos.</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            df_gen = children.sort_values(by="generacion", ascending=False)
            for _, agt in df_gen.iterrows():
                p1 = agt.get("padre_1_id", "Desconocido")
                p2 = agt.get("padre_2_id", "Desconocido")
                gen = agt.get("generacion", 2)
                estado = agt.get("estado", "activo")
                
                estado_color = EMERALD if estado == "activo" else RED
                
                st.markdown(f"""
                <div class="m-card">
                    <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                        <span style="font-size:11px; font-weight:700; color:{GOLD};">GEN {gen}</span>
                        <span style="font-size:10px; font-weight:700; color:{estado_color}; text-transform:uppercase; padding:2px 6px; border:1px solid {estado_color}; border-radius:4px;">{estado}</span>
                    </div>
                    <div style="font-size:16px; font-weight:700; margin-bottom:12px; color:{TEXT};">{agt['id']}</div>
                    <div style="background:{BG}; padding:10px; border-radius:6px; font-size:12px; border:1px solid {BORDER};">
                        <div style="color:{DIM}; margin-bottom:4px;">🧬 Padre 1: <span style="color:{TEXT}; font-weight:600;">{p1}</span></div>
                        <div style="color:{DIM};">🧬 Padre 2: <span style="color:{TEXT}; font-weight:600;">{p2}</span></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    _css()

    # Cargar datos
    with st.spinner("Sincronizando..."):
        df_active = D.fetch_agents(estados=["activo"])
        df_all    = D.fetch_agents()
        df_ops    = D.fetch_operations()
        status    = D.fetch_system_status()

    # Navegación principal
    main_tab_inicio, main_tab_agentes = st.tabs(["📱 Inicio", "🤖 Agentes"])

    with main_tab_inicio:
        _view_inicio(df_active, df_ops, status)

    with main_tab_agentes:
        _view_agentes(df_active, df_all)


if __name__ == "__main__":
    main()
