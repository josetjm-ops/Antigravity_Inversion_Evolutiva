"""
Motor Genético de INVERSIÓN EVOLUTIVA.

Ciclo diario:
  1. Evalúa el fitness (ROI) de todos los agentes activos.
  2. Selecciona los N_SURVIVE mejores (supervivientes).
  3. Elimina los N_ELIMINATE peores (selección natural).
  4. Genera N_ELIMINATE agentes nuevos mediante cruce + mutación gaussiana.
  5. Registra todo en ranking_historico y logs_juez.

Mutación gaussiana: param_hijo = param_padre * (1 + N(0, sigma))
Cruce (crossover): cada parámetro se hereda de padre1 con p=0.6, padre2 con p=0.4.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from datetime import date

from dotenv import load_dotenv

from db.connection import get_conn, get_dict_cursor
from utils.sheets_logger import SheetsLogger

load_dotenv()

# ── Configuración desde .env ─────────────────────────────────────────────────
N_ELIMINATE = int(os.getenv("AGENTS_ELIMINATE_PER_CYCLE", "5"))

SIGMA_WEIGHTS = float(os.getenv("MUTATION_SIGMA_WEIGHTS", "0.05"))
SIGMA_PERIODS = float(os.getenv("MUTATION_SIGMA_PERIODS", "0.08"))
SIGMA_RISK = float(os.getenv("MUTATION_SIGMA_RISK", "0.10"))
MIN_ROI_HALL_OF_FAME = float(os.getenv("MIN_ROI_FOR_HALL_OF_FAME", "0.05"))

# ── Rangos de seguridad para clamping post-mutación ──────────────────────────
_BOUNDS_TECNICOS_PERIODS = {
    "rsi_periodo":       (5,   50,  True),   # (min, max, is_int)
    "rsi_sobrecompra":   (55,  90,  False),
    "rsi_sobreventa":    (10,  45,  False),
    "ema_rapida":        (3,   29,  True),
    "ema_lenta":         (10,  50,  True),
    "macd_rapida":       (5,   20,  True),
    "macd_lenta":        (15,  40,  True),
    "macd_senal":        (3,   15,  True),
}
_BOUNDS_TECNICOS_WEIGHTS = {
    "peso_rsi":          (0.1, 0.7, False),
    "peso_ema":          (0.1, 0.7, False),
    "peso_macd":         (0.1, 0.7, False),
}
# Alias para compatibilidad con código que lea el dict completo
_BOUNDS_TECNICOS = {**_BOUNDS_TECNICOS_PERIODS, **_BOUNDS_TECNICOS_WEIGHTS}

_BOUNDS_MACRO = {
    "peso_noticias_alto":         (0.3, 0.9,  False),
    "peso_noticias_medio":        (0.05, 0.4, False),
    "peso_noticias_bajo":         (0.01, 0.2, False),
    "umbral_sentimiento_compra":  (0.55, 0.85, False),
    "umbral_sentimiento_venta":   (0.15, 0.45, False),
    "ventana_noticias_horas":     (1,   8,    True),
    "peso_total_macro":           (0.2, 0.7,  False),
}

_BOUNDS_RIESGO = {
    "stop_loss_pct":              (0.005, 0.05,  False),
    "take_profit_pct":            (0.01,  0.10,  False),
    "max_drawdown_diario_pct":    (0.03,  0.20,  False),
    "capital_por_operacion_pct":  (0.20,  0.80,  False),
    "umbral_confianza_minima":    (0.45,  0.85,  False),
    "peso_tecnico_vs_macro":      (0.30,  0.75,  False),
}

# Genes SMC — nacen con agentes nuevos desde Session 4
_DEFAULT_SMC_PARAMS: dict = {
    "fvg_min_pips":             5.0,
    "ob_impulse_pips":          10.0,
    "range_spike_multiplier":   1.5,
    "risk_reward_target":       2.0,
    "macro_quarantine_minutes": 60,
    "risk_pct_per_trade":       0.015,
    "peso_fvg":                 0.15,
    "peso_ob":                  0.15,
    # ATR-based SL + Trailing Stop (Session 6)
    "atr_factor":               1.5,    # SL = ATR × 1.5 ≈ 12-22 pips
    "trailing_activation_pips": 15.0,   # activar trailing tras 15 pips de profit
    "trailing_distance_pips":   10.0,   # mantener trailing a 10 pips del extremo
    "atr_period":               14,
}

_BOUNDS_SMC = {
    "fvg_min_pips":             (2.0,  15.0,  False),
    "ob_impulse_pips":          (5.0,  20.0,  False),
    "range_spike_multiplier":   (1.2,   3.0,  False),
    "risk_reward_target":       (1.5,   4.0,  False),
    "macro_quarantine_minutes": (30,  120,    True),
    "risk_pct_per_trade":       (0.01,  0.02, False),
    "peso_fvg":                 (0.05,  0.50, False),
    "peso_ob":                  (0.05,  0.50, False),
    # ATR-based SL + Trailing Stop (Session 6)
    "atr_factor":               (0.8,   3.0,  False),
    "trailing_activation_pips": (5.0,  40.0,  False),
    "trailing_distance_pips":   (5.0,  25.0,  False),
    "atr_period":               (7,    21,    True),
}


# ── Fitness: Calmar Ratio Proxy ──────────────────────────────────────────────

_FITNESS_SQL = """
    WITH capital_series AS (
        SELECT agente_id, timestamp_entrada,
               SUM(pnl) OVER (PARTITION BY agente_id ORDER BY timestamp_entrada)
                   AS capital_acumulado
        FROM operaciones WHERE estado = 'cerrada'
    ),
    drawdown_calc AS (
        SELECT agente_id,
               MAX(capital_acumulado) OVER (
                   PARTITION BY agente_id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
               ) AS peak,
               capital_acumulado
        FROM capital_series
    ),
    max_dd AS (
        SELECT agente_id,
               MAX((peak - capital_acumulado) / NULLIF(peak, 0)) AS max_drawdown
        FROM drawdown_calc GROUP BY agente_id
    ),
    ops_diarias AS (
        SELECT agente_id, AVG(ops_dia) AS avg_ops_dia
        FROM (
            SELECT agente_id, DATE(timestamp_entrada) AS dia, COUNT(*) AS ops_dia
            FROM operaciones
            WHERE estado IN ('cerrada', 'abierta')
            GROUP BY agente_id, DATE(timestamp_entrada)
        ) sub GROUP BY agente_id
    )
    SELECT
        a.id,
        a.roi_total,
        COALESCE(d.max_drawdown, 0) AS max_drawdown,
        COALESCE(o.avg_ops_dia,  0) AS avg_ops_dia,
        (a.roi_total / (COALESCE(d.max_drawdown, 0.01) + 1))
        - CASE
            WHEN o.avg_ops_dia > 3
                 AND (a.operaciones_ganadoras::float
                      / NULLIF(a.operaciones_total, 0)) < 0.5
            THEN 0.5 ELSE 0
          END AS fitness_score
    FROM agentes a
    LEFT JOIN max_dd      d ON a.id = d.agente_id
    LEFT JOIN ops_diarias o ON a.id = o.agente_id
    WHERE a.estado = 'activo'
"""


def calc_fitness_scores(conn, agent_ids: list[str] | None = None) -> dict[str, float]:
    """
    Calmar Ratio Proxy para agentes activos.
    Retorna {agente_id: fitness_score}.

    Fórmula: roi_total / (max_drawdown + 1) — penalidad_overtrading
    Penalidad: -0.5 si avg_ops_dia > 3 y win_rate < 50%
    """
    sql    = _FITNESS_SQL
    params: tuple = ()
    if agent_ids:
        sql    = _FITNESS_SQL + " AND a.id = ANY(%s)"
        params = (agent_ids,)

    cur = get_dict_cursor(conn)
    cur.execute(sql, params)
    return {row["id"]: float(row["fitness_score"] or 0) for row in cur.fetchall()}


# ── Helpers de mutación ──────────────────────────────────────────────────────

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _mutate_value(value: float, sigma: float, is_int: bool,
                  lo: float, hi: float) -> float | int:
    mutated = value * (1.0 + random.gauss(0, sigma))
    mutated = _clamp(mutated, lo, hi)
    return round(mutated) if is_int else round(mutated, 6)


def _mutate_block(params: dict, bounds: dict, sigma: float) -> dict:
    result = dict(params)
    for key, (lo, hi, is_int) in bounds.items():
        if key in result:
            result[key] = _mutate_value(result[key], sigma, is_int, lo, hi)
    return result


def _normalize_weights(params: dict, keys: list[str]) -> dict:
    """Asegura que los pesos indicados sumen 1.0 exactamente."""
    total = sum(params[k] for k in keys if k in params)
    if total > 0:
        for k in keys:
            if k in params:
                params[k] = round(params[k] / total, 6)
    return params


def _enforce_ema_constraint(params: dict) -> dict:
    """EMA rápida siempre < EMA lenta; si colisionan, ajusta lenta."""
    if params.get("ema_rapida", 9) >= params.get("ema_lenta", 21):
        params["ema_lenta"] = int(params["ema_rapida"]) + random.randint(3, 8)
        params["ema_lenta"] = _clamp(params["ema_lenta"], 10, 50)
    return params


def _enforce_sl_tp_constraint(params: dict) -> dict:
    """Take-profit siempre > Stop-loss (ratio mínimo 1.5:1)."""
    sl = params.get("stop_loss_pct", 0.02)
    tp = params.get("take_profit_pct", 0.04)
    if tp < sl * 1.5:
        params["take_profit_pct"] = round(sl * (1.5 + random.uniform(0, 0.5)), 6)
        params["take_profit_pct"] = _clamp(params["take_profit_pct"], 0.01, 0.10)
    return params


# ── Crossover ────────────────────────────────────────────────────────────────

def crossover(parent1: dict, parent2: dict, p1_weight: float = 0.6) -> dict:
    """
    Cruza dos diccionarios de parámetros.
    Cada clave se toma de parent1 con probabilidad p1_weight, de parent2 con (1-p1_weight).
    """
    child = {}
    all_keys = set(parent1.keys()) | set(parent2.keys())
    for k in all_keys:
        if random.random() < p1_weight:
            child[k] = parent1.get(k, parent2.get(k))
        else:
            child[k] = parent2.get(k, parent1.get(k))
    return child


# ── Generación de un agente hijo completo ────────────────────────────────────

def breed_agent(
    parent1: dict,
    parent2: dict,
    child_id: str,
    birth_date: date,
    generation: int,
) -> dict:
    """
    Genera un nuevo agente a partir de dos padres.
    1. Crossover de cada bloque de parámetros.
    2. Mutación gaussiana.
    3. Normalización y constraints de seguridad.
    """
    # Crossover con sesgo hacia el padre de mejor ROI
    roi1 = float(parent1.get("roi_total", 0))
    roi2 = float(parent2.get("roi_total", 0))
    p1_weight = 0.6 if roi1 >= roi2 else 0.4

    tec_child  = crossover(parent1["params_tecnicos"], parent2["params_tecnicos"], p1_weight)
    mac_child  = crossover(parent1["params_macro"],    parent2["params_macro"],    p1_weight)
    risk_child = crossover(parent1["params_riesgo"],   parent2["params_riesgo"],   p1_weight)
    smc_child  = crossover(
        parent1.get("params_smc", _DEFAULT_SMC_PARAMS),
        parent2.get("params_smc", _DEFAULT_SMC_PARAMS),
        p1_weight,
    )

    # Mutación gaussiana por bloque
    tec_child  = _mutate_block(tec_child,  _BOUNDS_TECNICOS_PERIODS, SIGMA_PERIODS)
    tec_child  = _mutate_block(tec_child,  _BOUNDS_TECNICOS_WEIGHTS, SIGMA_WEIGHTS)
    mac_child  = _mutate_block(mac_child,  _BOUNDS_MACRO,            SIGMA_WEIGHTS)
    risk_child = _mutate_block(risk_child, _BOUNDS_RIESGO,           SIGMA_RISK)
    smc_child  = _mutate_block(smc_child,  _BOUNDS_SMC,              SIGMA_RISK)

    # Normalizar pesos y aplicar constraints
    tec_child  = _normalize_weights(tec_child, ["peso_rsi", "peso_ema", "peso_macd"])
    tec_child  = _enforce_ema_constraint(tec_child)
    risk_child = _enforce_sl_tp_constraint(risk_child)

    return {
        "id":               child_id,
        "fecha_nacimiento": birth_date,
        "generacion":       generation,
        "padre_1_id":       parent1["id"],
        "padre_2_id":       parent2["id"],
        "params_tecnicos":  tec_child,
        "params_macro":     mac_child,
        "params_riesgo":    risk_child,
        "params_smc":       smc_child,
        "capital_inicial":  10.0,
        "capital_actual":   10.0,
    }


# ── Motor evolutivo principal ─────────────────────────────────────────────────

@dataclass
class EvolutionResult:
    fecha: date
    survivors: list[dict] = field(default_factory=list)
    eliminated: list[dict] = field(default_factory=list)
    new_agents: list[dict] = field(default_factory=list)
    ranking_snapshot: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    capital_pool_total: float = 0.0
    capital_por_agente: float = 0.0


class EvolutionEngine:

    def __init__(self, today: date | None = None):
        self.today = today or date.today()

    # ── Consultas a la DB ────────────────────────────────────────────────────

    def _get_active_agents_ranked(self) -> list[dict]:
        """
        Retorna los agentes activos ordenados por fitness descendente.

        Criterios de desempate (en orden):
          1. roi_total DESC        — mejor rendimiento acumulado primero
          2. fecha_nacimiento DESC — en empate de ROI, el agente más joven sobrevive
          3. id DESC               — mismo día de nacimiento: el creado después (índice mayor) sobrevive
        """
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            # CTE con Calmar Ratio Proxy — ranking por fitness en lugar de roi_total
            cur.execute("""
                WITH capital_series AS (
                    SELECT agente_id, timestamp_entrada,
                           SUM(pnl) OVER (PARTITION BY agente_id ORDER BY timestamp_entrada)
                               AS capital_acumulado
                    FROM operaciones WHERE estado = 'cerrada'
                ),
                drawdown_calc AS (
                    SELECT agente_id,
                           MAX(capital_acumulado) OVER (
                               PARTITION BY agente_id
                               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                           ) AS peak,
                           capital_acumulado
                    FROM capital_series
                ),
                max_dd AS (
                    SELECT agente_id,
                           MAX((peak - capital_acumulado) / NULLIF(peak, 0)) AS max_drawdown
                    FROM drawdown_calc GROUP BY agente_id
                ),
                ops_diarias AS (
                    SELECT agente_id, AVG(ops_dia) AS avg_ops_dia
                    FROM (
                        SELECT agente_id, DATE(timestamp_entrada) AS dia, COUNT(*) AS ops_dia
                        FROM operaciones
                        WHERE estado IN ('cerrada', 'abierta')
                        GROUP BY agente_id, DATE(timestamp_entrada)
                    ) sub GROUP BY agente_id
                ),
                fitness AS (
                    SELECT a.id,
                           (a.roi_total / (COALESCE(d.max_drawdown, 0.01) + 1))
                           - CASE
                               WHEN o.avg_ops_dia > 3
                                    AND (a.operaciones_ganadoras::float
                                         / NULLIF(a.operaciones_total, 0)) < 0.5
                               THEN 0.5 ELSE 0
                             END AS fitness_score
                    FROM agentes a
                    LEFT JOIN max_dd      d ON a.id = d.agente_id
                    LEFT JOIN ops_diarias o ON a.id = o.agente_id
                    WHERE a.estado = 'activo'
                )
                SELECT a.id, a.generacion, a.fecha_nacimiento, a.capital_actual,
                       a.roi_total, a.operaciones_total, a.operaciones_ganadoras,
                       a.params_tecnicos, a.params_macro, a.params_riesgo, a.params_smc,
                       COALESCE(f.fitness_score, 0) AS fitness_score
                FROM agentes a
                LEFT JOIN fitness f ON a.id = f.id
                WHERE a.estado = 'activo'
                ORDER BY COALESCE(f.fitness_score, 0) DESC, a.fecha_nacimiento DESC, a.id DESC
            """)
            return [dict(row) for row in cur.fetchall()]

    def _get_next_agent_index(self) -> int:
        """Calcula el próximo número consecutivo para el ID del día."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM agentes WHERE fecha_nacimiento = %s",
                (self.today,),
            )
            return cur.fetchone()[0] + 1

    # ── Selección natural ────────────────────────────────────────────────────

    def select_survivors_and_eliminated(
        self, agents: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """
        Divide la población activa en supervivientes (top N) y eliminados (bottom N).
        Si hay menos de 2*N agentes activos, sólo elimina la mitad inferior.
        """
        n = min(N_ELIMINATE, len(agents) // 2)
        survivors = agents[:len(agents) - n]
        eliminated = agents[len(agents) - n:]
        return survivors, eliminated

    # ── Escritura en DB ──────────────────────────────────────────────────────

    def _eliminate_agents(self, conn, eliminated: list[dict], razon: str) -> None:
        cur = conn.cursor()
        ids = [a["id"] for a in eliminated]
        cur.execute(
            """
            UPDATE agentes
            SET estado = 'eliminado',
                fecha_eliminacion = %s,
                razon_eliminacion = %s
            WHERE id = ANY(%s)
            """,
            (self.today, razon, ids),
        )
        
        for agent in eliminated:
            try:
                ops_t = int(agent.get("operaciones_total", 0) or 0)
                ops_w = int(agent.get("operaciones_ganadoras", 0) or 0)
                SheetsLogger().update_agent_status(
                    agent_id          = agent["id"],
                    status            = "eliminado",
                    roi               = float(agent.get("roi_total", 0) or 0),
                    ops               = ops_t,
                    ops_ganadoras     = ops_w,
                    fitness           = float(agent.get("fitness_score", 0) or 0),
                    fecha_eliminacion = str(self.today),
                    razon_eliminacion = razon,
                    capital_final     = float(agent.get("capital_actual", 10.0) or 10.0),
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"[EvolutionEngine] Error updating sheet for agent {agent['id']}: {e}")

    def _insert_new_agent(self, conn, agent: dict) -> None:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO agentes (
                id, fecha_nacimiento, generacion,
                padre_1_id, padre_2_id,
                params_tecnicos, params_macro, params_riesgo, params_smc,
                capital_inicial, capital_actual, estado
            ) VALUES (
                %(id)s, %(fecha_nacimiento)s, %(generacion)s,
                %(padre_1_id)s, %(padre_2_id)s,
                %(params_tecnicos)s, %(params_macro)s, %(params_riesgo)s, %(params_smc)s,
                %(capital_inicial)s, %(capital_actual)s, 'activo'
            )
            """,
            {
                **agent,
                "params_tecnicos": json.dumps(agent["params_tecnicos"]),
                "params_macro":    json.dumps(agent["params_macro"]),
                "params_riesgo":   json.dumps(agent["params_riesgo"]),
                "params_smc":      json.dumps(agent.get("params_smc", _DEFAULT_SMC_PARAMS)),
            },
        )
        
        try:
            SheetsLogger().log_agent(agent)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[EvolutionEngine] Error logging new agent {agent['id']} to sheet: {e}")

    def _snapshot_ranking(self, conn, agents: list[dict], evento_map: dict[str, str]) -> None:
        cur = conn.cursor()
        for pos, agent in enumerate(agents, start=1):
            roi_total = float(agent.get("roi_total", 0))
            ops_total = int(agent.get("operaciones_total", 0))

            # ROI diario: diferencia entre capital_actual y capital_inicial
            cap_actual  = float(agent.get("capital_actual", 10.0))
            cap_inicial = 10.0
            roi_diario  = round((cap_actual - cap_inicial) / cap_inicial * 100, 4)

            fitness = round(float(agent.get("fitness_score", 0) or 0), 6)
            cur.execute(
                """
                INSERT INTO ranking_historico (
                    fecha, agente_id, posicion_ranking,
                    roi_diario, roi_acumulado, capital_fin_dia,
                    operaciones_dia, evento, fitness_score
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fecha, agente_id) DO UPDATE SET
                    posicion_ranking = EXCLUDED.posicion_ranking,
                    evento           = EXCLUDED.evento,
                    fitness_score    = EXCLUDED.fitness_score
                """,
                (
                    self.today,
                    agent["id"],
                    pos,
                    roi_diario,
                    roi_total,
                    cap_actual,
                    ops_total,
                    evento_map.get(agent["id"], "evaluacion"),
                    fitness,
                ),
            )

    def _save_hall_of_fame(self, conn, survivors: list[dict]) -> None:
        """Registra en estrategias_exitosas los supervivientes con ROI > umbral."""
        cur = conn.cursor()
        for agent in survivors:
            if float(agent.get("roi_total", 0)) >= MIN_ROI_HALL_OF_FAME:
                ops = int(agent.get("operaciones_total", 0))
                won = int(agent.get("operaciones_ganadoras", 0))
                win_rate = round(won / ops, 4) if ops > 0 else None
                cur.execute(
                    """
                    INSERT INTO estrategias_exitosas (
                        agente_origen_id, fecha_registro, roi_que_genero,
                        win_rate, params_tecnicos, params_macro, params_riesgo
                    )
                    SELECT %s, %s, %s, %s, %s, %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM estrategias_exitosas
                        WHERE agente_origen_id = %s AND fecha_registro = %s
                    )
                    """,
                    (
                        agent["id"], self.today,
                        float(agent["roi_total"]),
                        win_rate,
                        json.dumps(agent["params_tecnicos"]),
                        json.dumps(agent["params_macro"]),
                        json.dumps(agent["params_riesgo"]),
                        agent["id"], self.today,
                    ),
                )

    # ── Redistribución de capital ────────────────────────────────────────────

    def _redistribute_capital(
        self, conn, new_agent_ids: list[str], pool_override: float | None = None
    ) -> tuple[float, float]:
        """
        Reparte el pool de capital equitativamente entre todos los agentes activos.

        pool_override debe ser el SUM(capital_actual) de los 10 agentes ANTES de que
        el ciclo evolutivo elimine/nazca ninguno — es decir, el pool real post-EOD.
        Sin override (fallback), re-consulta la DB (incluye nuevos a $10, valor incorrecto).

        El pool total solo fluctúa por P&L real de trading; no se inyecta capital nuevo.
        Los agentes nuevos reciben su cuota del pool existente (capital_inicial = cuota).
        Los supervivientes mantienen su capital_inicial histórico; solo cambia capital_actual.

        Retorna (pool_total, capital_por_agente).
        """
        import logging
        log_r = logging.getLogger(__name__)

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM agentes WHERE estado = 'activo'")
        n_agentes = int(cur.fetchone()[0])

        if pool_override is not None:
            pool_total = pool_override
        else:
            cur.execute(
                "SELECT COALESCE(SUM(capital_actual), 0) FROM agentes WHERE estado = 'activo'"
            )
            pool_total = float(cur.fetchone()[0])

        if n_agentes == 0:
            return 0.0, 0.0

        capital_por_agente = round(pool_total / n_agentes, 4)

        # Todos los agentes activos quedan con el mismo capital para mañana
        cur.execute(
            "UPDATE agentes SET capital_actual = %s WHERE estado = 'activo'",
            (capital_por_agente,),
        )

        # Los agentes nuevos registran su capital_inicial real (no el hardcoded 10.0)
        if new_agent_ids:
            cur.execute(
                "UPDATE agentes SET capital_inicial = %s WHERE id = ANY(%s)",
                (capital_por_agente, new_agent_ids),
            )

        log_r.info(
            "[EvolutionEngine] Capital redistribuido: pool=%.4f / %d agentes = %.4f c/u",
            pool_total, n_agentes, capital_por_agente,
        )
        return pool_total, capital_por_agente

    # ── Ciclo evolutivo completo ──────────────────────────────────────────────

    def run(self) -> EvolutionResult:
        """
        Ejecuta el ciclo evolutivo completo y retorna un EvolutionResult con
        el resumen de lo ocurrido para que el JudgeAgent lo registre en logs_juez.
        """
        result = EvolutionResult(fecha=self.today)

        try:
            agents = self._get_active_agents_ranked()
            if len(agents) < 2:
                result.errors.append("Menos de 2 agentes activos. Ciclo omitido.")
                return result

            survivors, eliminated = self.select_survivors_and_eliminated(agents)
            result.survivors  = survivors
            result.eliminated = eliminated

            # Generar nuevos agentes (un hijo por cada eliminado)
            next_idx = self._get_next_agent_index()
            new_agents: list[dict] = []
            max_gen = max(int(a["generacion"]) for a in survivors)

            for i in range(len(eliminated)):
                # Elige 2 padres aleatoriamente del pool de supervivientes,
                # con probabilidad proporcional a su ROI (fitness-proportionate selection)
                rois = [max(float(a["roi_total"]), 0.0001) for a in survivors]
                total_roi = sum(rois)
                weights = [r / total_roi for r in rois]
                p1, p2 = random.choices(survivors, weights=weights, k=2)
                # Garantiza padres distintos si hay suficientes
                if p1["id"] == p2["id"] and len(survivors) > 1:
                    others = [a for a in survivors if a["id"] != p1["id"]]
                    p2 = random.choice(others)

                child_id = f"{self.today.strftime('%Y-%m-%d')}_{next_idx + i:02d}"
                child = breed_agent(p1, p2, child_id, self.today, max_gen + 1)
                new_agents.append(child)

            result.new_agents = new_agents

            # Pool real del día: suma de todos los agentes ANTES de eliminar/nacer ninguno.
            # Se usa en _redistribute_capital para que las pérdidas/ganancias del día
            # se reflejen correctamente (los nuevos nacen con $10 hardcoded, lo que
            # inflaría el pool si se re-consultara la DB después de su inserción).
            pool_total_eod = round(
                sum(float(a.get("capital_actual", 10.0)) for a in agents), 4
            )

            # Construir mapa de eventos para el snapshot
            evento_map: dict[str, str] = {}
            for a in eliminated:
                evento_map[a["id"]] = "eliminacion"
            for a in new_agents:
                evento_map[a["id"]] = "nacimiento"
            for a in survivors:
                evento_map[a["id"]] = "supervivencia"

            # Escribir todo en una única transacción
            with get_conn() as conn:
                self._eliminate_agents(
                    conn, eliminated,
                    f"Selección natural {self.today}: bottom {len(eliminated)} "
                    f"por ROI (desempate: agente más reciente sobrevive)",
                )
                for child in new_agents:
                    self._insert_new_agent(conn, child)
                self._save_hall_of_fame(conn, survivors)

                # Snapshot de ranking: capital real del día ANTES de redistribuir
                all_for_snapshot = survivors + new_agents
                self._snapshot_ranking(conn, all_for_snapshot, evento_map)
                self._snapshot_ranking(conn, eliminated, evento_map)

                # Redistribuir capital equitativamente entre todos los agentes activos
                new_agent_ids = [a["id"] for a in new_agents]
                pool_total, capital_por_agente = self._redistribute_capital(
                    conn, new_agent_ids, pool_override=pool_total_eod
                )
                result.capital_pool_total  = pool_total
                result.capital_por_agente  = capital_por_agente

            result.ranking_snapshot = [
                {"id": a["id"], "posicion": i + 1, "roi": a.get("roi_total", 0)}
                for i, a in enumerate(agents)
            ]

        except Exception as exc:
            result.errors.append(str(exc))
            raise

        return result
