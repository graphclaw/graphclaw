"""graphclaw.cli._shared — Shared database context manager for CLI sub-commands.

Description
-----------
Provides ``cli_pool``, a single async context manager that validates
``DATABASE_URL``, creates a connection pool, yields ``(pool, AgeGraphStore)``,
and closes the pool on exit.  All CLI sub-command modules use this helper to
eliminate copy-paste database setup boilerplate.

Design Patterns
---------------
- Context Manager Factory: ``cli_pool`` encapsulates the open/yield/close
  lifecycle so CLI commands cannot accidentally leave pools open on error paths.

Public API
----------
- cli_pool: Async context manager yielding (AsyncConnectionPool, AgeGraphStore).

Dependencies
------------
- graphclaw.db.connection: create_pool.
- graphclaw.db: GraphStore ABC, AgeGraphStore concrete implementation.
"""

from __future__ import annotations

import os
import selectors
import sys
from contextlib import asynccontextmanager

from graphclaw.db.age import AgeGraphStore
from graphclaw.db.connection import create_pool


def run_async(coro: object) -> object:  # type: ignore[return]
    """Run a coroutine using SelectorEventLoop on Windows (required by psycopg3 async).

    On all other platforms falls back to the standard ``asyncio.run()``.
    """
    import asyncio  # noqa: PLC0415

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        try:
            return loop.run_until_complete(coro)  # type: ignore[arg-type]
        finally:
            loop.close()
    return asyncio.run(coro)  # type: ignore[arg-type]


@asynccontextmanager
async def cli_pool():
    """Shared async context manager for CLI commands that need DB access.

    Yields (pool, repo) and closes the pool on exit.  Prints an error and
    raises SystemExit(1) if DATABASE_URL is not set.

    Usage::

        async with cli_pool() as (pool, repo):
            nodes = await repo.list_nodes("TaskNode")
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print(
            "ERROR: DATABASE_URL is not set. "
            "Set it in your environment or .env file before running CLI commands.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    pool = await create_pool(dsn)
    try:
        repo = AgeGraphStore(pool)
        yield pool, repo
    finally:
        await pool.close()
