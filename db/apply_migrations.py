"""
Aplica las migraciones SQL pendientes a la base de datos Neon.

Uso:
    python -m db.apply_migrations
    python -m db.apply_migrations --only 005,006
    python -m db.apply_migrations --all

Las migraciones se ejecutan en ORDEN cronológico (por nombre de archivo).
Todas están diseñadas para ser idempotentes (uso de IF EXISTS / IF NOT EXISTS
y UPDATE condicional), por lo que volver a correrlas no rompe nada.

Por defecto aplica únicamente las migraciones de la Sesión 7 nocturna:
    005_cleanup_oanda_columns.sql
    006_atr_period_backfill.sql
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Asegurar que la raíz del proyecto esté en sys.path cuando se invoque como script
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ApplyMigrations")

_MIGRATIONS_DIR = ROOT / "db" / "migrations"
_DEFAULT_MIGRATIONS = ["005", "006"]


def _list_available_migrations() -> list[Path]:
    return sorted(p for p in _MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def _filter_by_prefixes(all_migrations: list[Path], prefixes: list[str]) -> list[Path]:
    selected = []
    for p in all_migrations:
        prefix = p.name.split("_")[0]
        if prefix in prefixes:
            selected.append(p)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aplica migraciones SQL a la base de datos Neon."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--only",
        type=str,
        default=",".join(_DEFAULT_MIGRATIONS),
        help=(
            "Lista de prefijos numéricos de migración separados por coma. "
            "Default: '005,006' (las dos nuevas de Sesión 7 nocturna)."
        ),
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Aplica TODAS las migraciones del directorio en orden cronológico.",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL no está configurada en el entorno (.env).")
        return 1

    # Import del helper aquí para que dotenv tenga tiempo de cargar el .env
    from db.connection import health_check, run_migration

    if not health_check():
        log.error("Health check de la DB fallido. Abortando.")
        return 1
    log.info("Health check OK — DB accesible.")

    available = _list_available_migrations()
    if not available:
        log.error("No se encontraron migraciones en %s", _MIGRATIONS_DIR)
        return 1

    if args.all:
        selected = available
    else:
        prefixes = [p.strip() for p in args.only.split(",") if p.strip()]
        selected = _filter_by_prefixes(available, prefixes)
        missing = set(prefixes) - {p.name.split("_")[0] for p in selected}
        if missing:
            log.error(
                "No se encontraron migraciones con prefijo(s): %s",
                ", ".join(sorted(missing)),
            )
            return 1

    if not selected:
        log.warning("No hay migraciones para aplicar.")
        return 0

    log.info(
        "Aplicando %d migración(es) en orden: %s",
        len(selected),
        ", ".join(p.name for p in selected),
    )

    errors: list[str] = []
    for path in selected:
        log.info("→ Ejecutando %s ...", path.name)
        try:
            run_migration(str(path))
            log.info("   OK  %s aplicada.", path.name)
        except Exception as exc:
            log.error("   FAIL %s: %s", path.name, exc)
            errors.append(f"{path.name}: {exc}")

    if errors:
        log.error("Se produjeron %d error(es):", len(errors))
        for e in errors:
            log.error("  - %s", e)
        return 1

    log.info("Todas las migraciones se aplicaron correctamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
