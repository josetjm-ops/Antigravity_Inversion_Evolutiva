"""
Diagnóstico previo al backfill:
  - Verifica variables de entorno (GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_JSON, DATABASE_URL)
  - Prueba conexión a Google Sheets
  - Prueba conexión a PostgreSQL
  - Lista agentes y operaciones en DB

Uso: python utils/diagnose_backfill.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback

# Permite ejecución desde la raíz del proyecto
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from dotenv import load_dotenv
load_dotenv()


def main() -> None:
    errors = []

    sid   = os.getenv("GOOGLE_SHEET_ID", "").strip()
    creds = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    db    = os.getenv("DATABASE_URL", "").strip()

    print(f"GOOGLE_SHEET_ID:         {'SET (len=' + str(len(sid)) + ')' if sid else 'EMPTY'}")
    print(f"GOOGLE_CREDENTIALS_JSON: {'SET (len=' + str(len(creds)) + ')' if creds else 'EMPTY'}")
    print(f"DATABASE_URL:            {'SET (len=' + str(len(db)) + ')' if db else 'EMPTY'}")
    print()

    # ── Validar JSON de credenciales ───────────────────────────────────────────
    creds_info = None
    if not creds:
        print("ERROR: GOOGLE_CREDENTIALS_JSON está vacío.")
        errors.append("GOOGLE_CREDENTIALS_JSON vacío")
    else:
        try:
            creds_info = json.loads(creds)
            print(
                f"Credentials JSON válido: "
                f"type={creds_info.get('type')}, "
                f"project={creds_info.get('project_id', '?')}, "
                f"email={str(creds_info.get('client_email', '?'))[:40]}"
            )
        except json.JSONDecodeError as e:
            # Quizás es una ruta a archivo
            if os.path.isfile(creds):
                try:
                    with open(creds) as f:
                        creds_info = json.load(f)
                    print(f"Credentials cargadas desde archivo: {creds}")
                except Exception as e2:
                    print(f"ERROR leyendo archivo de credenciales: {e2}")
                    errors.append(f"credentials file error: {e2}")
            else:
                print(f"ERROR: GOOGLE_CREDENTIALS_JSON no es JSON válido ni ruta a archivo: {e}")
                errors.append(f"credentials JSON inválido: {e}")

    # ── Probar conexión a Google Sheets ────────────────────────────────────────
    if not sid:
        print("ERROR: GOOGLE_SHEET_ID está vacío.")
        errors.append("GOOGLE_SHEET_ID vacío")
    elif creds_info is None:
        print("SKIP: conexión a Sheets omitida (credenciales inválidas).")
    else:
        try:
            import gspread
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            client = gspread.service_account_from_dict(creds_info, scopes=scopes)
            sh = client.open_by_key(sid)
            ws_names = [ws.title for ws in sh.worksheets()]
            print(f"Sheets conexión OK: título='{sh.title}'  pestañas={ws_names}")
        except Exception as e:
            print(f"ERROR conectando a Sheets: {type(e).__name__}: {e}")
            traceback.print_exc()
            errors.append(f"Sheets error: {e}")

    # ── Probar conexión a PostgreSQL ───────────────────────────────────────────
    print()
    if not db:
        print("ERROR: DATABASE_URL está vacío — no se puede conectar a la DB.")
        errors.append("DATABASE_URL vacío")
    else:
        try:
            import psycopg2
            conn = psycopg2.connect(db, connect_timeout=10)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM agentes")
            n_agentes = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM operaciones")
            n_ops = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM operaciones WHERE estado = 'abierta'")
            n_abiertas = cur.fetchone()[0]
            conn.close()
            print(
                f"DB conexión OK: "
                f"agentes={n_agentes}, "
                f"operaciones={n_ops} (abiertas={n_abiertas})"
            )
        except Exception as e:
            print(f"ERROR conectando a DB: {type(e).__name__}: {e}")
            traceback.print_exc()
            errors.append(f"DB error: {e}")

    # ── Resultado final ────────────────────────────────────────────────────────
    print()
    if errors:
        print(f"DIAGNÓSTICO FALLIDO — {len(errors)} error(es):")
        for err in errors:
            print(f"  · {err}")
        sys.exit(1)
    else:
        print("DIAGNÓSTICO OK — todos los servicios accesibles.")


if __name__ == "__main__":
    main()
