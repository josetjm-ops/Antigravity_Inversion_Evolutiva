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
import time
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
    "risk_reward_target":       3.0,
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
    # Salidas inteligentes (Sesión 22) — genes evolutivos
    "be_activation_r":          0.92,   # mover SL a break-even al ganar este múltiplo de R
    "exit_on_reversal":         0,      # 1=salir ante señal contraria fuerte; 0/1, muta por bit-flip
    # Trailing on/off como gen (2026-08-12). Hasta ahora el trailing era
    # OBLIGATORIO — trailing_activation_pips nunca podía valer 0 — y además el
    # backtester no lo modelaba, así que la evolución jamás pudo evaluar si
    # aporta. Al modelarlo se midió que CUESTA -0.189R sobre el holdout de 6
    # meses (+0.319R sin trailing vs +0.131R con él): convierte ganadores en
    # stops (TP cae de 11 a 3 sobre 117 operaciones, SL sube de 76 a 101),
    # porque salta a mitad de camino del objetivo y una retracción normal
    # cierra la posición. Ahora la selección natural decide, con el backtest
    # viéndolo por primera vez. Arranca en 0 (apagado) por lo que dice la
    # medición, pero el bit-flip lo mantiene re-descubrible.
    "trailing_enabled":         0,      # 1=trailing activo; 0/1, muta por bit-flip
    "min_profit_for_exit_r":    0.4,    # ganancia mínima (en R) para permitir salida por señal
    # Salida parcial + runner (Fase 3, rediseño 2026-07-02): al alcanzar este
    # múltiplo de R se cierra el 50% de la posición (fricción propia + BE en
    # el resto), dejando correr el resto hacia el TP/trailing normal. Ataca
    # la firma "avg_win≈avg_loss pese a R:R objetivo 2.0" de la auditoría
    # 2026-07-01 — el sistema cortaba ganadores antes de que corrieran.
    "partial_tp_r":              1.0,
    # Sesión de trading (Fase 3, rediseño 2026-07-02): "cualquiera" (default,
    # sin restricción horaria más allá de la ventana global de trading),
    # "londres" (07:00-16:00 UTC), "ny" (12:00-21:00 UTC), "overlap"
    # (12:00-16:00 UTC, máxima liquidez EUR/USD). Gen categórico — no se muta
    # gaussianamente, muta por sorteo (ver _CATEGORICAL_GENE_MUTATION_PROB).
    "sesion_trading":            "cualquiera",
}

_BOUNDS_SMC = {
    "fvg_min_pips":             (2.0,  15.0,  False),
    "ob_impulse_pips":          (5.0,  20.0,  False),
    "range_spike_multiplier":   (1.2,   3.0,  False),
    # Historial: 1.5 → 2.5 (auditoría 2026-07-31) → 3.4 (2026-08-10) → 2.5-3.5
    # (2026-08-12). El rango 3.4-4.0 producía objetivos de 34-40 pips que en 24
    # operaciones de producción no se alcanzaron ni una vez (máximo favorable
    # medio: 15.2% del objetivo, cero take-profits).
    #
    # La hipótesis inicial —que el culpable era el cierre EOD ausente en el
    # backtester— se midió y quedó REFUTADA: sobre el mismo holdout y con el
    # mismo filtro de sesión, añadir el cierre EOD solo resta 0.069R. El
    # backtest corregido sigue prefiriendo R:R alto (3.82→+0.366R, 2.7→+0.315R,
    # 2.2→+0.185R), así que bajar a 2.0-3.0 habría sido sobrerreaccionar a una
    # muestra de 2 días.
    #
    # 2.5-3.5 es el punto medio justificado: el backtester TAMPOCO modela el
    # trailing stop, que en vivo cierra ganadores antes del objetivo (69
    # salidas por trailing en 90 días a 11.4 pips medios vs 22.3 de los TP),
    # de modo que su óptimo está sesgado hacia arriba. Se conserva la mayor
    # parte del edge estimado con objetivos dentro del percentil 90 real de
    # las salidas ganadoras (27.7 pips).
    "risk_reward_target":       (2.5,   3.5,  False),
    "macro_quarantine_minutes": (30,  120,    True),
    "risk_pct_per_trade":       (0.01,  0.02, False),
    "peso_fvg":                 (0.05,  0.50, False),
    "peso_ob":                  (0.05,  0.50, False),
    # ATR-based SL + Trailing Stop (Session 6)
    # Sesión 22: tope de atr_factor 3.0 → 1.8. Con ATR alto, 3.0 producía SL
    # de 60+ pips cuyo TP (2R = 120+ pips) era inalcanzable intradía: el
    # fitness de esos genomas era ruido (solo EOD o SL completo) y la
    # selección natural operaba sobre datos sin señal.
    "atr_factor":               (0.8,   1.8,  False),
    "trailing_activation_pips": (5.0,  40.0,  False),
    "trailing_distance_pips":   (5.0,  25.0,  False),
    "atr_period":               (7,    21,    True),
    # Ruptura S3 (Fase 2) — mutables
    "breakout_lookback_bars":   (10,   50,    True),
    "breakout_min_pips":        (3.0,  15.0,  False),
    "peso_breakout":            (0.20,  0.70, False),
    # Salidas inteligentes (Sesión 22) — mutables
    # Piso 0.3→0.8 (auditoría 2026-07-31) →0.85 (revisión 2026-08-10): mismo
    # efecto atractor que risk_reward_target — los agentes del 8-ago nacieron
    # todos clavados en 0.80. Los campeones validados operan en 0.88-1.00.
    "be_activation_r":          (0.85,  1.2,  False),
    "min_profit_for_exit_r":    (0.2,   1.0,  False),
    # Salida parcial + runner (Fase 3) — mutable
    "partial_tp_r":              (0.5,   2.0,  False),
    # exit_on_reversal NO va aquí: es 0/1 y muta por bit-flip en breed_agent.
    # sesion_trading NO va aquí: es categórico, muta por sorteo (ver abajo).
}

# Probabilidad de invertir genes booleanos 0/1 en cada crianza (Sesión 22).
# Mantiene el rasgo re-descubrible si se extingue de la población; la
# selección natural decide si la salida por señal contraria aporta edge.
_BOOLEAN_GENE_FLIP_PROB = {"exit_on_reversal": 0.10, "trailing_enabled": 0.10}

# ── Genes categóricos (Fase 3, rediseño 2026-07-02) ─────────────────────────
# Como los booleanos, no se mutan gaussianamente: con probabilidad
# _CATEGORICAL_GENE_MUTATION_PROB, el gen sortea un valor nuevo (puede ser
# el mismo) de sus opciones — mantiene el rasgo explorable si se extingue.
_CATEGORICAL_GENE_OPTIONS = {
    "sesion_trading": ["cualquiera", "londres", "ny", "overlap"],
}
_CATEGORICAL_GENE_MUTATION_PROB = {"sesion_trading": 0.10}

# Mínimo de agentes por especie para garantizar diversidad real.
# El motor evolutivo no elimina un agente si hacerlo bajaría su especie de este umbral.
_MIN_AGENTS_PER_ESPECIE = int(os.getenv("MIN_AGENTS_PER_ESPECIE", "2"))

# ── Torneo con umbral de calidad (Fase 1 Sesión 17) ─────────────────────────
# Fitness OOS mínimo (estrictamente mayor) para desplegar un hijo del torneo.
TOURNAMENT_MIN_OOS_FITNESS = float(os.getenv("TOURNAMENT_MIN_OOS_FITNESS", "0.0"))
# Trades OOS mínimos para desplegar un hijo del torneo.
TOURNAMENT_MIN_OOS_TRADES = int(os.getenv("TOURNAMENT_MIN_OOS_TRADES", "5"))


def _passes_oos_gate(bt: dict) -> bool:
    """
    Fase 2 (PLAN_DE_MEJORA.md): gate único de despliegue de un candidato OOS.

    TOURNAMENT_GATE_MODE=legacy (default): umbral débil histórico
      (fitness OOS > 0 & n_trades OOS >= TOURNAMENT_MIN_OOS_TRADES).
    TOURNAMENT_GATE_MODE=bootstrap: exige que el límite inferior del IC
      (BOOTSTRAP_CI) de la expectancy, estimado por bootstrap sobre los
      P&L de oos_trades, sea > 0 — distingue edge real de azar en muestras
      pequeñas (ver bootstrap_edge_ok en evolution/backtester.py).

    Import perezoso de evolution.backtester (mismo patrón que el resto del
    módulo) para no forzar su carga en callers que no ejecutan backtest.
    """
    from evolution.backtester import TOURNAMENT_GATE_MODE, bootstrap_edge_ok

    if TOURNAMENT_GATE_MODE == "bootstrap":
        passes, _lower = bootstrap_edge_ok(bt.get("oos_trades", []))
        return passes

    return (
        bt["fitness"] > TOURNAMENT_MIN_OOS_FITNESS
        and bt["n_trades"] >= TOURNAMENT_MIN_OOS_TRADES
    )

# ── Tope de pérdida a la inmunidad por muestra (Fase 3 Sesión 17) ────────────
# Un agente inmune solo por muestra insuficiente pierde la inmunidad si su
# roi_total (en %) cae por debajo de este umbral negativo.
IMMUNITY_MAX_LOSS_PCT = float(os.getenv("IMMUNITY_MAX_LOSS_PCT", "8.0"))

# ── Muestra mínima híbrida (Fase 4 Sesión 17) ────────────────────────────────
# Un agente es elegible si n_trades >= MIN_SAMPLE_TRADES O edad >= MIN_SAMPLE_DAYS
# días hábiles (lo que llegue primero). Evita que especies poco frecuentes
# (p.ej. tendencia en régimen RANGO crónico) queden perpetuamente inmunes.
MIN_SAMPLE_DAYS = int(os.getenv("MIN_SAMPLE_DAYS", "7"))

# ── Regla de bleeder crónico (Fase 2, rediseño 2026-07-02) ───────────────────
# Elimina SIEMPRE (sin importar cuota ni piso de especie) a un agente con
# fitness catastrófico y muestra grande — un bleeder confirmado no debe
# sobrevivir solo porque su especie está en el mínimo. Ver auditoría
# 2026-07-01, hallazgo 3: el piso de especie blindaba a 2026-06-12_08 (ROI
# real -354%, fitness apenas -0.005 en la escala vieja comprimida en dólares).
# Con el fitness en R (Fase 1) -0.3 ya es una expectancy claramente mala.
BLEEDER_FITNESS_THRESHOLD = float(os.getenv("BLEEDER_FITNESS_THRESHOLD", "-0.3"))
BLEEDER_MIN_TRADES        = int(os.getenv("BLEEDER_MIN_TRADES", "20"))

# ── Tope de bajas por especie (decisión de diseño 2026-07-25) ────────────────
# De los 5 miembros de una especie salen como máximo 3 en un ciclo, de modo que
# siempre queden 2 padres de los que nazcan los 3 reemplazos y la población
# vuelva a 15. A diferencia del tope global AGENTS_ELIMINATE_PER_CYCLE, este se
# evalúa por especie e INCLUYE a los bleeders crónicos (antes iban fuera de
# cuota y podían vaciar una especie por debajo del piso de padres).
MAX_ELIMINATE_POR_ESPECIE = int(os.getenv("MAX_ELIMINATE_POR_ESPECIE", "3"))

# ── Capital proporcional a fitness (Fase 2, rediseño 2026-07-02) ────────────
# Reemplaza la redistribución equitativa (mismo capital para todos, ganador y
# perdedor) — auditoría 2026-07-01, hallazgo P0-2: "la redistribución
# equitativa de capital APAGA la selección natural". El peso multiplicador de
# cada agente es clamp(1 + fitness_score, FLOOR, CAP); el capital final se
# normaliza para que la suma siga siendo exactamente el pool total (no se
# crea ni destruye capital). FLOOR=CAP=1.0 colapsa al reparto equitativo
# anterior — es el kill-switch si hace falta revertir sin re-desplegar código.
CAPITAL_WEIGHT_FLOOR = float(os.getenv("CAPITAL_WEIGHT_FLOOR", "0.5"))
CAPITAL_WEIGHT_CAP   = float(os.getenv("CAPITAL_WEIGHT_CAP",   "2.0"))

# ── Gate de muestra para ponderar capital (decisión de diseño 2026-07-25) ────
# El propietario pidió que todos los agentes amanezcan con el mismo capital
# ("mismas condiciones"). El riesgo de igualarlo SIEMPRE es apagar la selección
# natural otra vez (hallazgo P0-2). El punto medio implementado: la cuota es
# EXACTAMENTE equitativa mientras el agente no tenga muestra suficiente — con
# pocas operaciones el fitness es ruido y ponderar por ruido añade varianza sin
# añadir retorno — y solo un agente con evidencia real se gana su sobrepeso.
#   =20    -> punto medio (default)
#   =0     -> ponderación siempre (comportamiento Fase 2 previo)
#   =99999 -> reparto equitativo puro para todos
CAPITAL_WEIGHT_MIN_TRADES = int(os.getenv("CAPITAL_WEIGHT_MIN_TRADES", "20"))

# ── Recuperación de cupos vacantes (Sesión 18 / 19) ──────────────────────────
# Objetivo de agentes activos por especie; el motor intenta llenar todos los
# cupos faltantes (3 especies × 5 = población objetivo de 15 agentes) PERO ya
# no fuerza genomas sin evidencia de edge (ver REPOBLACION_PERMITE_VACANTES).
TARGET_AGENTS_PER_ESPECIE  = int(os.getenv("TARGET_AGENTS_PER_ESPECIE",  "5"))
# Override opcional del cupo de "ruptura". Estuvo en 3 desde la Fase 2
# (auditoría 2026-07-01: 24.8% WR, 68% de la pérdida total). Vuelve a 5 por
# decisión de diseño del 2026-07-25: población fija de 15 agentes, 5 por
# especie, para conservar diversidad de régimen. Se conserva la variable como
# palanca por si hace falta volver a reducir el cupo sin re-desplegar código.
TARGET_AGENTS_RUPTURA = int(os.getenv("TARGET_AGENTS_RUPTURA", "2"))
TARGET_AGENTS_TENDENCIA = int(os.getenv("TARGET_AGENTS_TENDENCIA", "2"))
TARGET_AGENTS_REVERSION = int(os.getenv("TARGET_AGENTS_REVERSION", "11"))

# ── Concentración en la especie validada (2026-08-10) ───────────────────────
# Veredicto de la revisión del 10-ago: con grupo de control real en producción,
# el perfil campeón dio +0.184R (24 ops) mientras el legacy dio -0.794R (27
# ops); TODA la pérdida vino de agentes sin edge validado. De las 3 especies,
# solo reversion tiene un perfil que pasó el gate bootstrap contra holdout
# (+0.50R a +0.71R, n=92-115) — tendencia y ruptura fallaron el mismo gate en
# la evolución offline y pierden en producción.
#
# Se concentra la población en reversion (11) dejando tendencia y ruptura en
# el piso de 2 (_MIN_AGENTS_PER_ESPECIE) en vez de eliminarlas: conservan
# diversidad de régimen por si el mercado cambia, pero dejan de consumir 2/3
# del capital sin evidencia. Revierte la paridad 5-5-5 del 2026-07-25 SOLO
# durante la ventana de validación de 4 semanas; si reversion confirma el edge
# se puede reevaluar la distribución.
_TARGET_OVERRIDE_POR_ESPECIE = {
    "reversion": TARGET_AGENTS_REVERSION,
    "tendencia": TARGET_AGENTS_TENDENCIA,
    "ruptura":   TARGET_AGENTS_RUPTURA,
}

# ── Gate OOS sin bypass forzado (Fase 2, rediseño 2026-07-02) ────────────────
# Antes, si ningún candidato de cruce superaba el umbral OOS Y las rondas
# torneo/HoF se agotaban, el motor recurría a "forzado_cruce"/"forzado_clon_
# unico": desplegar el mejor genoma disponible SIN evidencia de edge, solo
# para garantizar 15 agentes. Eso es precisamente "cantidad sobre calidad" —
# la causa de que sobrevivan especies sin edge real (ver PLAN_REDISENO_
# RENTABILIDAD.md, hallazgo S3). Con este flag en True (default), un cupo sin
# candidato que demuestre edge queda VACANTE (población flotante, piso
# implícito = 2 por especie × 3 = 6, protegido por _MIN_AGENTS_PER_ESPECIE en
# la eliminación) en vez de forzar un genoma sin evidencia. False reproduce el
# comportamiento anterior (kill-switch operativo sin re-desplegar código).
REPOBLACION_PERMITE_VACANTES = (
    os.getenv("REPOBLACION_PERMITE_VACANTES", "true").lower() != "false"
)
# DEPRECADO (Sesión 19): el tope por ciclo se eliminó para garantizar los 15.
# Se conserva el símbolo por compatibilidad con .env / imports antiguos.
REPOPULATION_MAX_PER_CYCLE = int(os.getenv("REPOPULATION_MAX_PER_CYCLE", "3"))
# Sesión 19: rondas de reintento (torneo → HoF) por cupo antes de recurrir al
# clon forzado del Hall of Fame. Acota el costo de backtests para no colgar el cron.
REPOPULATION_MAX_ATTEMPTS_PER_SLOT = int(
    os.getenv("REPOPULATION_MAX_ATTEMPTS_PER_SLOT", "8")
)

# ── Presupuesto de tiempo de repoblación (Fase 3 PLAN_DE_MEJORA.md) ─────────
# El multi-fold cuesta ~1.68× un backtest single (medido). Este presupuesto
# SOLO se aplica cuando BACKTEST_MODE=multifold — en modo single (default)
# no se activa ninguna comprobación de tiempo (comportamiento legacy intacto).
# Si se agota, los cupos restantes saltan directo a la degradación configurada
# (por defecto: cupo vacante; con REPOBLACION_PERMITE_VACANTES=false, cascada
# forzado_cruce/forzado_clon_unico) en vez de intentar rondas completas de
# torneo→HoF — así el ciclo no agota el timeout de judge_daily.yml (25 min)
# por exceso de backtests multi-fold.
REPOPULATION_TIME_BUDGET_SECONDS = int(
    os.getenv("REPOPULATION_TIME_BUDGET_SECONDS", "900")
)


# ── Fitness: Expectancy en R ajustada por riesgo (Fase 1 rediseño 2026-07-02) ─
#
# Fórmula:
#   R_trade = pnl / riesgo_planificado_usd
#   riesgo_planificado_usd = capital_usado × pips_sl × 0.0001 / precio_entrada
#     (= la pérdida exacta si el SL se hubiera tocado; ya es lo que el sizer
#     de SubAgentRisk usa para dimensionar la posición, así que "1R" siempre
#     significa "el riesgo que este trade específico llevaba planeado")
#   expectancy_R = win_rate × avg_win_R − (1−win_rate) × avg_loss_R
#
#   confianza_estadistica = LEAST(1.0, n_trades / MIN_SAMPLE_TRADES)
#   (escala de 0→1 mientras el agente acumula su muestra mínima)
#
#   max_drawdown = pico a valle sobre la curva de capital REAL del agente
#   (capital_inicial fijo de nacimiento + SUM(pnl) acumulado), no una suma
#   sin base — antes arrancaba en 0 y no representaba una curva de equity real.
#
#   fitness = (expectancy_R / (max_drawdown + 1))
#             × confianza_estadistica
#             − penalidad_overtrading_continua
#
# Por qué expectancy en R y no en USD (hallazgo F1, auditoría 2026-07-01):
#   el capital de cada agente cambia con el tiempo (redistribución nocturna),
#   así que el pnl en dólares de un mismo agente no es comparable entre sí
#   mismo en distintas fechas, ni entre agentes con capital distinto. R
#   normaliza cada trade por SU PROPIO riesgo planeado — invariante a la
#   escala de capital, comparable entre agentes y a través del tiempo.
#
# Penalidad de overtrading continua (hallazgo F4): reemplaza el acantilado
# binario (-0.5 si ops/día>3 y winrate<50%) por una función continua que
# crece con el exceso de frecuencia y con qué tan mal es el winrate, sin
# discontinuidad — antes era ~25x la escala típica de la señal.
#
# Ventajas sobre el diseño previo (dólares + DD sin base):
#   - Expectancy en R es escala-invariante: comparable entre agentes y en
#     el tiempo pese a la redistribución de capital.
#   - confianza_estadistica impide que pocos trades de suerte den fitness alto.
#   - max_drawdown ahora refleja la curva de equity real del agente.
#   - El P&L ya es neto de costos → la evolución selecciona edges genuinos.

def _fitness_cte(min_sample: int) -> str:
    """
    CTE compartida que calcula fitness_score por agente activo. Antes vivía
    duplicada (con fórmula en dólares) en _build_fitness_sql() y en el SQL
    inline de _get_active_agents_ranked() — una sola fuente de verdad ahora.
    """
    return f"""
    capital_series AS (
        SELECT o.agente_id, o.timestamp_entrada,
               a.capital_inicial + SUM(o.pnl) OVER (
                   PARTITION BY o.agente_id ORDER BY o.timestamp_entrada
               ) AS capital_acumulado
        FROM operaciones o
        JOIN agentes a ON a.id = o.agente_id
        WHERE o.estado = 'cerrada'
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
               COUNT(*)                        AS n_trades,
               COUNT(*) FILTER (WHERE pnl > 0) AS n_wins,
               -- R = pnl / riesgo_planificado_usd; NULLIF evita división por 0
               -- cuando falta pips_sl/capital_usado/precio_entrada (trades
               -- previos a esa instrumentación) — AVG ignora los NULL.
               COALESCE(AVG(
                   pnl / NULLIF(capital_usado * pips_sl * 0.0001
                                / NULLIF(precio_entrada, 0), 0)
               ) FILTER (WHERE pnl > 0), 0)       AS avg_win_r,
               COALESCE(AVG(ABS(
                   pnl / NULLIF(capital_usado * pips_sl * 0.0001
                                / NULLIF(precio_entrada, 0), 0)
               )) FILTER (WHERE pnl < 0), 0)       AS avg_loss_r
        FROM operaciones
        WHERE estado = 'cerrada'
        GROUP BY agente_id
    ),
    fitness AS (
        SELECT a.id,
               COALESCE(s.n_trades, 0) AS n_trades_fitness,
               (
                   CASE WHEN COALESCE(s.n_trades, 0) > 0 THEN
                       (s.n_wins::float / s.n_trades)        * s.avg_win_r
                       - (1.0 - s.n_wins::float / s.n_trades) * s.avg_loss_r
                   ELSE 0 END
                   / (COALESCE(d.max_drawdown, 0.01) + 1)
                   * LEAST(1.0, COALESCE(s.n_trades, 0)::float / {min_sample})
               )
               - LEAST(0.3, GREATEST(0, COALESCE(o.avg_ops_dia, 0) - 3) * 0.03
                       * GREATEST(0, 0.5 - COALESCE(
                           s.n_wins::float / NULLIF(s.n_trades, 0), 0.5)))
                 AS fitness_score
        FROM agentes a
        LEFT JOIN max_dd      d ON a.id = d.agente_id
        LEFT JOIN ops_diarias o ON a.id = o.agente_id
        LEFT JOIN ops_stats   s ON a.id = s.agente_id
        WHERE a.estado = 'activo'
    )
    """


def _build_fitness_sql(min_sample: int) -> str:
    return f"""
    WITH {_fitness_cte(min_sample)}
    SELECT a.id, f.fitness_score
    FROM agentes a
    JOIN fitness f ON a.id = f.id
    WHERE a.estado = 'activo'
"""


def _build_fitness_detail_sql(min_sample: int) -> str:
    """Igual que _build_fitness_sql pero arrastra el tamaño de muestra.

    Lo necesita _redistribute_capital para NO ponderar capital con un fitness
    calculado sobre pocas operaciones (ver CAPITAL_WEIGHT_MIN_TRADES).
    """
    return f"""
    WITH {_fitness_cte(min_sample)}
    SELECT a.id, f.fitness_score, f.n_trades_fitness
    FROM agentes a
    JOIN fitness f ON a.id = f.id
    WHERE a.estado = 'activo'
"""


_FITNESS_SQL = _build_fitness_sql(MIN_SAMPLE_TRADES)
_FITNESS_DETAIL_SQL = _build_fitness_detail_sql(MIN_SAMPLE_TRADES)


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


def calc_fitness_detail(conn, agent_ids: list[str] | None = None) -> dict[str, dict]:
    """
    Como calc_fitness_scores pero con el tamaño de muestra de cada agente.
    Retorna {agente_id: {"fitness": float, "n_trades": int}}.

    Existe para que _redistribute_capital pueda distinguir un fitness
    respaldado por operaciones reales de uno que todavía es ruido.
    """
    sql    = _FITNESS_DETAIL_SQL
    params: tuple = ()
    if agent_ids:
        sql    = _FITNESS_DETAIL_SQL + " AND a.id = ANY(%s)"
        params = (agent_ids,)

    cur = get_dict_cursor(conn)
    cur.execute(sql, params)
    return {
        row["id"]: {
            "fitness":  float(row["fitness_score"] or 0),
            "n_trades": int(row["n_trades_fitness"] or 0),
        }
        for row in cur.fetchall()
    }


# ── Helpers de mutación ──────────────────────────────────────────────────────

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# Tope de las columnas NUMERIC(8,4) de ranking_historico (roi_diario,
# roi_acumulado, fitness_score): 8 dígitos totales, 4 decimales -> |v| < 10000.
_MAX_NUMERIC_8_4 = 9999.9999


def _log_modulo():
    """Logger del módulo. El resto del archivo importa logging localmente
    dentro de cada función; esto evita repetir el import en cada llamada."""
    import logging
    return logging.getLogger(__name__)


def _clamp_numerico(valor: float, tope: float, campo: str, agente_id: str) -> float:
    """
    Recorta un valor al rango de su columna en vez de dejar que el INSERT falle.

    Existe por el incidente del 2026-08-26: un roi_diario de 10.543 (calculado
    con un capital inicial hardcodeado) desbordó NUMERIC(8,4), el INSERT lanzó
    NumericValueOutOfRange y la transacción revirtió el ciclo evolutivo
    COMPLETO. El Juez murió así 21 días seguidos.

    La lección no es solo corregir aquel cálculo: es que un paso de AUDITORÍA
    nunca debería poder abortar la evolución. Si un valor se sale de rango se
    recorta y se registra un WARNING — el dato queda marcado como sospechoso,
    pero los agentes siguen evolucionando.
    """
    if valor != valor or valor in (float("inf"), float("-inf")):  # NaN / inf
        _log_modulo().warning(
            "[EvolutionEngine] %s de %s no es finito (%r) — se guarda 0.",
            campo, agente_id, valor,
        )
        return 0.0
    if abs(valor) > tope:
        recortado = tope if valor > 0 else -tope
        _log_modulo().warning(
            "[EvolutionEngine] %s de %s fuera de rango (%.4f) — recortado a "
            "%.4f para no abortar el ciclo. Revisar el calculo de origen.",
            campo, agente_id, valor, recortado,
        )
        return recortado
    return valor


def _fitness_y_muestra(entrada) -> tuple[float, int | None]:
    """
    Normaliza una entrada de fitness_map a (fitness, n_trades).

    Acepta el formato nuevo de calc_fitness_detail ({"fitness", "n_trades"}) y
    el antiguo de calc_fitness_scores (float suelto). En el formato antiguo el
    tamaño de muestra es desconocido y se devuelve None — el llamador decide
    qué hacer con esa incertidumbre.
    """
    if isinstance(entrada, dict):
        return (
            float(entrada.get("fitness", 0.0) or 0.0),
            int(entrada.get("n_trades", 0) or 0),
        )
    return float(entrada or 0.0), None


def _real_roi_pct(agent: dict) -> float:
    """
    ROI real del agente en % — (capital_actual - capital_inicial) / capital_inicial.

    Reemplaza el uso de `agentes.roi_total` para decisiones (Fase 1, rediseño
    2026-07-02): esa columna es una SUMA ARITMÉTICA de pnl_pct de cada trade
    (investor_agent.py:close_operation) sobre una base de capital que cambia
    cada noche por la redistribución equitativa — no es un ROI real y puede
    marcar -354%/+476% mientras el capital de todos los agentes es idéntico.
    capital_inicial es fijo desde el nacimiento del agente (solo lo actualiza
    _redistribute_capital una vez, al insertar), así que esta razón sí es un
    ROI geométrico válido sobre la vida del agente.
    """
    # OJO: NO usar `agent.get(k, default) or default` — un capital_inicial/
    # capital_actual legítimamente en 0.0 es falsy en Python y ese patrón lo
    # confundiría con "ausente", pisándolo con el default (bug encontrado por
    # test_real_roi_pct_capital_inicial_cero_no_explota).
    cap_ini_raw = agent.get("capital_inicial")
    cap_ini = float(cap_ini_raw) if cap_ini_raw is not None else 10.0
    cap_act_raw = agent.get("capital_actual")
    cap_act = float(cap_act_raw) if cap_act_raw is not None else cap_ini
    if cap_ini <= 0:
        return 0.0
    return round((cap_act - cap_ini) / cap_ini * 100, 4)


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
    p1_weight: float | None = None,
) -> dict:
    """
    Genera un nuevo agente a partir de dos padres.
    1. Crossover de cada bloque de parámetros.
    2. Mutación gaussiana.
    3. Normalización y constraints de seguridad.

    Las sigmas son opcionales: si no se pasan, se usan los defaults globales
    (lectura del .env). El motor evolutivo puede pasar valores boosteados
    cuando detecta baja diversidad genética en el pool de supervivientes.

    p1_weight opcional: por defecto el padre de mejor fitness domina el cruce
    (60/40). El cruce forzado entre especies lo sobreescribe para que el
    genoma de la especie correcta (parent1) sea siempre el dominante.
    """
    sw = SIGMA_WEIGHTS if sigma_weights is None else sigma_weights
    sp = SIGMA_PERIODS if sigma_periods is None else sigma_periods
    sr = SIGMA_RISK    if sigma_risk    is None else sigma_risk

    # Crossover con sesgo hacia el padre de mejor fitness (salvo override).
    # Antes usaba roi_total, que es una suma aritmética rota por la
    # redistribución de capital (ver _real_roi_pct) — fitness_score es la
    # métrica ajustada por riesgo que ya decide selección/eliminación, así
    # que domina el cruce con el mismo criterio. Los padres "virtuales" del
    # Hall of Fame también llevan fitness_score (fitness_registro persistido
    # al momento de su inscripción) desde la migración 013.
    if p1_weight is None:
        fit1 = float(parent1.get("fitness_score", 0) or 0)
        fit2 = float(parent2.get("fitness_score", 0) or 0)
        p1_weight = 0.6 if fit1 >= fit2 else 0.4

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

    # Mutación bit-flip de genes booleanos 0/1 (Sesión 22): con probabilidad
    # baja el rasgo se invierte, manteniéndolo explorable por la evolución.
    for _bool_gene, _flip_p in _BOOLEAN_GENE_FLIP_PROB.items():
        if random.random() < _flip_p:
            smc_child[_bool_gene] = 1 - int(smc_child.get(_bool_gene, 0) or 0)

    # Mutación por sorteo de genes categóricos (Fase 3): con probabilidad
    # baja, el gen sortea un valor nuevo de sus opciones (puede repetir el
    # heredado) — mismo espíritu que el bit-flip, para genes no numéricos.
    for _cat_gene, _cat_p in _CATEGORICAL_GENE_MUTATION_PROB.items():
        if random.random() < _cat_p:
            smc_child[_cat_gene] = random.choice(_CATEGORICAL_GENE_OPTIONS[_cat_gene])

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
        # Fase 1 (PLAN_DE_MEJORA.md): default None; el llamador lo sobreescribe
        # con la promesa OOS del torneo cuando hay backtest disponible.
        "fitness_oos_prometido":  None,
        "n_trades_oos_prometido": None,
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
          1. fitness_score DESC    — mejor desempeño ajustado por riesgo primero
          2. fecha_nacimiento DESC — en empate, el agente más joven sobrevive
          3. id DESC               — mismo día de nacimiento: el creado después (índice mayor) sobrevive

        Incluye capital_inicial (fijo desde el nacimiento del agente): lo usan
        _classify_eligibility (roi real, no el roi_total aditivo roto) para el
        tope de pérdida que revoca inmunidad por muestra.
        """
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            # CTE compartida con _build_fitness_sql (_fitness_cte) — ranking por fitness
            cur.execute(f"""
                WITH {_fitness_cte(MIN_SAMPLE_TRADES)}
                SELECT a.id, a.generacion, a.fecha_nacimiento,
                       a.capital_actual, a.capital_inicial,
                       a.roi_total, a.operaciones_total, a.operaciones_ganadoras,
                       a.params_tecnicos, a.params_macro, a.params_riesgo, a.params_smc,
                       COALESCE(a.especie, 'tendencia') AS especie,
                       COALESCE(f.fitness_score, 0) AS fitness_score,
                       COALESCE(f.n_trades_fitness, 0) AS n_trades
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

    def _renumber_contiguous(
        self, agents: list[dict], start_idx: int
    ) -> dict[str, str]:
        """
        Reasigna IDs consecutivos (_NN) a los agentes que SÍ se insertarán, en
        su orden de aparición y arrancando en start_idx, eliminando los huecos
        que dejan los slots rechazados por el umbral OOS.

        Sin esto el ID se asignaba por posición del cupo (next_idx + i): si un
        slot no pasaba el umbral quedaba vacante pero igual 'quemaba' su índice,
        y el agente realmente insertado podía nacer '_02' sin que existiera
        '_01'. Aquí los nacidos quedan _01, _02, … sin saltos.

        Muta cada dict in-place (campo 'id') y devuelve {id_viejo: id_nuevo} para
        propagar el cambio a los logs de auditoría (slots_recuperados).
        """
        remap: dict[str, str] = {}
        prefix = self.today.strftime("%Y-%m-%d")
        for offset, child in enumerate(agents):
            new_id = f"{prefix}_{start_idx + offset:02d}"
            if child.get("id") != new_id:
                remap[child["id"]] = new_id
                child["id"] = new_id
        return remap

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
             Si B aplica pero el ROI real (_real_roi_pct, capital_actual vs
             capital_inicial) <= -IMMUNITY_MAX_LOSS_PCT (%), la inmunidad se
             revoca: el agente pasa a eligible con fitness negativo y es
             candidato a eliminación. Documentado en razon_eliminacion.
             No afecta la inmunidad A (Periodo de Gracia).
             (Antes usaba agentes.roi_total, una suma aritmética de pnl_pct
             sin relación con la pérdida real del agente — ver _real_roi_pct.)
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
                roi_real = _real_roi_pct(a)
                if roi_real <= -IMMUNITY_MAX_LOSS_PCT:
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
        Selección con CUOTA DINÁMICA + regla de bleeder crónico (Fase 2).

        Espera `agents` ya filtrados (sin inmunes) y ordenados por fitness
        DESC.

        Tope por especie (decisión de diseño 2026-07-25): ninguna especie
        pierde más de MAX_ELIMINATE_POR_ESPECIE (3) miembros por ciclo ni baja
        del piso _MIN_AGENTS_PER_ESPECIE (2). Así siempre quedan 2 padres de
        los que nacen los 3 reemplazos y la población vuelve a 15.

        1. Bleeder crónico (Fase 2 rediseño 2026-07-02): agentes con
           fitness_score <= BLEEDER_FITNESS_THRESHOLD y n_trades >=
           BLEEDER_MIN_TRADES tienen PRIORIDAD de salida — un bleeder
           confirmado con muestra grande no es "mala suerte", es evidencia de
           que ese genoma no tiene edge. Desde 2026-07-25 consumen cupo de su
           especie (antes eran incondicionales); el que no quepa encabeza la
           fila del ciclo siguiente.

        2. Cuota dinámica (comportamiento previo, sobre el resto): nunca
           elimina agentes con fitness > 0 solo para cumplir la cuota de
           N_ELIMINATE. Ordena por fitness_score ASC, fecha_nacimiento ASC,
           id ASC — los primeros candidatos a salir son los veteranos
           rezagados con peor fitness. Solo elimina fitness_score <= 0.
        """
        if not agents:
            return [], []

        def _especie_de(a: dict) -> str:
            return str(a.get("especie") or "tendencia")

        # Presupuesto de bajas por especie: nunca más de
        # MAX_ELIMINATE_POR_ESPECIE, y nunca por debajo del piso de padres.
        vivos: dict[str, int] = {}
        for a in agents:
            esp = _especie_de(a)
            vivos[esp] = vivos.get(esp, 0) + 1
        bajas: dict[str, int] = {}

        def _hay_cupo(esp: str) -> bool:
            return (
                bajas.get(esp, 0) < MAX_ELIMINATE_POR_ESPECIE
                and vivos.get(esp, 0) > _MIN_AGENTS_PER_ESPECIE
            )

        def _tomar(a: dict) -> None:
            esp = _especie_de(a)
            bajas[esp] = bajas.get(esp, 0) + 1
            vivos[esp] = vivos.get(esp, 0) - 1

        # ── 1. Bleeder crónico: máxima prioridad, DENTRO del cupo ─────────────
        # Antes se eliminaban sin importar cuota ni piso de especie. Desde la
        # decisión de diseño 2026-07-25 cuentan contra los 3 cupos de su
        # especie y respetan el piso de 2 padres: la población debe volver a
        # 15 con 2 progenitores por especie. Un bleeder que no quepa este ciclo
        # sigue siendo el primero en la fila del siguiente (su fitness no mejora
        # solo), así que la regla se aplaza, no se anula.
        bleeders_candidatos = sorted(
            (a for a in agents
             if float(a.get("fitness_score", 0) or 0) <= BLEEDER_FITNESS_THRESHOLD
             and int(a.get("n_trades", 0) or 0) >= BLEEDER_MIN_TRADES),
            key=lambda a: (
                float(a.get("fitness_score", 0) or 0),
                a.get("fecha_nacimiento") or date.min,
                a.get("id", ""),
            ),
        )
        bleeders: list[dict] = []
        for a in bleeders_candidatos:
            if _hay_cupo(_especie_de(a)):
                bleeders.append(a)
                _tomar(a)

        bleeder_ids = {a["id"] for a in bleeders}
        # Los bleeders que NO cupieron quedan fuera de la cuota dinámica de
        # este ciclo: ya se evaluaron con su propia regla y no deben volver a
        # competir por un cupo que su especie no tiene.
        no_cupo_ids = {a["id"] for a in bleeders_candidatos} - bleeder_ids
        remaining = [a for a in agents
                     if a["id"] not in bleeder_ids and a["id"] not in no_cupo_ids]

        # ── 2. Cuota dinámica sobre el resto ──────────────────────────────────
        # Orden inverso para identificar a los peores: fitness ASC,
        # veteranos primero (fecha_nacimiento ASC, id ASC).
        ordered_for_elim = sorted(
            remaining,
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

        # Protección doble: cupo por especie (3) y piso de padres (2).
        por_cuota: list[dict] = []
        for a in eliminable:
            if len(por_cuota) >= N_ELIMINATE:
                break
            if _hay_cupo(_especie_de(a)):
                por_cuota.append(a)
                _tomar(a)
            # Si la especie agotó su cupo o está en el piso, queda protegido.

        eliminated = bleeders + por_cuota

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
                capital_inicial, capital_actual, especie, estado,
                fitness_oos_prometido, n_trades_oos_prometido
            ) VALUES (
                %(id)s, %(fecha_nacimiento)s, %(generacion)s,
                %(padre_1_id)s, %(padre_2_id)s,
                %(params_tecnicos)s, %(params_macro)s, %(params_riesgo)s, %(params_smc)s,
                %(capital_inicial)s, %(capital_actual)s, %(especie)s, 'activo',
                %(fitness_oos_prometido)s, %(n_trades_oos_prometido)s
            )
            """,
            {
                **agent,
                "params_tecnicos": json.dumps(agent["params_tecnicos"]),
                "params_macro":    json.dumps(agent["params_macro"]),
                "params_riesgo":   json.dumps(agent["params_riesgo"]),
                "params_smc":      json.dumps(agent.get("params_smc", _DEFAULT_SMC_PARAMS)),
                "fitness_oos_prometido":  agent.get("fitness_oos_prometido"),
                "n_trades_oos_prometido": agent.get("n_trades_oos_prometido"),
            },
        )
        
        try:
            SheetsLogger().log_agent(agent)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[EvolutionEngine] Error logging new agent {agent['id']} to sheet: {e}")

    def _snapshot_ranking(self, conn, agents: list[dict], evento_map: dict[str, str]) -> None:
        """
        Escribe el snapshot diario de ranking. Es un paso de AUDITORÍA: su
        fallo no debe abortar el ciclo evolutivo (ver _CLAMP más abajo).
        """
        cur = conn.cursor()
        for pos, agent in enumerate(agents, start=1):
            roi_total = float(agent.get("roi_total", 0))
            ops_total = int(agent.get("operaciones_total", 0))

            # ROI diario sobre el capital inicial REAL del agente.
            #
            # BUG 2026-08-26 → 2026-09-15 (21 días sin evolución): aquí
            # `cap_inicial` estaba hardcodeado en 10.0, que era el capital de
            # cada agente cuando se escribió. Al capitalizar a $15.000 el
            # 10-ago cada agente pasó a ~$1.000, y el cálculo devolvía
            # (1064-10)/10*100 = 10.543 contra una columna NUMERIC(8,4) cuyo
            # máximo es 9.999. El INSERT lanzaba NumericValueOutOfRange, la
            # transacción revertía el ciclo COMPLETO y el Juez moría cada día
            # en cuanto cualquier agente superaba ~$1.010. Con el capital real
            # los ROI quedan en el rango sano de -20% a +21%.
            cap_actual  = float(agent.get("capital_actual", 10.0))
            cap_inicial = float(agent.get("capital_inicial") or 0) or 10.0
            roi_diario  = round((cap_actual - cap_inicial) / cap_inicial * 100, 4)

            fitness = round(float(agent.get("fitness_score", 0) or 0), 6)

            # Red de seguridad: ningún valor de un informe puede volver a
            # tumbar la evolución. Si algo se sale del rango de la columna se
            # recorta y se avisa, en vez de reventar el ciclo entero.
            roi_diario    = _clamp_numerico(roi_diario, _MAX_NUMERIC_8_4, "roi_diario", agent["id"])
            roi_total     = _clamp_numerico(roi_total,  _MAX_NUMERIC_8_4, "roi_acumulado", agent["id"])
            fitness       = _clamp_numerico(fitness,    _MAX_NUMERIC_8_4, "fitness_score", agent["id"])
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
        """
        Registra en estrategias_exitosas los supervivientes con muestra
        suficiente y fitness > umbral (Fase 1, rediseño 2026-07-02).

        MIN_ROI_HALL_OF_FAME (env MIN_ROI_FOR_HALL_OF_FAME, default 0.05) ahora
        se compara contra fitness_score (expectancy en R ajustada por riesgo),
        no contra roi_total: ese umbral era prácticamente trivial en la escala
        de % (0.05% de ROI acumulado dejaba pasar casi cualquier agente vivo).
        En la escala de fitness (~±1 típico) 0.05 exige un edge modesto real.

        roi_que_genero pasa a guardar el ROI real ((capital_actual -
        capital_inicial)/capital_inicial, ver _real_roi_pct) en vez de la suma
        aritmética rota de roi_total — queda como campo informativo/auditoría.
        fitness_registro (migración 013) persiste el fitness_score real: lo
        usa _get_hof_parents para ponderar y dominar cruces, igual que
        fitness_score hace con los agentes vivos.
        """
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
            fitness_val = float(agent.get("fitness_score", 0) or 0)
            if fitness_val >= MIN_ROI_HALL_OF_FAME:
                ops = int(agent.get("operaciones_total", 0))
                won = int(agent.get("operaciones_ganadoras", 0))
                win_rate = round(won / ops, 4) if ops > 0 else None
                cur.execute(
                    """
                    INSERT INTO estrategias_exitosas (
                        agente_origen_id, fecha_registro, roi_que_genero,
                        fitness_registro, win_rate,
                        params_tecnicos, params_macro, params_riesgo
                    )
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM estrategias_exitosas
                        WHERE agente_origen_id = %s AND fecha_registro = %s
                    )
                    """,
                    (
                        agent["id"], self.today,
                        _real_roi_pct(agent),
                        fitness_val,
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
        (< 2) MEZCLA: conserva los padres de la especie y completa con el top
        global de cualquier especie (antes se descartaban los de la especie,
        lo que llevaba a criar hijos con genoma 100% de otra especie).

        Hace JOIN con agentes para recuperar params_smc, especie y estado, ya
        que estrategias_exitosas solo almacena params_tecnicos / macro / riesgo.
        El estado permite que los caminos de último recurso eviten usar un
        agente eliminado como genoma único.

        Dedup por agente (DISTINCT ON): un mismo agente puede tener varias
        entradas en estrategias_exitosas, y devolver duplicados rompía la
        selección de segundo padre (IndexError en random.choice — jun 10/11)
        además de inflar el conteo de "padres" disponibles.

        fitness_score (Fase 1, rediseño 2026-07-02): viene de
        estrategias_exitosas.fitness_registro (migración 013), persistido al
        inscribir en el Hall of Fame — así breed_agent()/_species_dominant_pair
        pueden comparar padres reales y padres "virtuales" de HoF con la misma
        vara. Entradas anteriores a la migración 013 no tienen fitness_registro
        (NULL); se usa roi_que_genero como aproximación de respaldo SOLO para
        esos registros legacy (conviven en distinta escala, es un fallback
        pragmático, no una equivalencia real — documentado, no se resuelve
        retroactivamente por falta de datos históricos).
        """
        _SELECT_HOF = """
            SELECT * FROM (
                SELECT DISTINCT ON (e.agente_origen_id)
                       e.agente_origen_id AS id,
                       e.roi_que_genero   AS roi_total,
                       COALESCE(e.fitness_registro, e.roi_que_genero, 0) AS fitness_score,
                       e.params_tecnicos,
                       e.params_macro,
                       e.params_riesgo,
                       a.params_smc,
                       COALESCE(a.especie, 'tendencia') AS especie,
                       a.estado
                FROM estrategias_exitosas e
                JOIN agentes a ON e.agente_origen_id = a.id
                {where}
                ORDER BY e.agente_origen_id,
                         COALESCE(e.fitness_registro, e.roi_que_genero, 0) DESC
            ) t
            ORDER BY t.fitness_score DESC
            LIMIT 10
        """
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            rows: list[dict] = []
            if especie:
                cur.execute(
                    _SELECT_HOF.format(
                        where="WHERE COALESCE(a.especie, 'tendencia') = %s"
                    ),
                    (especie,),
                )
                rows = [dict(r) for r in cur.fetchall()]
                if len(rows) >= 2:
                    return rows
            # Fallback: completar con el top global SIN descartar los de la
            # especie — así el mejor padre de la especie sigue disponible y
            # domina el cruce (60%) frente a un segundo padre global.
            cur.execute(_SELECT_HOF.format(where=""))
            seen = {r["id"] for r in rows}
            for r in cur.fetchall():
                if r["id"] not in seen:
                    rows.append(dict(r))
                    seen.add(r["id"])
            return rows[:10]

    # ── Pureza de especie: padre dominante de la especie del hijo ──────────────

    def _species_genome_pool(self, especie: str, *agent_lists) -> list[dict]:
        """
        Genomas PUROS de la especie, para garantizar pureza dura: agentes de la
        especie presentes en agent_lists (dedup, en el orden dado — los maduros
        primero) + el Hall of Fame de la especie.

        Incluye jóvenes/inmunes y eliminados a propósito: cualquier genoma de la
        especie sirve para no diluirla. El usuario aprobó (Sesión 25) usar padres
        jóvenes de la especie cuando no hay maduros y mantener el elitismo del HoF.

        `_get_hof_parents` mezcla el top global cuando la especie es delgada; se
        filtra aquí a la especie, así que solo entran sus entradas puras.
        """
        pool: dict[str, dict] = {}
        for lst in agent_lists:
            for a in (lst or []):
                if str(a.get("especie") or "tendencia") == especie and a["id"] not in pool:
                    pool[a["id"]] = a
        try:
            for h in self._get_hof_parents(especie):
                if str(h.get("especie") or "tendencia") == especie and h["id"] not in pool:
                    pool[h["id"]] = h
        except Exception:
            pass
        return list(pool.values())

    @staticmethod
    def _species_dominant_pair(
        p1: dict, p2: dict, especie: str, species_pool: list[dict]
    ) -> tuple[dict, dict, float | None]:
        """
        Pureza dura: garantiza que al menos un padre sea de la especie del hijo
        y lo devuelve como DOMINANTE. Retorna (dominante, secundario, p1_weight):

          - ambos de la especie  → (p1, p2, None)  60/40 por ROI, como siempre.
          - uno de la especie     → ese va primero, p1_weight=0.6 (la especie
                                     domina pese al ROI del otro).
          - ninguno de la especie → si hay genomas de la especie, sustituye uno
                                     (al azar, para preservar diversidad) y lo
                                     hace dominante (p1_weight=0.6). El segundo
                                     padre cross-species inyecta diversidad.
          - especie extinta (pool vacío) → deja el par tal cual (cross-species
                                     inevitable; debe registrarse arriba).
        """
        e1 = str(p1.get("especie") or "tendencia") == especie
        e2 = str(p2.get("especie") or "tendencia") == especie
        if e1 and e2:
            return p1, p2, None
        if e1:
            return p1, p2, 0.6
        if e2:
            return p2, p1, 0.6
        if species_pool:
            s = random.choice(species_pool)
            otro = p1 if s["id"] != p1["id"] else p2
            return s, otro, 0.6
        return p1, p2, None

    # ── Redistribución de capital ────────────────────────────────────────────

    def _redistribute_capital(
        self, conn, new_agent_ids: list[str], pool_override: float | None = None,
        fitness_map: dict[str, float] | None = None,
    ) -> tuple[float, float]:
        """
        Reparte el pool de capital entre los agentes activos PONDERADO POR FITNESS
        (Fase 2, rediseño 2026-07-02) — reemplaza el reparto equitativo anterior.

        Auditoría 2026-07-01, hallazgo P0-2: "la redistribución equitativa de
        capital APAGA la selección natural" — perdedor y ganador terminaban con
        el mismo capital cada noche, sin importar el fitness. Ahora el peso de
        cada agente veterano es clamp(1 + fitness_score, CAPITAL_WEIGHT_FLOOR,
        CAPITAL_WEIGHT_CAP); el capital final se normaliza para que la suma siga
        siendo EXACTAMENTE pool_total (no se crea ni destruye capital, solo se
        redistribuye). Los agentes recién nacidos este ciclo (new_agent_ids) no
        tienen fitness en vivo aún — reciben peso 1.0 (cuota equitativa estándar),
        igual que antes.

        Gate de muestra (decisión de diseño 2026-07-25): un veterano con menos
        de CAPITAL_WEIGHT_MIN_TRADES operaciones recibe cuota EQUITATIVA, igual
        que un recién nacido. Con muestra corta el fitness es mayormente ruido y
        ponderar por ruido añade varianza sin añadir retorno esperado. El
        sobrepeso hay que ganárselo con evidencia, no con suerte.

        fitness_map: {agente_id: {"fitness", "n_trades"}} de calc_fitness_detail.
        Se acepta también el formato antiguo {agente_id: float} de
        calc_fitness_scores, pero entonces el gate de muestra no puede aplicarse
        y se pondera como antes de 2026-07-25.

        pool_override debe ser el SUM(capital_actual) de los agentes ANTES de que
        el ciclo evolutivo elimine/nazca ninguno — es decir, el pool real post-EOD.
        Sin override (fallback), re-consulta la DB (incluye nuevos a $10, valor incorrecto).

        El pool total solo fluctúa por P&L real de trading; no se inyecta capital nuevo.
        Los agentes nuevos reciben su cuota del pool existente (capital_inicial = cuota).
        Los supervivientes mantienen su capital_inicial histórico; solo cambia capital_actual.

        Retorna (pool_total, cuota_base) — cuota_base es la cuota EQUITATIVA de
        referencia (pool_total/n), útil para logs/auditoría aunque el capital
        real de cada agente ya no sea uniforme.
        """
        import logging
        log_r = logging.getLogger(__name__)

        fitness_map = fitness_map or {}
        new_ids_set = set(new_agent_ids or [])

        cur = conn.cursor()
        cur.execute("SELECT id FROM agentes WHERE estado = 'activo' ORDER BY id")
        active_ids = [r[0] for r in cur.fetchall()]
        n_agentes = len(active_ids)

        if pool_override is not None:
            pool_total = pool_override
        else:
            cur.execute(
                "SELECT COALESCE(SUM(capital_actual), 0) FROM agentes WHERE estado = 'activo'"
            )
            pool_total = float(cur.fetchone()[0])

        if n_agentes == 0:
            return 0.0, 0.0

        cuota_base = round(pool_total / n_agentes, 4)

        # Peso por agente. Cuota EQUITATIVA (peso 1.0) para el recién nacido y
        # para todo agente sin muestra suficiente; solo se pondera por fitness a
        # quien ya acumuló CAPITAL_WEIGHT_MIN_TRADES operaciones.
        weights: list[float] = []
        n_equitativos = 0
        for aid in active_ids:
            if aid in new_ids_set:
                weights.append(1.0)
                n_equitativos += 1
                continue
            fit, n_trades = _fitness_y_muestra(fitness_map.get(aid))
            # n_trades None = el llamador pasó solo fitness (formato antiguo):
            # no se puede aplicar el gate, se pondera como antes.
            if n_trades is not None and n_trades < CAPITAL_WEIGHT_MIN_TRADES:
                weights.append(1.0)
                n_equitativos += 1
                continue
            weights.append(_clamp(1.0 + fit, CAPITAL_WEIGHT_FLOOR, CAPITAL_WEIGHT_CAP))
        total_weight = sum(weights) or float(n_agentes)  # defensivo: nunca 0

        capitales = [round(w / total_weight * pool_total, 4) for w in weights]
        # El redondeo puede dejar un remanente de centésimas; el último agente
        # lo absorbe para que sum(capitales) == pool_total EXACTO (no se crea
        # ni destruye capital en la redistribución).
        remanente = round(pool_total - sum(capitales), 4)
        if capitales:
            capitales[-1] = round(capitales[-1] + remanente, 4)

        id_to_capital = dict(zip(active_ids, capitales))
        for aid, cap in id_to_capital.items():
            cur.execute(
                "UPDATE agentes SET capital_actual = %s WHERE id = %s",
                (cap, aid),
            )

        # Los agentes nuevos registran su capital_inicial real (no el hardcoded 10.0)
        if new_agent_ids:
            for aid in new_agent_ids:
                if aid in id_to_capital:
                    cur.execute(
                        "UPDATE agentes SET capital_inicial = %s WHERE id = %s",
                        (id_to_capital[aid], aid),
                    )

        log_r.info(
            "[EvolutionEngine] Capital redistribuido: pool=%.4f / %d agentes — "
            "cuota base=%.4f, rango real=[%.4f, %.4f] — %d en cuota equitativa "
            "(recién nacidos o muestra < %d trades), %d ponderados por fitness",
            pool_total, n_agentes, cuota_base,
            min(capitales) if capitales else 0.0,
            max(capitales) if capitales else 0.0,
            n_equitativos, CAPITAL_WEIGHT_MIN_TRADES, n_agentes - n_equitativos,
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
                        capital=id_to_capital.get(ag[0], cuota_base),
                        roi=float(ag[1] or 0),
                        ops=int(ag[2] or 0),
                        ops_ganadoras=int(ag[3] or 0),
                    )
                except Exception as e_ag:
                    log_r.error("[EvolutionEngine] Error actualizando Sheets agente %s: %s", ag[0], e_ag)
        except Exception as e_sheets:
            log_r.error("[EvolutionEngine] Error actualizando Sheets tras redistribución: %s", e_sheets)

        return pool_total, cuota_base

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
        Intenta cubrir los cupos vacantes por especie hasta la población objetivo
        (Sesión 19: 15 agentes si target_by_especie lo permite — ruptura reducido
        a TARGET_AGENTS_RUPTURA desde Fase 2, ver auditoría 2026-07-01).

        Pipeline por cupo:
          1. Hasta REPOPULATION_MAX_ATTEMPTS_PER_SLOT rondas de
             (torneo N candidatos → umbral OOS) seguido de (HoF N candidatos → OOS).
             Se detiene en cuanto un candidato supera el umbral.
          2. Con REPOBLACION_PERMITE_VACANTES=true (default, Fase 2 rediseño
             2026-07-02): si nadie pasa, el cupo queda VACANTE — sin insertar
             genoma sin evidencia de edge. La población flota por debajo del
             objetivo hasta que un candidato real lo cubra en un ciclo futuro.
          3. Con REPOBLACION_PERMITE_VACANTES=false (kill-switch, comportamiento
             legacy): degradación a "mejor_candidato_oos" (mejor cruce sin pasar
             el umbral) y, si ni eso hay, a "forzado_cruce"/"forzado_clon_unico"
             (cruce o clon del mejor genoma disponible en HoF/pool) — garantiza
             llenar el cupo a costa de posiblemente insertar genética sin edge
             confirmado.

        Sin tope por ciclo: se intentan todos los cupos faltantes.

        Si backtest_data es None se omite silenciosamente (sin datos de mercado no
        hay control de calidad ni base para clonar — respeta el fallback sin Yahoo).

        Returns:
          (recovered, slots_rec_log, deficit_restante)
          - recovered: agentes listos para insertar en DB.
          - slots_rec_log: [{id, especie, fitness_oos, origen}] para trazabilidad.
            origen ∈ {torneo, hall_of_fame, mejor_candidato_oos, forzado_cruce,
            forzado_clon_unico} (los últimos tres solo si el kill-switch está off).
          - deficit_restante: {especie: n} cupos que no pudieron cubrirse — ahora
            el caso esperado por defecto cuando ningún candidato supera el gate,
            no solo el caso degenerado sin pool ni HoF.
        """
        import logging
        _log = logging.getLogger(__name__)

        # Fase 3 PLAN_DE_MEJORA.md: presupuesto de tiempo, solo activo en
        # modo multifold (ver constante REPOPULATION_TIME_BUDGET_SECONDS).
        from evolution.backtester import BACKTEST_MODE as _BT_MODE
        _repop_t_start = time.monotonic()
        _time_budget_active = (_BT_MODE == "multifold")

        ESPECIES = ("tendencia", "reversion", "ruptura")

        # Objetivo por especie: override individual por especie, con
        # TARGET_AGENTS_PER_ESPECIE como valor por defecto. Desde 2026-08-10 la
        # población se concentra en reversion — la única especie cuyo perfil
        # pasó el gate bootstrap contra holdout Y muestra expectancy positiva
        # en vivo. Ver _TARGET_OVERRIDE_POR_ESPECIE.
        target_by_especie = {
            esp: _TARGET_OVERRIDE_POR_ESPECIE.get(esp, TARGET_AGENTS_PER_ESPECIE)
            for esp in ESPECIES
        }

        count_by_especie: dict[str, int] = {esp: 0 for esp in ESPECIES}
        for a in current_population:
            esp = str(a.get("especie") or "tendencia")
            if esp in count_by_especie:
                count_by_especie[esp] += 1

        deficit_by_especie: dict[str, int] = {
            esp: max(0, target_by_especie[esp] - count_by_especie[esp])
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
            return _passes_oos_gate(bt)

        def _best_from_pool(pool: list[dict], esp_: str, cid: str,
                            species_pool: list[dict]) -> tuple[dict, dict]:
            """Cría N_CANDIDATE_CHILDREN ponderados por aptitud y devuelve
            (mejor_hijo, su_backtest). El pool debe tener >= 2 agentes.
            Pureza dura: cada candidato lleva ≥1 padre de la especie (dominante)."""
            scores = [
                max(float(a.get("fitness_score", a.get("roi_total", 0)) or 0), 0.0001)
                for a in pool
            ]
            total_score = sum(scores)
            weights = [s / total_score for s in scores]
            candidates: list[tuple[dict, dict]] = []
            for _c in range(N_CANDIDATE_CHILDREN):
                p1, p2 = random.choices(pool, weights=weights, k=2)
                if p1["id"] == p2["id"]:
                    others = [a for a in pool if a["id"] != p1["id"]]
                    if others:
                        p2 = random.choice(others)
                p1, p2, _pw = self._species_dominant_pair(p1, p2, esp_, species_pool)
                candidate = breed_agent(
                    p1, p2, cid, self.today, max_gen + 1,
                    sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                    especie=esp_, p1_weight=_pw,
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

            # Pureza dura (Sesión 25): pool de genomas puros de la especie para
            # forzar ≥1 padre de la especie en cada cruce. Incluye activos
            # jóvenes/inmunes (current_population) y el HoF de la especie.
            species_pool = self._species_genome_pool(esp, current_population)

            for _ in range(deficit):
                child_id = f"{self.today.strftime('%Y-%m-%d')}_{repop_idx:02d}"
                repop_idx += 1

                child: dict | None = None
                best_bt: dict | None = None
                origen = ""
                # Mejor hijo de CRUCE visto en todas las rondas (dos padres
                # distintos): si nadie pasa el umbral estricto, se despliega
                # este en vez de clonar — el cruce 60/40 nunca se abandona.
                best_cand: dict | None = None
                best_cand_bt: dict | None = None

                # ── Presupuesto de tiempo agotado (solo multifold): saltar ──────
                # directo a la cascada de degradación para este cupo, sin gastar
                # más backtests en rondas de torneo/HoF.
                _budget_exhausted = (
                    _time_budget_active
                    and (time.monotonic() - _repop_t_start) > REPOPULATION_TIME_BUDGET_SECONDS
                )
                if _budget_exhausted:
                    _log.warning(
                        "[EvolutionEngine] Repopulación %s (%s): presupuesto de "
                        "tiempo agotado (%ds) — cupo va directo a degradación.",
                        child_id, esp, REPOPULATION_TIME_BUDGET_SECONDS,
                    )

                # ── Reintentos torneo → HoF hasta MAX_ATTEMPTS rondas ──────────
                for _attempt in range(
                    0 if _budget_exhausted else REPOPULATION_MAX_ATTEMPTS_PER_SLOT
                ):
                    if len(tourn_pool) >= 2:
                        cand, bt = _best_from_pool(tourn_pool, esp, child_id, species_pool)
                        if _passes_oos(bt):
                            child, best_bt, origen = cand, bt, "torneo"
                            break
                        if best_cand_bt is None or bt["fitness"] > best_cand_bt["fitness"]:
                            best_cand, best_cand_bt = cand, bt
                    if len(hof_parents) >= 2:
                        cand, bt = _best_from_pool(hof_parents, esp, child_id, species_pool)
                        if _passes_oos(bt):
                            child, best_bt, origen = cand, bt, "hall_of_fame"
                            break
                        if best_cand_bt is None or bt["fitness"] > best_cand_bt["fitness"]:
                            best_cand, best_cand_bt = cand, bt

                # ── Degradaciones forzadas (kill-switch REPOBLACION_PERMITE_VACANTES) ──
                # Antes (comportamiento legacy, flag=False): si nadie pasaba el
                # umbral, se desplegaba igual el mejor candidato de cruce sin
                # evidencia de edge ("mejor_candidato_oos"), y si ni siquiera
                # había 2 padres disponibles, se forzaba un cruce/clon con
                # cualquier genoma existente ("forzado_cruce"/"forzado_clon_
                # unico"). Fase 2 (rediseño 2026-07-02, flag=True por defecto):
                # ningún candidato sin evidencia de edge se despliega — el cupo
                # queda vacante y la población flota por debajo de 15 hasta que
                # un candidato real supere el gate. Ver hallazgo S3,
                # PLAN_REDISENO_RENTABILIDAD.md: forzar genomas sin edge para
                # "completar 15" es precisamente la causa de que sobrevivan
                # especies sin edge real.
                if not REPOBLACION_PERMITE_VACANTES:
                    # ── Degradación 1: mejor candidato de cruce sin umbral ─────
                    if child is None and best_cand is not None:
                        child, best_bt = best_cand, best_cand_bt
                        origen = "mejor_candidato_oos"
                        _log.warning(
                            "[EvolutionEngine] Repopulación %s (%s): sin candidato sobre "
                            "umbral tras %d rondas → MEJOR CANDIDATO de cruce "
                            "(fitness=%.5f, n=%d).",
                            child_id, esp, REPOPULATION_MAX_ATTEMPTS_PER_SLOT,
                            best_bt["fitness"], best_bt["n_trades"],
                        )

                    # ── Degradación 2 (último recurso real): cruce forzado ─────
                    # Ningún pool tiene 2 padres — se cruzan los DOS MEJORES
                    # genomas distintos disponibles entre HoF y pool (60% el de
                    # la especie correcta / mejor puntuado). Un agente eliminado
                    # puede aportar como uno de los dos padres, pero nunca ser
                    # el genoma único. Auto-clon SOLO si hay literalmente un
                    # genoma en el sistema.
                    if child is None:
                        sources: dict[str, dict] = {}
                        for p in list(hof_parents) + list(tourn_pool):
                            if p["id"] not in sources:
                                sources[p["id"]] = p

                        def _score(p: dict) -> tuple:
                            return (
                                str(p.get("especie") or "tendencia") == esp,
                                p.get("estado", "activo") != "eliminado",
                                float(p.get("fitness_score") or p.get("roi_total") or 0),
                            )

                        ranked_src = sorted(sources.values(), key=_score, reverse=True)
                        if len(ranked_src) >= 2:
                            fp1, fp2 = ranked_src[0], ranked_src[1]
                            # fp1 (mejor de la especie correcta si existe) domina
                            # el cruce con el 60% del genoma, sin importar su ROI.
                            child = breed_agent(
                                fp1, fp2, child_id, self.today, max_gen + 1,
                                sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                                especie=esp, p1_weight=0.6,
                            )
                            origen = "forzado_cruce"
                            _log.warning(
                                "[EvolutionEngine] Repopulación %s (%s): pools sin 2 padres "
                                "→ CRUCE FORZADO %s × %s.",
                                child_id, esp, fp1["id"], fp2["id"],
                            )
                        elif len(ranked_src) == 1 and ranked_src[0].get("estado", "activo") != "eliminado":
                            fp1 = ranked_src[0]
                            child = breed_agent(
                                fp1, fp1, child_id, self.today, max_gen + 1,
                                sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                                especie=esp,
                            )
                            origen = "forzado_clon_unico"
                            _log.warning(
                                "[EvolutionEngine] Repopulación %s (%s): UN SOLO genoma "
                                "disponible (%s) → auto-clon inevitable.",
                                child_id, esp, fp1["id"],
                            )
                        if child is not None:
                            try:
                                best_bt = run_backtest(backtest_data, child)
                            except Exception:
                                best_bt = {"fitness": 0.0, "n_trades": 0}

                if child is None:
                    # Sin bypass forzado (default) o degenerado incluso con
                    # bypass activado: cupo vacante, la especie queda con
                    # déficit hasta el próximo ciclo.
                    _log.warning(
                        "[EvolutionEngine] Repopulación %s (%s): sin candidato con "
                        "edge confirmado — cupo queda vacante.", child_id, esp,
                    )
                    continue

                # Fase 1 (PLAN_DE_MEJORA.md): persistir la promesa OOS también
                # en los agentes recuperados en repoblación (todos los caminos
                # de este método corren con backtest_data disponible).
                child["fitness_oos_prometido"]  = round(float(best_bt["fitness"]), 6)
                child["n_trades_oos_prometido"] = int(best_bt["n_trades"])

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

            # Fase 2: fitness de cada agente ANTES del ciclo (veteranos), para
            # ponderar la redistribución de capital — evita una query aparte.
            # Desde 2026-07-25 arrastra también n_trades (el mismo
            # f.n_trades_fitness que ya trae _get_active_agents_ranked) para que
            # _redistribute_capital aplique el gate CAPITAL_WEIGHT_MIN_TRADES.
            fitness_map: dict[str, dict] = {
                a["id"]: {
                    "fitness":  float(a.get("fitness_score", 0) or 0),
                    "n_trades": int(a.get("n_trades", 0) or 0),
                }
                for a in agents
            }

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
                # Numeración consecutiva real (Cambio A) también en ciclo suspendido.
                _susp_remap = self._renumber_contiguous(_recovered_s, _susp_next_idx)
                for _s in result.slots_recuperados:
                    if _s["id"] in _susp_remap:
                        _s["id"] = _susp_remap[_s["id"]]
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
                            conn, [a["id"] for a in _recovered_s], pool_override=_pool_s,
                            fitness_map=fitness_map,
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

                # Pureza dura (Sesión 25): genomas puros de la especie (maduros
                # elegibles → todos los activos incl. inmunes → HoF de la especie)
                # para forzar ≥1 padre de la especie en cada cruce de este slot.
                species_pool = self._species_genome_pool(
                    child_especie, parent_candidates, survivors_all
                )

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
                    if p1_["id"] == p2_["id"]:
                        others_ = [a for a in _pool if a["id"] != p1_["id"]]
                        if others_:
                            p2_ = random.choice(others_)
                    return p1_, p2_

                child_id = f"{self.today.strftime('%Y-%m-%d')}_{next_idx + i:02d}"

                if use_backtest and backtest_data is not None:
                    # ── Torneo de N candidatos: criar N, desplegar el mejor OOS ──
                    candidates: list[tuple[dict, dict]] = []
                    for _c in range(N_CANDIDATE_CHILDREN):
                        p1, p2 = _select_parents()
                        p1, p2, _pw = self._species_dominant_pair(
                            p1, p2, child_especie, species_pool
                        )
                        candidate = breed_agent(
                            p1, p2, child_id, self.today, max_gen + 1,
                            sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                            especie=child_especie, p1_weight=_pw,
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

                    # ── Fase 1 Sesión 17 / Fase 2 PLAN_DE_MEJORA.md: umbral ──
                    passes = _passes_oos_gate(best_bt)
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
                            hof_scores = [max(float(p.get("fitness_score", p.get("roi_total", 0)) or 0), 0.0001)
                                          for p in hof_parents]
                            hof_total  = sum(hof_scores)
                            hof_w = [s / hof_total for s in hof_scores]
                            hof_candidates: list[tuple[dict, dict]] = []
                            for _hc in range(N_CANDIDATE_CHILDREN):
                                hp1, hp2 = random.choices(hof_parents, weights=hof_w, k=2)
                                if hp1["id"] == hp2["id"] and len(hof_parents) > 1:
                                    alternatives = [p for p in hof_parents if p["id"] != hp1["id"]]
                                    if alternatives:
                                        hp2 = random.choice(alternatives)
                                    # else: self-mutation (hp2 == hp1), breed_agent lo soporta
                                hp1, hp2, _hpw = self._species_dominant_pair(
                                    hp1, hp2, child_especie, species_pool
                                )
                                hof_child = breed_agent(
                                    hp1, hp2, child_id, self.today, max_gen + 1,
                                    sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                                    especie=child_especie, p1_weight=_hpw,
                                )
                                try:
                                    hof_bt = run_backtest(backtest_data, hof_child)
                                except Exception:
                                    hof_bt = {"fitness": 0.0, "expectancy": 0.0, "n_trades": 0}
                                hof_candidates.append((hof_child, hof_bt))
                            hof_best, hof_best_bt = max(
                                hof_candidates, key=lambda x: x[1]["fitness"]
                            )
                            passes = _passes_oos_gate(hof_best_bt)
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
                        # Fase 1 (PLAN_DE_MEJORA.md): persistir la promesa OOS del
                        # torneo para poder comparar luego contra el fitness real.
                        child["fitness_oos_prometido"]  = round(float(best_bt["fitness"]), 6)
                        child["n_trades_oos_prometido"] = int(best_bt["n_trades"])
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
                    p1, p2, _pw = self._species_dominant_pair(
                        p1, p2, child_especie, species_pool
                    )
                    child = breed_agent(
                        p1, p2, child_id, self.today, max_gen + 1,
                        sigma_weights=sw, sigma_periods=sp, sigma_risk=sr,
                        especie=child_especie, p1_weight=_pw,
                    )
                    # Sin backtest disponible: no hay promesa OOS que registrar.
                    child["fitness_oos_prometido"]  = None
                    child["n_trades_oos_prometido"] = None
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

            # ── Numeración consecutiva real (Cambio A) ────────────────────────
            # Los agentes que SÍ se insertan llevan _01, _02, … sin huecos. Un
            # slot rechazado por el umbral OOS ya no 'quema' su índice (antes
            # nacía '_02' sin existir '_01'). Los slots vacantes se reetiquetan
            # DESPUÉS de los nacidos para no colisionar con un ID real.
            id_remap = self._renumber_contiguous(new_agents, next_idx)
            for _s in result.slots_recuperados:
                if _s["id"] in id_remap:
                    _s["id"] = id_remap[_s["id"]]
            _vac_base = next_idx + len(new_agents)
            for _j, _sv in enumerate(result.slots_vacantes):
                _sv["id"] = f"{self.today.strftime('%Y-%m-%d')}_{_vac_base + _j:02d}"

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
                        roi_real = _real_roi_pct(a)
                        razones_extra[a["id"]] = (
                            f"Inmunidad revocada por drawdown "
                            f"(roi={roi_real:.1f}% <= -{IMMUNITY_MAX_LOSS_PCT:.1f}%). "
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

                # Redistribuir capital ponderado por fitness entre los agentes
                # activos. fitness_map (calculado al inicio de run(), ANTES de
                # eliminar/criar) es lo que activa la ponderación de Fase 2 —
                # sin él, _redistribute_capital degrada a reparto equitativo
                # (bug detectado 2026-07-03: el primer ciclo en prod corrió sin
                # pasarlo y todos los agentes amanecieron con capital idéntico).
                new_agent_ids = [a["id"] for a in new_agents]
                pool_total, capital_por_agente = self._redistribute_capital(
                    conn, new_agent_ids, pool_override=pool_total_eod,
                    fitness_map=fitness_map,
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
