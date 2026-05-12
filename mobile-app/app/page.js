"use client";
import { useState, useEffect, useCallback } from "react";

// ═══════════════════════════════════════════════════════════════════════
// LOADING SCREEN
// ═══════════════════════════════════════════════════════════════════════
function LoadingScreen() {
  return (
    <div className="loading-screen">
      <div className="loading-pulse" />
      <div className="loading-text">Inversion Evolutiva</div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// CAPITAL EVOLUTION CHART (SVG Area Chart)
// ═══════════════════════════════════════════════════════════════════════
function CapitalChart({ history, currentCapital }) {
  if (!history || history.length === 0) {
    return (
      <div className="chart-container" style={{ marginBottom: 24 }}>
        <div className="chart-title">Evolución del Capital</div>
        <div style={{ textAlign: "center", color: "var(--dim)", fontSize: 13, padding: "20px 0" }}>
          Datos de historial no disponibles aún.
        </div>
      </div>
    );
  }

  // Build data points: history + today's live value
  const points = history.map((h) => ({
    date: h.fecha,
    value: h.capital_total,
  }));

  // Add today's live capital as the last point
  const today = new Date().toISOString().split("T")[0];
  const lastHistDate = points[points.length - 1]?.date;
  if (lastHistDate !== today) {
    points.push({ date: today, value: currentCapital });
  }

  const values = points.map((p) => p.value);
  const minVal = Math.min(...values) * 0.998;
  const maxVal = Math.max(...values) * 1.002;
  const range = maxVal - minVal || 1;

  // SVG dimensions
  const W = 400;
  const H = 140;
  const padL = 0;
  const padR = 0;
  const padT = 8;
  const padB = 4;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;

  // Compute SVG coordinates
  const coords = points.map((p, i) => ({
    x: padL + (i / (points.length - 1 || 1)) * chartW,
    y: padT + chartH - ((p.value - minVal) / range) * chartH,
  }));

  // Build polyline and area paths
  const linePath = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x},${c.y}`).join(" ");
  const areaPath = `${linePath} L${coords[coords.length - 1].x},${H} L${coords[0].x},${H} Z`;

  // Trend: compare first vs last
  const trend = values[values.length - 1] - values[0];
  const trendColor = trend >= 0 ? "var(--emerald)" : "var(--red)";
  const trendSign = trend >= 0 ? "+" : "";

  // Date labels (show first, middle, last)
  const formatDate = (d) => {
    const parts = d.split("-");
    return `${parts[1]}/${parts[2]}`;
  };
  const dateLabels = [];
  if (points.length >= 3) {
    dateLabels.push({ idx: 0, label: formatDate(points[0].date) });
    const mid = Math.floor(points.length / 2);
    dateLabels.push({ idx: mid, label: formatDate(points[mid].date) });
    dateLabels.push({ idx: points.length - 1, label: formatDate(points[points.length - 1].date) });
  } else {
    points.forEach((p, i) => dateLabels.push({ idx: i, label: formatDate(p.date) }));
  }

  return (
    <div className="chart-container" style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div className="chart-title" style={{ margin: 0 }}>Evolución del Capital</div>
        <div style={{ fontSize: 12, fontWeight: 700, color: trendColor }}>
          {trendSign}${Math.abs(trend).toFixed(2)}
        </div>
      </div>

      {/* Y-axis labels */}
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--dim)", marginBottom: 4 }}>
        <span>${maxVal.toFixed(2)}</span>
        <span>${minVal.toFixed(2)}</span>
      </div>

      {/* SVG Chart */}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" className="capital-svg">
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--gold)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--gold)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {/* Grid lines */}
        <line x1={padL} y1={padT} x2={W} y2={padT} stroke="var(--border)" strokeWidth="0.5" />
        <line x1={padL} y1={padT + chartH / 2} x2={W} y2={padT + chartH / 2} stroke="var(--border)" strokeWidth="0.5" strokeDasharray="4 4" />
        <line x1={padL} y1={H - padB} x2={W} y2={H - padB} stroke="var(--border)" strokeWidth="0.5" />
        {/* Area fill */}
        <path d={areaPath} fill="url(#areaGrad)" />
        {/* Line */}
        <path d={linePath} fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {/* Last point dot */}
        <circle cx={coords[coords.length - 1].x} cy={coords[coords.length - 1].y} r="3.5" fill="var(--gold)" />
      </svg>

      {/* X-axis date labels */}
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--dim)", marginTop: 6, paddingLeft: 2, paddingRight: 2 }}>
        {dateLabels.map((dl) => (
          <span key={dl.idx}>{dl.label}</span>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// INICIO VIEW (COMMAND CENTER)
// ═══════════════════════════════════════════════════════════════════════
function InicioView({ data }) {
  const { status, openOperations } = data;
  const pnlColor = status.openPnl >= 0 ? "emerald" : "red";
  const pnlSign = status.openPnl >= 0 ? "+" : "";

  return (
    <div className="fade-in">
      {/* Hero: Capital Total */}
      <div className="hero">
        <div className="hero-value">${status.totalCapital.toFixed(4)}</div>
        <div className="hero-sub">Capital Total Portafolio</div>
      </div>

      {/* Grid de Métricas */}
      <div className="metric-grid">
        <div className="metric-card">
          <div className="metric-label">PnL Abierto</div>
          <div className={`metric-value ${pnlColor}`}>
            {pnlSign}${status.openPnl.toFixed(2)}
          </div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Operaciones</div>
          <div className="metric-value">
            {status.openOpsCount}
            <span className="metric-suffix">Activas</span>
          </div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Win Rate</div>
          <div className="metric-value">{status.winRate.toFixed(1)}%</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Top Fitness</div>
          <div className="metric-value gold">
            {status.bestFitness.toFixed(4)}
          </div>
        </div>
      </div>

      {/* Gráfica de Evolución del Capital */}
      <CapitalChart history={data.capitalHistory || []} currentCapital={status.totalCapital} />

      {/* Operaciones Activas */}
      <div className="section-title">Operaciones Activas</div>
      {openOperations.length === 0 ? (
        <div className="empty-state">
          <div className="empty-text">
            No hay operaciones abiertas en este momento.
          </div>
        </div>
      ) : (
        openOperations.map((op) => (
          <div className="op-card" key={op.id}>
            <div className="op-header">
              <div className="op-id">{op.id}</div>
              <div className={`op-type ${op.accion?.toLowerCase()}`}>
                {op.accion}
              </div>
            </div>
            <div className="op-details">
              <div>
                <div className="op-detail-label">Entrada</div>
                <div className="op-detail-value">
                  {op.precio_entrada?.toFixed(4) ?? "—"}
                </div>
              </div>
              <div>
                <div className="op-detail-label">SL</div>
                <div className="op-detail-value">
                  {op.stop_loss?.toFixed(4) ?? "—"}
                </div>
              </div>
              <div>
                <div className="op-detail-label">TP</div>
                <div className="op-detail-value">
                  {op.take_profit?.toFixed(4) ?? "—"}
                </div>
              </div>
              <div>
                <div className="op-detail-label">PnL</div>
                <div
                  className="op-detail-value"
                  style={{
                    color:
                      (op.pnl || 0) >= 0
                        ? "var(--emerald)"
                        : "var(--red)",
                  }}
                >
                  {(op.pnl || 0) >= 0 ? "+" : ""}${(op.pnl || 0).toFixed(2)}
                </div>
              </div>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// AGENTES VIEW (RANKING + GENEALOGÍA)
// ═══════════════════════════════════════════════════════════════════════
function AgentesView({ data }) {
  const [tab, setTab] = useState("ranking");
  const { activeAgents, genealogy } = data;

  return (
    <div className="fade-in">
      <div className="page-title">Agentes Activos</div>

      {/* Sub-tabs */}
      <div className="tabs">
        <button
          className={`tab ${tab === "ranking" ? "active" : ""}`}
          onClick={() => setTab("ranking")}
        >
          🏆 Ranking
        </button>
        <button
          className={`tab ${tab === "genealogia" ? "active" : ""}`}
          onClick={() => setTab("genealogia")}
        >
          🧬 Genealogía
        </button>
      </div>

      {tab === "ranking" ? (
        <RankingList agents={activeAgents} />
      ) : (
        <GenealogyList agents={genealogy} />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// FITNESS BY GENERATION CHART
// ═══════════════════════════════════════════════════════════════════════
function FitnessByGenChart({ agents }) {
  // Calcular el mejor fitness por generación
  const genMap = {};
  agents.forEach((agt) => {
    const gen = agt.generacion;
    if (!genMap[gen] || agt.fitness_score > genMap[gen]) {
      genMap[gen] = agt.fitness_score;
    }
  });

  const gens = Object.keys(genMap)
    .map(Number)
    .sort((a, b) => a - b);
  const maxFitness = Math.max(...Object.values(genMap), 0.001);

  if (gens.length === 0) return null;

  return (
    <div className="chart-container">
      <div className="chart-title">Mejor Fitness por Generación</div>
      <div className="chart-area">
        {/* Y-axis labels */}
        <div className="chart-y-axis">
          <span>{maxFitness.toFixed(2)}</span>
          <span>{(maxFitness / 2).toFixed(2)}</span>
          <span>0</span>
        </div>
        {/* Bars */}
        <div className="chart-bars">
          {gens.map((gen) => {
            const val = genMap[gen];
            const pct = maxFitness > 0 ? (val / maxFitness) * 100 : 0;
            return (
              <div className="chart-bar-col" key={gen}>
                <div className="chart-bar-value">{val.toFixed(4)}</div>
                <div className="chart-bar-track">
                  <div
                    className="chart-bar-fill"
                    style={{ height: `${Math.max(pct, 2)}%` }}
                  />
                </div>
                <div className="chart-bar-label">Gen {gen}</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function RankingList({ agents }) {
  if (agents.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-text">No hay agentes activos.</div>
      </div>
    );
  }

  return (
    <>
      <FitnessByGenChart agents={agents} />
      <div className="section-title" style={{ marginTop: 8 }}>Ranking de Agentes</div>
      {agents.map((agt) => {

        const roiColor = agt.roi_total >= 0 ? "var(--emerald)" : "var(--red)";
        const roiSign = agt.roi_total >= 0 ? "+" : "";

        return (
          <div className="agent-card" key={agt.id}>
            <div className="agent-info">
              <div className="agent-gen">GEN {agt.generacion}</div>
              <div className="agent-id">{agt.id}</div>
              <div className="agent-stats">
                <div>
                  <span className="agent-stat-label">ROI:</span>
                  <span className="agent-stat-value" style={{ color: roiColor }}>
                    {roiSign}{agt.roi_total.toFixed(2)}%
                  </span>
                </div>
                <div>
                  <span className="agent-stat-label">WR:</span>
                  <span className="agent-stat-value">{agt.win_rate_pct.toFixed(1)}%</span>
                </div>
              </div>
            </div>
            <div className="agent-fitness">
              <div className="fitness-label">Fitness</div>
              <div className="fitness-value">{agt.fitness_score.toFixed(4)}</div>
            </div>
          </div>
        );
      })}
    </>
  );
}

function GenealogyList({ agents }) {
  if (agents.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-text">Aún no hay cruces genéticos.</div>
      </div>
    );
  }

  // Calculate summary
  let improvements = 0;
  let declines = 0;

  const crosses = agents.map(agt => {
    const p1Fit = agt.padre_1_fitness || 0;
    const p2Fit = agt.padre_2_fitness || 0;
    const bestParentFit = Math.max(p1Fit, p2Fit);
    const childFit = agt.fitness_score || 0;
    
    // Improvement logic
    const isImprovement = childFit > bestParentFit;
    
    // Delta percentage calculation
    let delta = 0;
    if (bestParentFit !== 0) {
      delta = ((childFit - bestParentFit) / Math.abs(bestParentFit)) * 100;
    } else {
      delta = childFit > 0 ? 100 : (childFit < 0 ? -100 : 0);
    }
    
    if (isImprovement) improvements++;
    else declines++;

    return { ...agt, bestParentFit, isImprovement, delta, p1Fit, p2Fit };
  });

  const successRate = agents.length > 0 ? (improvements / agents.length) * 100 : 0;

  return (
    <div className="genealogy-container fade-in">
      {/* Summary Bar */}
      <div className="evo-summary">
        <div className="evo-summary-title">Resumen Evolutivo</div>
        <div className="evo-summary-stats">
          <span>Cruces: {agents.length}</span>
          <span className="stat-improved">Mejoras: {improvements}</span>
          <span className="stat-declined">Declives: {declines}</span>
        </div>
        <div className="evo-progress-bg">
          <div className="evo-progress-fill" style={{ width: `${successRate}%` }}></div>
        </div>
        <div className="evo-success-rate">{successRate.toFixed(1)}% de éxito</div>
      </div>

      {/* DNA Cards */}
      {crosses.map((agt) => (
        <div className={`dna-card ${agt.isImprovement ? "improved" : "declined"}`} key={agt.id}>
          {/* Parents Row */}
          <div className="dna-parents">
            <div className="dna-parent">
              <div className="dna-parent-label">PADRE 1 (Gen {agt.generacion - 1})</div>
              <div className="dna-parent-id">{agt.padre_1_id?.substring(0, 13)}</div>
              <div className="dna-parent-fit">Fit: {agt.p1Fit.toFixed(4)}</div>
            </div>
            <div className="dna-parent">
              <div className="dna-parent-label">PADRE 2 (Gen {agt.generacion - 1})</div>
              <div className="dna-parent-id">{agt.padre_2_id ? agt.padre_2_id.substring(0, 13) : "—"}</div>
              <div className="dna-parent-fit">Fit: {agt.p2Fit.toFixed(4)}</div>
            </div>
          </div>
          
          {/* Connector */}
          <div className="dna-connector">
            <div className="dna-line"></div>
            <div className="dna-arrow">▼</div>
          </div>

          {/* Child Box */}
          <div className="dna-child">
            <div className="dna-child-header">
              <span className="dna-child-gen">GEN {agt.generacion}</span>
              <span className={`status-badge ${agt.estado}`}>{agt.estado}</span>
            </div>
            <div className="dna-child-id">{agt.id}</div>
            
            <div className="dna-child-metrics">
              <div className="dna-child-fitness">
                <span className="label">Fitness</span>
                <span className="value">{agt.fitness_score.toFixed(4)}</span>
              </div>
              <div className={`dna-delta ${agt.isImprovement ? "positive" : "negative"}`}>
                <span className="delta-icon">{agt.isImprovement ? "▲" : "▼"}</span>
                <span className="delta-val">{agt.delta > 0 ? "+" : ""}{agt.delta.toFixed(1)}%</span>
                <span className="delta-desc">vs mejor padre</span>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// BOTTOM NAVIGATION
// ═══════════════════════════════════════════════════════════════════════
function BottomNav({ view, setView }) {
  return (
    <nav className="bottom-nav">
      <div className="nav-inner">
        <button
          className={`nav-item ${view === "inicio" ? "active" : ""}`}
          onClick={() => setView("inicio")}
        >
          <span className="nav-icon">📊</span>
          <span className="nav-label">Inicio</span>
        </button>
        <button
          className={`nav-item ${view === "agentes" ? "active" : ""}`}
          onClick={() => setView("agentes")}
        >
          <span className="nav-icon">🤖</span>
          <span className="nav-label">Agentes</span>
        </button>
      </div>
    </nav>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════
export default function Home() {
  const [view, setView] = useState("inicio");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = useCallback(async (silent = false) => {
    try {
      if (!silent) setRefreshing(true);
      const res = await fetch("/api/dashboard");
      const json = await res.json();
      if (!json.error) setData(json);
    } catch (err) {
      console.error("Fetch error:", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(() => fetchData(true), 60000);
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading || !data) return <LoadingScreen />;

  return (
    <>
      <button
        className={`refresh-btn ${refreshing ? "spinning" : ""}`}
        onClick={() => fetchData()}
        title="Actualizar"
      >
        ⟳
      </button>

      <div className="app">
        <header className="app-header">
          <img src="/icon-192.png" alt="Logo" className="app-logo" />
          <span className="app-header-title">INVERSIÓN EVOLUTIVA</span>
        </header>

        {view === "inicio" ? (
          <InicioView data={data} />
        ) : (
          <AgentesView data={data} />
        )}
      </div>

      <BottomNav view={view} setView={setView} />
    </>
  );
}
