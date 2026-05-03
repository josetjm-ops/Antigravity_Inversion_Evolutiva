"""
Fábrica de gráficas Plotly para el Command Center.
Paleta oscura con acentos dorado / esmeralda / rojo.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

# ── Paleta ───────────────────────────────────────────────────────────────────
GOLD    = "#d4af37"
EMERALD = "#00c878"
RED     = "#e05252"
AMBER   = "#f59e0b"
BG      = "rgba(0,0,0,0)"
BORDER  = "#22223a"
TEXT    = "#e2e2e2"
DIM     = "#6a6a8a"

COLORSCALE_ROI = [
    [0.00, "#7a0e0e"],
    [0.28, "#cc3030"],
    [0.47, "#2a2a3a"],
    [0.53, "#2a2a3a"],
    [0.72, "#1a7744"],
    [1.00, "#00c878"],
]

_BASE = dict(
    paper_bgcolor=BG,
    plot_bgcolor=BG,
    font=dict(color=TEXT, family="'SF Mono','Fira Code',monospace", size=11),
    margin=dict(l=12, r=12, t=44, b=12),
)


def _empty(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
        showarrow=False, font=dict(color=DIM, size=13),
    )
    fig.update_layout(**_BASE, height=260)
    return fig


# ── 1. ROI Heatmap ────────────────────────────────────────────────────────────

def roi_heatmap(df: pd.DataFrame) -> go.Figure:
    """
    Matriz 2D: filas = generación · columnas = rank dentro de la generación.
    Verde → ROI positivo · Rojo → ROI negativo · Celda vacía = gris oscuro.
    """
    if df.empty:
        return _empty("Sin agentes para mostrar")

    df = df.copy()
    df["rank_gen"] = (
        df.groupby("generacion")["roi_total"]
        .rank(method="first", ascending=False)
        .astype(int)
    )

    gens     = sorted(df["generacion"].unique())
    max_rank = int(df["rank_gen"].max())

    z_mat, id_mat, est_mat = [], [], []
    for gen in gens:
        sub = df[df["generacion"] == gen].set_index("rank_gen")
        z_row, id_row, est_row = [], [], []
        for rank in range(1, max_rank + 1):
            if rank in sub.index:
                r = sub.loc[rank]
                if isinstance(r, pd.DataFrame):
                    r = r.iloc[0]
                z_row.append(float(r["roi_total"]))
                id_row.append(str(r["id"]))
                est_row.append(str(r["estado"]))
            else:
                z_row.append(None)
                id_row.append("")
                est_row.append("")
        z_mat.append(z_row)
        id_mat.append(id_row)
        est_mat.append(est_row)

    # Text annotations inside each cell
    text_mat = [
        [
            f"{id_mat[gi][ri]}<br>{z_mat[gi][ri]:+.2f}%" if id_mat[gi][ri] else ""
            for ri in range(max_rank)
        ]
        for gi in range(len(gens))
    ]

    # Mark eliminated agents with a dimmer opacity list
    # (Plotly Heatmap doesn't support per-cell opacity, so we overlay an annotation layer)
    fig = go.Figure(go.Heatmap(
        z=z_mat,
        x=[f"#{r}" for r in range(1, max_rank + 1)],
        y=[f"Gen {g}" for g in gens],
        text=text_mat,
        texttemplate="%{text}",
        textfont=dict(size=9, color="rgba(255,255,255,0.85)"),
        colorscale=COLORSCALE_ROI,
        zmid=0,
        hovertemplate=(
            "<b>%{y}  ·  Rank %{x}</b><br>"
            "ROI: <b>%{z:.4f}%</b><extra></extra>"
        ),
        colorbar=dict(
            title=dict(text="ROI %", font=dict(color=DIM, size=10)),
            tickfont=dict(color=DIM, size=9),
            bgcolor=BG,
            bordercolor=BORDER,
            tickformat=".2f",
            thickness=12,
            len=0.85,
        ),
        xgap=4,
        ygap=4,
    ))

    fig.update_layout(
        **_BASE,
        title=dict(
            text="Mapa de Rentabilidad — ROI por Agente",
            font=dict(color=GOLD, size=13),
            x=0,
        ),
        xaxis=dict(
            title=dict(text="Ranking dentro de Generación", font=dict(color=DIM, size=10)),
            tickfont=dict(color=DIM),
            showgrid=False,
        ),
        yaxis=dict(
            title=dict(text="Generación", font=dict(color=DIM, size=10)),
            tickfont=dict(color=DIM),
            showgrid=False,
        ),
        height=max(240, len(gens) * 90 + 130),
    )
    return fig


# ── 2. Curva de Supervivencia ─────────────────────────────────────────────────

def survival_curve(df: pd.DataFrame) -> go.Figure:
    """
    Scatter: fecha_nacimiento vs roi_total.
    Activos = esmeralda · Eliminados = rojo · Tamaño = operaciones.
    """
    if df.empty:
        return _empty("Sin datos de supervivencia")

    df = df.copy()
    df["fecha_nacimiento"] = pd.to_datetime(df["fecha_nacimiento"])
    df["sz"] = (df["operaciones_total"].clip(0, 30) + 2) * 2.5
    df["label"] = df.apply(
        lambda r: (
            f"<b>{r['id']}</b><br>"
            f"ROI: <b>{r['roi_total']:+.4f}%</b><br>"
            f"Gen: {r['generacion']}  ·  {r['estado']}<br>"
            f"Ops: {r['operaciones_total']}  ·  WR: {r['win_rate_pct']:.1f}%"
        ),
        axis=1,
    )

    fig = go.Figure()

    style_map = {
        "activo":   (EMERALD, "circle",     0.9),
        "eliminado": (RED,    "x",          0.65),
    }

    for estado, (color, symbol, opacity) in style_map.items():
        sub = df[df["estado"] == estado]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["fecha_nacimiento"],
            y=sub["roi_total"],
            mode="markers",
            name=estado.capitalize(),
            marker=dict(
                size=sub["sz"],
                color=color,
                opacity=opacity,
                symbol=symbol,
                line=dict(color="rgba(255,255,255,0.1)", width=1),
            ),
            text=sub["label"],
            hovertemplate="%{text}<extra></extra>",
        ))

    fig.add_hline(
        y=0,
        line_dash="dot",
        line_color=BORDER,
        line_width=1.5,
        annotation_text="ROI = 0 %",
        annotation_font_color=DIM,
        annotation_font_size=9,
        annotation_position="bottom right",
    )

    fig.update_layout(
        **_BASE,
        title=dict(
            text="Curva de Supervivencia — Longevidad vs. Rendimiento",
            font=dict(color=GOLD, size=13),
            x=0,
        ),
        xaxis=dict(
            title=dict(text="Fecha de Nacimiento", font=dict(color=DIM, size=10)),
            tickfont=dict(color=DIM),
            gridcolor=BORDER, showgrid=True, gridwidth=0.4,
            zeroline=False,
        ),
        yaxis=dict(
            title=dict(text="ROI Total (%)", font=dict(color=DIM, size=10)),
            tickfont=dict(color=DIM),
            gridcolor=BORDER, showgrid=True, gridwidth=0.4,
            tickformat=".2f",
        ),
        legend=dict(
            font=dict(color=DIM, size=10),
            bgcolor=BG,
            bordercolor=BORDER,
            x=0.01, y=0.99,
        ),
        height=360,
    )
    return fig


# ── 3. Rendimiento por Generación ─────────────────────────────────────────────

def generation_bars(df: pd.DataFrame) -> go.Figure:
    """Barras: ROI promedio ± rango por generación, con conteo de supervivientes."""
    if df.empty:
        return _empty("Sin datos por generación")

    stats = (
        df.groupby("generacion")
        .agg(
            roi_mean=("roi_total",   "mean"),
            roi_max=("roi_total",    "max"),
            roi_min=("roi_total",    "min"),
            n_total=("id",           "count"),
            n_activos=("estado",     lambda x: (x == "activo").sum()),
        )
        .reset_index()
    )

    bar_colors = [EMERALD if v >= 0 else RED for v in stats["roi_mean"]]

    fig = go.Figure()

    # Barra principal
    fig.add_trace(go.Bar(
        x=[f"Gen {g}" for g in stats["generacion"]],
        y=stats["roi_mean"],
        marker_color=bar_colors,
        marker_opacity=0.80,
        name="ROI Promedio",
        text=[f"{v:+.2f}%" for v in stats["roi_mean"]],
        textposition="outside",
        textfont=dict(size=10, color=TEXT),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "ROI medio: %{y:.4f}%<br>"
            "<extra></extra>"
        ),
    ))

    # Error bar mostrando rango min-max
    fig.add_trace(go.Scatter(
        x=[f"Gen {g}" for g in stats["generacion"]],
        y=stats["roi_mean"],
        mode="markers",
        marker=dict(color=GOLD, size=7, symbol="diamond"),
        error_y=dict(
            type="data",
            symmetric=False,
            array=(stats["roi_max"] - stats["roi_mean"]).clip(0),
            arrayminus=(stats["roi_mean"] - stats["roi_min"]).clip(0),
            color=GOLD,
            thickness=1.5,
            width=7,
        ),
        name="Rango (min-max)",
        customdata=list(zip(
            stats["roi_max"], stats["roi_min"],
            stats["n_activos"], stats["n_total"],
        )),
        hovertemplate=(
            "Max: %{customdata[0]:.4f}%<br>"
            "Min: %{customdata[1]:.4f}%<br>"
            "Activos: %{customdata[2]} / %{customdata[3]}<extra></extra>"
        ),
    ))

    fig.update_layout(
        **_BASE,
        title=dict(
            text="Rendimiento por Generación",
            font=dict(color=GOLD, size=13),
            x=0,
        ),
        xaxis=dict(tickfont=dict(color=DIM), showgrid=False),
        yaxis=dict(
            tickfont=dict(color=DIM),
            gridcolor=BORDER, showgrid=True, gridwidth=0.4,
            tickformat=".2f",
        ),
        legend=dict(font=dict(color=DIM, size=9), bgcolor=BG),
        bargap=0.45,
        height=300,
    )
    return fig


# ── 4. Capital acumulado en el tiempo (ranking_history) ──────────────────────

def capital_timeline(df_hist: pd.DataFrame) -> go.Figure:
    """
    Líneas: capital_fin_dia por agente a lo largo del tiempo.
    Sólo muestra agentes activos en la última fecha disponible.
    """
    if df_hist.empty:
        return _empty("Sin historial de capital disponible")

    last_active = (
        df_hist[df_hist["evento"].isin(["supervivencia", "evaluacion", "nacimiento"])]
        ["agente_id"]
        .unique()
    )
    plot_df = df_hist[df_hist["agente_id"].isin(last_active)].copy()

    n_agents = plot_df["agente_id"].nunique()
    palette  = px.colors.sample_colorscale(
        "Viridis", [i / max(n_agents - 1, 1) for i in range(n_agents)]
    )

    fig = go.Figure()
    for idx, (agent_id, sub) in enumerate(plot_df.groupby("agente_id")):
        sub = sub.sort_values("fecha")
        fig.add_trace(go.Scatter(
            x=sub["fecha"],
            y=sub["capital_fin_dia"],
            mode="lines+markers",
            name=agent_id,
            line=dict(color=palette[idx % len(palette)], width=1.5),
            marker=dict(size=5),
            hovertemplate=(
                f"<b>{agent_id}</b><br>"
                "Capital: $%{y:.4f}<br>"
                "Fecha: %{x}<extra></extra>"
            ),
        ))

    fig.add_hline(y=10.0, line_dash="dot", line_color=GOLD,
                  line_width=1, annotation_text="Capital inicial $10",
                  annotation_font_color=DIM, annotation_font_size=9)

    fig.update_layout(
        **_BASE,
        title=dict(
            text="Evolución de Capital por Agente",
            font=dict(color=GOLD, size=13),
            x=0,
        ),
        xaxis=dict(tickfont=dict(color=DIM), gridcolor=BORDER, showgrid=True, gridwidth=0.4),
        yaxis=dict(
            title=dict(text="Capital (USD)", font=dict(color=DIM, size=10)),
            tickfont=dict(color=DIM),
            gridcolor=BORDER, showgrid=True, gridwidth=0.4,
            tickformat="$.4f",
        ),
        legend=dict(font=dict(color=DIM, size=8), bgcolor=BG, bordercolor=BORDER),
        showlegend=(n_agents <= 15),
        height=320,
    )
    return fig


# ── 5. Distribución de PnL ────────────────────────────────────────────────────

def pnl_distribution(df: pd.DataFrame) -> go.Figure:
    """Histograma de PnL con línea de referencia en 0."""
    if df.empty or df["pnl"].isna().all():
        return _empty("Sin operaciones con PnL registrado")

    pnl = df["pnl"].dropna()
    pos = pnl[pnl >= 0]
    neg = pnl[pnl <  0]

    fig = go.Figure()
    if not pos.empty:
        fig.add_trace(go.Histogram(
            x=pos, nbinsx=15,
            marker_color=EMERALD, opacity=0.80,
            name="Ganadora",
            hovertemplate="PnL: $%{x:.4f}<br>Count: %{y}<extra></extra>",
        ))
    if not neg.empty:
        fig.add_trace(go.Histogram(
            x=neg, nbinsx=15,
            marker_color=RED, opacity=0.80,
            name="Perdedora",
            hovertemplate="PnL: $%{x:.4f}<br>Count: %{y}<extra></extra>",
        ))

    fig.add_vline(x=0, line_dash="dot", line_color=GOLD, line_width=1.5)
    fig.update_layout(
        **_BASE,
        title=dict(text="Distribución de PnL", font=dict(color=GOLD, size=12), x=0),
        xaxis=dict(
            title=dict(text="PnL (USD)", font=dict(color=DIM, size=10)),
            tickfont=dict(color=DIM),
            tickformat="$.4f",
        ),
        yaxis=dict(title=dict(text="Frecuencia", font=dict(color=DIM, size=10)), tickfont=dict(color=DIM)),
        barmode="overlay",
        legend=dict(font=dict(color=DIM, size=9), bgcolor=BG),
        height=240,
    )
    return fig


# ── 6. Gauge de Win Rate ──────────────────────────────────────────────────────

def win_rate_gauge(win_rate: float) -> go.Figure:
    color = EMERALD if win_rate >= 50 else (AMBER if win_rate >= 35 else RED)
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=win_rate,
        number=dict(suffix="%", font=dict(color=TEXT, size=32)),
        delta=dict(
            reference=50,
            valueformat=".1f",
            suffix="%",
            increasing=dict(color=EMERALD),
            decreasing=dict(color=RED),
        ),
        gauge=dict(
            axis=dict(
                range=[0, 100],
                tickfont=dict(color=DIM, size=9),
                tickcolor=BORDER,
            ),
            bar=dict(color=color, thickness=0.25),
            bgcolor="rgba(0,0,0,0)",
            bordercolor=BORDER,
            steps=[
                dict(range=[0,   35], color="#2a0f0f"),
                dict(range=[35,  50], color="#2a2010"),
                dict(range=[50, 100], color="#0f2a18"),
            ],
            threshold=dict(
                line=dict(color=GOLD, width=2),
                thickness=0.75,
                value=50,
            ),
        ),
        title=dict(text="Win Rate Global", font=dict(color=DIM, size=11)),
        domain=dict(x=[0, 1], y=[0, 1]),
    ))
    fig.update_layout(
        **{**_BASE, "margin": dict(l=20, r=20, t=30, b=10)},
        height=220,
    )
    return fig
