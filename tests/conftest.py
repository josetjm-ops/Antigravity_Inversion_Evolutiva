"""
Aislamiento de entorno para TODA la suite de tests.

CRÍTICO: db/connection.py lee DATABASE_URL del entorno en el momento del
import, y load_dotenv() NO sobreescribe variables ya presentes. Sin este
conftest, cualquier test que ejecute EvolutionEngine u otro código que use
db.connection operaría contra la base de datos de PRODUCCIÓN (Supabase)
tomada del .env local — mientras los fixtures escriben en la sandbox Neon.

Esto OCURRIÓ el 2026-06-11: un run de pytest ejecutó un ciclo evolutivo
completo contra producción (eliminó 4 agentes reales y crió 4 hijos) y hubo
que revertirlo a mano. Este archivo es la barrera que lo impide.

pytest importa conftest.py ANTES que cualquier módulo de tests, por lo que
estas asignaciones ganan la carrera contra load_dotenv().
"""
import os

# Sandbox Neon de integración — misma constante que usan los tests de DB.
SANDBOX_DB = (
    "postgresql://neondb_owner:npg_HpqvWm94yaLr@"
    "ep-crimson-heart-amtwwmvf.c-5.us-east-1.aws.neon.tech/"
    "inversion_evolutiva?channel_binding=require&sslmode=require"
)

os.environ["DATABASE_URL"] = SANDBOX_DB

# Neutralizar Google Sheets: con GOOGLE_SHEET_ID vacío SheetsLogger queda
# sin cliente y todas sus llamadas son no-ops. Los tests jamás deben
# escribir en la hoja de producción.
os.environ["GOOGLE_SHEET_ID"] = ""
os.environ["GOOGLE_CREDENTIALS_JSON"] = ""
