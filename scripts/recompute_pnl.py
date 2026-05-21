"""
Migración one-off: recalcula pnl, capital_usado (nocional USD) y pnl_porcentaje
para todas las operaciones cerradas (BUY/SELL) que tenían capital_usado en lotes.

Antes del fix el sizer guardaba lotes (~0.044) en capital_usado y el P&L usaba
una fórmula de retorno porcentual sobre ese valor → pnl ~1000× comprimido.
Ahora capital_usado = nocional USD = lotes × 1000 × precio_entrada.

Fórmulas corregidas:
  BUY:  pnl = (precio_salida - precio_entrada) * capital_usado_lotes * 1000
  SELL: pnl = (precio_entrada - precio_salida) * capital_usado_lotes * 1000
  pnl_pct = pnl / capital_inicial * 100   (aprox con capital_inicial=$10)

Reconstruye capital_actual y roi_total de cada agente desde cero.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from db.connection import get_conn, get_dict_cursor

UNITS_PER_LOT   = 1000.0
CAPITAL_INICIAL = 10.0   # capital inicial por agente en Gen1


def recompute() -> None:
    with get_conn() as conn:
        cur = get_dict_cursor(conn)

        # 1. Traer todas las ops cerradas BUY/SELL
        cur.execute("""
            SELECT id, agente_id, accion,
                   precio_entrada::float,
                   precio_salida::float,
                   capital_usado::float
            FROM operaciones
            WHERE estado = 'cerrada' AND accion IN ('BUY', 'SELL')
            ORDER BY agente_id, id
        """)
        ops = [dict(r) for r in cur.fetchall()]

    print(f"Operaciones cerradas a recomputar: {len(ops)}")

    # 2. Calcular nuevos valores
    updates: list[tuple] = []
    for op in ops:
        lotes           = op["capital_usado"]          # valor viejo = nº de lotes
        entrada         = op["precio_entrada"]
        salida          = op["precio_salida"]
        nocional_usd    = round(lotes * UNITS_PER_LOT * entrada, 4)

        if op["accion"] == "BUY":
            pnl = round((salida - entrada) * lotes * UNITS_PER_LOT, 4)
        else:
            pnl = round((entrada - salida) * lotes * UNITS_PER_LOT, 4)

        pnl_pct = round(pnl / CAPITAL_INICIAL * 100, 4)

        updates.append((nocional_usd, pnl, pnl_pct, op["id"]))
        print(
            f"  Op {op['id']:4d} | {op['agente_id']} | {op['accion']:4s} "
            f"| lotes={lotes:.4f} -> nocional=${nocional_usd:.2f} "
            f"| pnl_old=${op.get('pnl',0):.4f} -> pnl_new=${pnl:.4f}"
        )

    # 3. Actualizar operaciones
    with get_conn() as conn:
        cur = conn.cursor()
        for nocional_usd, pnl, pnl_pct, op_id in updates:
            cur.execute(
                """
                UPDATE operaciones
                SET capital_usado   = %s,
                    pnl             = %s,
                    pnl_porcentaje  = %s
                WHERE id = %s
                """,
                (nocional_usd, pnl, pnl_pct, op_id),
            )
    print("\nOperaciones actualizadas en BD.")

    # 4. Reconstruir capital_actual y roi_total por agente
    agent_pnl: dict[str, float] = {}
    for nocional_usd, pnl, pnl_pct, op_id in updates:
        # buscar agente_id para este op_id
        op = next(o for o in ops if o["id"] == op_id)
        agent_pnl.setdefault(op["agente_id"], 0.0)
        agent_pnl[op["agente_id"]] += pnl

    print("\nReconstrucción de capital por agente:")
    with get_conn() as conn:
        cur = conn.cursor()
        for agent_id, total_pnl in agent_pnl.items():
            nuevo_capital = round(CAPITAL_INICIAL + total_pnl, 4)
            roi_total     = round(total_pnl / CAPITAL_INICIAL * 100, 4)
            print(
                f"  {agent_id}: pnl_total=${total_pnl:.4f} "
                f"-> capital=${nuevo_capital:.4f} roi={roi_total:.4f}%"
            )
            cur.execute(
                """
                UPDATE agentes
                SET capital_actual = %s,
                    roi_total      = %s
                WHERE id = %s
                """,
                (nuevo_capital, roi_total, agent_id),
            )

    # Agentes sin ningún trade cerrado: dejarlos en capital_inicial (ya están bien)
    print("\nMigracion completada.")


if __name__ == "__main__":
    recompute()
