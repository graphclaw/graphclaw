"""Database connection pool management for GraphClaw.

Provides an async psycopg3 connection pool with Apache AGE initialised on
every connection.  The AGE extension must already be installed in Postgres;
this module only handles runtime session setup (LOAD + search_path).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# SQL executed once per physical connection to enable AGE queries.
_AGE_SETUP_SQL = """
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
"""


async def _setup_age(conn: AsyncConnection) -> None:
    """Run AGE session setup commands on a freshly opened connection."""
    await conn.execute("LOAD 'age'")
    await conn.execute("SET search_path = ag_catalog, \"$user\", public")


async def create_pool(
    dsn: str,
    min_size: int = 2,
    max_size: int = 10,
) -> AsyncConnectionPool:
    """Create and open an async connection pool with AGE pre-configured.

    Parameters
    ----------
    dsn:
        Postgres connection string, e.g.
        ``postgresql://user:pass@host:5432/dbname``
    min_size:
        Minimum number of connections kept open.
    max_size:
        Maximum number of connections the pool may open.

    Returns
    -------
    AsyncConnectionPool
        An open pool.  Caller is responsible for calling ``pool.close()``
        when the application shuts down.
    """
    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        # configure() runs synchronously during connection open inside the
        # pool's background thread, but psycopg_pool 3.x also supports an
        # async configure callable via open=False + manual open().
        open=False,
    )
    await pool.open(wait=True)
    logger.info(
        "Database pool opened",
        extra={"min_size": min_size, "max_size": max_size},
    )

    # Initialise AGE on every connection in the pool.
    async with pool.connection() as conn:
        await _setup_age(conn)

    return pool


@asynccontextmanager
async def get_connection(
    pool: AsyncConnectionPool,
) -> AsyncIterator[AsyncConnection]:
    """Async context manager that yields a connection from the pool.

    AGE session setup is re-applied on each checkout so it survives
    connection resets.

    Usage::

        async with get_connection(pool) as conn:
            await conn.execute(...)
    """
    async with pool.connection() as conn:
        await _setup_age(conn)
        yield conn
