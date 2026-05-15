import { Pool } from "pg";

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
  max: 5,
});

export const dynamic = "force-dynamic";

function toUnixSeconds(date) {
  return Math.floor(date.getTime() / 1000);
}

function bogotaDayStartUtc() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Bogota",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((p) => [p.type, p.value]));
  return new Date(Date.UTC(Number(value.year), Number(value.month) - 1, Number(value.day), 5, 0, 0));
}

async function fetchEurUsdCandles(range = "5d") {
  const end = new Date();
  const ranges = {
    today: {
      start: bogotaDayStartUtc(),
      interval: "15m",
    },
    "5d": {
      start: new Date(end.getTime() - 5 * 24 * 60 * 60 * 1000),
      interval: "15m",
    },
    "1m": {
      start: new Date(end.getTime() - 31 * 24 * 60 * 60 * 1000),
      interval: "1h",
    },
  };
  const config = ranges[range] || ranges["5d"];
  const url = new URL("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X");
  url.searchParams.set("period1", String(toUnixSeconds(config.start)));
  url.searchParams.set("period2", String(toUnixSeconds(end)));
  url.searchParams.set("interval", config.interval);
  url.searchParams.set("includePrePost", "true");

  const res = await fetch(url, {
    next: { revalidate: 60 },
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Accept": "application/json",
    },
  });
  if (!res.ok) throw new Error(`Yahoo Finance ${res.status}`);

  const payload = await res.json();
  const result = payload?.chart?.result?.[0];
  const timestamps = result?.timestamp || [];
  const quote = result?.indicators?.quote?.[0] || {};

  return timestamps
    .map((ts, i) => ({
      timestamp: new Date(ts * 1000).toISOString(),
      open: quote.open?.[i] ?? null,
      high: quote.high?.[i] ?? null,
      low: quote.low?.[i] ?? null,
      close: quote.close?.[i] ?? null,
    }))
    .filter((c) => c.close !== null);
}

async function fetchMarketRanges() {
  const entries = await Promise.allSettled([
    fetchEurUsdCandles("today"),
    fetchEurUsdCandles("5d"),
    fetchEurUsdCandles("1m"),
  ]);
  const keys = ["today", "5d", "1m"];
  const result = {};
  const errors = {};
  entries.forEach((entry, index) => {
    if (entry.status === "fulfilled") {
      result[keys[index]] = entry.value;
    } else {
      result[keys[index]] = [];
      errors[keys[index]] = entry.reason?.message || "No disponible";
    }
  });
  return { result, errors };
}

export async function GET() {
  const client = await pool.connect();
  try {
    const activeRes = await client.query(`
      SELECT a.id, a.generacion, a.estado,
             a.capital_actual::float, a.roi_total::float,
             a.operaciones_total, a.operaciones_ganadoras,
             a.padre_1_id, a.padre_2_id,
             CASE WHEN a.operaciones_total > 0
                  THEN ROUND(a.operaciones_ganadoras::numeric / a.operaciones_total * 100, 2)::float
                  ELSE 0 END AS win_rate_pct,
             COALESCE(rh.fitness_score, 0)::float AS fitness_score
      FROM agentes a
      LEFT JOIN LATERAL (
          SELECT fitness_score FROM ranking_historico
          WHERE agente_id = a.id ORDER BY fecha DESC LIMIT 1
      ) rh ON true
      WHERE a.estado = 'activo'
      ORDER BY COALESCE(rh.fitness_score, 0) DESC, a.roi_total DESC
    `);

    const genealogyRes = await client.query(`
      SELECT a.id, a.generacion, a.estado,
             a.fecha_nacimiento::text AS fecha_nacimiento,
             a.padre_1_id, a.padre_2_id,
             a.roi_total::float,
             CASE WHEN a.operaciones_total > 0
                  THEN ROUND(a.operaciones_ganadoras::numeric / a.operaciones_total * 100, 2)::float
                  ELSE 0 END AS win_rate_pct,
             COALESCE(rh.fitness_score, 0)::float AS fitness_score,
             COALESCE(p1_rh.fitness_score, 0)::float AS padre_1_fitness,
             COALESCE(p2_rh.fitness_score, 0)::float AS padre_2_fitness
      FROM agentes a
      LEFT JOIN LATERAL (
          SELECT fitness_score FROM ranking_historico
          WHERE agente_id = a.id ORDER BY fecha DESC LIMIT 1
      ) rh ON true
      LEFT JOIN LATERAL (
          SELECT fitness_score FROM ranking_historico
          WHERE agente_id = a.padre_1_id ORDER BY fecha DESC LIMIT 1
      ) p1_rh ON true
      LEFT JOIN LATERAL (
          SELECT fitness_score FROM ranking_historico
          WHERE agente_id = a.padre_2_id ORDER BY fecha DESC LIMIT 1
      ) p2_rh ON true
      ORDER BY a.generacion ASC, a.fecha_nacimiento ASC, a.id ASC
    `);

    const opsRes = await client.query(`
      SELECT o.id, o.agente_id, o.accion,
             a.generacion,
             o.timestamp_entrada,
             o.timestamp_salida,
             o.precio_entrada::float AS precio_entrada,
             o.precio_salida::float AS precio_salida,
             o.capital_usado::float AS capital_usado,
             o.pnl::float AS pnl,
             o.pnl_porcentaje::float AS pnl_porcentaje,
             o.estado,
             COALESCE(o.sl_dinamico::float, (o.decision_riesgo->>'stop_loss')::float) AS stop_loss,
             (o.decision_riesgo->>'take_profit')::float AS take_profit,
             (o.decision_riesgo->>'confianza_final')::float AS confianza_final,
             (o.decision_riesgo->>'confianza_tecnica')::float AS confianza_tecnica,
             (o.decision_riesgo->>'confianza_macro')::float AS confianza_macro,
             o.decision_riesgo->>'razonamiento' AS razonamiento_llm,
             (o.senal_tecnico->'indicadores'->>'rsi')::float AS rsi,
             o.senal_tecnico->'indicadores'->>'fvg_direccion' AS fvg_direccion,
             (o.senal_tecnico->'indicadores'->>'fvg_pips')::float AS fvg_pips,
             (o.senal_tecnico->'indicadores'->>'ob_activo')::boolean AS ob_activo,
             o.senal_tecnico->'indicadores'->>'ob_direccion' AS ob_direccion,
             (o.senal_tecnico->'indicadores'->>'range_spike')::boolean AS range_spike,
             o.senal_tecnico->>'recomendacion' AS recomendacion_tecnica,
             o.senal_macro->>'recomendacion' AS recomendacion_macro
      FROM operaciones o
      LEFT JOIN agentes a ON a.id = o.agente_id
      WHERE o.timestamp_entrada >= NOW() - INTERVAL '3 months'
      ORDER BY o.timestamp_entrada DESC
      LIMIT 1000
    `);

    const capitalHistRes = await client.query(`
      SELECT fecha::text AS fecha,
             (datos_json->>'capital_pool_total')::float AS capital_total,
             (datos_json->>'capital_por_agente')::float AS capital_por_agente
      FROM logs_juez
      WHERE tipo_evento = 'evaluacion_diaria'
        AND datos_json ? 'capital_pool_total'
        AND fecha >= CURRENT_DATE - INTERVAL '3 months'
      ORDER BY fecha ASC
    `);

    const judgeLogsRes = await client.query(`
      SELECT fecha::text AS fecha, tipo_evento, agente_afectado_id,
             descripcion, razonamiento_llm, created_at
      FROM logs_juez
      ORDER BY created_at DESC
      LIMIT 30
    `);

    const capRes = await client.query(`
      SELECT COALESCE(SUM(capital_actual::float), 0) as total
      FROM agentes WHERE estado='activo'
    `);
    const genRes = await client.query(`SELECT COALESCE(MAX(generacion), 1) as max_gen FROM agentes`);

    let candlesByRange = {};
    let priceError = null;
    try {
      const { result, errors } = await fetchMarketRanges();
      candlesByRange = result;
      priceError = Object.values(errors)[0] || null;
    } catch (err) {
      priceError = err.message;
    }

    const activeAgents = activeRes.rows;
    const operations = opsRes.rows;
    const openOperations = operations.filter((o) => o.estado === "abierta");
    const totalOps = activeAgents.reduce((s, a) => s + (a.operaciones_total || 0), 0);
    const totalWon = activeAgents.reduce((s, a) => s + (a.operaciones_ganadoras || 0), 0);
    const lastPrice = candlesByRange.today?.at(-1)?.close || candlesByRange["5d"]?.at(-1)?.close || null;

    return Response.json({
      activeAgents,
      genealogy: genealogyRes.rows,
      operations,
      openOperations,
      capitalHistory: capitalHistRes.rows,
      judgeLogs: judgeLogsRes.rows,
      market: {
        pair: "EUR/USD",
        lastPrice,
        candles: candlesByRange["5d"] || [],
        ranges: candlesByRange,
        priceError,
        updatedAt: new Date().toISOString(),
      },
      status: {
        totalCapital: capRes.rows[0]?.total || 0,
        openPnl: openOperations.reduce((s, o) => s + (o.pnl || 0), 0),
        openOpsCount: openOperations.length,
        winRate: totalOps > 0 ? (totalWon / totalOps) * 100 : 0,
        bestFitness: activeAgents.length > 0 ? activeAgents[0].fitness_score : 0,
        activeCount: activeAgents.length,
        maxGen: genRes.rows[0]?.max_gen || 1,
      },
    });
  } catch (err) {
    console.error("DB Error:", err);
    return Response.json({ error: err.message }, { status: 500 });
  } finally {
    client.release();
  }
}
