"""graphclaw.migrations.runner — Async migration runner backed by psycopg.

Description
-----------
``MigrationRunner`` manages the ``graphclaw_migrations`` tracking table and
applies forward-only schema migrations in version order.  It is the sole
component that executes DDL against the database; all SQL is supplied by the
``Migration`` objects in the catalogue.

Design Patterns
---------------
- Single responsibility: the runner owns connection management, the tracking
  table, and the apply/skip/fail logic; it does not define migrations.
- Forward-only, non-destructive: ``apply()`` refuses any migration whose
  ``is_destructive`` flag is ``True``.
- Idempotent: re-running ``apply_all`` on a fully-migrated database is safe —
  all versions are returned as ``SKIPPED``.

Public API
----------
- MigrationRunner: Async migration runner class.

Dependencies
------------
- psycopg: async PostgreSQL driver (psycopg3).
- graphclaw.migrations.models: Migration, MigrationStatus, MigrationError.
"""

# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from graphclaw.migrations.models import Migration, MigrationError, MigrationStatus

# ---------------------------------------------------------------------------
# Tracking table DDL
# ---------------------------------------------------------------------------

_CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS public.graphclaw_migrations (
    version     TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      TEXT        NOT NULL,
    PRIMARY KEY (version)
);
"""

_INSERT_MIGRATION_RECORD = """
INSERT INTO public.graphclaw_migrations (version, name, applied_at, status)
VALUES (%s, %s, %s, %s)
ON CONFLICT (version) DO UPDATE
    SET applied_at = EXCLUDED.applied_at,
        status     = EXCLUDED.status;
"""

_SELECT_APPLIED_VERSIONS = """
SELECT version FROM public.graphclaw_migrations
WHERE status = 'APPLIED';
"""


class MigrationRunner:
    """Applies forward-only schema migrations to a GraphClaw PostgreSQL database.

    Parameters
    ----------
    conn_string:
        psycopg3-compatible DSN, e.g.
        ``"postgresql://graphclaw:graphclaw@localhost:5432/graphclaw"``.
    """

    def __init__(self, conn_string: str) -> None:
        self._conn_string = conn_string

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_connection(self) -> psycopg.AsyncConnection:
        """Open and return a new psycopg async connection."""
        return await psycopg.AsyncConnection.connect(self._conn_string, autocommit=False)

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def ensure_migrations_table(self) -> None:
        """Create the ``public.graphclaw_migrations`` tracking table if absent.

        Safe to call on every startup — uses ``CREATE TABLE IF NOT EXISTS``.
        """
        conn = await self._get_connection()
        try:
            await conn.execute(_CREATE_MIGRATIONS_TABLE)
            await conn.commit()
        finally:
            await conn.close()

    async def get_applied_versions(self) -> set[str]:
        """Return the set of version strings that have been successfully applied.

        Returns
        -------
        set[str]
            Zero-padded 4-digit version strings, e.g. ``{"0001", "0002"}``.
        """
        conn = await self._get_connection()
        try:
            cursor = await conn.execute(_SELECT_APPLIED_VERSIONS)
            rows = await cursor.fetchall()
            return {row[0] for row in rows}
        finally:
            await conn.close()

    async def apply(self, migration: Migration) -> MigrationStatus:
        """Apply a single migration.

        Steps
        -----
        1. Reject ``is_destructive=True`` — raise :class:`MigrationError`.
        2. Check if the version is already applied — return ``SKIPPED``.
        3. Execute ``migration.sql_up`` inside a transaction.
        4. INSERT / UPSERT into ``graphclaw_migrations`` with ``status=APPLIED``.
        5. On any exception: record ``status=FAILED``, re-raise as
           :class:`MigrationError`.

        Parameters
        ----------
        migration:
            The :class:`~graphclaw.migrations.models.Migration` to apply.

        Returns
        -------
        MigrationStatus
            ``APPLIED`` on success, ``SKIPPED`` if already applied.

        Raises
        ------
        MigrationError
            If the migration is destructive or if DDL execution fails.
        """
        if migration.is_destructive:
            raise MigrationError(
                f"Migration {migration.version} ({migration.name!r}) is marked "
                f"is_destructive=True.  Destructive migrations are forbidden by "
                f"the GraphClaw forward-only migration policy."
            )

        applied = await self.get_applied_versions()
        if migration.version in applied:
            return MigrationStatus.SKIPPED

        conn = await self._get_connection()
        try:
            await conn.execute(migration.sql_up)
            now = datetime.now(UTC)
            await conn.execute(
                _INSERT_MIGRATION_RECORD,
                (migration.version, migration.name, now, MigrationStatus.APPLIED.value),
            )
            await conn.commit()
            return MigrationStatus.APPLIED

        except Exception as exc:
            # Best-effort: record the failure for observability, then re-raise
            try:
                await conn.rollback()
                now = datetime.now(UTC)
                await conn.execute(
                    _INSERT_MIGRATION_RECORD,
                    (
                        migration.version,
                        migration.name,
                        now,
                        MigrationStatus.FAILED.value,
                    ),
                )
                await conn.commit()
            except Exception:
                pass  # Don't mask the original error

            raise MigrationError(
                f"Migration {migration.version} ({migration.name!r}) failed: {exc}"
            ) from exc

        finally:
            await conn.close()

    async def apply_all(
        self, migrations: list[Migration]
    ) -> list[tuple[Migration, MigrationStatus]]:
        """Apply all migrations in version order, stopping on first failure.

        Parameters
        ----------
        migrations:
            List of :class:`~graphclaw.migrations.models.Migration` objects.
            Applied in ascending ``version`` order regardless of list order.

        Returns
        -------
        list[tuple[Migration, MigrationStatus]]
            One entry per migration, in version order.  Migrations not
            attempted (because a prior one failed) are not included.

        Notes
        -----
        - Migrations already applied are returned as ``SKIPPED``.
        - On the first ``FAILED`` result the loop stops; subsequent migrations
          are not attempted.
        """
        ordered = sorted(migrations, key=lambda m: m.version)
        results: list[tuple[Migration, MigrationStatus]] = []

        for migration in ordered:
            try:
                status = await self.apply(migration)
            except MigrationError:
                results.append((migration, MigrationStatus.FAILED))
                break  # stop on first failure
            else:
                results.append((migration, status))

        return results
