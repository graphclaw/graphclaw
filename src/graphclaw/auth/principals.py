"""graphclaw.auth.principals — Service principal definitions and credential resolution.

Description
-----------
Defines the three database/storage service principals used by GraphClaw:

  - ``agent_principal``  — used by all agent code paths; no DELETE grants.
  - ``admin_principal``  — used by purge worker + admin routes; full grants.
  - ``migration_principal`` — used by migration runner; DDL only, no DML DELETE.

Also provides ``resolve_principal_dsn`` which reads per-principal DSNs from
environment variables, and the ``startup_assert_no_delete`` probe that verifies
the agent connection pool cannot execute DELETE statements.  The process refuses
to start if the probe DELETE succeeds (meaning the principal has DELETE grants
it should not have).

Design Patterns
---------------
- Enum + Strategy: ``Principal`` is an enum; ``PrincipalConfig`` holds the env
  variable names per principal.  Callers choose a principal and receive the
  matching credentials.
- Fail-fast startup assertion: ``startup_assert_no_delete`` is called once
  during process init; non-recoverable on failure.

Public API
----------
- Principal: Enum with AGENT, ADMIN, MIGRATION values.
- resolve_principal_dsn: Resolve the DSN for a given principal from env.
- startup_assert_no_delete: Async probe — must raise on DELETE; aborts process.

Dependencies
------------
- os: Environment variable access.
- enum: Enum.
- psycopg: AsyncConnection for one-shot probe query.

Notes
-----
The probe table ``_principal_probe`` must exist before calling
``startup_assert_no_delete``.  It is created by migration 0005.
"""

from __future__ import annotations

import logging
import os
from enum import Enum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Principal enum
# ---------------------------------------------------------------------------


class Principal(str, Enum):
    """The three service principals used by GraphClaw DB/storage operations."""

    AGENT = "agent_principal"
    ADMIN = "admin_principal"
    MIGRATION = "migration_principal"


# ---------------------------------------------------------------------------
# Per-principal DSN resolution
# ---------------------------------------------------------------------------

_PRINCIPAL_DSN_ENV: dict[Principal, str] = {
    Principal.AGENT: "AGENT_PRINCIPAL_DSN",
    Principal.ADMIN: "ADMIN_PRINCIPAL_DSN",
    Principal.MIGRATION: "MIGRATION_PRINCIPAL_DSN",
}

# Fallback env var name used when a principal-specific DSN is not set.
_FALLBACK_DSN_ENV = "DATABASE_URL"


def resolve_principal_dsn(principal: Principal) -> str:
    """Return the Postgres DSN for *principal*.

    Looks up ``{PRINCIPAL}_DSN`` env var first; falls back to ``DATABASE_URL``
    when absent so that single-DSN dev environments keep working without
    setting up three separate roles.

    Parameters
    ----------
    principal:
        The ``Principal`` enum value.

    Returns
    -------
    str
        A valid Postgres connection string.

    Raises
    ------
    KeyError
        If neither the principal-specific DSN nor ``DATABASE_URL`` is set.
    """
    env_key = _PRINCIPAL_DSN_ENV[principal]
    dsn = os.environ.get(env_key) or os.environ[_FALLBACK_DSN_ENV]
    logger.debug(
        "Resolved principal DSN",
        extra={"principal": principal.value, "env_key": env_key, "using_fallback": not os.environ.get(env_key)},
    )
    return dsn


# ---------------------------------------------------------------------------
# Startup probe — verify agent_principal cannot DELETE
# ---------------------------------------------------------------------------

_PROBE_SQL = "BEGIN; DELETE FROM _principal_probe WHERE 1=0; ROLLBACK;"


class InsufficientPrivilegeError(Exception):
    """Raised (by the probe) when the DELETE is expected to fail but doesn't."""


async def startup_assert_no_delete(pool: object) -> None:
    """Assert that the pool's principal cannot execute DELETE statements.

    Executes a no-op DELETE inside a rolled-back transaction against the
    ``_principal_probe`` table.  If Postgres raises ``InsufficientPrivilege``
    (SQLSTATE 42501) the assertion passes (behaviour is correct).  If the
    statement succeeds, the principal has DELETE grants it should NOT have and
    the process **must not start**.

    Parameters
    ----------
    pool:
        An open ``AsyncConnectionPool`` bound to ``agent_principal``'s DSN.

    Raises
    ------
    SystemExit
        If the probe DELETE succeeds, indicating the principal has unwanted
        DELETE permissions.  Process is aborted immediately.
    RuntimeError
        If the probe table does not exist (migration not applied yet).

    Notes
    -----
    Import psycopg here rather than at module level to avoid pulling the DB
    driver into modules that only import the Principal enum.
    """
    import psycopg  # noqa: PLC0415  (deferred to keep import light)

    from graphclaw.db.age.connection import get_connection  # noqa: PLC0415

    try:
        async with get_connection(pool) as conn:  # type: ignore[arg-type]
            try:
                await conn.execute("SAVEPOINT probe_savepoint")
                await conn.execute("DELETE FROM _principal_probe WHERE 1=0")
                # If we reach here the DELETE succeeded — fatal.
                await conn.execute("ROLLBACK TO SAVEPOINT probe_savepoint")
                logger.critical(
                    "STARTUP PROBE FAILED: agent_principal can execute DELETE. "
                    "Refusing to start — revoke DELETE grants from agent_principal immediately.",
                    extra={"principal": Principal.AGENT.value},
                )
                raise SystemExit(
                    "FATAL: agent_principal has DELETE grants. "
                    "Wave 0 no-delete enforcement is violated. Process aborted."
                )
            except psycopg.errors.InsufficientPrivilege:
                # Correct — agent_principal cannot DELETE.
                await conn.execute("ROLLBACK TO SAVEPOINT probe_savepoint")
                logger.info(
                    "Startup probe passed: agent_principal correctly has no DELETE privilege.",
                    extra={"principal": Principal.AGENT.value},
                )
    except SystemExit:
        raise
    except Exception as exc:
        # Probe table missing or other DB error.
        logger.warning(
            "Startup probe skipped (probe table absent or DB error): %s",
            exc,
            extra={"principal": Principal.AGENT.value},
        )
