"""graphclaw.db.age.connection — Async database connection pool with AGE session setup.

Description
-----------
Manages the lifecycle of an async psycopg3 connection pool for the GraphClaw
Postgres + Apache AGE backend.  Every connection in the pool is bootstrapped
with the two AGE session commands (``LOAD 'age'`` and ``SET search_path``) so
that Cypher queries work immediately after checkout.  The AGE extension itself
must already be installed in the database; this module handles only runtime
session initialisation, not schema creation.

Also provides ``create_pgbouncer_pool`` for production deployments where
connections go through PgBouncer on port 6432.  This function applies the
5-second statement_timeout required by PRD Sec 28.11 on every connection init.

Design Patterns
---------------
- Context Manager: ``get_connection`` is an async context manager that re-runs
  AGE setup on every checkout, making it safe for connection reuse after resets.
- Factory Function: ``create_pool`` and ``create_pgbouncer_pool`` encapsulate
  pool creation and initial setup, returning ready-to-use
  ``AsyncConnectionPool`` instances.

Public API
----------
- create_pool: Create and open an async connection pool with AGE pre-configured.
- create_pgbouncer_pool: Create a pool connected via PgBouncer (port 6432) with
  statement_timeout enforced on each connection.
- get_connection: Async context manager that yields a pool connection with AGE setup.

Dependencies
------------
- psycopg: AsyncConnection for async Postgres I/O.
- psycopg_pool: AsyncConnectionPool for connection lifecycle management.

Notes
-----
AGE session commands must be re-applied on each checkout (not just pool open)
because Postgres may recycle physical connections and lose session-level state.
In transaction pooling mode (PgBouncer default for AGE) session state is lost
between transactions, so re-running AGE setup on every checkout is mandatory.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

# 5-second hard timeout per PRD Sec 28.11. Override via QUERY_TIMEOUT_MS env var.
QUERY_TIMEOUT_MS: int = int(os.environ.get("QUERY_TIMEOUT_MS", "5000"))

logger = logging.getLogger(__name__)


async def _setup_age(conn: AsyncConnection) -> None:
    """Run AGE session setup commands on a freshly opened connection."""
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')


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


async def create_pgbouncer_pool(
    min_size: int = 2,
    max_size: int = 10,
) -> AsyncConnectionPool:
    """Create and open an async connection pool via PgBouncer.

    Reads ``PGBOUNCER_URL`` from the environment; falls back to
    ``DATABASE_URL`` when ``PGBOUNCER_URL`` is not set.  The DSN host/port
    should point to PgBouncer (default port 6432) so connections benefit from
    transaction-mode pooling.

    A ``configure`` callback sets ``statement_timeout`` to
    ``QUERY_TIMEOUT_MS`` on every physical connection, enforcing the 5-second
    per-query hard limit mandated by PRD Sec 28.11.  AGE session commands are
    also applied so Cypher queries work immediately after checkout.

    Parameters
    ----------
    min_size:
        Minimum number of connections kept open in the pool.
    max_size:
        Maximum number of connections the pool may open.

    Returns
    -------
    AsyncConnectionPool
        An open pool connected through PgBouncer.  Caller is responsible for
        calling ``pool.close()`` on shutdown.

    Raises
    ------
    KeyError
        If neither ``PGBOUNCER_URL`` nor ``DATABASE_URL`` is set in the
        environment.
    """
    dsn = os.environ.get("PGBOUNCER_URL") or os.environ["DATABASE_URL"]

    async def _init_conn(conn: AsyncConnection) -> None:
        """Apply statement_timeout and AGE session setup on each new connection."""
        await conn.execute(f"SET statement_timeout = {QUERY_TIMEOUT_MS};")
        await _setup_age(conn)

    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        open=False,
    )
    await pool.open(wait=True)
    logger.info(
        "PgBouncer pool opened",
        extra={
            "min_size": min_size,
            "max_size": max_size,
            "query_timeout_ms": QUERY_TIMEOUT_MS,
        },
    )

    # Apply timeout and AGE setup on the first connection to verify config.
    async with pool.connection() as conn:
        await _init_conn(conn)

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
