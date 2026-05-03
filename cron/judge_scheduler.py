"""
Scheduler del Agente Juez.

Modos de operación:
  1. Demonio (--daemon): Corre indefinidamente con APScheduler.
     Dispara el ciclo evolutivo diariamente a las 17:00 (America/Bogota = UTC-5).
  2. One-shot (--run-now): Ejecuta el ciclo inmediatamente y termina.
     Usado por GitHub Actions y pruebas manuales.

Uso:
  python -m cron.judge_scheduler --run-now
  python -m cron.judge_scheduler --daemon
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Configurar logging antes de cualquier import del proyecto
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("JudgeScheduler")

# Añadir raíz del proyecto al path si se lanza como script
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_judge_cycle() -> dict:
    """Función que invoca el ciclo completo del Agente Juez."""
    from agents.judge_agent import JudgeAgent
    from db.connection import health_check

    log.info("Verificando conexión a la base de datos...")
    if not health_check():
        log.error("Health check fallido. Abortando ciclo.")
        return {"status": "error", "reason": "DB health check failed"}

    log.info("Iniciando ciclo del Agente Juez...")
    judge = JudgeAgent()
    result = judge.run_daily_cycle()

    log.info("Ciclo completado: %s", json.dumps({
        "status":    result.get("status"),
        "survivors": len(result.get("survivors", [])),
        "eliminated":len(result.get("eliminated", [])),
        "new_agents":len(result.get("new_agents", [])),
        "elapsed":   result.get("elapsed_sec"),
    }))
    return result


def start_daemon() -> None:
    """
    Inicia el scheduler APScheduler en modo bloqueante.
    Dispara run_judge_cycle() todos los días a las 17:00 America/Bogota.
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.error("APScheduler no instalado. Ejecuta: pip install apscheduler")
        sys.exit(1)

    tz = os.getenv("JUDGE_TIMEZONE", "America/Bogota")
    run_time = os.getenv("JUDGE_RUN_TIME", "17:00")
    hour, minute = run_time.split(":")

    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        func=run_judge_cycle,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
        id="judge_daily_cycle",
        name="Agente Juez — Ciclo Evolutivo Diario",
        misfire_grace_time=300,  # 5 min de gracia si el proceso estuvo caído
        coalesce=True,           # Sólo ejecuta una vez aunque haya disparos perdidos
    )

    log.info(
        "Scheduler iniciado. Próxima ejecución: %s todos los días a las %s (%s).",
        run_time, run_time, tz
    )

    next_run = scheduler.get_jobs()[0].next_run_time
    log.info("Próxima ejecución programada: %s", next_run)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler detenido por el usuario.")
        scheduler.shutdown(wait=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scheduler del Agente Juez — INVERSIÓN EVOLUTIVA"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-now",
        action="store_true",
        help="Ejecuta el ciclo evolutivo inmediatamente y termina.",
    )
    group.add_argument(
        "--daemon",
        action="store_true",
        help="Corre como demonio con APScheduler (17:00 Bogotá diario).",
    )
    group.add_argument(
        "--next-run",
        action="store_true",
        help="Muestra la próxima hora de ejecución programada y termina.",
    )

    args = parser.parse_args()

    if args.run_now:
        log.info("Modo: one-shot (--run-now)")
        result = run_judge_cycle()
        sys.exit(0 if result.get("status") == "success" else 1)

    elif args.daemon:
        log.info("Modo: demonio (--daemon)")
        start_daemon()

    elif args.next_run:
        tz = os.getenv("JUDGE_TIMEZONE", "America/Bogota")
        run_time = os.getenv("JUDGE_RUN_TIME", "17:00")
        print(f"Próxima ejecución: {run_time} {tz} (diario)")
        sys.exit(0)


if __name__ == "__main__":
    main()
