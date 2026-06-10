from contextlib import contextmanager
from typing import Generator, Optional

import psycopg2
from psycopg2.pool import SimpleConnectionPool

from .config import get_settings

PoolType = Optional[SimpleConnectionPool]
_pool: PoolType = None


def init_pool() -> None:
    """Initialise the global PostgreSQL connection pool."""

    global _pool
    if _pool is not None:
        return

    settings = get_settings()
    _pool = SimpleConnectionPool(
        minconn=settings.postgres_min_pool_size,
        maxconn=settings.postgres_max_pool_size,
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def close_pool() -> None:
    """Close the connection pool if initialised."""

    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """Context manager yielding a pooled connection."""

    if _pool is None:
        raise RuntimeError("Database pool not initialised. Call init_pool() first.")

    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


@contextmanager
def get_cursor(commit: bool = False):
    """Yield a database cursor and optionally commit changes."""

    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()



