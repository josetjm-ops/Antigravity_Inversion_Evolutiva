"""
Migración completa al perfil campeón + capitalización — 2026-08-10.

VEREDICTO QUE LA MOTIVA
-----------------------
Revisión del 10-ago con grupo de control real en producción (31jul-10ago):

    perfil CAMPEON:  24 ops  +$0.3395  WR 37.5%  expectancy +0.184 R
    perfil LEGACY :  27 ops  -$1.8570  WR 14.8%  expectancy -0.794 R

TODA la pérdida vino de agentes sin edge validado. El contrafactual de esos
mismos 8 días con 100% perfil campeón era +$0.72 en vez de -$1.52.

DOS HALLAZGOS ESTRUCTURALES QUE ESTA MIGRACIÓN CORRIGE
-------------------------------------------------------
1. EL PISO ERA UN ATRACTOR. Los 5 agentes nacidos el 8-ago heredaron
   exactamente risk_reward_target=2.50 y be_activation_r=0.80 — el mínimo
   permitido — en vez de converger hacia el 3.54-3.82 / 0.88-1.00 de los
   campeones validados. Con muestras de 10-20 trades la selección natural no
   distingue 2.5 de 3.8, así que se acomoda en el límite inferior del rango.
   Corregido subiendo los pisos AL valor validado (no por debajo "para dar
   margen"): MIN_RISK_REWARD_TARGET 2.5->3.5, BE_ACTIVATION_MIN_R 0.8->0.88.

2. CONCENTRACIÓN EN LA ESPECIE VALIDADA. De las 3 especies solo reversion
   pasó el gate bootstrap contra holdout (+0.50R a +0.71R, n=92-115).
   Tendencia y ruptura fallaron ese gate offline Y pierden en producción.
   Nueva distribución: reversion 11, tendencia 2, ruptura 2 (= 15). No se
   eliminan las otras dos: quedan en el piso para conservar diversidad de
   régimen, pero dejan de consumir 2/3 del capital sin evidencia.

DIVERSIDAD GENÉTICA
-------------------
Los hijos NO se clonan de un solo padre: se crían emparejando los 4 agentes
con perfil campeón ya vivos en rotación. 15 copias de un mismo genoma no son
15 estrategias — son UNA estrategia con 15x de tamaño, y su riesgo es
perfectamente correlacionado. Emparejar padres distintos preserva variedad
real dentro del perfil validado.

CAPITALIZACIÓN
--------------
El sistema es PAPER TRADING (data/simulated_broker.py: precios reales de
Yahoo, dinero virtual) — inyectar capital no arriesga dinero real. A $92 de
pool las posiciones eran de 67 unidades EUR = 0.0007 lotes estándar, 14x por
debajo del mínimo ejecutable de cualquier bróker (0.01): la simulación estaba
operando tamaños imposibles. Con $15.000 / 15 agentes = $1.000 c/u las
posiciones son ~11.667 unidades = 0.1167 lotes, que redondean a 0.12 con
2.9% de desviación. La expectancy NO cambia (es un ratio); lo que cambia es
que el resultado pasa a ser trasladable a una cuenta real.

Uso:
    python -m scripts.migracion_completa_campeon
    python -m scripts.migracion_completa_campeon --confirmar
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("MigracionCampeon")

CAPITAL_TOTAL = float(__import__("os").getenv("MIGRACION_CAPITAL_TOTAL", "15000.0"))

# Distribución objetivo tras la migración (= TARGET_AGENTS_* del workflow).
OBJETIVO = {"reversion": 11, "tendencia": 2, "ruptura": 2}

# Un agente cuenta como "perfil campeón" si sus genes ya están en la zona
# validada. Se usa para decidir a quién conservar y a quién criar.
def es_campeon(smc: dict) -> bool:
    return (float(smc.get("risk_reward_target") or 0) >= 3.4
            and float(smc.get("be_activation_r") or 0) >= 0.85)


def _cargar(v):
    return v if isinstance(v, dict) else json.loads(v or "{}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Migracion completa al perfil campeon")
    ap.add_argument("--confirmar", action="store_true",
                    help="Ejecuta los cambios reales. Sin este flag es dry-run.")
    args = ap.parse_args()

    from db.connection import get_conn, get_dict_cursor
    from evolution.evolution_engine import (
        EvolutionEngine, breed_agent, calc_fitness_scores,
        SIGMA_WEIGHTS, SIGMA_PERIODS, SIGMA_RISK,
    )

    hoy = date.today()

    with get_conn() as conn:
        cur = get_dict_cursor(conn)

        cur.execute("SELECT * FROM agentes WHERE estado='activo' ORDER BY id")
        activos = cur.fetchall()
        for a in activos:
            a["_smc"] = _cargar(a["params_smc"])

        campeones = [a for a in activos if es_campeon(a["_smc"])]
        legacy    = [a for a in activos if not es_campeon(a["_smc"])]

        if not campeones:
            log.error("No hay ningun agente con perfil campeon vivo. Abortado.")
            return 1

        # Guardarrail: no retirar un agente con una posicion ABIERTA — quedaria
        # huerfana y el monitor no volveria a tocarla. Solo importa para los que
        # se van: los campeones conservados pueden tener posiciones abiertas sin
        # problema (siguen vivos y su capital_usado ya quedo fijado a la entrada,
        # asi que recapitalizar no altera el P&L de esa operacion).
        cur.execute("""
            SELECT o.id, o.agente_id FROM operaciones o
            WHERE o.estado='abierta' AND o.agente_id = ANY(%s)
        """, ([a["id"] for a in legacy],))
        bloqueantes = cur.fetchall()
        if bloqueantes:
            log.error("Hay %d operacion(es) ABIERTA(S) de agentes a retirar: %s",
                      len(bloqueantes),
                      [(b["id"], b["agente_id"]) for b in bloqueantes])
            log.error("Abortado: quedarian huerfanas. Reintentar cuando cierren "
                      "(el monitor las cierra en SL/TP o al EOD).")
            return 1

        cur.execute("SELECT COUNT(*) AS n FROM operaciones WHERE estado='abierta'")
        n_abiertas = int(cur.fetchone()["n"])
        if n_abiertas:
            log.info("Hay %d posicion(es) abierta(s), todas de campeones que se "
                     "conservan — la migracion no las afecta.", n_abiertas)

        log.info("Poblacion actual: %d activos -> %d campeon / %d legacy",
                 len(activos), len(campeones), len(legacy))
        for a in campeones:
            log.info("  CONSERVAR %s (%s) RR=%.2f be=%.2f",
                     a["id"], a["especie"], a["_smc"].get("risk_reward_target", 0),
                     a["_smc"].get("be_activation_r", 0))

        # Cuantos hijos hacen falta por especie para llegar al objetivo,
        # contando solo los campeones que ya sobreviven.
        conservados_por_esp: dict[str, int] = {}
        for a in campeones:
            conservados_por_esp[a["especie"]] = conservados_por_esp.get(a["especie"], 0) + 1

        faltan = {esp: max(0, obj - conservados_por_esp.get(esp, 0))
                  for esp, obj in OBJETIVO.items()}
        total_nuevos = sum(faltan.values())

        log.info("Objetivo %s | conservados %s | a criar %s (total %d)",
                 OBJETIVO, conservados_por_esp, faltan, total_nuevos)
        log.info("A retirar: %d agentes legacy", len(legacy))

        cur.execute("SELECT COALESCE(MAX(generacion),1) AS g FROM agentes")
        generacion = int(cur.fetchone()["g"]) + 1

        fitness_map = calc_fitness_scores(conn)
        padres = []
        for a in campeones:
            padres.append({
                "id": a["id"],
                "fitness_score": fitness_map.get(a["id"], 0.0),
                "params_tecnicos": _cargar(a["params_tecnicos"]),
                "params_macro":    _cargar(a["params_macro"]),
                "params_riesgo":   _cargar(a["params_riesgo"]),
                "params_smc":      a["_smc"],
            })

        # Indice de IDs libres para hoy.
        cur.execute("SELECT id FROM agentes WHERE id LIKE %s ORDER BY id DESC LIMIT 1",
                    (f"{hoy.isoformat()}_%",))
        fila = cur.fetchone()
        idx = (int(str(fila["id"]).split("_")[-1]) + 1) if fila else 1

        hijos = []
        k = 0
        for esp, cuantos in faltan.items():
            for _ in range(cuantos):
                # Emparejamiento rotatorio entre padres distintos: evita que
                # los 15 agentes sean el mismo genoma con ruido.
                p1 = padres[k % len(padres)]
                p2 = padres[(k + 1) % len(padres)] if len(padres) > 1 else p1
                child = breed_agent(
                    p1, p2, f"{hoy.isoformat()}_{idx:02d}", hoy, generacion,
                    sigma_weights=SIGMA_WEIGHTS, sigma_periods=SIGMA_PERIODS,
                    sigma_risk=SIGMA_RISK, especie=esp,
                )
                child["especie"] = esp
                # El gen de sesión NO se hereda a ciegas de padres reversion.
                # Lo que se quiere copiar del campeón es su ECONOMÍA (RR, be,
                # atr); la ventana horaria es un hallazgo específico por
                # especie y la evidencia difiere:
                #   reversion: dentro overlap +$0.56 (WR 56%) vs fuera -$1.68
                #              (WR 33%) -> overlap validado, se fuerza.
                #   ruptura:   dentro overlap -$0.72 (WR 21%) vs fuera -$0.00
                #              (WR 44%) -> overlap PERJUDICA, no se impone.
                #   tendencia: muestra insuficiente -> no se impone nada.
                # Sin este override los hijos de ruptura/tendencia heredaban
                # overlap de sus padres reversion, justo lo contrario de lo
                # que dicen los datos.
                child["params_smc"]["sesion_trading"] = (
                    "overlap" if esp == "reversion" else "cualquiera"
                )
                child["capital_inicial"] = 0.0
                child["capital_actual"] = 0.0
                child["fitness_oos_prometido"] = None
                child["n_trades_oos_prometido"] = None
                child["padre_1_id"] = p1["id"]
                child["padre_2_id"] = p2["id"]
                hijos.append(child)
                idx += 1
                k += 1

        n_final = len(campeones) + len(hijos)
        cuota = round(CAPITAL_TOTAL / n_final, 4)

        log.info("Hijos a crear (%d):", len(hijos))
        for h in hijos:
            s = h["params_smc"]
            log.info("  %s (%s) RR=%.2f be=%.2f atr=%.2f ses=%-10s padres=%s x %s",
                     h["id"], h["especie"], s.get("risk_reward_target", 0),
                     s.get("be_activation_r", 0), s.get("atr_factor", 0),
                     s.get("sesion_trading"), h["padre_1_id"], h["padre_2_id"])

        log.info("")
        log.info("Capital: $%.2f entre %d agentes = $%.4f cada uno (equitativo)",
                 CAPITAL_TOTAL, n_final, cuota)

        if not args.confirmar:
            log.info("")
            log.info("DRY-RUN: no se modifico nada. Repite con --confirmar.")
            return 0

        # ── Ejecucion ────────────────────────────────────────────────────
        for a in legacy:
            cur.execute(
                """
                UPDATE agentes SET estado='eliminado', fecha_eliminacion=%s,
                       razon_eliminacion=%s WHERE id=%s
                """,
                (hoy,
                 "Migracion 2026-08-10: perfil legacy sin edge validado "
                 "(-0.794R en produccion vs +0.184R del perfil campeon).",
                 a["id"]),
            )
        log.info("Retirados %d agentes legacy.", len(legacy))

        engine = EvolutionEngine(hoy)
        for h in hijos:
            engine._insert_new_agent(conn, h)
        log.info("Insertados %d hijos.", len(hijos))

        # Normalizar la sesión de los campeones CONSERVADOS. Sin esto queda un
        # agente de reversion con sesion='cualquiera' (2026-07-31_01), que es
        # justo la configuración que pierde: reversion fuera de la ventana
        # overlap dio -$1.68 (WR 33%) frente a +$0.56 (WR 56%) dentro. Solo se
        # toca este gen — su economía (RR/be/atr) ya está validada y no se
        # altera.
        n_norm = 0
        for a in campeones:
            deseada = "overlap" if a["especie"] == "reversion" else "cualquiera"
            if a["_smc"].get("sesion_trading") != deseada:
                smc = dict(a["_smc"])
                smc["sesion_trading"] = deseada
                cur.execute("UPDATE agentes SET params_smc=%s WHERE id=%s",
                            (json.dumps(smc), a["id"]))
                log.info("  sesion normalizada en %s (%s): %s -> %s",
                         a["id"], a["especie"],
                         a["_smc"].get("sesion_trading"), deseada)
                n_norm += 1
        if n_norm:
            log.info("Sesion normalizada en %d campeon(es) conservado(s).", n_norm)

        # Capital EQUITATIVO explicito (decision del propietario): todos
        # arrancan igual. No se usa _redistribute_capital porque aqui no se
        # reparte un pool existente — se fija un capital nuevo de partida.
        cur.execute("SELECT id FROM agentes WHERE estado='activo' ORDER BY id")
        ids_finales = [r["id"] for r in cur.fetchall()]
        cur2 = conn.cursor()
        for aid in ids_finales:
            cur2.execute(
                "UPDATE agentes SET capital_actual=%s, capital_inicial=%s WHERE id=%s",
                (cuota, cuota, aid),
            )
        log.info("Capital fijado a $%.4f en %d agentes.", cuota, len(ids_finales))

        for aid in [h["id"] for h in hijos]:
            cur2.execute(
                """
                INSERT INTO logs_juez (fecha, tipo_evento, agente_afectado_id,
                                       descripcion, datos_json)
                VALUES (%s, 'nuevo_agente', %s, %s, %s)
                """,
                (hoy, aid,
                 "Migracion completa al perfil campeon (2026-08-10) + "
                 "capitalizacion a $15.000.",
                 json.dumps({"origen": "migracion_completa_campeon",
                             "capital_total": CAPITAL_TOTAL})),
            )

        cur.execute("SELECT COALESCE(SUM(capital_actual),0) AS p, COUNT(*) AS n "
                    "FROM agentes WHERE estado='activo'")
        r = cur.fetchone()

    log.info("")
    log.info("MIGRACION COMPLETA: %d agentes activos, pool $%.2f",
             int(r["n"]), float(r["p"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
