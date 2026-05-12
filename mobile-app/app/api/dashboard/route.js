import { Pool } from "pg";

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
  max: 5,
});

export const dynamic = "force-dynamic";

export async function GET() {
  const client = await pool.connect();
  try {
    // 1. Active agents with fitness
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
      ORDER BY COALESCE(rh.fitness_score, 0) DESC
    `);

    // 2. All agents with parents (for genealogy) — includes parent fitness
    const genealogyRes = await client.query(`
      SELECT a.id, a.generacion, a.estado,
             a.padre_1_id, a.padre_2_id,
             a.roi_total::float,
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
      WHERE a.padre_1_id IS NOT NULL
      ORDER BY a.generacion DESC, a.id DESC
    `);

    // 3. Open operations with SL/TP
    const opsRes = await client.query(`
      SELECT o.id, o.agente_id, o.accion,
             o.precio_entrada::float AS precio_entrada,
             o.pnl::float AS pnl,
             o.estado,
             (o.decision_riesgo->>'stop_loss')::float AS stop_loss,
             (o.decision_riesgo->>'take_profit')::float AS take_profit
      FROM operaciones o
      WHERE o.estado = 'abierta'
      ORDER BY o.timestamp_entrada DESC
    `);

    // 4. Capital history: suma supervivientes + eliminados (capital real de trading).
    //    Se excluyen 'nacimiento' porque su capital_fin_dia es $10 hardcodeado, no trading.
    const capitalHistRes = await client.query(`
      SELECT rh.fecha::text AS fecha,
             SUM(rh.capital_fin_dia::float) AS capital_total
      FROM ranking_historico rh
      WHERE rh.evento != 'nacimiento'
      GROUP BY rh.fecha
      ORDER BY rh.fecha ASC
    `);

    // 5. System status
    const capRes = await client.query(`SELECT SUM(capital_actual::float) as total FROM agentes WHERE estado='activo'`);
    const genRes = await client.query(`SELECT MAX(generacion) as max_gen FROM agentes`);

    const activeAgents = activeRes.rows;
    const totalOps = activeAgents.reduce((s, a) => s + (a.operaciones_total || 0), 0);
    const totalWon = activeAgents.reduce((s, a) => s + (a.operaciones_ganadoras || 0), 0);

    return Response.json({
      activeAgents,
      genealogy: genealogyRes.rows,
      openOperations: opsRes.rows,
      capitalHistory: capitalHistRes.rows,
      status: {
        totalCapital: capRes.rows[0]?.total || 0,
        openPnl: opsRes.rows.reduce((s, o) => s + (o.pnl || 0), 0),
        openOpsCount: opsRes.rows.length,
        winRate: totalOps > 0 ? (totalWon / totalOps * 100) : 0,
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
