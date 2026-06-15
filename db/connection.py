import logging
import os
import time
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

_log = logging.getLogger(__name__)

_DATABASE_URL = os.environ["DATABASE_URL"]

_engine = create_engine(
    _DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

_SessionFactory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# El pooler de Supabase sufre timeouts transitorios esporádicos (cold-starts);
# un solo intento tumbaba el run completo del workflow (issues de jun 2-10).
# El 2026-06-15 11:00 UTC un cold-start largo agotó los 3 intentos y volvió a
# tumbar el run → 4º intento + backoff más amplio para absorber el arranque
# en frío del pooler sin alertar por un blip.
_CONNECT_ATTEMPTS = 4
_CONNECT_BACKOFF_S = (5, 10, 20)


def _connect_with_retry():
    last_exc: Exception | None = None
    for attempt in range(_CONNECT_ATTEMPTS):
        try:
            return psycopg2.connect(_DATABASE_URL, connect_timeout=15)
        except psycopg2.OperationalError as exc:
            last_exc = exc
            if attempt < _CONNECT_ATTEMPTS - 1:
                wait = _CONNECT_BACKOFF_S[attempt]
                _log.warning(
                    "[DB] Conexión falló (intento %d/%d): %s — reintento en %ds",
                    attempt + 1, _CONNECT_ATTEMPTS, exc, wait,
                )
                time.sleep(wait)
    raise last_exc


@contextmanager
def get_conn():
    """
    Contexto psycopg2 puro con cursor RealDict para acceso por nombre de columna.
    Preferido en el motor evolutivo por simplicidad y rendimiento.
    """
    conn = _connect_with_retry()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def run_migration(sql_path: str) -> None:
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()
    with _engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()


def health_check() -> bool:
    for attempt in range(_CONNECT_ATTEMPTS):
        try:
            with _engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            if attempt < _CONNECT_ATTEMPTS - 1:
                wait = _CONNECT_BACKOFF_S[attempt]
                _log.warning(
                    "[DB] health_check falló (intento %d/%d): %s — reintento en %ds",
                    attempt + 1, _CONNECT_ATTEMPTS, exc, wait,
                )
                time.sleep(wait)
    return False
