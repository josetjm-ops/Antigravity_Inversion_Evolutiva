"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

const money = (value, digits = 2) => `$${Number(value || 0).toFixed(digits)}`;
const pct = (value, digits = 1) => `${Number(value || 0).toFixed(digits)}%`;
const fmtPrice = (value) => (value ? Number(value).toFixed(5) : "--");
const fmtNum = (value, digits = 2) => (value === null || value === undefined ? "--" : Number(value).toFixed(digits));
const fmtDateTime = (value) => (value ? new Date(value).toLocaleString("es-CO") : "--");

function LoadingScreen() {
  return (
    <div className="loading-screen">
      <img src="/icon-192.png" alt="" className="loading-logo" />
      <div className="loading-text">Inversion Evolutiva</div>
    </div>
  );
}

function EmptyState({ children }) {
  return (
    <div className="empty-state">
      <div className="empty-text">{children}</div>
    </div>
  );
}

function KpiCard({ label, value, tone, suffix }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${tone || ""}`}>
        {value}
        {suffix ? <span className="metric-suffix">{suffix}</span> : null}
      </div>
    </div>
  );
}

function CapitalChart({ history, currentCapital }) {
  const points = useMemo(() => {
    const base = (history || []).map((h) => ({
      date: h.fecha,
      value: Number(h.capital_total || 0),
    }));
    const today = new Date().toISOString().slice(0, 10);
    if (base.length === 0 || base.at(-1).date !== today) {
      base.push({ date: today, value: Number(currentCapital || 0) });
    }
    return base.filter((p) => Number.isFinite(p.value));
  }, [history, currentCapital]);

  if (points.length < 2) {
    return (
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Capital diario</h2>
            <p>Ultimos 3 meses</p>
          </div>
        </div>
        <EmptyState>Datos de historial insuficientes.</EmptyState>
      </section>
    );
  }

  const W = 900;
  const H = 260;
  const pad = { l: 48, r: 18, t: 22, b: 34 };
  const values = points.map((p) => p.value);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const rawRange = rawMax - rawMin;
  const rangePadding = rawRange > 0 ? rawRange * 0.24 : 0.01;
  const min = rawMin - rangePadding;
  const max = rawMax + rangePadding;
  const range = max - min || 1;
  const x = (i) => pad.l + (i / (points.length - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (H - pad.t - pad.b) - ((v - min) / range) * (H - pad.t - pad.b);
  const coords = points.map((p, i) => ({ x: x(i), y: y(p.value), ...p }));
  const line = coords.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
  const area = `${line} L${coords.at(-1).x},${H - pad.b} L${coords[0].x},${H - pad.b} Z`;
  const delta = points.at(-1).value - points[0].value;

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>Capital diario</h2>
          <p>Ultimos 3 meses</p>
        </div>
        <strong className={delta >= 0 ? "emerald" : "red"}>{delta >= 0 ? "+" : ""}{money(delta, 4)}</strong>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="wide-chart" role="img" aria-label="Capital diario de los ultimos 3 meses">
        <defs>
          <linearGradient id="capitalFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--gold)" stopOpacity="0.32" />
            <stop offset="100%" stopColor="var(--gold)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {[0, 0.5, 1].map((t) => {
          const yy = pad.t + t * (H - pad.t - pad.b);
          const val = max - t * range;
          return (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={yy} y2={yy} className="grid-line" />
              <text x={8} y={yy + 4} className="axis-label">{money(val, 4)}</text>
            </g>
          );
        })}
        <path d={area} fill="url(#capitalFill)" />
        <path d={line} className="capital-line" />
        {coords.filter((_, i) => i === 0 || i === coords.length - 1).map((p) => (
          <circle key={p.date} cx={p.x} cy={p.y} r="5" className="capital-dot" />
        ))}
        <text x={pad.l} y={H - 8} className="axis-label">{points[0].date}</text>
        <text x={W - pad.r - 78} y={H - 8} className="axis-label">{points.at(-1).date}</text>
      </svg>
    </section>
  );
}

function PriceChart({ market, operations }) {
  const [range, setRange] = useState("5d");
  const [agentId, setAgentId] = useState("");
  const rangeOptions = [
    ["today", "Hoy · 15m"],
    ["5d", "Ultimos 5 dias · 15m"],
    ["1m", "Ultimo mes · 1h"],
  ];
  const candles = market?.ranges?.[range] || market?.candles || [];
  const agentOptions = useMemo(() => {
    return [...new Set((operations || [])
      .filter((op) => ["BUY", "SELL"].includes(op.accion))
      .map((op) => op.agente_id)
      .filter(Boolean))]
      .sort();
  }, [operations]);
  const selectedOps = (operations || [])
    .filter((op) => agentId && op.agente_id === agentId && op.precio_entrada && op.timestamp_entrada && ["BUY", "SELL"].includes(op.accion))
    .slice()
    .reverse();

  if (candles.length < 2) {
    return (
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>EUR/USD</h2>
            <p>{market?.priceError || "Precio no disponible"}</p>
          </div>
        </div>
        <EmptyState>No hay velas recientes para graficar.</EmptyState>
      </section>
    );
  }

  const W = 900;
  const H = 330;
  const pad = { l: 58, r: 22, t: 18, b: 36 };
  const lows = candles.map((c) => Number(c.low || c.close));
  const highs = candles.map((c) => Number(c.high || c.close));
  const min = Math.min(...lows) - 0.00015;
  const max = Math.max(...highs) + 0.00015;
  const priceRange = max - min || 1;
  const startTs = Date.parse(candles[0].timestamp);
  const endTs = Date.parse(candles.at(-1).timestamp);
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;
  const xAtTime = (ts) => pad.l + ((Date.parse(ts) - startTs) / Math.max(endTs - startTs, 1)) * plotW;
  const xAtIndex = (i) => pad.l + (i / Math.max(candles.length - 1, 1)) * plotW;
  const y = (v) => pad.t + plotH - ((Number(v) - min) / priceRange) * plotH;
  const candleWidth = Math.max(2, Math.min(10, (plotW / candles.length) * 0.64));
  const spanMs = endTs - startTs;
  const xLabelFmt = spanMs <= 26 * 60 * 60 * 1000
    ? (ts) => new Date(ts).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", timeZone: "America/Bogota" })
    : (ts) => new Date(ts).toLocaleDateString("es-CO", { month: "2-digit", day: "2-digit", timeZone: "America/Bogota" });
  const xAxisTicks = Array.from({ length: 5 }, (_, i) => {
    const idx = Math.round(i * (candles.length - 1) / 4);
    const ts = Date.parse(candles[idx].timestamp);
    return { ts, x: xAtIndex(idx), label: xLabelFmt(ts), anchor: i === 0 ? "start" : i === 4 ? "end" : "middle" };
  });
  const visibleOps = selectedOps.filter((op) => {
    const ts = Date.parse(op.timestamp_entrada);
    return ts >= startTs && ts <= endTs;
  });

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>Precio EUR/USD</h2>
          <p>Diagrama de velas · selecciona un agente para ver entradas y salidas.</p>
        </div>
        <strong className="gold">{fmtPrice(market?.lastPrice)}</strong>
      </div>
      <div className="market-controls">
        <div>
          <span>Temporalidad</span>
          <div className="segmented">
            {rangeOptions.map(([id, label]) => (
              <button key={id} className={range === id ? "active" : ""} onClick={() => setRange(id)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <label>
          <span>Agente</span>
          <select value={agentId} onChange={(event) => setAgentId(event.target.value)}>
            <option value="">Sin marcadores</option>
            {agentOptions.map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </label>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="wide-chart" role="img" aria-label="Precio EUR/USD y operaciones de agentes">
        {[0, 0.5, 1].map((t) => {
          const yy = pad.t + t * plotH;
          const val = max - t * priceRange;
          return (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={yy} y2={yy} className="grid-line" />
              <text x={8} y={yy + 4} className="axis-label">{fmtPrice(val)}</text>
            </g>
          );
        })}
        {xAxisTicks.filter((_, i) => i > 0 && i < 4).map(({ ts, x: xPos }) => (
          <line key={`xtick-${ts}`} x1={xPos} x2={xPos} y1={pad.t} y2={H - pad.b} className="grid-line" />
        ))}
        {candles.map((c, i) => {
          const x = xAtIndex(i);
          const open = Number(c.open ?? c.close);
          const close = Number(c.close);
          const high = Number(c.high ?? c.close);
          const low = Number(c.low ?? c.close);
          const up = close >= open;
          const bodyTop = y(Math.max(open, close));
          const bodyHeight = Math.max(2, Math.abs(y(open) - y(close)));
          return (
            <g key={`${c.timestamp}-${i}`} className={up ? "candle candle-up" : "candle candle-down"}>
              <line x1={x} x2={x} y1={y(high)} y2={y(low)} />
              <rect x={x - candleWidth / 2} y={bodyTop} width={candleWidth} height={bodyHeight} rx="1.5" />
            </g>
          );
        })}
        {visibleOps.map((op) => {
          const entryX = xAtTime(op.timestamp_entrada);
          const entryY = y(op.precio_entrada);
          const exitTs = op.timestamp_salida ? Date.parse(op.timestamp_salida) : null;
          const hasExit = op.timestamp_salida && op.precio_salida && exitTs >= startTs && exitTs <= endTs;
          const exitX = hasExit ? xAtTime(op.timestamp_salida) : null;
          const exitY = hasExit ? y(op.precio_salida) : null;
          const won = Number(op.pnl || 0) > 0;
          const lost = Number(op.pnl || 0) < 0;
          const tone = won ? "won" : lost ? "lost" : "flat";
          return (
            <g key={op.id} className={`agent-trade ${tone}`}>
              {hasExit ? <line x1={entryX} x2={exitX} y1={entryY} y2={exitY} className="trade-path" /> : null}
              <path d={`M${entryX},${entryY - 8} L${entryX - 7},${entryY + 6} L${entryX + 7},${entryY + 6} Z`} className={`trade-entry ${op.accion?.toLowerCase()}`} />
              {hasExit ? <circle cx={exitX} cy={exitY} r="6" className="trade-exit" /> : null}
              <title>{`#${op.id} ${op.accion} ${op.agente_id} entrada ${fmtPrice(op.precio_entrada)} salida ${fmtPrice(op.precio_salida)} P&G ${money(op.pnl, 4)}`}</title>
            </g>
          );
        })}
        {xAxisTicks.map(({ ts, x: xPos, label, anchor }) => (
          <text key={`xlabel-${ts}`} x={xPos} y={H - 8} textAnchor={anchor} className="axis-label">{label}</text>
        ))}
      </svg>
      <div className="legend-row">
        <span><i className="legend-dot candle-up-dot" /> Vela alcista</span>
        <span><i className="legend-dot candle-down-dot" /> Vela bajista</span>
        <span><i className="legend-dot won" /> Ganada</span>
        <span><i className="legend-dot lost" /> Perdida</span>
        <span>{agentId ? `${visibleOps.length} operaciones visibles de ${agentId}` : "Sin agente seleccionado"}</span>
      </div>
    </section>
  );
}

function RankingList({ agents }) {
  if (!agents?.length) return <EmptyState>No hay agentes activos.</EmptyState>;
  return (
    <div className="ranking-table-wrap">
      <table className="ranking-table">
        <thead>
          <tr>
            <th>ID Agente</th>
            <th>Gen</th>
            <th>Estado</th>
            <th>Fitness</th>
            <th>ROI %</th>
            <th>Capital ($)</th>
            <th>Ops</th>
            <th>Win Rate %</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((agt) => {
            const positive = Number(agt.roi_total || 0) >= 0;
            const winRate = Number(agt.win_rate_pct || 0);
            return (
              <tr key={agt.id}>
                <td className="mono strong">{agt.id}</td>
                <td>{agt.generacion}</td>
                <td><span className={`status-badge ${agt.estado}`}>{agt.estado}</span></td>
                <td className="mono">{Number(agt.fitness_score || 0).toFixed(4)}</td>
                <td className={positive ? "emerald mono" : "red mono"}>{positive ? "+" : ""}{Number(agt.roi_total || 0).toFixed(4)} %</td>
                <td className="mono">{money(agt.capital_actual, 4)}</td>
                <td className="mono">{agt.operaciones_total || 0}</td>
                <td>
                  <div className="win-cell">
                    <div className="win-track">
                      <span style={{ width: `${Math.max(0, Math.min(100, winRate))}%` }} />
                    </div>
                    <strong className="mono">{winRate.toFixed(1)} %</strong>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="agent-list compact-ranking">
        {agents.map((agt, idx) => {
        const positive = Number(agt.roi_total || 0) >= 0;
        const winRate = Number(agt.win_rate_pct || 0);
        return (
          <article className="agent-card" key={agt.id}>
            <div className="rank-pill">#{idx + 1}</div>
            <div className="agent-info">
              <div className="agent-gen">GEN {agt.generacion}</div>
              <div className="agent-id">{agt.id}</div>
              <div className="agent-stats">
                <span className={`status-badge ${agt.estado}`}>{agt.estado}</span>
                <span className={positive ? "emerald" : "red"}>{positive ? "+" : ""}{Number(agt.roi_total || 0).toFixed(4)} %</span>
                <span>{money(agt.capital_actual, 4)}</span>
                <span>Ops {agt.operaciones_total || 0}</span>
                <span>WR {pct(winRate)}</span>
              </div>
              <div className="mobile-win">
                <div className="win-track">
                  <span style={{ width: `${Math.max(0, Math.min(100, winRate))}%` }} />
                </div>
              </div>
            </div>
            <div className="agent-fitness">
              <div className="fitness-label">Fitness</div>
              <div className="fitness-value">{Number(agt.fitness_score || 0).toFixed(4)}</div>
            </div>
          </article>
        );
        })}
      </div>
    </div>
  );
}

function GenealogyTree({ agents }) {
  const [scale, setScale] = useState(0.86);
  const [offset, setOffset] = useState({ x: 24, y: 24 });
  const dragRef = useRef(null);

  const tree = useMemo(() => {
    const list = agents || [];
    const byGen = new Map();
    list.forEach((agent) => {
      const gen = Number(agent.generacion || 1);
      if (!byGen.has(gen)) byGen.set(gen, []);
      byGen.get(gen).push(agent);
    });

    const levels = [...byGen.entries()].sort((a, b) => a[0] - b[0]);
    const nodeW = 172;
    const nodeH = 72;
    const xGap = 48;
    const yGap = 132;
    const maxCount = Math.max(...levels.map(([, nodes]) => nodes.length), 1);
    const width = Math.max(900, maxCount * (nodeW + xGap) + 120);
    const nodes = [];
    const pos = new Map();

    levels.forEach(([gen, genNodes], levelIndex) => {
      const rowW = genNodes.length * nodeW + (genNodes.length - 1) * xGap;
      const startX = (width - rowW) / 2;
      genNodes.forEach((agent, i) => {
        const node = {
          ...agent,
          x: startX + i * (nodeW + xGap),
          y: levelIndex * yGap + 20,
          w: nodeW,
          h: nodeH,
          gen,
        };
        nodes.push(node);
        pos.set(agent.id, node);
      });
    });

    const links = [];
    nodes.forEach((node) => {
      [node.padre_1_id, node.padre_2_id].filter(Boolean).forEach((parentId) => {
        const parent = pos.get(parentId);
        if (parent) links.push({ parent, child: node });
      });
    });

    return {
      nodes,
      links,
      width,
      height: levels.length * yGap + 120,
    };
  }, [agents]);

  const startDrag = (event) => {
    const point = "touches" in event ? event.touches[0] : event;
    dragRef.current = { x: point.clientX, y: point.clientY, ox: offset.x, oy: offset.y };
  };
  const moveDrag = (event) => {
    if (!dragRef.current) return;
    const point = "touches" in event ? event.touches[0] : event;
    setOffset({
      x: dragRef.current.ox + point.clientX - dragRef.current.x,
      y: dragRef.current.oy + point.clientY - dragRef.current.y,
    });
  };
  const endDrag = () => {
    dragRef.current = null;
  };
  const zoom = (delta) => setScale((s) => Math.max(0.35, Math.min(1.6, Number((s + delta).toFixed(2)))));

  if (!agents?.length) return <EmptyState>No hay agentes para construir el arbol.</EmptyState>;

  return (
    <section className="panel tree-panel">
      <div className="panel-head">
        <div>
          <h2>Arbol genealogico</h2>
          <p>Arrastra para moverte. Usa zoom para ampliar o reducir.</p>
        </div>
        <div className="tree-controls">
          <button onClick={() => zoom(-0.12)} aria-label="Reducir">-</button>
          <span>{Math.round(scale * 100)}%</span>
          <button onClick={() => zoom(0.12)} aria-label="Ampliar">+</button>
        </div>
      </div>
      <div
        className="tree-viewport"
        onMouseDown={startDrag}
        onMouseMove={moveDrag}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        onTouchStart={startDrag}
        onTouchMove={moveDrag}
        onTouchEnd={endDrag}
      >
        <svg
          width={tree.width}
          height={tree.height}
          style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
          className="genealogy-svg"
        >
          <g>
            {tree.links.map((link, i) => {
              const x1 = link.parent.x + link.parent.w / 2;
              const y1 = link.parent.y + link.parent.h;
              const x2 = link.child.x + link.child.w / 2;
              const y2 = link.child.y;
              const mid = (y1 + y2) / 2;
              return (
                <path
                  key={`${link.parent.id}-${link.child.id}-${i}`}
                  d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`}
                  className="tree-link"
                />
              );
            })}
            {tree.nodes.map((node) => {
              const active = node.estado === "activo";
              const positive = Number(node.roi_total || 0) >= 0;
              return (
                <g key={node.id} transform={`translate(${node.x},${node.y})`} className="tree-node">
                  <rect width={node.w} height={node.h} rx="8" className={active ? "node-active" : "node-dead"} />
                  <text x="14" y="22" className="node-gen">GEN {node.gen}</text>
                  <text x="14" y="42" className="node-id">{node.id}</text>
                  <text x="14" y="61" className={positive ? "node-roi positive" : "node-roi negative"}>
                    ROI {positive ? "+" : ""}{Number(node.roi_total || 0).toFixed(2)}%
                  </text>
                  <circle cx={node.w - 18} cy="18" r="6" className={active ? "node-dot active" : "node-dot dead"} />
                </g>
              );
            })}
          </g>
        </svg>
      </div>
    </section>
  );
}

function OperationsList({ operations }) {
  const list = (operations || []).slice(0, 8);
  if (!list.length) return <EmptyState>No hay operaciones recientes.</EmptyState>;
  return (
    <div>
      {list.map((op) => (
        <article className="op-card" key={op.id}>
          <div className="op-header">
            <div>
              <div className="op-id">#{op.id} · {op.agente_id}</div>
              <div className="op-date">{new Date(op.timestamp_entrada).toLocaleString("es-CO")}</div>
            </div>
            <div className={`op-type ${op.accion?.toLowerCase()}`}>{op.accion}</div>
          </div>
          <div className="op-details">
            <div><span>Entrada</span><strong>{fmtPrice(op.precio_entrada)}</strong></div>
            <div><span>SL</span><strong>{fmtPrice(op.stop_loss)}</strong></div>
            <div><span>TP</span><strong>{fmtPrice(op.take_profit)}</strong></div>
            <div><span>PnL</span><strong className={(op.pnl || 0) >= 0 ? "emerald" : "red"}>{money(op.pnl)}</strong></div>
          </div>
        </article>
      ))}
    </div>
  );
}

function DetailMetric({ label, value, tone }) {
  return (
    <div className="detail-metric">
      <span>{label}</span>
      <strong className={tone || ""}>{value ?? "--"}</strong>
    </div>
  );
}

function OperationsTable({ operations }) {
  const [expanded, setExpanded] = useState(null);
  const [actionFilter, setActionFilter] = useState("ALL");
  const [stateFilter, setStateFilter] = useState("ALL");
  const tableScrollRef = useRef(null);

  const filtered = useMemo(() => {
    return (operations || []).filter((op) => {
      const actionOk = actionFilter === "ALL" || op.accion === actionFilter;
      const stateOk = stateFilter === "ALL" || op.estado === stateFilter;
      return actionOk && stateOk;
    });
  }, [operations, actionFilter, stateFilter]);

  if (!operations?.length) return <EmptyState>No hay operaciones registradas.</EmptyState>;

  const csvColumns = [
    ["ID", "id"],
    ["Agente", "agente_id"],
    ["Generacion", "generacion"],
    ["Fecha Hora Apertura", "timestamp_entrada"],
    ["Accion", "accion"],
    ["Precio Entrada", "precio_entrada"],
    ["SL", "stop_loss"],
    ["TP", "take_profit"],
    ["Capital Usado", "capital_usado"],
    ["Estado", "estado"],
    ["Fecha Hora Salida", "timestamp_salida"],
    ["Precio Salida", "precio_salida"],
    ["P&G", "pnl"],
    ["P&G %", "pnl_porcentaje"],
    ["Confianza Final", "confianza_final"],
    ["Confianza Tecnica", "confianza_tecnica"],
    ["Confianza Macro", "confianza_macro"],
    ["RSI", "rsi"],
    ["FVG Direccion", "fvg_direccion"],
    ["FVG Pips", "fvg_pips"],
    ["OB Activo", "ob_activo"],
    ["OB Direccion", "ob_direccion"],
    ["Range Spike", "range_spike"],
    ["Senal Tecnica", "recomendacion_tecnica"],
    ["Senal Macro", "recomendacion_macro"],
    ["Razonamiento LLM", "razonamiento_llm"],
  ];

  const csvEscape = (value) => {
    if (value === null || value === undefined) return "";
    const text = String(value).replace(/\r?\n/g, " ");
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };

  const downloadCsv = () => {
    const header = csvColumns.map(([label]) => csvEscape(label)).join(",");
    const rows = filtered.map((op) => csvColumns.map(([, key]) => csvEscape(op[key])).join(","));
    const csv = [header, ...rows].join("\n");
    const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const today = new Date().toISOString().slice(0, 10);
    link.href = url;
    link.download = `operaciones_inversion_evolutiva_${today}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const moveTable = (mode) => {
    const el = tableScrollRef.current;
    if (!el) return;
    const step = Math.max(360, el.clientWidth * 0.72);
    if (mode === "start") el.scrollTo({ left: 0, behavior: "smooth" });
    if (mode === "left") el.scrollBy({ left: -step, behavior: "smooth" });
    if (mode === "right") el.scrollBy({ left: step, behavior: "smooth" });
    if (mode === "end") el.scrollTo({ left: el.scrollWidth, behavior: "smooth" });
  };

  const actionOptions = ["ALL", "BUY", "SELL", "HOLD"];
  const stateOptions = ["ALL", "abierta", "cerrada", "cancelada"];

  return (
    <section className="panel operations-panel">
      <div className="panel-head">
        <div>
          <h2>Operaciones</h2>
          <p>Listado completo de operaciones ejecutadas con detalle tecnico, macro y riesgo.</p>
        </div>
        <strong className="gold">{filtered.length} / {operations.length}</strong>
      </div>

      <div className="filters-row">
        <div>
          <span>Accion</span>
          <div className="segmented">
            {actionOptions.map((item) => (
              <button key={item} className={actionFilter === item ? "active" : ""} onClick={() => setActionFilter(item)}>
                {item === "ALL" ? "Todas" : item}
              </button>
            ))}
          </div>
        </div>
        <div>
          <span>Estado</span>
          <div className="segmented">
            {stateOptions.map((item) => (
              <button key={item} className={stateFilter === item ? "active" : ""} onClick={() => setStateFilter(item)}>
                {item === "ALL" ? "Todos" : item}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="table-action-bar">
        <button onClick={() => moveTable("start")}>Inicio</button>
        <button onClick={() => moveTable("left")}>←</button>
        <button onClick={() => moveTable("right")}>→</button>
        <button onClick={() => moveTable("end")}>Fin</button>
        <button className="csv-button" onClick={downloadCsv}>Descargar CSV</button>
      </div>

      <div className="operations-table-wrap" ref={tableScrollRef}>
        <table className="operations-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Agente</th>
              <th>Gen</th>
              <th>Apertura</th>
              <th>Accion</th>
              <th>Entrada</th>
              <th>SL</th>
              <th>TP</th>
              <th>Capital</th>
              <th>Estado</th>
              <th>Salida</th>
              <th>Precio Salida</th>
              <th>P&G</th>
              <th>Conf.</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((op) => {
              const isOpen = expanded === op.id;
              const pnlTone = Number(op.pnl || 0) >= 0 ? "emerald" : "red";
              return (
                <Fragment key={op.id}>
                  <tr className={isOpen ? "selected-row" : ""}>
                    <td className="mono strong">#{op.id}</td>
                    <td className="mono strong">{op.agente_id}</td>
                    <td>{op.generacion ?? "--"}</td>
                    <td>{fmtDateTime(op.timestamp_entrada)}</td>
                    <td><span className={`op-type mini ${op.accion?.toLowerCase()}`}>{op.accion}</span></td>
                    <td className="mono">{fmtPrice(op.precio_entrada)}</td>
                    <td className="mono">{fmtPrice(op.stop_loss)}</td>
                    <td className="mono">{fmtPrice(op.take_profit)}</td>
                    <td className="mono">{money(op.capital_usado, 4)}</td>
                    <td><span className={`state-badge ${op.estado}`}>{op.estado}</span></td>
                    <td>{fmtDateTime(op.timestamp_salida)}</td>
                    <td className="mono">{fmtPrice(op.precio_salida)}</td>
                    <td className={`${pnlTone} mono`}>{money(op.pnl, 4)}</td>
                    <td className="mono">{fmtNum(op.confianza_final, 3)}</td>
                    <td>
                      <button className="row-toggle" onClick={() => setExpanded(isOpen ? null : op.id)}>
                        {isOpen ? "Cerrar" : "Detalle"}
                      </button>
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr className="detail-row">
                      <td colSpan="15">
                        <div className="operation-detail">
                          <div className="detail-grid">
                            <DetailMetric label="Confianza Final" value={fmtNum(op.confianza_final, 4)} />
                            <DetailMetric label="Confianza Tecnica" value={fmtNum(op.confianza_tecnica, 4)} />
                            <DetailMetric label="Confianza Macro" value={fmtNum(op.confianza_macro, 4)} />
                            <DetailMetric label="RSI" value={fmtNum(op.rsi, 2)} />
                            <DetailMetric label="FVG Direccion" value={op.fvg_direccion || "--"} />
                            <DetailMetric label="FVG Pips" value={fmtNum(op.fvg_pips, 2)} />
                            <DetailMetric label="OB Activo" value={op.ob_activo ? "SI" : "NO"} tone={op.ob_activo ? "emerald" : ""} />
                            <DetailMetric label="OB Direccion" value={op.ob_direccion || "--"} />
                            <DetailMetric label="Range Spike" value={op.range_spike ? "SI" : "NO"} tone={op.range_spike ? "gold" : ""} />
                            <DetailMetric label="Señal Tecnica" value={op.recomendacion_tecnica || "--"} />
                            <DetailMetric label="Señal Macro" value={op.recomendacion_macro || "--"} />
                            <DetailMetric
                              label="P&G %"
                              value={op.pnl_porcentaje === null || op.pnl_porcentaje === undefined ? "--" : `${fmtNum(op.pnl_porcentaje, 4)} %`}
                              tone={pnlTone}
                            />
                          </div>
                          <div className="reason-box">
                            <span>Razonamiento LLM</span>
                            <p>{op.razonamiento_llm || "Sin razonamiento registrado."}</p>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function JudgeLogs({ logs }) {
  const list = (logs || []).slice(0, 6);
  if (!list.length) return null;
  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>Agente Juez</h2>
          <p>Ultimos eventos evolutivos</p>
        </div>
      </div>
      <div className="judge-list">
        {list.map((log, idx) => (
          <article className="judge-item" key={`${log.created_at}-${idx}`}>
            <span>{log.tipo_evento}</span>
            <strong>{log.agente_afectado_id || "Sistema"}</strong>
            <p>{log.razonamiento_llm || log.descripcion}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function InicioView({ data }) {
  const { status } = data;
  return (
    <main className="view fade-in">
      <section className="hero">
        <div className="hero-value">{money(status.totalCapital, 4)}</div>
        <div className="hero-sub">Capital total del portafolio</div>
      </section>
      <div className="metric-grid">
        <KpiCard label="PnL Abierto" value={`${status.openPnl >= 0 ? "+" : ""}${money(status.openPnl)}`} tone={status.openPnl >= 0 ? "emerald" : "red"} />
        <KpiCard label="Operaciones" value={status.openOpsCount} suffix="activas" />
        <KpiCard label="Win Rate" value={pct(status.winRate)} />
        <KpiCard label="Top Fitness" value={Number(status.bestFitness || 0).toFixed(4)} tone="gold" />
      </div>
      <CapitalChart history={data.capitalHistory} currentCapital={status.totalCapital} />
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Operaciones abiertas</h2>
            <p>Posiciones vivas monitoreadas por el sistema</p>
          </div>
        </div>
        <OperationsList operations={data.openOperations} />
      </section>
      <JudgeLogs logs={data.judgeLogs} />
    </main>
  );
}

function AgentesView({ data }) {
  return (
    <main className="view fade-in">
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Ranking de agentes</h2>
            <p>Ordenado por fitness y ROI</p>
          </div>
          <strong className="gold">Gen {data.status.maxGen}</strong>
        </div>
        <RankingList agents={data.activeAgents} />
      </section>
    </main>
  );
}

function MercadoView({ data }) {
  return (
    <main className="view fade-in">
      <PriceChart market={data.market} operations={data.operations} />
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Operaciones recientes</h2>
            <p>Entradas usadas como marcadores del precio</p>
          </div>
        </div>
        <OperationsList operations={data.operations} />
      </section>
    </main>
  );
}

function OperacionesView({ data }) {
  return (
    <main className="view fade-in">
      <OperationsTable operations={data.operations} />
    </main>
  );
}

function ArbolView({ data }) {
  return (
    <main className="view fade-in">
      <GenealogyTree agents={data.genealogy} />
    </main>
  );
}

function BottomNav({ view, setView }) {
  const items = [
    ["inicio", "Inicio"],
    ["agentes", "Agentes"],
    ["mercado", "Mercado"],
    ["operaciones", "Operaciones"],
    ["arbol", "Arbol"],
  ];
  return (
    <nav className="bottom-nav">
      <div className="nav-inner">
        {items.map(([id, label]) => (
          <button key={id} className={`nav-item ${view === id ? "active" : ""}`} onClick={() => setView(id)}>
            <span className="nav-label">{label}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}

export default function Home() {
  const [view, setView] = useState("inicio");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const fetchData = useCallback(async (silent = false) => {
    try {
      if (!silent) setRefreshing(true);
      const res = await fetch("/api/dashboard", { cache: "no-store" });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "No se pudo cargar el dashboard");
      setData(json);
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchData();
    const interval = setInterval(() => fetchData(true), 60000);
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading || !data) {
    return (
      <>
        <LoadingScreen />
        {error ? <div className="load-error">{error}</div> : null}
      </>
    );
  }

  return (
    <>
      <button className={`refresh-btn ${refreshing ? "spinning" : ""}`} onClick={() => fetchData()} title="Actualizar">
        Actualizar
      </button>
      <div className="app">
        <header className="app-header">
          <img src="/icon-192.png" alt="Inversion Evolutiva" className="app-logo" />
          <div>
            <span className="app-header-title">INVERSION EVOLUTIVA</span>
            <span className="app-header-sub">EUR/USD · Algorithmic Evolution</span>
          </div>
        </header>
        {error ? <div className="inline-error">{error}</div> : null}
        {view === "inicio" ? <InicioView data={data} /> : null}
        {view === "agentes" ? <AgentesView data={data} /> : null}
        {view === "mercado" ? <MercadoView data={data} /> : null}
        {view === "operaciones" ? <OperacionesView data={data} /> : null}
        {view === "arbol" ? <ArbolView data={data} /> : null}
      </div>
      <BottomNav view={view} setView={setView} />
    </>
  );
}
