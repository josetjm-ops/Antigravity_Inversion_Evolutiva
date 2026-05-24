"""
Diagnóstico previo al backfill de Google Sheets.
Verifica que la DB y las credenciales de Sheets estén disponibles
y muestra un resumen del estado actual antes de sincronizar.
"""
from __future__ import annotations

import json
import logging
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

errors = 0

# ── 1. DB ─────────────────────────────────────────────────────────────────────
try:
    from db.connection import get_conn, get_dict_cursor, health_check
    if not health_check():
        log.error("❌ DB health check FALLIDA")
        errors += 1
    else:
        with get_conn() as conn:
            cur = get_dict_cursor(conn)
            cur.execute("SELECT COUNT(*) AS n FROM agentes WHERE estado='activo'")
            n_activos = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM agentes")
            n_total = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM operaciones")
            n_ops = cur.fetchone()["n"]
            cur.execute("SELECT MAX(generacion) AS g FROM agentes")
            max_gen = cur.fetchone()["g"] or 0
        log.info("✅ DB OK — agentes: %d activos / %d total | ops: %d | max_gen: %d",
                 n_activos, n_total, n_ops, max_gen)
except Exception as e:
    log.error("❌ Error conectando a DB: %s", e)
    errors += 1

# ── 2. Google Sheets credentials ──────────────────────────────────────────────
sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
creds_raw = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

if not sheet_id:
    log.error("❌ GOOGLE_SHEET_ID no configurado")
    errors += 1
else:
    log.info("✅ GOOGLE_SHEET_ID configurado (%s...)", sheet_id[:8])

if not creds_raw:
    log.error("❌ GOOGLE_CREDENTIALS_JSON no configurado")
    errors += 1
else:
    try:
        creds = json.loads(creds_raw)
        email = creds.get("client_email", "?")
        log.info("✅ GOOGLE_CREDENTIALS_JSON OK — service account: %s", email)
    except json.JSONDecodeError:
        log.error("❌ GOOGLE_CREDENTIALS_JSON no es JSON válido")
        errors += 1

# ── Resultado ─────────────────────────────────────────────────────────────────
if errors:
    log.error("Diagnóstico FALLIDO — %d error(es). Abortando backfill.", errors)
    sys.exit(1)

log.info("Diagnóstico OK — listo para sincronizar.")
