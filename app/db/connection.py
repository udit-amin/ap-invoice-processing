"""Postgres connection pool and schema application.

A single lazily-initialised psycopg connection pool is shared process-wide.
The DSN comes from config.get_database_url() (env DATABASE_URL → .env →
docker-compose default).
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from app import config

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Return the shared connection pool, opening it on first use."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=config.get_database_url(),
            min_size=1,
            max_size=10,
            open=True,
            # Fail fast rather than blocking forever when Postgres is down,
            # so callers/tests can degrade gracefully.
            timeout=5.0,
        )
        atexit.register(close_pool)
    return _pool


@contextmanager
def cursor(autocommit: bool = True):
    """Yield a cursor from a pooled connection.

    autocommit=True (default) suits append-only writes and simple reads.
    Pass autocommit=False when you need an explicit transaction.
    """
    pool = get_pool()
    with pool.connection() as conn:
        conn.autocommit = autocommit
        with conn.cursor() as cur:
            yield cur


def apply_schema() -> None:
    """Apply app/db/schema.sql (idempotent — CREATE TABLE IF NOT EXISTS)."""
    ddl = config.SCHEMA_PATH.read_text()
    with cursor() as cur:
        cur.execute(ddl)


def close_pool() -> None:
    """Close the pool (mainly for test teardown)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
