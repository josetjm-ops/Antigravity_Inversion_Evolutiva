"""Reconstruye los ciclos de 15 min desde la DB (SOLO LECTURA).

Para cada bucket de 15 min muestra cuántos agentes distintos registraron
decisión y el desglose por especie/acción. Confirma cadencia y participación.
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv()
from db.connection import get_conn, get_dict_cursor


def main() -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=14)
    with get_conn() as conn:
        cur = get_dict_cursor(conn)

        # Por ciclo de 15 min: agentes distintos + acciones
        cur.execute(
            """
            SELECT to_char(date_trunc('hour', o.timestamp_entrada)
                     + floor(extract(minute from o.timestamp_entrada)/15)*interval '15 min',
                     'MM-DD HH24:MI') AS ciclo,
                   COUNT(DISTINCT o.agente_id) agentes,
                   SUM((o.accion='HOLD')::int)  holds,
                   SUM((o.accion='BUY')::int)   buys,
                   SUM((o.accion='SELL')::int)  sells,
                   string_agg(DISTINCT COALESCE(a.especie,'?'), ',') especies
            FROM operaciones o JOIN agentes a ON a.id=o.agente_id
            WHERE o.timestamp_entrada >= %s
            GROUP BY ciclo ORDER BY ciclo DESC
            """,
            (since,),
        )
        print(f"=== Ciclos de 15 min — últimas 14h (UTC) — ahora={now.strftime('%H:%M')} ===")
        print(f"{'ciclo':12} {'agentes':>7} {'HOLD':>5} {'BUY':>4} {'SELL':>5}  especies-que-actuaron")
        for r in cur.fetchall():
            print(f"{r['ciclo']:12} {r['agentes']:7} {r['holds'] or 0:5} {r['buys'] or 0:4} "
                  f"{r['sells'] or 0:5}  {r['especies']}")

        # ¿Aparece cada uno de los 15 agentes en las últimas 24h?
        cur.execute(
            """
            SELECT a.id, COALESCE(a.especie,'?') especie,
                   COUNT(o.id) decisiones_24h,
                   MAX(o.timestamp_entrada) ultima
            FROM agentes a
            LEFT JOIN operaciones o ON o.agente_id=a.id
                 AND o.timestamp_entrada >= %s
            WHERE a.estado='activo'
            GROUP BY a.id, a.especie ORDER BY a.especie, a.id
            """,
            (now - timedelta(hours=24),),
        )
        print(f"\n=== Participación por agente — últimas 24h ===")
        print(f"{'id':18} {'especie':10} {'decisiones':>10}  última")
        for r in cur.fetchall():
            ult = r['ultima'].strftime('%m-%d %H:%M') if r['ultima'] else '—NUNCA—'
            flag = '' if r['decisiones_24h'] else '   <-- sin actividad 24h'
            print(f"{r['id']:18} {r['especie']:10} {r['decisiones_24h']:10}  {ult}{flag}")


if __name__ == "__main__":
    main()
