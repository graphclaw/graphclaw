# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_auth.test_principals — FR-DEL-001 unit acceptance tests.

Verifies Principal enum, DSN resolution, AgeGraphStore principal tracking,
S3StorageClient principal tracking, and the no-delete feature flag.
No live DB required — all tests use mocks or env-var manipulation only.
"""

from __future__ import annotations

import pytest


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

    def test_admin_dsn_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from graphclaw.auth.principals import Principal, resolve_principal_dsn

        monkeypatch.setenv("ADMIN_PRINCIPAL_DSN", "postgresql://admin_user/db")
        monkeypatch.setenv("DATABASE_URL", "postgresql://fallback/db")

        dsn = resolve_principal_dsn(Principal.ADMIN)
        assert dsn == "postgresql://admin_user/db"


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

    def test_migration_principal_name(self) -> None:
        from unittest.mock import MagicMock

        from graphclaw.db.age.repository import AgeGraphStore

        mock_pool = MagicMock()
        store = AgeGraphStore(pool=mock_pool, principal_name="migration_principal")
        assert store.principal_name == "migration_principal"

    def test_principal_name_base_class_default(self) -> None:
        """GraphStore ABC default principal_name returns 'unknown'."""

        # We can't instantiate the ABC directly, but we can test via AgeGraphStore
        from unittest.mock import MagicMock

        from graphclaw.db.age.repository import AgeGraphStore

        store = AgeGraphStore(pool=MagicMock())
        # AgeGraphStore overrides principal_name; it does not return 'unknown'
        assert store.principal_name != "unknown"


class TestS3StorageClientPrincipalTracking:
    """S3StorageClient stores principal_name and exposes it."""

    def test_default_principal_name(self) -> None:
        from graphclaw.infra.storage import S3StorageClient

        client = S3StorageClient(bucket="test-bucket")
        assert client._principal_name == "agent_principal"

    def test_explicit_agent_principal(self) -> None:
        from graphclaw.infra.storage import S3StorageClient

        client = S3StorageClient(bucket="test-bucket", principal_name="agent_principal")
        assert client._principal_name == "agent_principal"

    def test_explicit_admin_principal(self) -> None:
        from graphclaw.infra.storage import S3StorageClient

        client = S3StorageClient(bucket="test-bucket", principal_name="admin_principal")
        assert client._principal_name == "admin_principal"


class TestNoDeleteFeatureFlag:
    """Config correctly exposes the no_delete_enforcement flag."""

    def test_feature_flag_default_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GRAPHCLAW_NO_DELETE_ENFORCEMENT", raising=False)
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

    def test_feature_flag_false_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRAPHCLAW_NO_DELETE_ENFORCEMENT", "false")
        from graphclaw.config import AppConfig

        cfg = AppConfig()
        assert cfg.no_delete_enforcement is False
