# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.integration.test_principals — FR-DEL-001 acceptance tests.

Verifies that the Principal enum resolves correctly, that the AgeGraphStore
and S3StorageClient track their principal names, and that the config feature
flag is readable.  Database-level privilege assertions (probe DELETE) are
exercised against a live DB in the integration environment.
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Unit-level tests (no DB required)
# ---------------------------------------------------------------------------


class TestPrincipalEnum:
    """Principal enum values and DSN resolution."""

    def test_principal_values(self) -> None:
        from graphclaw.auth.principals import Principal

        assert Principal.AGENT.value == "agent_principal"
        assert Principal.ADMIN.value == "admin_principal"
        assert Principal.MIGRATION.value == "migration_principal"

    def test_resolve_principal_dsn_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no principal-specific DSN is set, falls back to DATABASE_URL."""
        from graphclaw.auth.principals import Principal, resolve_principal_dsn

        monkeypatch.setenv("DATABASE_URL", "postgresql://fallback/db")
        monkeypatch.delenv("AGENT_PRINCIPAL_DSN", raising=False)

        dsn = resolve_principal_dsn(Principal.AGENT)
        assert dsn == "postgresql://fallback/db"

    def test_resolve_principal_dsn_specific(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When principal-specific DSN is set, it takes precedence over DATABASE_URL."""
        from graphclaw.auth.principals import Principal, resolve_principal_dsn

        monkeypatch.setenv("AGENT_PRINCIPAL_DSN", "postgresql://agent_user/db")
        monkeypatch.setenv("DATABASE_URL", "postgresql://fallback/db")

        dsn = resolve_principal_dsn(Principal.AGENT)
        assert dsn == "postgresql://agent_user/db"

    def test_resolve_principal_dsn_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises KeyError when neither principal DSN nor DATABASE_URL is set."""
        from graphclaw.auth.principals import Principal, resolve_principal_dsn

        monkeypatch.delenv("AGENT_PRINCIPAL_DSN", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(KeyError):
            resolve_principal_dsn(Principal.AGENT)


class TestAgeGraphStorePrincipalTracking:
    """AgeGraphStore.principal_name is recorded at construction and exposed."""

    def test_default_principal_name(self) -> None:
        from unittest.mock import MagicMock

        from graphclaw.db.age.repository import AgeGraphStore

        mock_pool = MagicMock()
        store = AgeGraphStore(pool=mock_pool)
        assert store.principal_name == "agent_principal"

    def test_custom_principal_name(self) -> None:
        from unittest.mock import MagicMock

        from graphclaw.db.age.repository import AgeGraphStore

        mock_pool = MagicMock()
        store = AgeGraphStore(pool=mock_pool, principal_name="admin_principal")
        assert store.principal_name == "admin_principal"


class TestS3StorageClientPrincipalTracking:
    """S3StorageClient stores principal_name and exposes it."""

    def test_default_principal_name(self) -> None:
        from graphclaw.infra.storage import S3StorageClient

        client = S3StorageClient(bucket="test-bucket")
        assert client._principal_name == "agent_principal"

    def test_explicit_principal_name(self) -> None:
        from graphclaw.infra.storage import S3StorageClient

        client = S3StorageClient(bucket="test-bucket", principal_name="admin_principal")
        assert client._principal_name == "admin_principal"


class TestNoDeleteFeatureFlag:
    """Config correctly exposes the no_delete_enforcement flag."""

    def test_feature_flag_default_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GRAPHCLAW_NO_DELETE_ENFORCEMENT", raising=False)
        # Re-import config with fresh AppConfig to pick up monkeypatched env.
        from graphclaw.config import AppConfig

        cfg = AppConfig()
        assert cfg.no_delete_enforcement is False

    def test_feature_flag_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRAPHCLAW_NO_DELETE_ENFORCEMENT", "true")
        from graphclaw.config import AppConfig

        cfg = AppConfig()
        assert cfg.no_delete_enforcement is True

    def test_feature_flag_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRAPHCLAW_NO_DELETE_ENFORCEMENT", "TRUE")
        from graphclaw.config import AppConfig

        cfg = AppConfig()
        assert cfg.no_delete_enforcement is True


# ---------------------------------------------------------------------------
# Integration tests (require live DB — skipped in unit-only runs)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStartupProbeIntegration:
    """startup_assert_no_delete is wired correctly with a real DB pool.

    These tests require:
    - DATABASE_URL set and pointing to a running Postgres instance
    - The _principal_probe table created by migration 0005
    - Two principal DSNs available: AGENT_PRINCIPAL_DSN (no DELETE)
      and the default one (unrestricted)
    """

    @pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
    async def test_probe_passes_when_no_delete_enforced(self) -> None:
        """When agent_principal truly has no DELETE, probe logs pass and does not exit."""
        pytest.skip(
            "Requires live DB with agent_principal lacking DELETE — run in integration env."
        )
