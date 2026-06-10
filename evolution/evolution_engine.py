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
import math
import os
import random
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from dotenv import load_dotenv

from db.connection import get_conn, get_dict_cursor
from utils.sheets_logger import SheetsLogger

load_dotenv()

# ── Configuración desde .env ─────────────────────────────────────────────────
N_ELIMINATE = int(os.getenv("AGENTS_ELIMINATE_PER_CYCLE", "9"))  # 3 por especie × 3 especies

SIGMA_WEIGHTS = float(os.getenv("MUTATION_SIGMA_WEIGHTS", "0.05"))
SIGMA_PERIODS = float(os.getenv("MUTATION_SIGMA_PERIODS", "0.08"))
SIGMA_RISK = float(os.getenv("MUTATION_SIGMA_RISK", "0.10"))
MIN_ROI_HALL_OF_FAME = float(os.getenv("MIN_ROI_FOR_HALL_OF_FAME", "0.05"))

# ── Configuración evolutiva avanzada (Sesión 7) ──────────────────────────────
# Periodo de Gracia Operativa: agentes sin operaciones y más jóvenes que este
# umbral (en días HÁBILES, lun-vie) quedan inmunes a la eliminación.
GRACE_PERIOD_DAYS = int(os.getenv("GRACE_PERIOD_DAYS", "2"))

# Umbral de coeficiente de variación promedio del ADN de los supervivientes.
# Si el CV cae por debajo de este valor, se considera que el pool es un clon
# y el motor duplica la sigma de mutación para forzar exploración.
DIVERSITY_VARIANCE_THRESHOLD = float(os.getenv("DIVERSITY_VARIANCE_THRESHOLD", "0.01"))

# Multiplicador aplicado a las sigmas cuando se detecta baja diversidad genética.
SIGMA_BOOST_FACTOR = float(os.getenv("SIGMA_BOOST_FACTOR", "2.0"))

# ── Integridad evolutiva (Fase 1) ────────────────────────────────────────────
# Muestra mínima de operaciones cerradas antes de que un agente sea:
#   - elegible para eliminación
#   - elegible como padre de reproducción
#   - candidato al Hall of Fame
# Con < MIN_SAMPLE_TRADES el agente queda inmune (sin suficiente señal estadística).
# A 2-3 trades/día ≈ 5-7 días de trading para salir de inmunidad.
MIN_SAMPLE_TRADES = int(os.getenv("MIN_SAMPLE_TRADES", "15"))

# ── Rangos de seguridad para clamping post-mutación ──────────────────────────
_BOUNDS_TECNICOS_PERIODS = {
    "rsi_periodo":       (5,   50,  True),   # (min, max, is_int)
    "rsi_sobrecompra":   (55,  90,  False),
    "rsi_sobreventa":    (10,  45,  False),
    "rsi_zona_muerta":   (1.0, 15.0, False), # banda neutral RSI momentum (Session 15 — Fase 2)
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
    # Sesgo tendencial HTF (Session 15 — Fase 3): intensidad del prior de tendencia
    "peso_sesgo_tendencia":       (0.20, 0.65, False),
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
    "atr_factor":               1.5,
    "trailing_activation_pips": 15.0,
    "trailing_distance_pips":   10.0,
    "atr_period":               14,
    # HTF trend filter (Session 15 — Fase 1)
    "htf_filter_enabled":       1,      # 1=activo, 0=desactivado; no se muta gaussianamente
    # Ruptura S3 (Fase 2)
    "breakout_lookback_bars":   20,     # velas 15m para detectar ruptura de estructura
    "breakout_min_pips":        5.0,    # distancia mínima de ruptura confirmada
    "peso_breakout":            0.40,   # peso del score de ruptura en el ensamble S3
    # Régimen (Fase 2) — no se mutan gaussianamente; son umbrales estratégicos
    "adx_period":               14,
    "adx_threshold":            25.0,
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
    # Ruptura S3 (Fase 2) — mutables
    "breakout_lookback_bars":   (10,   50,    True),
    "breakout_min_pips":        (3.0,  15.0,  False),
    "peso_breakout":            (0.20,  0.70, False),
}

# Mínimo de agentes por especie para garantizar diversidad real.
# El motor evolutivo no elimina un agente si hacerlo bajaría su especie de este umbral.
_MIN_AGENTS_PER_ESPECIE = int(os.getenv("MIN_AGENTS_PER_ESPECIE", "2"))

# ── Torneo con umbral de calidad (Fase 1 Sesión 17) ─────────────────────────
# Fitness OOS mínimo (estrictamente mayor) para desplegar un hijo del torneo.
TOURNAMENT_MIN_OOS_FITNESS = float(os.getenv("TOURNAMENT_MIN_OOS_FITNESS", "0.0"))
# Trades OOS mínimos para desplegar un hijo del torneo.
TOURNAMENT_MIN_OOS_TRADES = int(os.getenv("TOURNAMENT_MIN_OOS_TRADES", "5"))

# ── Tope de pérdida a la inmunidad por muestra (Fase 3 Sesión 17) ────────────
# Un agente inmune solo por muestra insuficiente pierde la inmunidad si su
# roi_total (en %) cae por debajo de este umbral negativo.
IMMUNITY_MAX_LOSS_PCT = float(os.getenv("IMMUNITY_MAX_LOSS_PCT", "8.0"))

# ── Muestra mínima híbrida (Fase 4 Sesión 17) ────────────────────────────────
# Un agente es elegible si n_trades >= MIN_SAMPLE_TRADES O edad >= MIN_SAMPLE_DAYS
# días hábiles (lo que llegue primero). Evita que especies poco frecuentes
# (p.ej. tendencia en régimen RANGO crónico) queden perpetuamente inmunes.
MIN_SAMPLE_DAYS = int(os.getenv("MIN_SAMPLE_DAYS", "7"))

# ── Recuperación de cupos vacantes (Sesión 18 / 19) ──────────────────────────
# Objetivo de agentes activos por especie; el motor SIEMPRE intenta llenar todos
# los cupos faltantes (3 especies × 5 = población objetivo de 15 agentes).
TARGET_AGENTS_PER_ESPECIE  = int(os.getenv("TARGET_AGENTS_PER_ESPECIE",  "5"))
# DEPRECADO (Sesión 19): el tope por ciclo se eliminó para garantizar los 15.
# Se conserva el símbolo por compatibilidad con .env / imports antiguos.
REPOPULATION_MAX_PER_CYCLE = int(os.getenv("REPOPULATION_MAX_PER_CYCLE", "3"))
# Sesión 19: rondas de reintento (torneo → HoF) por cupo antes de recurrir al
# clon forzado del Hall of Fame. Acota el costo de backtests para no colgar el cron.
REPOPULATION_MAX_ATTEMPTS_PER_SLOT = int(
    os.getenv("REPOPULATION_MAX_ATTEMPTS_PER_SLOT", "8")
)


# ── Fitness: Expectancy ajustada por riesgo (Fase 1) ────────────────────────
#
# Fórmula:
#   expectancy_por_trade = win_rate × avg_win − (1−win_rate) × avg_loss
#   (ya incluye fricción: el P&L en DB es neto de spread+slippage desde Fase 0)
#
#   confianza_estadistica = LEAST(1.0, n_trades / MIN_SAMPLE_TRADES)
#   (escala de 0→1 mientras el agente acumula su muestra mínima)
#
#   fitness = (expectancy / (max_drawdown + 0.01))
#             × confianza_estadistica
#             − penalidad_overtrading
#
# Ventajas sobre el Calmar-ROI previo:
#   - Expectancy es por-trade: no se infla con pocas operaciones ganadoras.
#   - confianza_estadistica impide que 3 trades de suerte den fitness alto.
#   - max_drawdown penaliza el riesgo real tomado.
#   - El P&L ya es neto de costos → la evolución selecciona edges genuinos.

def _build_fitness_sql(min_sample: int) -> str:
    return f"""
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
    ops_stats AS (
        SELECT agente_id,
               COUNT(*)                                         AS n_trades,
               COUNT(*) FILTER (WHERE pnl > 0)                 AS n_wins,
               COALESCE(AVG(pnl)       FILTER (WHERE pnl > 0), 0) AS avg_win,
               COALESCE(AVG(ABS(pnl))  FILTER (WHERE pnl < 0), 0) AS avg_loss
        FROM operaciones
        WHERE estado = 'cerrada'
        GROUP BY agente_id
    )
    SELECT
        a.id,
        a.roi_total,
        COALESCE(d.max_drawdown,  0) AS max_drawdown,
        COALESCE(o.avg_ops_dia,   0) AS avg_ops_dia,
        COALESCE(s.n_trades,      0) AS n_trades,
        -- Expectancy neta por operacion
        CASE WHEN COALESCE(s.n_trades, 0) > 0 THEN
            (s.n_wins::float / s.n_trades)        * s.avg_win
            - (1.0 - s.n_wins::float / s.n_trades) * s.avg_loss
        ELSE 0 END                                AS expectancy_per_trade,
        -- Fitness = expectancy / drawdown * confianza_estadistica - overtrading
        (
            CASE WHEN COALESCE(s.n_trades, 0) > 0 THEN
                (s.n_wins::float / s.n_trades)        * s.avg_win
                - (1.0 - s.n_wins::float / s.n_trades) * s.avg_loss
            ELSE 0 END
            / (COALESCE(d.max_drawdown, 0.01) + 1)
            * LEAST(1.0, COALESCE(s.n_trades, 0)::float / {min_sample})
        )
        - CASE
            WHEN o.avg_ops_dia > 3
                 AND (a.operaciones_ganadoras::float
                      / NULLIF(a.operaciones_total, 0)) < 0.5
            THEN 0.5 ELSE 0
          END AS fitness_score
    FROM agentes a
    LEFT JOIN max_dd      d ON a.id = d.agente_id
    LEFT JOIN ops_diarias o ON a.id = o.agente_id
    LEFT JOIN ops_stats   s ON a.id = s.agente_id
    WHERE a.estado = 'activo'
"""


_FITNESS_SQL = _build_fitness_sql(MIN_SAMPLE_TRADES)


def calc_fitness_scores(conn, agent_ids: list[str] | None = None) -> dict[str, float]:
    """
    Expectancy ajustada por riesgo para agentes activos.
    Retorna {agente_id: fitness_score}.

    Fórmula: (expectancy/trade / (max_drawdown+1)) × confianza_estadistica − overtrading
    Neta de fricción (ya descontada en close_operation desde Fase 0).
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


# ── Cálculo de edad en días hábiles (Periodo de Gracia) ──────────────────────

def _business_days_between(start: date, end: date) -> int:
    """
    Cuenta días hábiles (lunes a viernes, weekday 0-4) en [start, end).

    El mercado Forex institucional no opera sábado ni domingo, por lo que
    estos días no acumulan edad para el Periodo de Gracia Operativa.

    Si end <= start retorna 0. El día de nacimiento se considera el día 0:
    un agente que nace el lunes y se evalúa el martes tiene 1 día hábil.
    """
    if end <= start:
        return 0
    days = 0
    cursor = start
    while cursor < end:
        if cursor.weekday() < 5:  # 0=lunes ... 4=viernes
            days += 1
        cursor += timedelta(days=1)
    return days


# ── Forzado de diversidad genética (Sesión 7) ────────────────────────────────

# Claves numéricas representativas que se inspeccionan para medir el ADN.
_DIVERSITY_KEYS_TEC = ("rsi_periodo", "ema_rapida", "ema_lenta",
                       "peso_rsi", "peso_ema", "peso_macd")
_DIVERSITY_KEYS_MAC = ("peso_noticias_alto", "umbral_sentimiento_compra",
                       "ventana_noticias_horas", "peso_total_macro")
_DIVERSITY_KEYS_SMC = ("fvg_min_pips", "risk_reward_target",
                       "macro_quarantine_minutes", "peso_fvg", "peso_ob",
                       "atr_factor")


def _compute_genetic_variance(agents: list[dict]) -> float:
    """
    Coeficiente de variación promedio (std/|mean|) sobre las claves numéricas
    representativas del ADN de los agentes.

    Un valor cercano a 0 indica que los supervivientes son clones cercanos
    (ADN estancado); valores >0.05 indican diversidad sana.

    Retorna 0.0 si hay menos de 2 agentes (no se puede medir varianza).
    """
    if len(agents) < 2:
        return 0.0

    sources = (
        ("params_tecnicos", _DIVERSITY_KEYS_TEC),
        ("params_macro",    _DIVERSITY_KEYS_MAC),
        ("params_smc",      _DIVERSITY_KEYS_SMC),
    )

    cvs: list[float] = []
    for block_key, keys in sources:
        for key in keys:
            values: list[float] = []
            for a in agents:
                block = a.get(block_key) or {}
                if key in block and block[key] is not None:
                    try:
                        values.append(float(block[key]))
                    except (TypeError, ValueError):
                        continue
            if len(values) < 2:
                continue
            mean = statistics.fmean(values)
            if mean == 0:
                # std absoluto sobre mean cero → si todos son cero, CV=0
                std = statistics.pstdev(values)
                cvs.append(0.0 if std == 0 else float("inf"))
            else:
                std = statistics.pstdev(values)
                cvs.append(std / abs(mean))

    finite_cvs = [c for c in cvs if math.isfinite(c)]
    if not finite_cvs:
        return 0.0
    return float(statistics.fmean(finite_cvs))


# ── Generación de un agente hijo completo ────────────────────────────────────

def breed_agent(
    parent1: dict,
    parent2: dict,
    child_id: str,
    birth_date: date,
    generation: int,
    sigma_weights: float | None = None,
    sigma_periods: float | None = None,
    sigma_risk: float | None = None,
    especie: str = "tendencia",
) -> dict:
    """
    Genera un nuevo agente a partir de dos padres.
    1. Crossover de cada bloque de parámetros.
    2. Mutación gaussiana.
    3. Normalización y constraints de seguridad.

    Las sigmas son opcionales: si no se pasan, se usan los defaults globales
    (lectura del .env). El motor evolutivo puede pasar valores boosteados
    cuando detecta baja diversidad genética en el pool de supervivientes.
    """
    sw = SIGMA_WEIGHTS if sigma_weights is None else sigma_weights
    sp = SIGMA_PERIODS if sigma_periods is None else sigma_periods
    sr = SIGMA_RISK    if sigma_risk    is None else sigma_risk

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

    # Mutación gaussiana por bloque (sigmas dinámicas)
    tec_child  = _mutate_block(tec_child,  _BOUNDS_TECNICOS_PERIODS, sp)
    tec_child  = _mutate_block(tec_child,  _BOUNDS_TECNICOS_WEIGHTS, sw)
    mac_child  = _mutate_block(mac_child,  _BOUNDS_MACRO,            sw)
    risk_child = _mutate_block(risk_child, _BOUNDS_RIESGO,           sr)
    smc_child  = _mutate_block(smc_child,  _BOUNDS_SMC,              sr)

    # Normalizar pesos y aplicar constraints
    tec_child  = _normalize_weights(tec_child, ["peso_rsi", "peso_ema", "peso_macd"])
    tec_child  = _enforce_ema_constraint(tec_child)
    risk_child = _enforce_sl_tp_constraint(risk_child)

    # S2 (reversion): forzar rsi_modo=reversion y htf_filter_enabled=0 tras crossover.
    # S1/S3: asegurar htf_filter_enabled=1 (no lo heredan apagado de un padre S2).
    if especie == "reversion":
        tec_child["rsi_modo"]         = "reversion"
        smc_child["htf_filter_enabled"] = 0
    else:
        smc_child["htf_filter_enabled"] = 1

    return {
        "id":               child_id,
        "fecha_nacimiento": birth_date,
        "generacion":       generation,
        "especie":          especie,
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

    # ── Periodo de Gracia / Cuota Dinámica (Sesión 7) ─────────────────────
    # Agentes inmunes esta tarde por Periodo de Gracia Operativa.
    immune_agents: list[dict] = field(default_factory=list)
    # Veteranos remanentes evaluables tras filtrar inmunes.
    eligible_veterans: list[dict] = field(default_factory=list)
    # Indica si el ciclo de eliminación/reproducción quedó suspendido.
    cycle_suspended: bool = False
    # Justificación técnica de la suspensión (se persiste en logs_juez).
    suspension_reason: str = ""

    # ── Diversidad genética (Sesión 7) ────────────────────────────────────
    # Coeficiente de variación promedio del ADN de los supervivientes.
    genetic_variance_cv: float = 0.0
    # True si las sigmas se duplicaron por baja diversidad.
    sigma_boost_applied: bool = False
    # Sigmas efectivamente utilizadas en este ciclo (para auditoría).
    sigma_used: dict = field(default_factory=dict)

    # ── Torneo con umbral de calidad (Fase 1 Sesión 17) ───────────────────
    # Slots que quedaron vacantes porque ningún candidato superó el umbral OOS.
    # Cada elemento: {"id": child_id, "especie": especie, "razon": str}.
    slots_vacantes: list[dict] = field(default_factory=list)

    # ── Recuperación de cupos vacantes (Sesión 18) ─────────────────────────
    # Cupos recuperados este ciclo: [{id, especie, fitness_oos, origen}].
    slots_recuperados: list[dict] = field(default_factory=list)
    # Déficit residual por especie que no pudo cubrirse: {especie: int}.
    deficit_restante: dict = field(default_factory=dict)


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
            # CTE con Expectancy ajustada por riesgo — ranking por fitness
            cur.execute(f"""
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
                ops_stats AS (
                    SELECT agente_id,
                           COUNT(*)                                         AS n_trades,
                           COUNT(*) FILTER (WHERE pnl > 0)                 AS n_wins,
                           COALESCE(AVG(pnl)      FILTER (WHERE pnl > 0), 0) AS avg_win,
                           COALESCE(AVG(ABS(pnl)) FILTER (WHERE pnl < 0), 0) AS avg_loss
                    FROM operaciones WHERE estado = 'cerrada'
                    GROUP BY agente_id
                ),
                fitness AS (
                    SELECT a.id,
                           (
                               CASE WHEN COALESCE(s.n_trades, 0) > 0 THEN
                                   (s.n_wins::float / s.n_trades)          * s.avg_win
                                   - (1.0 - s.n_wins::float / s.n_trades)  * s.avg_loss
                               ELSE 0 END
                               / (COALESCE(d.max_drawdown, 0.01) + 1)
                               * LEAST(1.0, COALESCE(s.n_trades, 0)::float / {MIN_SAMPLE_TRADES})
                           )
                           - CASE
                               WHEN o.avg_ops_dia > 3
                                    AND (a.operaciones_ganadoras::float
                                         / NULLIF(a.operaciones_total, 0)) < 0.5
                               THEN 0.5 ELSE 0
                             END AS fitness_score
                    FROM agentes a
                    LEFT JOIN max_dd      d ON a.id = d.agente_id
                    LEFT JOIN ops_diarias o ON a.id = o.agente_id
                    LEFT JOIN ops_stats   s ON a.id = s.agente_id
                    WHERE a.estado = 'activo'
                )
                SELECT a.id, a.generacion, a.fecha_nacimiento, a.capital_actual,
                       a.roi_total, a.operaciones_total, a.operaciones_ganadoras,
                       a.params_tecnicos, a.params_macro, a.params_riesgo, a.params_smc,
                       COALESCE(a.especie, 'tendencia') AS especie,
                       COALESCE(f.fitness_score, 0) AS fitness_score,
                       COALESCE(s.n_trades, 0) AS n_trades
                FROM agentes a
                LEFT JOIN fitness    f ON a.id = f.id
                LEFT JOIN ops_stats  s ON a.id = s.agente_id
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

    # ── Filtro de Elegibilidad: Periodo de Gracia Operativa ──────────────────

    def _classify_eligibility(
        self, agents: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """
        Separa la población activa en:
          - immune: agentes NO elegibles para eliminación esta tarde.
          - eligible: agentes con muestra suficiente para evaluación.

        Condiciones de inmunidad (se requiere al menos una):

          A. Periodo de Gracia original (sin datos, recién nacido):
             ops_total == 0 Y edad < GRACE_PERIOD_DAYS días hábiles.
             Protege agentes que el broker no ha podido ni evaluar.
             Esta inmunidad es INVIOLABLE (no la revoca el tope de pérdida).

          B. Muestra mínima híbrida (Fase 4 Sesión 17):
             n_trades < MIN_SAMPLE_TRADES Y edad < MIN_SAMPLE_DAYS días hábiles.
             Ambas condiciones deben cumplirse simultáneamente — si el agente
             lleva >= MIN_SAMPLE_DAYS días en producción ya es evaluable aunque
             tenga pocos trades (especie en régimen adverso, baja frecuencia).

          Excepción (Fase 3 Sesión 17 — tope de pérdida):
             Si B aplica pero roi_total <= -IMMUNITY_MAX_LOSS_PCT (%), la
             inmunidad se revoca: el agente pasa a eligible con fitness negativo
             y es candidato a eliminación. Documentado en razon_eliminacion.
             No afecta la inmunidad A (Periodo de Gracia).
        """
        immune: list[dict] = []
        eligible: list[dict] = []
        for a in agents:
            ops_total  = int(a.get("operaciones_total", 0) or 0)
            n_trades   = int(a.get("n_trades", ops_total) or ops_total)
            birth = a.get("fecha_nacimiento")
            if isinstance(birth, str):
                try:
                    birth = date.fromisoformat(birth)
                except ValueError:
                    birth = None
            age_business_days = (
                _business_days_between(birth, self.today) if birth else 999
            )

            # A. Periodo de Gracia (inviolable)
            immune_grace = (ops_total == 0 and age_business_days < GRACE_PERIOD_DAYS)

            # B. Muestra mínima híbrida (Fase 4): elegible si trades O días suficientes
            immune_sample = (
                n_trades < MIN_SAMPLE_TRADES
                and age_business_days < MIN_SAMPLE_DAYS
            )

            # Fase 3: tope de pérdida revoca inmunidad por muestra (no la de gracia)
            immunity_revoked = False
            if immune_sample and not immune_grace:
                roi = float(a.get("roi_total", 0) or 0)
                if roi <= -IMMUNITY_MAX_LOSS_PCT:
                    immune_sample = False
                    immunity_revoked = True

            if immune_grace or immune_sample:
                immune.append(a)
            else:
                # Propagar flag de revocación para documentarlo en razon_eliminacion
                if immunity_revoked:
                    a = dict(a)
                    a["_immunity_revoked"] = True
                eligible.append(a)
        return immune, eligible

    # ── Selección natural con Cuota Dinámica ─────────────────────────────────

    def select_survivors_and_eliminated(
        self, agents: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """
        Selección con CUOTA DINÁMICA: nunca elimina agentes con Fitness > 0
        solo para cumplir la cuota de N_ELIMINATE.

        Espera `agents` ya filtrados (sin inmunes) y ordenados por fitness
        DESC. La función ordena internamente por el criterio de eliminación:
          fitness_score ASC, fecha_nacimiento ASC, id ASC
        de modo que los primeros candidatos a salir son los veteranos
        rezagados con peor fitness.

        Solo se eliminan agentes cuyo fitness_score <= 0. Si todos los
        elegibles tienen fitness > 0, retorna (todos como supervivientes, []).
        """
        if not agents:
            return [], []

        # Orden inverso para identificar a los peores: fitness ASC,
        # veteranos primero (fecha_nacimiento ASC, id ASC).
        ordered_for_elim = sorted(
            agents,
            key=lambda a: (
                float(a.get("fitness_score", 0) or 0),
                a.get("fecha_nacimiento") or date.min,
                a.get("id", ""),
            ),
        )

        # Solo son eliminables los que arrastran fitness <= 0 (negativo o cero).
        # Esto protege a cualquier agente rentable y eficiente.
        eliminable = [
            a for a in ordered_for_elim
            if float(a.get("fitness_score", 0) or 0) <= 0
        ]

        # Protección de diversidad de especies (Fase 2): no eliminar un agente
        # si hacerlo bajaría su especie por debajo de _MIN_AGENTS_PER_ESPECIE.
        # Contamos cuántos activos hay por especie ANTES de eliminar nadie.
        especie_counts: dict[str, int] = {}
        for a in agents:
            esp = str(a.get("especie") or "tendencia")
            especie_counts[esp] = especie_counts.get(esp, 0) + 1

        protected_eliminable: list[dict] = []
        temp_counts = dict(especie_counts)
        for a in eliminable:
            esp = str(a.get("especie") or "tendencia")
            if temp_counts.get(esp, 0) > _MIN_AGENTS_PER_ESPECIE:
                protected_eliminable.append(a)
                temp_counts[esp] = temp_counts[esp] - 1
            # Si la especie ya está en el mínimo, este agente queda protegido.

        n = min(N_ELIMINATE, len(protected_eliminable))
        eliminated = protected_eliminable[:n]

        elim_ids = {a["id"] for a in eliminated}
        survivors = [a for a in agents if a["id"] not in elim_ids]
        return survivors, eliminated

    # ── Escritura en DB ──────────────────────────────────────────────────────

    def _eliminate_agents(
        self,
        conn,
        eliminated: list[dict],
        razon_default: str,
        razones_extra: dict[str, str] | None = None,
    ) -> None:
        """
        Elimina los agentes en la lista. Si razones_extra contiene el id del agente,
        usa esa razón (prefijada) en vez de razon_default. Esto permite documentar
        casos especiales como 'inmunidad revocada por drawdown' por agente.
        """
        cur = conn.cursor()
        for a in eliminated:
            razon = (razones_extra or {}).get(a["id"], razon_default)
            cur.execute(
                """
                UPDATE agentes
                SET estado = 'eliminado',
                    fecha_eliminacion = %s,
                    razon_eliminacion = %s
                WHERE id = %s
                """,
                (self.today, razon, a["id"]),
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
                capital_inicial, capital_actual, especie, estado
            ) VALUES (
                %(id)s, %(fecha_nacimiento)s, %(generacion)s,
                %(padre_1_id)s, %(padre_2_id)s,
                %(params_tecnicos)s, %(params_macro)s, %(params_riesgo)s, %(params_smc)s,
                %(capital_inicial)s, %(capital_actual)s, %(especie)s, 'activo'
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
        """Registra en estrategias_exitosas los supervivientes con muestra suficiente y ROI > umbral."""
        cur = conn.cursor()
        for agent in survivors:
            n_trades = int(agent.get("n_trades", 0) or 0)
            # Fase 4: muestra mínima híbrida — elegible si trades O días suficientes
            birth = agent.get("fecha_nacimiento")
            if isinstance(birth, str):
                try:
                    birth = date.fromisoformat(birth)
                except ValueError:
                    birth = None
            age_bd = _business_days_between(birth, self.today) if birth else 999
            has_enough_sample = (n_trades >= MIN_SAMPLE_TRADES or age_bd >= MIN_SAMPLE_DAYS)
            if not has_enough_sample:
                continue  # muestra insuficiente: no inscribir en Hall of Fame aún
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

    # ── Hall of Fame: consulta para fallback del torneo (Fase 1 Sesión 17) ──────

    def _get_hof_parents(self, especie: str | None = None) -> list[dict]:
        """
        Devuelve hasta 10 entradas del Hall of Fame como dicts de 'padre virtual',
        con todos los parámetros necesarios para breed_agent.

        Si se especifica especie, prioriza esa especie; si no hay suficientes
        (< 2) retorna de cualquier especie como fallback.

        Hace JOIN con agentes para recuperar params_smc y especie, ya que
        estrategias_exitosas solo almacena params_tecnicos / macro / riesgo.
        """
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            if especie:
                cur.execute(
                    """
                    SELECT e.agente_origen_id AS id,
                           e.roi_que_genero   AS roi_total,
                           e.params_tecnicos,
                           e.params_macro,
                           e.params_riesgo,
                           a.params_smc,
                           COALESCE(a.especie, 'tendencia') AS especie
                    FROM estrategias_exitosas e
                    JOIN agentes a ON e.agente_origen_id = a.id
                    WHERE COALESCE(a.especie, 'tendencia') = %s
                    ORDER BY e.roi_que_genero DESC
                    LIMIT 10
                    """,
                    (especie,),
                )
                rows = [dict(r) for r in cur.fetchall()]
                if len(rows) >= 2:
                    return rows
            # Fallback: cualquier especie
            cur.execute(
                """
                SELECT e.agente_origen_id AS id,
                       e.roi_que_genero   AS roi_total,
                       e.params_tecnicos,
                       e.params_macro,
                       e.params_riesgo,
                       a.params_smc,
                       COALESCE(a.especie, 'tendencia') AS especie
                FROM estrategias_exitosas e
                JOIN agentes a ON e.agente_origen_id = a.id
                ORDER BY e.roi_que_genero DESC
                LIMIT 10
                """
            )
            return [dict(r) for r in cur.fetchall()]

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

        # Reflejar nuevo capital de cada agente en Google Sheets
        try:
            cur.execute(
                """
                SELECT id, roi_total, operaciones_total, operaciones_ganadoras
                FROM agentes WHERE estado = 'activo'
                """
            )
            agents_for_sheets = cur.fetchall()
            sl = SheetsLogger()
            for ag in agents_for_sheets:
                try:
                    sl.update_agent_live(
                        agent_id=ag[0],
                        capital=capital_por_agente,
                        roi=float(ag[1] or 0),
                        ops=int(ag[2] or 0),
                        ops_ganadoras=int(ag[3] or 0),
                    )
                except Exception as e_ag:
                    log_r.error("[EvolutionEngine] Error actualizando Sheets agente %s: %s", ag[0], e_ag)
        except Exception as e_sheets:
            log_r.error("[EvolutionEngine] Error actualizando Sheets tras redistribución: %s", e_sheets)

        return pool_total, capital_por_agente

    # ── Recuperación de cupos vacantes (Sesión 18) ───────────────────────────

    def _try_repopulate(
        self,
        current_population: list[dict],
        parent_pool: list[dict],
        backtest_data,
        start_idx: int,
        max_gen: int,
        sw: float,
        sp: float,
        sr: float,
    ) -> tuple[list[dict], list[dict], dict]:
        """
        Recupera TODOS los cupos vacantes por especie hasta la población objetivo
        (Sesión 19: garantía de 15 agentes activos).

        Pipeline por cupo:
          1. Hasta REPOPULATION_MAX_ATTEMPTS_PER_SLOT rondas de
             (torneo N candidatos → umbral OOS) seguido de (HoF N candidatos → OOS).
             Se detiene en cuanto un candidato supera el umbral.
          2. Si tras agotar las rondas nadie pasa → CLON FORZADO del mejor agente
             del Hall of Fame (genética probada, origen='forzado_hof'); si no hay
             HoF, del mejor del pool de torneo (origen='forzado_pool'). Esto
             garantiza llenar el cupo sin insertar genética aleatoria.

        Sin tope por ciclo: se intentan todos los cupos faltantes.

        Si backtest_data es None se omite silenciosamente (sin datos de mercado no
        hay control de calidad ni base para clonar — respeta el fallback sin Yahoo).

        Returns:
          (recovered, slots_rec_log, deficit_restante)
          - recovered: agentes listos para insertar en DB.
          - slots_rec_log: [{id, especie, fitness_oos, origen}] para trazabilidad.
            origen ∈ {torneo, hall_of_fame, forzado_hof, forzado_pool}.
          - deficit_restante: {especie: n} cupos que no pudieron cubrirse (solo en
            casos degenerados sin pool ni HoF).
        """
        import logging
        _log = logging.getLogger(__name__)

        ESPECIES = ("tendencia", "reversion", "ruptura")

        count_by_especie: dict[str, int] = {esp: 0 for esp in ESPECIES}
        for a in current_population:
            esp = str(a.get("especie") or "tendencia")
            if esp in count_by_especie:
                count_by_especie[esp] += 1

        deficit_by_especie: dict[str, int] = {
            esp: max(0, TARGET_AGENTS_PER_ESPECIE - count_by_especie[esp])
            for esp in ESPECIES
        }
        total_deficit = sum(deficit_by_especie.values())

        if total_deficit == 0:
            return [], [], {}

        if backtest_data is None:
            _log.info(
                "[EvolutionEngine] Repopulación omitida: backtest no disponible. "
                "Déficit: %s", deficit_by_especie,
            )
            return [], [], {esp: d for esp, d in deficit_by_especie.items() if d > 0}

        from evolution.backtester import run_backtest, N_CANDIDATE_CHILDREN

        def _passes_oos(bt: dict) -> bool:
            return (
                bt["fitness"] > TOURNAMENT_MIN_OOS_FITNESS
                and bt["n_trades"] >= TOURNAMENT_MIN_OOS_TRADES
            )

        def _best_from_pool(pool: list[dict], esp_: str, cid: str) -> tuple[dict, dict]:
            """Cría N_CANDIDATE_CHILDREN ponderados por aptitud y devuelve
            (mejor_hijo, su_backtest). El pool debe tener >= 2 agentes."""
            scores = [
                max(float(a.get("fitness_score", a.get("roi_total", 0)) or 0), 0.0001)
                for a in pool
            ]
            total_score = sum(scores)
            weights = [s / total_score for s in scores]
            candidates: list[tuple[dict, dict]] = []
            for _c in range(N_CANDIDATE_CHILDREN):
                p1, p2 = random.choices(pool, weights=weights, k=2)
                if p1["id"] == p2["id"] and len(pool) > 1:
                    p2 = random.choice([a for a in pool if a["id"] != p1["id"]])
                candidate = breed_agent(
                    p1, p2, cid, self.today, max_gen + 1,
                    sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                    especie=esp_,
                )
                try:
                    bt = run_backtest(backtest_data, candidate)
                except Exception:
                    bt = {"fitness": 0.0, "n_trades": 0}
                candidates.append((candidate, bt))
            return max(candidates, key=lambda x: x[1]["fitness"])

        recovered: list[dict] = []
        slots_rec_log: list[dict] = []
        repop_idx = start_idx

        # Sesión 19: SIN tope por ciclo — se intentan TODOS los cupos faltantes.
        for esp in ESPECIES:
            deficit = deficit_by_especie[esp]
            if deficit == 0:
                continue

            same_species = [
                a for a in parent_pool
                if str(a.get("especie") or "tendencia") == esp
            ]
            tourn_pool = same_species if len(same_species) >= 2 else parent_pool

            # HoF de la especie: se consulta una vez por especie (no por cupo).
            try:
                hof_parents = self._get_hof_parents(esp)
            except Exception as _he:
                _log.warning(
                    "[EvolutionEngine] HoF query falló en repopulación: %s", _he
                )
                hof_parents = []

            for _ in range(deficit):
                child_id = f"{self.today.strftime('%Y-%m-%d')}_{repop_idx:02d}"
                repop_idx += 1

                child: dict | None = None
                best_bt: dict | None = None
                origen = ""

                # ── Reintentos torneo → HoF hasta MAX_ATTEMPTS rondas ──────────
                for _attempt in range(REPOPULATION_MAX_ATTEMPTS_PER_SLOT):
                    if len(tourn_pool) >= 2:
                        cand, bt = _best_from_pool(tourn_pool, esp, child_id)
                        if _passes_oos(bt):
                            child, best_bt, origen = cand, bt, "torneo"
                            break
                    if len(hof_parents) >= 2:
                        cand, bt = _best_from_pool(hof_parents, esp, child_id)
                        if _passes_oos(bt):
                            child, best_bt, origen = cand, bt, "hall_of_fame"
                            break

                # ── Último recurso: CLON FORZADO del mejor histórico ───────────
                # Garantiza el cupo. Genética probada (no aleatoria), pero entra
                # sin superar el OOS de hoy. Se marca origen='forzado_*' para
                # trazabilidad y se backtestea solo para registrar su fitness.
                if child is None:
                    forced_source = None
                    forced_origen = ""
                    if hof_parents:
                        forced_source = max(
                            hof_parents,
                            key=lambda p: float(p.get("roi_total", 0) or 0),
                        )
                        forced_origen = "forzado_hof"
                    elif tourn_pool:
                        forced_source = max(
                            tourn_pool,
                            key=lambda p: float(p.get("fitness_score", 0) or 0),
                        )
                        forced_origen = "forzado_pool"

                    if forced_source is not None:
                        child = breed_agent(
                            forced_source, forced_source, child_id, self.today,
                            max_gen + 1, sigma_weights=sw, sigma_periods=sp,
                            sigma_risk=sr, especie=esp,
                        )
                        try:
                            best_bt = run_backtest(backtest_data, child)
                        except Exception:
                            best_bt = {"fitness": 0.0, "n_trades": 0}
                        origen = forced_origen
                        _log.warning(
                            "[EvolutionEngine] Repopulación %s (%s): sin candidato OOS "
                            "tras %d rondas → CLON FORZADO de %s (origen=%s).",
                            child_id, esp, REPOPULATION_MAX_ATTEMPTS_PER_SLOT,
                            forced_source["id"], origen,
                        )

                if child is None:
                    # Degenerado: ni pool de torneo ni HoF disponibles.
                    _log.warning(
                        "[EvolutionEngine] Repopulación %s (%s): sin pool ni HoF; "
                        "cupo queda vacante.", child_id, esp,
                    )
                    continue

                recovered.append(child)
                slots_rec_log.append({
                    "id":          child["id"],
                    "especie":     esp,
                    "fitness_oos": round(best_bt["fitness"], 6),
                    "origen":      origen,
                })
                _log.info(
                    "[EvolutionEngine] Repopulación %s (%s): cubierto via %s "
                    "(fitness=%.5f, n=%d).",
                    child["id"], esp, origen,
                    best_bt["fitness"], best_bt["n_trades"],
                )

        recovered_by_esp: dict[str, int] = {esp: 0 for esp in ESPECIES}
        for r in slots_rec_log:
            recovered_by_esp[r["especie"]] += 1
        deficit_restante = {
            esp: deficit_by_especie[esp] - recovered_by_esp[esp]
            for esp in ESPECIES
            if deficit_by_especie[esp] - recovered_by_esp[esp] > 0
        }

        return recovered, slots_rec_log, deficit_restante

    # ── Ciclo evolutivo completo ──────────────────────────────────────────────

    def run(self) -> EvolutionResult:
        """
        Ejecuta el ciclo evolutivo completo y retorna un EvolutionResult con
        el resumen de lo ocurrido para que el JudgeAgent lo registre en logs_juez.

        Flujo (Sesión 7):
          A. Identifica y protege a los agentes bajo Periodo de Gracia.
          B. Evalúa veteranos remanentes con cuota dinámica.
          C. Si la cuota cae a 0, suspende eliminación/reproducción/redistribución.
          D. Si hay eliminación, aplica sigma boost cuando los supervivientes
             son clones genéticos cercanos.
        """
        result = EvolutionResult(fecha=self.today)
        # Sigmas por defecto (pueden ser sobrescritas por el boost de diversidad).
        result.sigma_used = {
            "weights": SIGMA_WEIGHTS,
            "periods": SIGMA_PERIODS,
            "risk":    SIGMA_RISK,
        }

        try:
            agents = self._get_active_agents_ranked()
            if len(agents) < 2:
                result.errors.append("Menos de 2 agentes activos. Ciclo omitido.")
                return result

            # ── PASO A: Periodo de Gracia Operativa ──────────────────────────
            immune, eligible = self._classify_eligibility(agents)
            result.immune_agents     = immune
            result.eligible_veterans = eligible

            # ── PASO B: Cuota dinámica sobre los elegibles ───────────────────
            survivors_eligible, eliminated = self.select_survivors_and_eliminated(eligible)

            # ── PASO C: ¿Se suspende el ciclo? ────────────────────────────────
            # Si no hay nada que eliminar (todos los elegibles tienen fitness>0
            # o el pool elegible está vacío porque todos están en gracia),
            # suspendemos: no se elimina, no se reproduce, no se redistribuye.
            if not eliminated:
                result.cycle_suspended = True
                if not eligible:
                    result.suspension_reason = (
                        f"Cuota = 0. Toda la población ({len(immune)} agentes) "
                        f"está bajo Periodo de Gracia Operativa "
                        f"(operaciones_total=0 y edad < {GRACE_PERIOD_DAYS} días hábiles). "
                        f"Se preserva la generación recién creada para que acumule "
                        f"datos reales en las próximas sesiones."
                    )
                else:
                    result.suspension_reason = (
                        f"Cuota = 0. Los {len(eligible)} veteranos elegibles "
                        f"presentan Fitness > 0 (rentables y eficientes) y los "
                        f"{len(immune)} agentes nuevos están bajo Periodo de Gracia. "
                        f"Eliminar a un veterano rentable solo para cumplir la cuota "
                        f"rígida canibalizaría capital sano; se suspende el ciclo."
                    )

                # Supervivientes = TODA la población activa (nadie sale).
                result.survivors  = agents
                result.eliminated = []
                result.new_agents = []

                # ── Sesión 18: intentar recuperación de cupos en ciclo suspendido ──
                _susp_bt_data = None
                try:
                    from evolution.backtester import fetch_backtest_data as _fetch_bt
                    _susp_bt_data = _fetch_bt()
                except Exception as _susp_exc:
                    import logging as _lg_susp
                    _lg_susp.getLogger(__name__).warning(
                        "[EvolutionEngine] Ciclo suspendido: backtest no disponible "
                        "(%s) — repopulación omitida.", _susp_exc,
                    )
                _susp_next_idx = self._get_next_agent_index()
                _susp_max_gen  = max((int(a["generacion"]) for a in agents), default=0)
                _recovered_s, _slots_rec_s, _deficit_rest_s = self._try_repopulate(
                    current_population=agents,
                    parent_pool=agents,
                    backtest_data=_susp_bt_data,
                    start_idx=_susp_next_idx,
                    max_gen=_susp_max_gen,
                    sw=SIGMA_WEIGHTS, sp=SIGMA_PERIODS, sr=SIGMA_RISK,
                )
                if _recovered_s:
                    result.new_agents = _recovered_s
                result.slots_recuperados = _slots_rec_s
                result.deficit_restante  = _deficit_rest_s
                # ──────────────────────────────────────────────────────────────

                # Snapshot de ranking para auditoría diaria (sin redistribuir capital
                # salvo que la repopulación haya tenido éxito).
                # NOTA: el CHECK constraint de ranking_historico.evento solo acepta
                # 'supervivencia', 'eliminacion', 'nacimiento', 'evaluacion'. La
                # distincion entre supervivencia normal y suspensión queda en
                # logs_juez.datos_json (immune_agents, cycle_suspended, suspension_reason).
                evento_map = {a["id"]: "supervivencia" for a in agents}
                for _ra in _recovered_s:
                    evento_map[_ra["id"]] = "nacimiento"
                with get_conn() as conn:
                    self._snapshot_ranking(conn, agents, evento_map)
                    for _ra in _recovered_s:
                        self._insert_new_agent(conn, _ra)
                    if _recovered_s:
                        self._snapshot_ranking(conn, _recovered_s, evento_map)
                        _pool_s = round(
                            sum(float(a.get("capital_actual", 10.0)) for a in agents), 4
                        )
                        pool_total, _cap_s = self._redistribute_capital(
                            conn, [a["id"] for a in _recovered_s], pool_override=_pool_s
                        )
                        result.capital_pool_total = pool_total
                        result.capital_por_agente = _cap_s
                    else:
                        pool_total = round(
                            sum(float(a.get("capital_actual", 10.0)) for a in agents), 4
                        )
                        n_active = len(agents) or 1
                        result.capital_pool_total = pool_total
                        result.capital_por_agente = round(pool_total / n_active, 4)

                result.ranking_snapshot = [
                    {"id": a["id"], "posicion": i + 1, "roi": a.get("roi_total", 0)}
                    for i, a in enumerate(agents)
                ]
                return result

            # ── PASO D: Ciclo activo — los supervivientes globales incluyen
            # tanto a los veteranos que sobrevivieron como a los inmunes.
            survivors_all = survivors_eligible + immune
            result.survivors  = survivors_all
            result.eliminated = eliminated

            # ── Forzado de diversidad genética ────────────────────────────────
            # Se mide sobre los supervivientes elegibles (los que harán de padres);
            # los inmunes recién nacidos podrían inflar artificialmente la varianza.
            parent_pool = survivors_eligible if survivors_eligible else survivors_all
            cv = _compute_genetic_variance(parent_pool)
            result.genetic_variance_cv = round(cv, 6)
            sw, sp, sr = SIGMA_WEIGHTS, SIGMA_PERIODS, SIGMA_RISK
            if cv < DIVERSITY_VARIANCE_THRESHOLD:
                sw = SIGMA_WEIGHTS * SIGMA_BOOST_FACTOR
                sp = SIGMA_PERIODS * SIGMA_BOOST_FACTOR
                sr = SIGMA_RISK    * SIGMA_BOOST_FACTOR
                result.sigma_boost_applied = True
            result.sigma_used = {
                "weights": round(sw, 6),
                "periods": round(sp, 6),
                "risk":    round(sr, 6),
            }

            # Generar nuevos agentes (un hijo por cada eliminado)
            next_idx = self._get_next_agent_index()
            new_agents: list[dict] = []
            max_gen = max(int(a["generacion"]) for a in survivors_all)

            # Pool de padres: usa solo elegibles si los hay para evitar
            # reproducir agentes que aún no han probado su rendimiento.
            parent_candidates = parent_pool

            # ── Fase 3: descargar datos de backtest UNA VEZ para todos los hijos ──
            # Si Yahoo Finance no está disponible, se degrada a crianza sin backtest.
            backtest_data = None
            use_backtest  = False
            if eliminated:
                try:
                    from evolution.backtester import fetch_backtest_data, run_backtest, N_CANDIDATE_CHILDREN
                    backtest_data = fetch_backtest_data()
                    use_backtest  = True
                    import logging as _lg
                    _lg.getLogger(__name__).info(
                        "[EvolutionEngine] Backtest habilitado: %d candidatos/slot.",
                        N_CANDIDATE_CHILDREN,
                    )
                except Exception as _exc:
                    import logging as _lg
                    _lg.getLogger(__name__).warning(
                        "[EvolutionEngine] Backtest no disponible (%s) — crianza sin preselección.",
                        _exc,
                    )

            # Fase 2: caché OOS de padres (evita re-backtestear el mismo padre por slot)
            parent_bt_cache: dict[str, float] = {}
            import logging as _lg_main
            _log = _lg_main.getLogger(__name__)

            slots_vacantes: list[dict] = []

            for i, elim in enumerate(eliminated):
                # El hijo hereda la especie del eliminado: sustituye como-por-como.
                child_especie = str(elim.get("especie") or "tendencia")

                # Prefiere padres de la misma especie para cruzar genes coherentes.
                same_species = [a for a in parent_candidates
                                if str(a.get("especie") or "tendencia") == child_especie]
                pool = same_species if len(same_species) >= 2 else parent_candidates

                # ── Fase 2: pesos OOS cuando todos los padres pierden ─────────
                # Si TODO el pool tiene fitness_score <= 0 y hay backtest disponible,
                # se pondera por fitness OOS en lugar del floor uniforme 0.0001.
                all_pool_negative = all(
                    float(a.get("fitness_score", 0) or 0) <= 0 for a in pool
                )
                if all_pool_negative and use_backtest and backtest_data is not None:
                    oos_scores = []
                    for _p in pool:
                        _pid = _p["id"]
                        if _pid not in parent_bt_cache:
                            try:
                                parent_bt_cache[_pid] = run_backtest(backtest_data, _p)["fitness"]
                            except Exception:
                                parent_bt_cache[_pid] = 0.0
                        oos_scores.append(max(parent_bt_cache[_pid], 0.0001))
                    scores = oos_scores
                    _log.info(
                        "[EvolutionEngine] Fase2: todos padres (%s) negativos — "
                        "pesos OOS: %s",
                        child_especie,
                        [f"{s:.5f}" for s in scores],
                    )
                else:
                    scores = [max(float(a.get("fitness_score", 0) or 0), 0.0001)
                              for a in pool]

                total_score = sum(scores)
                weights = [s / total_score for s in scores]

                def _select_parents(
                    _pool=pool, _weights=weights
                ):
                    p1_, p2_ = random.choices(_pool, weights=_weights, k=2)
                    if p1_["id"] == p2_["id"] and len(_pool) > 1:
                        p2_ = random.choice([a for a in _pool if a["id"] != p1_["id"]])
                    return p1_, p2_

                child_id = f"{self.today.strftime('%Y-%m-%d')}_{next_idx + i:02d}"

                if use_backtest and backtest_data is not None:
                    # ── Torneo de N candidatos: criar N, desplegar el mejor OOS ──
                    candidates: list[tuple[dict, dict]] = []
                    for _c in range(N_CANDIDATE_CHILDREN):
                        p1, p2 = _select_parents()
                        candidate = breed_agent(
                            p1, p2, child_id, self.today, max_gen + 1,
                            sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                            especie=child_especie,
                        )
                        try:
                            bt = run_backtest(backtest_data, candidate)
                        except Exception as _e:
                            _log.warning(
                                "[EvolutionEngine] Backtest candidato %s falló: %s",
                                child_id, _e,
                            )
                            bt = {"fitness": 0.0, "expectancy": 0.0, "n_trades": 0}
                        candidates.append((candidate, bt))

                    # Elegir el candidato con mayor fitness OOS
                    child, best_bt = max(candidates, key=lambda x: x[1]["fitness"])
                    _log.info(
                        "[EvolutionEngine] Slot %s (%s): %d candidatos → "
                        "mejor OOS fitness=%.5f expectancy=%.5f n=%d",
                        child_id, child_especie, len(candidates),
                        best_bt["fitness"], best_bt["expectancy"], best_bt["n_trades"],
                    )

                    # ── Fase 1: umbral de calidad ─────────────────────────────
                    passes = (
                        best_bt["fitness"] > TOURNAMENT_MIN_OOS_FITNESS
                        and best_bt["n_trades"] >= TOURNAMENT_MIN_OOS_TRADES
                    )
                    if not passes:
                        _log.warning(
                            "[EvolutionEngine] Slot %s (%s) no superó umbral OOS "
                            "(fitness=%.5f, n_trades=%d) — intentando HoF.",
                            child_id, child_especie,
                            best_bt["fitness"], best_bt["n_trades"],
                        )
                        # Fallback a: criar desde Hall of Fame
                        try:
                            hof_parents = self._get_hof_parents(child_especie)
                        except Exception as _he:
                            _log.warning("[EvolutionEngine] HoF query falló: %s", _he)
                            hof_parents = []
                        if len(hof_parents) >= 2:
                            hof_scores = [max(float(p.get("roi_total", 0) or 0), 0.0001)
                                          for p in hof_parents]
                            hof_total  = sum(hof_scores)
                            hof_w = [s / hof_total for s in hof_scores]
                            hof_candidates: list[tuple[dict, dict]] = []
                            for _hc in range(N_CANDIDATE_CHILDREN):
                                hp1, hp2 = random.choices(hof_parents, weights=hof_w, k=2)
                                if hp1["id"] == hp2["id"] and len(hof_parents) > 1:
                                    hp2 = random.choice(
                                        [p for p in hof_parents if p["id"] != hp1["id"]]
                                    )
                                hof_child = breed_agent(
                                    hp1, hp2, child_id, self.today, max_gen + 1,
                                    sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                                    especie=child_especie,
                                )
                                try:
                                    hof_bt = run_backtest(backtest_data, hof_child)
                                except Exception:
                                    hof_bt = {"fitness": 0.0, "expectancy": 0.0, "n_trades": 0}
                                hof_candidates.append((hof_child, hof_bt))
                            hof_best, hof_best_bt = max(
                                hof_candidates, key=lambda x: x[1]["fitness"]
                            )
                            passes = (
                                hof_best_bt["fitness"] > TOURNAMENT_MIN_OOS_FITNESS
                                and hof_best_bt["n_trades"] >= TOURNAMENT_MIN_OOS_TRADES
                            )
                            if passes:
                                child = hof_best
                                best_bt = hof_best_bt
                                _log.info(
                                    "[EvolutionEngine] Slot %s (%s) cubierto por HoF: "
                                    "fitness=%.5f n=%d",
                                    child_id, child_especie,
                                    hof_best_bt["fitness"], hof_best_bt["n_trades"],
                                )

                    if passes:
                        new_agents.append(child)
                    else:
                        razon_vac = (
                            f"Ningún candidato superó umbral OOS "
                            f"(mejor fitness={best_bt['fitness']:.5f}, "
                            f"n_trades={best_bt['n_trades']})"
                        )
                        slots_vacantes.append({
                            "id": child_id,
                            "especie": child_especie,
                            "razon": razon_vac,
                        })
                        _log.warning(
                            "[EvolutionEngine] Slot %s (%s) VACANTE: %s",
                            child_id, child_especie, razon_vac,
                        )
                else:
                    p1, p2 = _select_parents()
                    child = breed_agent(
                        p1, p2, child_id, self.today, max_gen + 1,
                        sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                        especie=child_especie,
                    )
                    new_agents.append(child)

            result.new_agents    = new_agents
            result.slots_vacantes = slots_vacantes

            # ── Sesión 18: Recuperar cupos vacantes ───────────────────────────
            _recovered, _slots_rec_log, _deficit_rest = self._try_repopulate(
                current_population=survivors_all + new_agents,
                parent_pool=survivors_all,
                backtest_data=backtest_data if use_backtest else None,
                start_idx=next_idx + len(eliminated),
                max_gen=max_gen,
                sw=sw, sp=sp, sr=sr,
            )
            if _recovered:
                new_agents.extend(_recovered)
                result.new_agents = new_agents
            result.slots_recuperados = _slots_rec_log
            result.deficit_restante  = _deficit_rest

            # Pool real del día: suma de todos los agentes ANTES de eliminar/nacer ninguno.
            pool_total_eod = round(
                sum(float(a.get("capital_actual", 10.0)) for a in agents), 4
            )

            # Construir mapa de eventos para el snapshot
            evento_map: dict[str, str] = {}
            for a in eliminated:
                evento_map[a["id"]] = "eliminacion"
            for a in new_agents:
                evento_map[a["id"]] = "nacimiento"
            for a in survivors_eligible:
                evento_map[a["id"]] = "supervivencia"
            for a in immune:
                # NOTA: usamos 'supervivencia' (no 'supervivencia_gracia') porque
                # el CHECK constraint de ranking_historico.evento no acepta otros
                # valores. La condicion de inmunidad queda registrada en
                # logs_juez.datos_json.immune_agents.
                evento_map[a["id"]] = "supervivencia"

            # Escribir todo en una única transacción
            with get_conn() as conn:
                razon_elim_base = (
                    f"Selección natural {self.today}: cuota dinámica = "
                    f"{len(eliminated)} (fitness <= 0). Desempate por veteranía "
                    f"(fecha_nacimiento ASC, id ASC). Inmunes en gracia: "
                    f"{len(immune)}."
                )
                # Fase 3: razones individuales para agentes con inmunidad revocada
                razones_extra: dict[str, str] = {}
                for a in eliminated:
                    if a.get("_immunity_revoked"):
                        roi = float(a.get("roi_total", 0) or 0)
                        razones_extra[a["id"]] = (
                            f"Inmunidad revocada por drawdown "
                            f"(roi={roi:.1f}% <= -{IMMUNITY_MAX_LOSS_PCT:.1f}%). "
                            + razon_elim_base
                        )
                self._eliminate_agents(conn, eliminated, razon_elim_base, razones_extra)
                for child in new_agents:
                    self._insert_new_agent(conn, child)
                self._save_hall_of_fame(conn, survivors_eligible)

                # Snapshot de ranking: capital real del día ANTES de redistribuir
                all_for_snapshot = survivors_all + new_agents
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
