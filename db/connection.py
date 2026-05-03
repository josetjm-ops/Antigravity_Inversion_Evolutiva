import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

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


@contextmanager
def get_conn():
    """
    Contexto psycopg2 puro con cursor RealDict para acceso por nombre de columna.
    Preferido en el motor evolutivo por simplicidad y rendimiento.
    """
    conn = psycopg2.connect(_DATABASE_URL)
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
    sql = open(sql_path, encoding="utf-8").read()
    with _engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()


def health_check() -> bool:
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
