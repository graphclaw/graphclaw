"""graphclaw.db.connection — Async database connection pool with AGE session setup.

Description
-----------
Manages the lifecycle of an async psycopg3 connection pool for the GraphClaw
Postgres + Apache AGE backend.  Every connection in the pool is bootstrapped
with the two AGE session commands (``LOAD 'age'`` and ``SET search_path``) so
that Cypher queries work immediately after checkout.  The AGE extension itself
must already be installed in the database; this module handles only runtime
session initialisation, not schema creation.

Design Patterns
---------------
- Context Manager: ``get_connection`` is an async context manager that re-runs
  AGE setup on every checkout, making it safe for connection reuse after resets.
- Factory Function: ``create_pool`` encapsulates pool creation and initial setup,
  returning a ready-to-use ``AsyncConnectionPool``.

Public API
----------
- create_pool: Create and open an async connection pool with AGE pre-configured.
- get_connection: Async context manager that yields a pool connection with AGE setup.

Dependencies
------------
- psycopg: AsyncConnection for async Postgres I/O.
- psycopg_pool: AsyncConnectionPool for connection lifecycle management.

Notes
-----
AGE session commands must be re-applied on each checkout (not just pool open)
because Postgres may recycle physical connections and lose session-level state.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

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
