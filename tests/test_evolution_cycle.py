"""
Test de integración del ciclo evolutivo completo.
Ejecuta contra la DB Neon real: selección, mutación, persistencia y genealogía.
"""

import json
import os
import sys
import random
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras

DB = "postgresql://neondb_owner:npg_HpqvWm94yaLr@ep-crimson-heart-amtwwmvf.c-5.us-east-1.aws.neon.tech/inversion_evolutiva?channel_binding=require&sslmode=require"


def get_conn():
    return psycopg2.connect(DB)


# ── Helpers de fixtures ──────────────────────────────────────────────────────

def _get_active_agents():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, generacion, roi_total, capital_actual,
               operaciones_total, operaciones_ganadoras,
               params_tecnicos, params_macro, params_riesgo
        FROM agentes WHERE estado = 'activo' ORDER BY roi_total DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _inject_varied_rois():
    """
    Inyecta ROI y operaciones variadas en los 10 agentes génesis
    para simular un día de trading real. Necesario para probar la
    selección diferencial (algunos buenos, algunos malos).
    """
    conn = get_conn()
    cur = conn.cursor()
    # Distribuir ROIs simulados: primeros 5 positivos, últimos 5 negativos
    rois = [0.08, 0.06, 0.04, 0.03, 0.01, -0.01, -0.02, -0.03, -0.05, -0.08]
    agents = _get_active_agents()
    for agent, roi in zip(agents, rois):
        new_capital = round(10.0 * (1 + roi), 4)
        ops = random.randint(3, 8)
        won = ops if roi > 0 else max(0, ops - random.randint(1, 3))
        cur.execute("""
            UPDATE agentes
            SET roi_total = %s,
                capital_actual = %s,
                operaciones_total = %s,
                operaciones_ganadoras = %s
            WHERE id = %s
        """, (roi * 100, new_capital, ops, won, agent["id"]))
    conn.commit()
    conn.close()
    print("  [SETUP] ROIs variados inyectados en 10 agentes génesis.")


def _reset_agents():
    """Resetea todos los agentes a estado activo y ROI=0 para tests limpios."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE agentes
        SET estado = 'activo', roi_total = 0, capital_actual = 10.0,
            operaciones_total = 0, operaciones_ganadoras = 0,
            fecha_eliminacion = NULL, razon_eliminacion = NULL
        WHERE fecha_nacimiento = '2026-05-03'
    """)
    # Eliminar agentes de generaciones > 1 creados en tests anteriores
    cur.execute("DELETE FROM agentes WHERE generacion > 1")
    cur.execute("DELETE FROM ranking_historico")
    cur.execute("DELETE FROM logs_juez")
    cur.execute("DELETE FROM estrategias_exitosas")
    conn.commit()
    conn.close()
    print("  [SETUP] DB reseteada a estado génesis limpio.")


# ── Tests de la lógica de mutación ──────────────────────────────────────────

def test_mutation_respects_constraints():
    """La mutación nunca debe producir parámetros fuera de los rangos seguros."""
    from evolution.evolution_engine import breed_agent, _BOUNDS_TECNICOS, _BOUNDS_RIESGO

    base = {
        "id": "2026-05-03_01", "roi_total": 0.05, "generacion": 1,
        "params_tecnicos": {
            # Genes originales
            "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
            "ema_rapida": 9, "ema_lenta": 21,
            "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
            "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
            # Genes Sesión 15 Fase 2: banda neutral RSI momentum
            "rsi_zona_muerta": 5.0,
        },
        "params_macro": {
            "peso_noticias_alto": 0.60, "peso_noticias_medio": 0.25,
            "peso_noticias_bajo": 0.10, "umbral_sentimiento_compra": 0.65,
            "umbral_sentimiento_venta": 0.35, "ventana_noticias_horas": 4,
            "peso_total_macro": 0.40,
            # Gen Sesión 15 Fase 3: sesgo de tendencia HTF
            "peso_sesgo_tendencia": 0.40,
        },
        "params_riesgo": {
            "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
            "max_drawdown_diario_pct": 0.10, "capital_por_operacion_pct": 0.50,
            "umbral_confianza_minima": 0.60, "peso_tecnico_vs_macro": 0.55,
        },
        # Genes SMC completos (Sesiones 4, 6, 15, 16)
        "params_smc": {
            "fvg_min_pips": 5.0, "ob_impulse_pips": 10.0,
            "range_spike_multiplier": 1.5, "risk_reward_target": 2.0,
            "macro_quarantine_minutes": 60, "risk_pct_per_trade": 0.015,
            "peso_fvg": 0.15, "peso_ob": 0.15,
            "atr_factor": 1.5, "trailing_activation_pips": 15.0,
            "trailing_distance_pips": 10.0, "atr_period": 14,
            "htf_filter_enabled": 1,
            "breakout_lookback_bars": 20, "breakout_min_pips": 5.0,
            "peso_breakout": 0.40,
            "adx_period": 14, "adx_threshold": 25.0,
        },
    }

    for _ in range(20):
        child = breed_agent(base, base, "TEST_CHILD", date.today(), 2)
        tec = child["params_tecnicos"]
        risk = child["params_riesgo"]

        # Restricciones EMA
        assert tec["ema_rapida"] < tec["ema_lenta"], \
            f"EMA rapida ({tec['ema_rapida']}) >= EMA lenta ({tec['ema_lenta']})"

        # Restricción TP > SL * 1.5
        assert risk["take_profit_pct"] >= risk["stop_loss_pct"] * 1.5, \
            f"TP ({risk['take_profit_pct']}) < SL*1.5 ({risk['stop_loss_pct']*1.5})"

        # Pesos técnicos suman ~1.0
        peso_sum = tec["peso_rsi"] + tec["peso_ema"] + tec["peso_macd"]
        assert abs(peso_sum - 1.0) < 0.001, f"Pesos técnicos no suman 1: {peso_sum}"

        # Rangos bounds_tecnicos
        for key, (lo, hi, is_int) in _BOUNDS_TECNICOS.items():
            val = tec[key]
            assert lo <= val <= hi, f"{key}={val} fuera de [{lo},{hi}]"

        # Rangos bounds_riesgo
        for key, (lo, hi, is_int) in _BOUNDS_RIESGO.items():
            val = risk[key]
            assert lo <= val <= hi, f"{key}={val} fuera de [{lo},{hi}]"

    print("  [PASS] 20 iteraciones de mutación: todos los constraints satisfechos.")


def test_crossover_inherits_from_both_parents():
    """El cruce debe producir genes de ambos padres cuando se ejecuta N veces."""
    from evolution.evolution_engine import crossover

    p1 = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
    p2 = {"a": 10.0, "b": 20.0, "c": 30.0, "d": 40.0}

    from_p1, from_p2 = 0, 0
    for _ in range(200):
        child = crossover(p1, p2, p1_weight=0.6)
        for k in p1:
            if child[k] == p1[k]:
                from_p1 += 1
            elif child[k] == p2[k]:
                from_p2 += 1

    assert from_p1 > 0 and from_p2 > 0, "El cruce no generó herencia de ambos padres"
    ratio = from_p1 / (from_p1 + from_p2)
    assert 0.45 <= ratio <= 0.85, f"Ratio de herencia del padre1 fuera de rango: {ratio:.2f}"
    print(f"  [PASS] Cruce: {from_p1} genes de p1, {from_p2} genes de p2 (ratio={ratio:.2f})")


# ── Tests de selección natural ───────────────────────────────────────────────

def test_selection_picks_top_and_bottom():
    """Los supervivientes deben ser los de mayor ROI, los eliminados los de menor."""
    from evolution.evolution_engine import EvolutionEngine

    agents = [{"id": f"A{i:02d}", "roi_total": float(i), "generacion": 1,
               "operaciones_total": 5, "operaciones_ganadoras": 3}
              for i in range(10, 0, -1)]  # ROIs: 10,9,8,...,1

    engine = EvolutionEngine(date.today())
    survivors, eliminated = engine.select_survivors_and_eliminated(agents)

    assert len(survivors) + len(eliminated) == len(agents)
    assert len(eliminated) <= 5
    # Los eliminados deben tener ROI inferior a todos los supervivientes
    max_elim_roi = max(float(a["roi_total"]) for a in eliminated)
    min_surv_roi = min(float(a["roi_total"]) for a in survivors)
    assert max_elim_roi <= min_surv_roi, \
        f"Eliminado con ROI={max_elim_roi} > superviviente con ROI={min_surv_roi}"
    print(f"  [PASS] Selección: {len(survivors)} supervivientes (min ROI={min_surv_roi}), "
          f"{len(eliminated)} eliminados (max ROI={max_elim_roi})")


# ── Test de ciclo evolutivo completo en DB real ──────────────────────────────

def test_full_evolution_cycle_on_db():
    """
    Ejecuta el ciclo evolutivo completo contra la DB Neon:
    - Inyecta ROIs variados
    - Corre EvolutionEngine.run()
    - Verifica que se eliminaron agentes, se crearon nuevos y se registró genealogía
    """
    _reset_agents()
    _inject_varied_rois()

    from evolution.evolution_engine import EvolutionEngine
    engine = EvolutionEngine(date.today())
    result = engine.run()

    assert not result.errors, f"Errores en el ciclo: {result.errors}"
    assert len(result.survivors)  > 0, "No hubo supervivientes"
    assert len(result.eliminated) > 0, "No se eliminó ningún agente"
    assert len(result.new_agents) > 0, "No se crearon nuevos agentes"
    assert len(result.eliminated) == len(result.new_agents), \
        "El número de eliminados debe igualar el de nuevos agentes"

    print(f"  [PASS] Ciclo evolutivo: "
          f"{len(result.survivors)} supervivientes, "
          f"{len(result.eliminated)} eliminados, "
          f"{len(result.new_agents)} nuevos agentes.")

    # Verificar en DB
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Los eliminados deben tener estado='eliminado'
    elim_ids = [a["id"] for a in result.eliminated]
    cur.execute("SELECT id, estado FROM agentes WHERE id = ANY(%s)", (elim_ids,))
    for row in cur.fetchall():
        assert row["estado"] == "eliminado", \
            f"Agente {row['id']} debería ser 'eliminado', es '{row['estado']}'"

    # Los nuevos agentes deben existir con padre_1_id y padre_2_id
    new_ids = [a["id"] for a in result.new_agents]
    cur.execute("SELECT id, padre_1_id, padre_2_id, generacion FROM agentes WHERE id = ANY(%s)", (new_ids,))
    rows = cur.fetchall()
    assert len(rows) == len(result.new_agents), "No todos los nuevos agentes se insertaron en DB"
    for row in rows:
        assert row["padre_1_id"] is not None, f"Agente {row['id']} sin padre_1_id"
        assert row["padre_2_id"] is not None, f"Agente {row['id']} sin padre_2_id"
        assert int(row["generacion"]) > 1, f"Generación incorrecta: {row['generacion']}"
        print(f"    Nuevo: {row['id']} (Gen {row['generacion']}) ← {row['padre_1_id']} x {row['padre_2_id']}")

    # Verificar ranking_historico
    cur.execute("SELECT COUNT(*) as c FROM ranking_historico WHERE fecha = CURRENT_DATE")
    ranking_count = cur.fetchone()["c"]
    assert ranking_count > 0, "No se registró ningún ranking_historico"
    print(f"  [PASS] ranking_historico: {ranking_count} filas insertadas.")

    conn.close()


def test_genealogy_chain():
    """Verifica que los nuevos agentes tienen padres válidos que existen en la tabla."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT a.id, a.padre_1_id, a.padre_2_id, a.generacion
        FROM agentes a
        WHERE a.generacion > 1
        ORDER BY a.id
    """)
    children = cur.fetchall()
    if not children:
        print("  [SKIP] No hay agentes de generacion > 1 todavia.")
        conn.close()
        return

    for child in children:
        # Verificar padre_1
        cur.execute("SELECT id FROM agentes WHERE id = %s", (child["padre_1_id"],))
        assert cur.fetchone(), f"padre_1_id '{child['padre_1_id']}' no existe para hijo {child['id']}"
        # Verificar padre_2
        cur.execute("SELECT id FROM agentes WHERE id = %s", (child["padre_2_id"],))
        assert cur.fetchone(), f"padre_2_id '{child['padre_2_id']}' no existe para hijo {child['id']}"

    print(f"  [PASS] Genealogía verificada: {len(children)} hijos con padres válidos en DB.")
    conn.close()


# ── Tests Sesión 7: Periodo de Gracia + Cuota Dinámica + Diversidad ─────────

def test_business_days_calculation():
    """Días hábiles cuentan sólo lun-vie; sábado y domingo se ignoran."""
    from evolution.evolution_engine import _business_days_between

    # Lunes 2026-05-04 → miércoles 2026-05-06 = 2 días hábiles
    assert _business_days_between(date(2026, 5, 4), date(2026, 5, 6)) == 2
    # Viernes 2026-05-08 → lunes 2026-05-11 atraviesa fin de semana = 1 día hábil (vie)
    assert _business_days_between(date(2026, 5, 8), date(2026, 5, 11)) == 1
    # Mismo día → 0
    assert _business_days_between(date(2026, 5, 11), date(2026, 5, 11)) == 0
    # End < start → 0
    assert _business_days_between(date(2026, 5, 12), date(2026, 5, 10)) == 0
    # Sábado a domingo → 0 días hábiles
    assert _business_days_between(date(2026, 5, 9), date(2026, 5, 11)) == 0
    print("  [PASS] _business_days_between correcto en weekends y rangos vacíos.")


def test_classify_eligibility_grace_period():
    """Semántica de eligibilidad tras Sesión 17 Fases 3-4:
    - Grace inviolable (ops=0, edad < GRACE_PERIOD_DAYS)
    - Muestra mínima híbrida: inmune si trades<MIN Y días<MIN_SAMPLE_DAYS
    - Elegible por días: trades<MIN pero edad>=MIN_SAMPLE_DAYS
    - Elegible por trades: trades>=MIN (cualquier edad)
    - Inmunidad revocada por drawdown (roi <= -IMMUNITY_MAX_LOSS_PCT)
    """
    from evolution.evolution_engine import EvolutionEngine, IMMUNITY_MAX_LOSS_PCT

    today = date(2026, 5, 14)  # jueves
    # Días hábiles desde las fechas usadas abajo hasta hoy (exclusive):
    #   date(2026, 5, 13) Wed → 1 bd
    #   date(2026, 5, 11) Mon → 3 bd  (lun, mar, mié)
    #   date(2026, 5,  5) Tue → 7 bd  (mar…mié, excl. fin de semana)
    engine = EvolutionEngine(today)

    agents = [
        # A. Periodo de Gracia (inviolable): ops=0, age=1bd < GRACE_PERIOD_DAYS=2
        {"id": "A_GRACE",
         "operaciones_total": 0, "fecha_nacimiento": date(2026, 5, 13),
         "roi_total": 0},
        # B. Muestra mínima híbrida: ops=5<15 AND age=3bd<MIN_SAMPLE_DAYS=7 → inmune
        {"id": "A_SAMPLE",
         "operaciones_total": 5, "fecha_nacimiento": date(2026, 5, 11),
         "roi_total": 0},
        # C. Elegible por días: ops=5<15 PERO age=7bd >= MIN_SAMPLE_DAYS=7 → elegible
        {"id": "A_ELIGIBLE_DAYS",
         "operaciones_total": 5, "fecha_nacimiento": date(2026, 5, 5),
         "roi_total": 0},
        # D. Elegible por trades: ops=20>=15, age=1bd<7 → elegible (OR cumplida por trades)
        {"id": "A_ELIGIBLE_TRADES",
         "operaciones_total": 20, "fecha_nacimiento": date(2026, 5, 13),
         "roi_total": 0},
        # E. Inmunidad revocada: ops=5<15, age=3bd<7, roi=-10 <= -IMMUNITY_MAX_LOSS_PCT
        {"id": "A_REVOKED",
         "operaciones_total": 5, "fecha_nacimiento": date(2026, 5, 11),
         "roi_total": -(IMMUNITY_MAX_LOSS_PCT + 2.0)},  # −10.0 %
    ]

    immune, eligible = engine._classify_eligibility(agents)
    immune_ids   = {a["id"] for a in immune}
    eligible_ids = {a["id"] for a in eligible}

    assert immune_ids == {"A_GRACE", "A_SAMPLE"}, \
        f"Inmunes esperados: A_GRACE, A_SAMPLE. Recibí: {immune_ids}"
    assert "A_ELIGIBLE_DAYS"   in eligible_ids, "A_ELIGIBLE_DAYS debe ser elegible (días >= MIN)"
    assert "A_ELIGIBLE_TRADES" in eligible_ids, "A_ELIGIBLE_TRADES debe ser elegible (trades >= MIN)"
    assert "A_REVOKED"         in eligible_ids, "A_REVOKED debe ser elegible (inmunidad revocada)"
    revoked = next(a for a in eligible if a["id"] == "A_REVOKED")
    assert revoked.get("_immunity_revoked") is True, "A_REVOKED debe tener flag _immunity_revoked=True"
    print("  [PASS] _classify_eligibility: gracia, muestra híbrida, elegible días/trades, revocado.")


def test_dynamic_quota_protects_profitable_veterans():
    """Cuota dinámica = 0 cuando todos los elegibles tienen fitness > 0."""
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 5, 14))
    agents = [
        {"id": f"V{i}", "fitness_score": 0.05 + i * 0.01,
         "fecha_nacimiento": date(2026, 4, 1), "operaciones_total": 20,
         "roi_total": 0.03 + i * 0.01}
        for i in range(5)
    ]
    survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    assert len(eliminated) == 0, \
        f"No se debe eliminar a veteranos rentables, recibí: {[a['id'] for a in eliminated]}"
    assert len(survivors) == len(agents)
    print("  [PASS] Cuota dinámica = 0 cuando todo el pool tiene fitness > 0.")


def test_dynamic_quota_eliminates_only_negative_fitness():
    """Solo se eliminan agentes con fitness <= 0, hasta tope N_ELIMINATE."""
    from evolution.evolution_engine import EvolutionEngine

    engine = EvolutionEngine(date(2026, 5, 14))
    # 6 negativos + 4 positivos
    agents = (
        [{"id": f"N{i}", "fitness_score": -0.1 - i * 0.01,
          "fecha_nacimiento": date(2026, 4, 1), "operaciones_total": 20,
          "roi_total": -0.05}
         for i in range(6)]
        + [{"id": f"P{i}", "fitness_score": 0.05 + i * 0.01,
            "fecha_nacimiento": date(2026, 4, 1), "operaciones_total": 20,
            "roi_total": 0.04}
           for i in range(4)]
    )
    survivors, eliminated = engine.select_survivors_and_eliminated(agents)
    # Tope por N_ELIMINATE=5 aunque haya 6 con fitness negativo
    assert len(eliminated) == 5
    # Todos los eliminados deben tener fitness <= 0
    assert all(a["fitness_score"] <= 0 for a in eliminated), \
        "Se eliminó un agente con fitness > 0"
    # Ningún positivo en la lista de eliminados
    elim_ids = {a["id"] for a in eliminated}
    assert not any(eid.startswith("P") for eid in elim_ids), \
        f"Se eliminó un agente positivo: {elim_ids}"
    print(f"  [PASS] Cuota dinámica eliminó {len(eliminated)} agentes con fitness <= 0.")


def test_genetic_variance_low_for_clones():
    """Pool de clones idénticos retorna CV ≈ 0; pool diverso retorna CV alto."""
    from evolution.evolution_engine import _compute_genetic_variance

    base_tec = {"rsi_periodo": 14, "ema_rapida": 9, "ema_lenta": 21,
                "peso_rsi": 0.34, "peso_ema": 0.33, "peso_macd": 0.33}
    base_mac = {"peso_noticias_alto": 0.6, "umbral_sentimiento_compra": 0.65,
                "ventana_noticias_horas": 4, "peso_total_macro": 0.4}
    base_smc = {"fvg_min_pips": 5.0, "risk_reward_target": 2.0,
                "macro_quarantine_minutes": 60, "peso_fvg": 0.15,
                "peso_ob": 0.15, "atr_factor": 1.5}

    clones = [{"id": f"C{i}", "params_tecnicos": dict(base_tec),
               "params_macro": dict(base_mac), "params_smc": dict(base_smc)}
              for i in range(5)]
    cv_clones = _compute_genetic_variance(clones)
    assert cv_clones < 0.001, f"CV de clones debe ser ~0, fue {cv_clones}"

    # Pool diverso: alteramos cada agente
    diverse = []
    for i in range(5):
        tec = dict(base_tec)
        tec["rsi_periodo"]  = 8 + i * 2
        tec["ema_rapida"]   = 5 + i
        tec["peso_rsi"]     = 0.25 + i * 0.04
        mac = dict(base_mac)
        mac["peso_noticias_alto"] = 0.4 + i * 0.05
        smc = dict(base_smc)
        smc["fvg_min_pips"] = 3.0 + i * 1.5
        smc["risk_reward_target"] = 1.5 + i * 0.3
        diverse.append({"id": f"D{i}", "params_tecnicos": tec,
                        "params_macro": mac, "params_smc": smc})
    cv_diverse = _compute_genetic_variance(diverse)
    assert cv_diverse > 0.05, f"CV de pool diverso debe ser > 0.05, fue {cv_diverse}"
    print(f"  [PASS] CV clones={cv_clones:.6f}, CV diverso={cv_diverse:.4f}")


def test_breed_agent_accepts_dynamic_sigmas():
    """breed_agent acepta sigmas custom y los aplica en la mutación."""
    from evolution.evolution_engine import breed_agent

    base = {
        "id": "P_01", "roi_total": 0.05, "generacion": 1,
        "params_tecnicos": {
            "rsi_periodo": 14, "rsi_sobrecompra": 70, "rsi_sobreventa": 30,
            "ema_rapida": 9, "ema_lenta": 21,
            "macd_rapida": 12, "macd_lenta": 26, "macd_senal": 9,
            "peso_rsi": 0.35, "peso_ema": 0.35, "peso_macd": 0.30,
        },
        "params_macro": {
            "peso_noticias_alto": 0.60, "peso_noticias_medio": 0.25,
            "peso_noticias_bajo": 0.10, "umbral_sentimiento_compra": 0.65,
            "umbral_sentimiento_venta": 0.35, "ventana_noticias_horas": 4,
            "peso_total_macro": 0.40,
        },
        "params_riesgo": {
            "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
            "max_drawdown_diario_pct": 0.10, "capital_por_operacion_pct": 0.50,
            "umbral_confianza_minima": 0.60, "peso_tecnico_vs_macro": 0.55,
        },
    }

    # Genera 30 hijos con sigmas duplicadas — la dispersión debe ser mayor
    random.seed(42)
    boosted = [breed_agent(base, base, f"B_{i}", date.today(), 2,
                           sigma_weights=0.10, sigma_periods=0.16, sigma_risk=0.20)
               for i in range(30)]
    boosted_rsi = [c["params_tecnicos"]["rsi_periodo"] for c in boosted]
    range_boosted = max(boosted_rsi) - min(boosted_rsi)

    random.seed(42)
    normal = [breed_agent(base, base, f"N_{i}", date.today(), 2)
              for i in range(30)]
    normal_rsi = [c["params_tecnicos"]["rsi_periodo"] for c in normal]
    range_normal = max(normal_rsi) - min(normal_rsi)

    assert range_boosted >= range_normal, \
        f"Sigmas boosteadas debieron ampliar rango (boost={range_boosted}, normal={range_normal})"
    print(f"  [PASS] Rango RSI con boost={range_boosted}, normal={range_normal}.")


def test_suspension_on_hold_day_on_db():
    """
    Caso real en DB: día de HOLD generalizado.
    Todos los agentes con operaciones_total=0 y edad < 2 días hábiles → cuota=0.
    """
    _reset_agents()
    # Hacemos a TODOS los agentes inmunes: ops=0 y fecha_nacimiento=ayer (1 día hábil).
    today = date.today()
    # Buscar el día hábil anterior
    ayer = today - timedelta(days=1)
    while ayer.weekday() >= 5:
        ayer -= timedelta(days=1)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE agentes
        SET fecha_nacimiento = %s,
            roi_total = 0,
            capital_actual = 10.0,
            operaciones_total = 0,
            operaciones_ganadoras = 0,
            estado = 'activo',
            fecha_eliminacion = NULL,
            razon_eliminacion = NULL
        WHERE estado = 'activo'
    """, (ayer,))
    conn.commit()
    conn.close()

    from evolution.evolution_engine import EvolutionEngine
    engine = EvolutionEngine(today)
    result = engine.run()

    assert not result.errors, f"Errores: {result.errors}"
    assert result.cycle_suspended, "El ciclo debió quedar suspendido"
    assert len(result.eliminated) == 0
    assert len(result.new_agents) == 0
    assert len(result.immune_agents) > 0, "Debieron haber agentes inmunes"
    assert "Periodo de Gracia" in result.suspension_reason
    print(f"  [PASS] Ciclo suspendido: {len(result.immune_agents)} inmunes, "
          f"razón='{result.suspension_reason[:60]}...'")


def test_logs_juez_suspension_event_on_db():
    """Tras un ciclo suspendido, logs_juez tiene exactamente UN registro de evaluacion_diaria."""
    today = date.today()
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT descripcion, datos_json, razonamiento_llm
        FROM logs_juez
        WHERE fecha = %s AND tipo_evento = 'evaluacion_diaria'
        ORDER BY created_at DESC
    """, (today,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("  [SKIP] No hay evaluacion_diaria en logs_juez para hoy.")
        return

    latest = rows[0]
    datos = latest["datos_json"] or {}
    if datos.get("cycle_suspended"):
        assert datos.get("suspension_reason"), "Falta suspension_reason en datos_json"
        assert datos.get("cuota_aplicada") == 0
        assert "SUSPENDIDO" in (latest["descripcion"] or "").upper()
        print("  [PASS] logs_juez registra suspensión con justificación técnica.")
    else:
        print("  [SKIP] El último ciclo no estaba suspendido; no aplica esta verificación.")


def test_logs_juez_written():
    """Verifica que el ciclo evolutivo escribió entradas en logs_juez."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT tipo_evento, COUNT(*) as c
        FROM logs_juez
        WHERE fecha = CURRENT_DATE
        GROUP BY tipo_evento ORDER BY c DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("  [SKIP] No hay logs_juez para hoy (el ciclo quizás no se ejecutó aún).")
        return

    for row in rows:
        print(f"    logs_juez[{row['tipo_evento']}] = {row['c']} registros")

    tipos = {row["tipo_evento"] for row in rows}
    assert "evaluacion_diaria" in tipos or "eliminacion" in tipos, \
        "No se encontraron logs de evaluación o eliminación"
    print("  [PASS] logs_juez contiene registros del ciclo.")


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("Mutación respeta constraints (20 iter.)", test_mutation_respects_constraints),
        ("Crossover hereda de ambos padres", test_crossover_inherits_from_both_parents),
        ("Selección elige top y bottom correctamente", test_selection_picks_top_and_bottom),
        # Sesión 7 — Periodo de Gracia, Cuota Dinámica, Diversidad
        ("Días hábiles (lun-vie) — cálculo correcto", test_business_days_calculation),
        ("Clasificación de elegibilidad — Periodo de Gracia", test_classify_eligibility_grace_period),
        ("Cuota dinámica = 0 con veteranos rentables", test_dynamic_quota_protects_profitable_veterans),
        ("Cuota dinámica elimina solo fitness <= 0", test_dynamic_quota_eliminates_only_negative_fitness),
        ("Varianza genética: clones vs diverso", test_genetic_variance_low_for_clones),
        ("breed_agent acepta sigmas dinámicos", test_breed_agent_accepts_dynamic_sigmas),
        ("Suspensión por HOLD generalizado (DB)", test_suspension_on_hold_day_on_db),
        ("logs_juez: evento de suspensión", test_logs_juez_suspension_event_on_db),
        ("Ciclo evolutivo completo en DB Neon", test_full_evolution_cycle_on_db),
        ("Genealogía: hijos con padres válidos en DB", test_genealogy_chain),
        ("logs_juez: registros escritos", test_logs_juez_written),
    ]

    passed = failed = 0
    print("\n" + "="*65)
    print("  INVERSIÓN EVOLUTIVA — Test Suite Fase 3 (Motor Evolutivo)")
    print("="*65)

    for name, fn in tests:
        print(f"\n[TEST] {name}")
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print("\n" + "="*65)
    print(f"  RESULTADO: {passed}/{len(tests)} tests pasados | {failed} fallidos")
    print("="*65)
    sys.exit(0 if failed == 0 else 1)
