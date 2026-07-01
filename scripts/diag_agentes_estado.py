"""Diagnóstico SOLO-LECTURA del estado de los agentes en producción.

No escribe nada. Verifica:
  1. Conteo de agentes por estado / especie / generación.
  2. Los agentes activos: capital, ROI, si tienen posición abierta.
  3. Posiciones abiertas ahora mismo.
  4. Actividad reciente del ciclo de 15 min (operaciones últimas 48h).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from db.connection import get_conn, get_dict_cursor

MIN_CAPITAL = float(os.getenv("MIN_CAPITAL_TO_TRADE", "2.0"))


def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"=== DIAGNÓSTICO AGENTES — {now.isoformat()} (UTC) ===\n")

    with get_conn() as conn:
        cur = get_dict_cursor(conn)

        # 1. Conteo por estado
        cur.execute("SELECT estado, COUNT(*) n FROM agentes GROUP BY estado ORDER BY estado")
        print("--- Agentes por estado ---")
        for r in cur.fetchall():
            print(f"  {r['estado']:12} : {r['n']}")

        # 2. Activos por especie y generación
        cur.execute(
            """
            SELECT COALESCE(especie,'(null)') especie, COUNT(*) n
            FROM agentes WHERE estado='activo' GROUP BY especie ORDER BY especie
            """
        )
        print("\n--- Activos por especie ---")
        for r in cur.fetchall():
            print(f"  {r['especie']:12} : {r['n']}")

        # 3. Detalle de cada agente activo + posición abierta
        cur.execute(
            """
            SELECT a.id, COALESCE(a.especie,'?') especie, a.generacion,
                   a.capital_actual::float cap, a.roi_total::float roi,
                   a.operaciones_total tot, a.operaciones_ganadoras gan,
                   (SELECT COUNT(*) FROM operaciones o
                      WHERE o.agente_id=a.id AND o.estado='abierta'
                        AND o.accion IN ('BUY','SELL')) AS pos_abierta
            FROM agentes a
            WHERE a.estado='activo'
            ORDER BY a.especie, a.id
            """
        )
        rows = cur.fetchall()
        print(f"\n--- Detalle de {len(rows)} agentes activos ---")
        print(f"{'id':18} {'especie':10} {'gen':>4} {'capital':>9} {'roi%':>8} {'ops':>4} {'win':>4} {'posAb':>5} {'puede?':>8}")
        for r in rows:
            puede = "SI" if (r['cap'] >= MIN_CAPITAL and r['pos_abierta'] == 0) else ("POS-abierta" if r['pos_abierta'] else "cap<min")
            print(f"{r['id']:18} {r['especie']:10} {r['generacion']:4} "
                  f"{r['cap']:9.4f} {r['roi']*100:8.2f} "
                  f"{(r['tot'] or 0):4} {(r['gan'] or 0):4} {r['pos_abierta']:5} {puede:>8}")

        # 4. Posiciones abiertas ahora
        cur.execute(
            """
            SELECT o.id, o.agente_id, o.accion, o.timestamp_entrada,
                   o.precio_entrada::float pe
            FROM operaciones o
            WHERE o.estado='abierta' AND o.accion IN ('BUY','SELL')
            ORDER BY o.timestamp_entrada
            """
        )
        op = cur.fetchall()
        print(f"\n--- Posiciones abiertas ahora: {len(op)} ---")
        for r in op:
            edad = now - r['timestamp_entrada'].replace(tzinfo=timezone.utc) if r['timestamp_entrada'] else None
            print(f"  op#{r['id']} {r['agente_id']} {r['accion']} entrada={r['pe']:.5f} ts={r['timestamp_entrada']} edad={edad}")

        # 5. Actividad reciente (últimas 48h) — ¿corre el ciclo?
        since = now - timedelta(hours=48)
        cur.execute(
            """
            SELECT DATE_TRUNC('hour', timestamp_entrada) hora,
                   accion, COUNT(*) n
            FROM operaciones
            WHERE timestamp_entrada >= %s
            GROUP BY hora, accion ORDER BY hora DESC, accion
            """,
            (since,),
        )
        act = cur.fetchall()
        print(f"\n--- Operaciones creadas últimas 48h (agrupadas por hora UTC) ---")
        if not act:
            print("  (ninguna operación registrada en 48h)")
        for r in act:
            print(f"  {r['hora']}  {r['accion']:5} x{r['n']}")

        # 6. Última operación de cualquier tipo
        cur.execute(
            "SELECT id, agente_id, accion, estado, timestamp_entrada FROM operaciones ORDER BY id DESC LIMIT 5"
        )
        print("\n--- Últimas 5 operaciones (cualquier estado) ---")
        for r in cur.fetchall():
            print(f"  op#{r['id']} {r['agente_id']} {r['accion']:5} estado={r['estado']:10} ts={r['timestamp_entrada']}")


if __name__ == "__main__":
    main()
