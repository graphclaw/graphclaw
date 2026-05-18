# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_db.test_lifecycle_field_protection — FR-DEL-002 acceptance tests.

Verifies that AgeGraphStore.update_node() raises InsufficientPrivilegeError
when agent_principal attempts to update lifecycle fields.

Also verifies that:
 - admin_principal is allowed to update lifecycle fields.
 - Non-lifecycle field updates from agent_principal proceed normally.
 - BaseNode lifecycle fields are present with correct defaults.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Lifecycle fields that agent_principal must never update directly.
_LIFECYCLE_FIELDS = [
    "archived_at",
    "archived_by",
    "archive_reason",
    "purge_after",
    "purge_cancelled_at",
    "legal_hold",
    "hold_reason",
    "hold_set_by",
    "hold_set_at",
    "link_status",
]


class TestAgentPrincipalLifecycleFieldProtection:
    """agent_principal cannot update any lifecycle field (AC1)."""

    async def _make_store(self, principal: str) -> object:
        from graphclaw.db.age.repository import AgeGraphStore

        return AgeGraphStore(pool=MagicMock(), principal_name=principal)

    @pytest.mark.parametrize("field", _LIFECYCLE_FIELDS)
    async def test_lifecycle_field_blocked_for_agent(self, field: str) -> None:
        """update_node raises InsufficientPrivilegeError for each lifecycle field."""
        from graphclaw.db.base import InsufficientPrivilegeError

        store = await self._make_store("agent_principal")

        with pytest.raises(InsufficientPrivilegeError) as exc_info:
            await store.update_node("TSK-AB-0001-ATM", {field: "anything"})

        assert "agent_principal" in str(exc_info.value)
        assert field in str(exc_info.value)

    async def test_multiple_lifecycle_fields_blocked(self) -> None:
        """update_node raises when updates dict contains multiple lifecycle fields."""
        from graphclaw.db.base import InsufficientPrivilegeError

        store = await self._make_store("agent_principal")

        with pytest.raises(InsufficientPrivilegeError):
            await store.update_node(
                "TSK-AB-0001-ATM",
                {"archived_at": "2026-01-01", "purge_after": "2026-01-02"},
            )

    async def test_mixed_safe_and_lifecycle_field_blocked(self) -> None:
        """update_node raises even when lifecycle field is mixed with safe fields."""
        from graphclaw.db.base import InsufficientPrivilegeError

        store = await self._make_store("agent_principal")

        with pytest.raises(InsufficientPrivilegeError):
            await store.update_node(
                "TSK-AB-0001-ATM",
                {"title": "new title", "archived_at": "2026-01-01"},
            )


class TestAgentPrincipalNonLifecycleAllowed:
    """agent_principal can update safe fields (title, state, description, etc.)."""

    async def test_safe_field_update_passes_validation(self) -> None:
        """update_node does not raise for non-lifecycle fields.

        The actual DB call is mocked — this test only verifies that the
        application-layer guard does NOT fire for safe fields.
        """
        from graphclaw.db.age.repository import AgeGraphStore

        store = AgeGraphStore(pool=MagicMock(), principal_name="agent_principal")

        # Patch the internal DB execution so the test doesn't need a live DB.
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=AsyncMock(fetchone=AsyncMock(return_value=None)))

        class _MockCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *args):
                pass

        with patch("graphclaw.db.age.repository.get_connection", return_value=_MockCtx()):
            result = await store.update_node("TSK-AB-0001-ATM", {"title": "safe update"})
        # Returns None or {} depending on mock — the key assertion is no exception raised.


class TestAdminPrincipalLifecycleFieldAllowed:
    """admin_principal is not blocked — it may update lifecycle fields."""

    async def test_lifecycle_field_allowed_for_admin(self) -> None:
        """admin_principal does not hit the application-layer guard."""
        from graphclaw.db.age.repository import AgeGraphStore

        store = AgeGraphStore(pool=MagicMock(), principal_name="admin_principal")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=AsyncMock(fetchone=AsyncMock(return_value=None)))

        class _MockCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *args):
                pass

        with patch("graphclaw.db.age.repository.get_connection", return_value=_MockCtx()):
            # Should not raise InsufficientPrivilegeError.
            await store.update_node("TSK-AB-0001-ATM", {"archived_at": "2026-01-01"})


class TestBaseNodeLifecycleFields:
    """BaseNode exposes lifecycle fields with correct defaults."""

    def test_lifecycle_fields_present(self) -> None:
        from graphclaw.models.base import BaseNode

        fields = BaseNode.model_fields
        for field in _LIFECYCLE_FIELDS:
            assert field in fields, f"BaseNode missing lifecycle field: {field}"

    def test_lifecycle_fields_default_none(self) -> None:
        """All nullable lifecycle fields default to None/False."""
        from graphclaw.models.base import BaseNode, utcnow

        now = utcnow()
        node = BaseNode(id="TEST-1", created_at=now, updated_at=now)

        assert node.archived_at is None
        assert node.archived_by is None
        assert node.archive_reason is None
        assert node.purge_after is None
        assert node.purge_cancelled_at is None
        assert node.legal_hold is False
        assert node.hold_reason is None
        assert node.hold_set_by is None
        assert node.hold_set_at is None
        assert node.link_status is None

    def test_migration_0009_exists(self) -> None:
        from graphclaw.migrations.catalogue import MIGRATIONS

        versions = [m.version for m in MIGRATIONS]
        assert "0009" in versions

    def test_migration_0009_has_trigger(self) -> None:
        from graphclaw.migrations.catalogue import MIGRATIONS

        m = next(m for m in MIGRATIONS if m.version == "0009")
        assert "prevent_lifecycle_field_update" in m.sql_up
