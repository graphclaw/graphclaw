# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_auth.test_no_delete_probes — FR-DEL-001 / NFR-005 probe tests.

Unit tests verifying the startup_assert_no_delete probe behaviour:
 - When DELETE raises InsufficientPrivilege → probe passes (expected path).
 - When DELETE succeeds → probe calls SystemExit (enforcement violation).
 - When probe table is absent (RuntimeError) → probe warns and continues.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestStartupProbe:
    """startup_assert_no_delete raises SystemExit on successful DELETE."""

    async def test_probe_passes_on_insufficient_privilege(self) -> None:
        """When DELETE raises InsufficientPrivilege, probe logs success and returns."""
        import psycopg.errors

        from graphclaw.auth.principals import startup_assert_no_delete

        mock_conn = AsyncMock()
        # SAVEPOINT succeeds; DELETE raises InsufficientPrivilege; ROLLBACK TO succeeds.
        mock_conn.execute = AsyncMock(
            side_effect=[
                None,  # SAVEPOINT
                psycopg.errors.InsufficientPrivilege("not allowed"),  # DELETE
                None,  # ROLLBACK TO SAVEPOINT
            ]
        )

        mock_pool = MagicMock()

        class _MockCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *args):
                pass

        with patch("graphclaw.db.age.connection.get_connection", return_value=_MockCtx()):
            # Should complete without raising.
            await startup_assert_no_delete(mock_pool)

    async def test_probe_exits_when_delete_succeeds(self) -> None:
        """When DELETE succeeds, probe calls SystemExit — agent must not start."""
        from graphclaw.auth.principals import startup_assert_no_delete

        mock_conn = AsyncMock()
        # SAVEPOINT succeeds; DELETE succeeds (wrong!) → probe fires SystemExit.
        mock_conn.execute = AsyncMock(
            side_effect=[
                None,  # SAVEPOINT
                None,  # DELETE succeeded — fatal
                None,  # ROLLBACK TO SAVEPOINT
            ]
        )

        mock_pool = MagicMock()

        class _MockCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *args):
                pass

        with patch("graphclaw.db.age.connection.get_connection", return_value=_MockCtx()):
            with pytest.raises(SystemExit) as exc_info:
                await startup_assert_no_delete(mock_pool)

        assert "FATAL" in str(exc_info.value)

    async def test_probe_warns_on_missing_table(self) -> None:
        """When probe table does not exist (DB error), warning is emitted but process continues."""
        import psycopg

        from graphclaw.auth.principals import startup_assert_no_delete

        mock_pool = MagicMock()

        class _ErrorCtx:
            async def __aenter__(self):
                raise psycopg.OperationalError("connection refused")

            async def __aexit__(self, *args):
                pass

        with patch("graphclaw.db.age.connection.get_connection", return_value=_ErrorCtx()):
            # Should not raise — just warn.
            await startup_assert_no_delete(mock_pool)


class TestMigration0008:
    """Migration 0008 is present in the catalogue with correct version."""

    def test_migration_0008_exists(self) -> None:
        from graphclaw.migrations.catalogue import MIGRATIONS

        versions = [m.version for m in MIGRATIONS]
        assert "0008" in versions

    def test_migration_0008_name(self) -> None:
        from graphclaw.migrations.catalogue import MIGRATIONS

        m = next(m for m in MIGRATIONS if m.version == "0008")
        assert m.name == "wave0_principal_probe"

    def test_migration_0008_creates_probe_table(self) -> None:
        from graphclaw.migrations.catalogue import MIGRATIONS

        m = next(m for m in MIGRATIONS if m.version == "0008")
        assert "_principal_probe" in m.sql_up

    def test_migrations_ordered(self) -> None:
        """Catalogue is in strictly ascending version order."""
        from graphclaw.migrations.catalogue import MIGRATIONS

        versions = [m.version for m in MIGRATIONS]
        assert versions == sorted(versions), "Migration catalogue must be in ascending order"
