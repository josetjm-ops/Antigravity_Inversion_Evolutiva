"""
Motor evolutivo OFFLINE — Fase B del plan de rentabilidad (2026-07-23).

EL PROBLEMA QUE RESUELVE
------------------------
En producción la evolución avanza a ~10 trades/día repartidos entre 13
agentes, y cría 1-3 hijos por noche. A ese ritmo, una búsqueda genética
seria (miles de evaluaciones de genomas) tomaría años. Ese es el cuello de
botella #1 del sistema: no es que el motor esté mal, es que nunca ha tenido
suficientes datos para aprender.

Este módulo desacopla la evolución del reloj de producción: corre cientos
de generaciones sobre AÑOS de histórico de Dukascopy en horas de CPU local,
y solo los campeones validados se promueven a producción.

QUÉ REUSA (deliberadamente)
---------------------------
Todo el material genético y de evaluación ya existente y probado:
  - `breed_agent()` del motor de producción → crossover + mutación idénticos
  - `_walk_forward_trades()` del backtester → misma simulación de trading
  - `_calc_metrics()` → mismo fitness en R (Fase 1)
  - `bootstrap_edge_ok()` → mismo gate estadístico (Fase 2)
Si la evolución offline usara una simulación propia, sus campeones no serían
portables a producción — el punto entero del ejercicio.

PROTOCOLO ANTI-OVERFIT
----------------------
1. Los folds de evolución NUNCA tocan el tramo de holdout final.
2. El fitness de selección es multi-fold (media − λ·desviación entre folds),
   así un genoma que solo funciona en un régimen queda penalizado.
3. Un campeón solo se reporta como promovible si además pasa el holdout
   —datos que la evolución jamás vio— con expectancy positiva y el gate
   bootstrap. Ver `evaluar_holdout()`.

REANUDABLE
----------
Cada generación se guarda en checkpoint JSON. Si el proceso se corta, se
retoma con `--reanudar` desde la última generación completa.
"""

from __future__ import annotations

import json
import logging
import os
import random
import statistics
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

ESPECIES = ("tendencia", "reversion", "ruptura")


# ── Construcción de la población inicial ────────────────────────────────────

def genoma_aleatorio(especie: str, rng: random.Random) -> dict:
    """
    Genera un genoma aleatorio DENTRO de los bounds evolutivos del motor de
    producción. Partir de ruido uniforme (en vez de clonar la población
    actual) es intencional: la población viva ya está sesgada por meses de
    selección bajo un fitness que resultó estar roto, y arrastrar ese sesgo
    desperdiciaría la exploración.
    """
    from evolution.evolution_engine import (
        _BOUNDS_TECNICOS, _BOUNDS_MACRO, _BOUNDS_RIESGO, _BOUNDS_SMC,
        _DEFAULT_SMC_PARAMS, _CATEGORICAL_GENE_OPTIONS,
        _normalize_weights, _enforce_ema_constraint, _enforce_sl_tp_constraint,
    )

    def _muestrear(bounds: dict, base: dict | None = None) -> dict:
        salida = dict(base or {})
        for gen, (lo, hi, es_entero) in bounds.items():
            valor = rng.uniform(lo, hi)
            salida[gen] = int(round(valor)) if es_entero else round(valor, 6)
        return salida

    tec = _muestrear(_BOUNDS_TECNICOS)
    mac = _muestrear(_BOUNDS_MACRO)
    rie = _muestrear(_BOUNDS_RIESGO)
    smc = _muestrear(_BOUNDS_SMC, base=dict(_DEFAULT_SMC_PARAMS))

    # Genes no gaussianos: booleano por sorteo, categórico por opción.
    smc["exit_on_reversal"] = rng.choice([0, 1])
    for gen, opciones in _CATEGORICAL_GENE_OPTIONS.items():
        smc[gen] = rng.choice(opciones)

    # Mismos constraints de integridad que aplica la crianza en producción.
    tec = _enforce_ema_constraint(_normalize_weights(tec, ["peso_rsi", "peso_ema", "peso_macd"]))
    rie = _enforce_sl_tp_constraint(rie)

    # Marcas de especie (idénticas a las que fuerza breed_agent).
    if especie == "reversion":
        tec["rsi_modo"] = "reversion"
        smc["htf_filter_enabled"] = 0
    else:
        tec["rsi_modo"] = "momentum"
        smc["htf_filter_enabled"] = 1

    return {
        # id sintético: breed_agent lo lee para la genealogía (padre_N_id).
        # No toca la DB — es solo una etiqueta para el cruce offline.
        "id": f"OFF_{especie}_{rng.getrandbits(32):08x}",
        "params_tecnicos": tec, "params_macro": mac,
        "params_riesgo": rie, "params_smc": smc, "especie": especie,
        "fitness_score": 0.0,   # se actualiza tras evaluar (dominancia del cruce)
    }


# ── Evaluación multi-fold sobre histórico largo ─────────────────────────────

def _construir_folds(
    df_15m: pd.DataFrame, n_folds: int, train_dias: int, val_dias: int,
    purge_dias: int, velas_por_dia: int,
) -> list[tuple[int, int]]:
    """
    Devuelve los tramos (inicio_oos, fin_oos) de cada fold, avanzando por el
    histórico. El `purge` descarta velas entre train y validate para que los
    indicadores del warmup no filtren información hacia el tramo evaluado.
    """
    total = len(df_15m)
    largo_train = train_dias * velas_por_dia
    largo_val = val_dias * velas_por_dia
    largo_purge = purge_dias * velas_por_dia
    bloque = largo_train + largo_purge + largo_val

    if total < bloque:
        return []

    # Se reparten los folds de forma uniforme sobre todo el histórico
    # disponible: así cubren regímenes distintos, que es el objetivo.
    paso = max(1, (total - bloque) // max(1, n_folds - 1)) if n_folds > 1 else 0
    folds = []
    for i in range(n_folds):
        inicio_bloque = i * paso
        if inicio_bloque + bloque > total:
            break
        oos_ini = inicio_bloque + largo_train + largo_purge
        folds.append((oos_ini, oos_ini + largo_val))
    return folds


def evaluar_genoma(
    genoma: dict, df_15m: pd.DataFrame, folds: list[tuple[int, int]],
    htf_trend: dict, lam: float = 0.5,
) -> dict:
    """
    Evalúa un genoma sobre todos los folds y devuelve el fitness agregado
    penalizado por varianza: `media(fitness) − λ·desviación(fitness)`.

    Un genoma que rinde +2 en un fold y −1 en otro es peor que uno estable
    en +0.4: el primero encontró un régimen, el segundo un edge.
    """
    from evolution.backtester import _walk_forward_trades, _calc_metrics

    fitness_folds, trades_todos, n_total = [], [], 0

    for oos_ini, oos_fin in folds:
        try:
            trades = _walk_forward_trades(
                df_15m, oos_ini, oos_fin, htf_trend,
                genoma["params_tecnicos"], genoma["params_smc"],
                genoma["params_riesgo"], genoma["especie"],
            )
        except Exception as exc:
            log.debug("[Offline] Fold falló: %s", exc)
            continue

        m = _calc_metrics(trades)
        fitness_folds.append(m["fitness"])
        trades_todos.extend(trades)
        n_total += m["n_trades"]

    if not fitness_folds:
        return {"fitness": 0.0, "n_trades": 0, "estabilidad": 0.0, "oos_trades": []}

    media = statistics.mean(fitness_folds)
    desv = statistics.stdev(fitness_folds) if len(fitness_folds) > 1 else 0.0

    return {
        "fitness": round(media - lam * desv, 6),
        "fitness_medio": round(media, 6),
        "desviacion": round(desv, 6),
        "n_trades": n_total,
        "n_folds": len(fitness_folds),
        "oos_trades": trades_todos,
    }


# ── Bucle evolutivo ─────────────────────────────────────────────────────────

# ── Evaluación en paralelo ──────────────────────────────────────────────────
#
# Una evaluación de genoma cuesta ~39 s (medido): 3 folds × cientos de
# llamadas a calc_signals. En serie, una corrida de 30×40 tomaría ~6.5 h por
# especie. Las evaluaciones son independientes y CPU-bound, así que se
# reparten entre procesos. El estado pesado (el DataFrame del histórico) se
# envía UNA VEZ por worker vía initializer, no en cada tarea.

_ESTADO_WORKER: dict[str, Any] = {}


def _init_worker(df_15m, folds, htf_trend, lam) -> None:
    _ESTADO_WORKER.update(
        {"df": df_15m, "folds": folds, "htf": htf_trend, "lam": lam}
    )


def _evaluar_en_worker(genoma: dict) -> dict:
    ev = evaluar_genoma(
        genoma, _ESTADO_WORKER["df"], _ESTADO_WORKER["folds"],
        _ESTADO_WORKER["htf"], _ESTADO_WORKER["lam"],
    )
    ev.pop("oos_trades", None)
    return ev


def _evaluar_sin_trades(genoma, df_15m, folds, htf_trend, lam) -> dict:
    ev = evaluar_genoma(genoma, df_15m, folds, htf_trend, lam)
    ev.pop("oos_trades", None)
    return ev


def _evaluar_poblacion(genomas, df_15m, folds, htf_trend, lam, procesos):
    """Evalúa una lista de genomas en paralelo; cae a serie si el pool falla."""
    if procesos is not None and procesos <= 1:
        return [_evaluar_sin_trades(g, df_15m, folds, htf_trend, lam) for g in genomas]
    try:
        with ProcessPoolExecutor(
            max_workers=procesos, initializer=_init_worker,
            initargs=(df_15m, folds, htf_trend, lam),
        ) as pool:
            return list(pool.map(_evaluar_en_worker, genomas))
    except Exception as exc:
        log.warning("[Offline] Pool no disponible (%s) — evaluando en serie.", exc)
        return [_evaluar_sin_trades(g, df_15m, folds, htf_trend, lam) for g in genomas]


def _seleccion_torneo(poblacion: list[dict], k: int, rng: random.Random) -> dict:
    """Selección por torneo: k candidatos al azar, gana el de mejor fitness.
    Mantiene presión selectiva sin colapsar la diversidad como haría elegir
    siempre al mejor global."""
    aspirantes = rng.sample(poblacion, min(k, len(poblacion)))
    return max(aspirantes, key=lambda g: g["_eval"]["fitness"])


def evolucionar(
    df_15m: pd.DataFrame,
    htf_trend: dict,
    especie: str,
    generaciones: int = 30,
    tam_poblacion: int = 40,
    elite: int = 4,
    torneo_k: int = 3,
    n_folds: int = 4,
    train_dias: int = 40,
    val_dias: int = 15,
    purge_dias: int = 1,
    velas_por_dia: int = 96,
    semilla: int = 42,
    procesos: int | None = None,
    checkpoint: Path | None = None,
    reanudar: bool = False,
    on_generacion=None,
) -> list[dict]:
    """
    Corre la evolución para UNA especie y devuelve la población final
    ordenada por fitness descendente.

    `velas_por_dia=96` corresponde a velas de 15m sobre 24h de mercado FX
    (el histórico de Dukascopy cubre las 24h, a diferencia de las ~26 velas
    útiles que asumía el backtester con datos de Yahoo).

    `reanudar=True`: si existe `checkpoint`, carga su población y continúa
    desde la generación siguiente en vez de empezar de cero — para que apagar
    el computador a mitad de una corrida de horas no cueste el trabajo hecho.
    (La trayectoria aleatoria tras el punto de reanudación no es idéntica a la
    de una corrida ininterrumpida, pero sigue siendo una evolución válida.)
    """
    from evolution.evolution_engine import breed_agent

    rng = random.Random(semilla)
    folds = _construir_folds(df_15m, n_folds, train_dias, val_dias, purge_dias, velas_por_dia)
    if not folds:
        raise ValueError(
            f"Histórico insuficiente: {len(df_15m)} velas no alcanzan para "
            f"{n_folds} folds de {train_dias}+{purge_dias}+{val_dias} días."
        )

    log.info("[Offline/%s] %d folds sobre %d velas", especie, len(folds), len(df_15m))

    procesos = procesos or max(1, (os.cpu_count() or 2))
    log.info("[Offline/%s] Evaluando con %d proceso(s)", especie, procesos)

    gen_inicial = 1
    datos_cp = cargar_checkpoint(checkpoint) if (reanudar and checkpoint) else None
    if datos_cp and datos_cp.get("poblacion"):
        # El checkpoint guarda cada genoma con su _eval (fitness) y sus bloques
        # de params — todo lo que el bucle necesita para seleccionar y criar.
        # No hay que re-evaluar la población cargada.
        poblacion = datos_cp["poblacion"]
        gen_inicial = int(datos_cp.get("generacion", 0)) + 1
        log.info(
            "[Offline/%s] REANUDANDO desde el checkpoint: gen %d completa, "
            "continuando en gen %d.", especie, gen_inicial - 1, gen_inicial,
        )
    else:
        poblacion = [genoma_aleatorio(especie, rng) for _ in range(tam_poblacion)]
        for g, ev in zip(poblacion, _evaluar_poblacion(
                poblacion, df_15m, folds, htf_trend, 0.5, procesos)):
            g["_eval"] = ev
            g["fitness_score"] = ev["fitness"]
        poblacion.sort(key=lambda g: g["_eval"]["fitness"], reverse=True)

    if gen_inicial > generaciones:
        log.info("[Offline/%s] El checkpoint ya alcanzó %d generaciones — nada que evolucionar.",
                 especie, generaciones)
        return poblacion

    for gen in range(gen_inicial, generaciones + 1):
        nueva = [dict(g) for g in poblacion[:elite]]          # elitismo

        hijos = []
        while len(nueva) + len(hijos) < tam_poblacion:
            p1 = _seleccion_torneo(poblacion, torneo_k, rng)
            p2 = _seleccion_torneo(poblacion, torneo_k, rng)
            hijo = breed_agent(
                p1, p2, f"OFFLINE_{especie}_{gen}_{len(hijos)}",
                date.today(), gen, especie=especie,
            )
            hijo["especie"] = especie
            hijos.append(hijo)

        for h, ev in zip(hijos, _evaluar_poblacion(
                hijos, df_15m, folds, htf_trend, 0.5, procesos)):
            h["_eval"] = ev
            h["fitness_score"] = ev["fitness"]
        nueva.extend(hijos)

        poblacion = sorted(nueva, key=lambda g: g["_eval"]["fitness"], reverse=True)
        mejor = poblacion[0]["_eval"]

        log.info(
            "[Offline/%s] Gen %d/%d — mejor fitness=%.4f (medio=%.4f desv=%.4f, %d trades)",
            especie, gen, generaciones, mejor["fitness"],
            mejor.get("fitness_medio", 0), mejor.get("desviacion", 0), mejor["n_trades"],
        )

        if checkpoint:
            _guardar_checkpoint(checkpoint, especie, gen, poblacion)
        if on_generacion:
            on_generacion(especie, gen, poblacion)

    return poblacion


# ── Validación final en holdout (datos jamás vistos) ────────────────────────

def evaluar_holdout(
    genoma: dict, df_holdout: pd.DataFrame, htf_trend: dict,
    velas_por_dia: int = 96, warmup_dias: int = 10,
) -> dict:
    """
    Evalúa un campeón sobre el tramo de holdout — datos que la evolución
    NUNCA tocó. Este es el único número que debería inspirar confianza: el
    fitness de evolución está, por construcción, optimizado sobre sus folds.

    `warmup_dias=10`: solo lo necesario para que los indicadores (EMA lenta,
    ATR, ADX) se estabilicen. NO son los 40 días de train de los folds de
    evolución — aquí no se entrena nada, solo se calientan indicadores, y un
    warmup grande desperdiciaría holdout evaluable (un warmup de 40 sobre un
    holdout de 25 días dejaba CERO velas evaluables — bug de la corrida
    preliminar 2026-07-23, que reportaba n=0 como si fuera "sin edge").

    `motivo="holdout_insuficiente"` (con guion bajo) señala EXPLÍCITAMENTE el
    caso técnico de holdout demasiado corto, para que el runner NO lo
    confunda con un veredicto real de "el genoma no tiene edge" — esa
    confusión podría empujar erróneamente a cerrar el proyecto.

    Devuelve además el veredicto del gate bootstrap (Fase 2).
    """
    from evolution.backtester import _walk_forward_trades, _calc_metrics, bootstrap_edge_ok

    inicio = warmup_dias * velas_por_dia
    if len(df_holdout) <= inicio + velas_por_dia:
        return {"fitness": 0.0, "n_trades": 0, "pasa_bootstrap": False,
                "motivo": "holdout_insuficiente"}

    trades = _walk_forward_trades(
        df_holdout, inicio, len(df_holdout), htf_trend,
        genoma["params_tecnicos"], genoma["params_smc"],
        genoma["params_riesgo"], genoma["especie"],
    )
    m = _calc_metrics(trades)
    pasa, limite = bootstrap_edge_ok(trades, seed=7)

    return {
        "fitness": m["fitness"],
        "expectancy_R": m["expectancy"],
        "n_trades": m["n_trades"],
        "win_rate": m["win_rate"],
        "max_drawdown": m["max_drawdown"],
        "pasa_bootstrap": pasa,
        "ic_inferior": limite,
    }


# ── Checkpoints ─────────────────────────────────────────────────────────────

def _limpiar(genoma: dict) -> dict:
    """Copia serializable: sin los trades OOS, que pesan megas y no aportan
    al reanudar."""
    salida = {k: v for k, v in genoma.items() if k != "_eval"}
    ev = dict(genoma.get("_eval", {}))
    ev.pop("oos_trades", None)
    salida["_eval"] = ev
    return salida


def _guardar_checkpoint(ruta: Path, especie: str, generacion: int, poblacion: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = {
        "especie": especie,
        "generacion": generacion,
        "guardado": datetime.now(timezone.utc).isoformat(),
        "poblacion": [_limpiar(g) for g in poblacion],
    }
    # default=str serializa los date/datetime que breed_agent deja en el
    # genoma (fecha_nacimiento).
    ruta.write_text(json.dumps(datos, indent=2, default=str), encoding="utf-8")


def cargar_checkpoint(ruta: Path) -> dict | None:
    if not ruta.exists():
        return None
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("[Offline] Checkpoint ilegible (%s): %s", ruta, exc)
        return None
