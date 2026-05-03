"""tests.test_db.test_repo_acl_boundary — FR-AL-001 acceptance tests.

Verifies:
  AC1: Without caller_context, repo methods are no-ops when flag is off.
  AC2: Without caller_context, repo methods raise ACLContextMissingError when flag is on.
  AC3: With a valid CallerContext, methods proceed normally.
  AC4: CallerContext Pydantic model validates required fields.
  AC5: system_caller_context() factory returns valid context.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.cross_tenant.acl import CallerContext, require_caller_context, system_caller_context
from graphclaw.db.base import ACLContextMissingError


class TestCallerContextModel:
    """AC4: CallerContext model validation."""

    def test_valid_context(self) -> None:
        ctx = CallerContext(user_id="USER-123", org_id="ORG-1")
        assert ctx.user_id == "USER-123"
        assert ctx.org_id == "ORG-1"
        assert ctx.principal == "agent_principal"
        assert ctx.session_id is None

    def test_missing_user_id(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CallerContext(org_id="ORG-1")  # type: ignore[call-arg]

    def test_missing_org_id(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CallerContext(user_id="USER-123")  # type: ignore[call-arg]

    def test_custom_principal(self) -> None:
        ctx = CallerContext(user_id="u", org_id="o", principal="admin_principal")
        assert ctx.principal == "admin_principal"


class TestSystemCallerContext:
    """AC5: system_caller_context factory."""

    def test_defaults(self) -> None:
        ctx = system_caller_context()
        assert ctx.user_id == "system"
        assert ctx.org_id == "system"
        assert ctx.principal == "admin_principal"

    def test_custom_principal(self) -> None:
        ctx = system_caller_context("migration_principal")
        assert ctx.principal == "migration_principal"


class TestRequireCallerContext:
    """AC1/AC2: enforcement behaviour based on feature flag."""

    def test_none_context_flag_off_is_noop(self) -> None:
        """AC1: flag off → None context is silently accepted."""
        with patch("graphclaw.config.AppConfig") as MockCfg:
            MockCfg.return_value.no_delete_enforcement = False
            require_caller_context(None)  # must not raise

    def test_none_context_flag_on_raises(self) -> None:
        """AC2: flag on → None context raises ACLContextMissingError."""
        with patch("graphclaw.config.AppConfig") as MockCfg:
            MockCfg.return_value.no_delete_enforcement = True
            with pytest.raises(ACLContextMissingError):
                require_caller_context(None)

    def test_valid_context_flag_on_succeeds(self) -> None:
        """AC3: valid context never raises regardless of flag."""
        ctx = CallerContext(user_id="u", org_id="o")
        # Flag state doesn't matter when context is present.
        require_caller_context(ctx)  # must not raise


class TestRepoCallerContextIntegration:
    """AC3: repo methods accept caller_context without error."""

    def _make_store(self) -> object:
        from graphclaw.db.age.repository import AgeGraphStore

        pool = MagicMock()
        return AgeGraphStore(pool=pool, principal_name="agent_principal")

    async def test_get_node_accepts_context(self) -> None:
        store = self._make_store()
        ctx = CallerContext(user_id="u", org_id="o")

        mock_conn = AsyncMock()
        mock_result = AsyncMock()
        mock_result.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        class _Ctx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                pass

        with patch("graphclaw.db.age.repository.get_connection", return_value=_Ctx()):
            result = await store.get_node("NODE-1", caller_context=ctx)

        assert result is None

    async def test_get_node_no_context_flag_off(self) -> None:
        """Without context and flag off, get_node still works (no-op)."""
        store = self._make_store()

        mock_conn = AsyncMock()
        mock_result = AsyncMock()
        mock_result.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        class _Ctx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                pass

        with patch("graphclaw.db.age.repository.get_connection", return_value=_Ctx()):
                with patch("graphclaw.config.AppConfig") as MockCfg:
                    MockCfg.return_value.no_delete_enforcement = False
                    result = await store.get_node("NODE-1")  # no caller_context

        assert result is None

    async def test_get_node_no_context_flag_on_raises(self) -> None:
        """With flag on and no context, get_node raises ACLContextMissingError."""
        store = self._make_store()

        with patch("graphclaw.config.AppConfig") as MockCfg:
            MockCfg.return_value.no_delete_enforcement = True
            with pytest.raises(ACLContextMissingError):
                await store.get_node("NODE-1")  # no caller_context, flag on
