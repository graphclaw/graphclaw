# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_observability.test_startup_audit — FR-DEL-008 acceptance tests.

Verifies:
  AC1: No lifecycle rules → AuditResult(ok=True, violations=[]).
  AC2: Lifecycle rule on users/ prefix → violation detected.
  AC3: Lifecycle rule on non-user prefix (tmp/) → no violation.
  AC4: Storage client without list_lifecycle_rules → no-op (ok=True).
  AC5: startup_assert_no_lifecycle_rules raises SystemExit on violation.
  AC6: startup_assert_no_lifecycle_rules passes when no violations.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.observability.startup_audit import (
    audit_lifecycle_rules,
    startup_assert_no_lifecycle_rules,
)


def _storage_with_rules(rules: list[dict]) -> object:
    """Build a mock storage client with list_lifecycle_rules returning *rules*."""
    client = MagicMock()
    client.list_lifecycle_rules = AsyncMock(return_value=rules)
    return client


def _storage_without_method() -> object:
    """Build a mock storage client that has no list_lifecycle_rules method."""
    return MagicMock(spec=[])  # no attributes


class TestAuditLifecycleRules:
    async def test_no_rules_returns_ok(self) -> None:
        """AC1: Empty rule set → ok."""
        client = _storage_with_rules([])
        result = await audit_lifecycle_rules(client)
        assert result.ok is True
        assert result.violations == []

    async def test_forbidden_prefix_users_detected(self) -> None:
        """AC2: users/ prefix rule → violation."""
        rules = [
            {"id": "expire-users", "Filter": {"Prefix": "users/"}, "Status": "Enabled"},
        ]
        client = _storage_with_rules(rules)
        result = await audit_lifecycle_rules(client)
        assert result.ok is False
        assert len(result.violations) == 1
        assert "users/" in result.violations[0]

    async def test_forbidden_prefix_tasks_detected(self) -> None:
        """tasks/ prefix rule → violation."""
        rules = [{"Filter": {"Prefix": "tasks/"}, "Status": "Enabled"}]
        client = _storage_with_rules(rules)
        result = await audit_lifecycle_rules(client)
        assert result.ok is False

    async def test_safe_prefix_no_violation(self) -> None:
        """AC3: tmp/ prefix rule → no violation."""
        rules = [{"Filter": {"Prefix": "tmp/"}, "Status": "Enabled"}]
        client = _storage_with_rules(rules)
        result = await audit_lifecycle_rules(client)
        assert result.ok is True
        assert result.violations == []

    async def test_logs_prefix_no_violation(self) -> None:
        """logs/ prefix is safe."""
        rules = [{"Filter": {"Prefix": "logs/"}, "Status": "Enabled"}]
        client = _storage_with_rules(rules)
        result = await audit_lifecycle_rules(client)
        assert result.ok is True

    async def test_no_list_method_is_noop(self) -> None:
        """AC4: client without list_lifecycle_rules → ok without calling anything."""
        client = _storage_without_method()
        result = await audit_lifecycle_rules(client)
        assert result.ok is True
        assert result.violations == []

    async def test_multiple_violations_reported(self) -> None:
        """Multiple forbidden rules → all violations returned."""
        rules = [
            {"Filter": {"Prefix": "users/"}, "Status": "Enabled"},
            {"Filter": {"Prefix": "tasks/"}, "Status": "Enabled"},
        ]
        client = _storage_with_rules(rules)
        result = await audit_lifecycle_rules(client)
        assert result.ok is False
        assert len(result.violations) == 2

    async def test_list_rules_exception_treated_as_ok(self) -> None:
        """Exception from list_lifecycle_rules is caught and treated as no-violation."""
        client = MagicMock()
        client.list_lifecycle_rules = AsyncMock(side_effect=RuntimeError("connection error"))
        result = await audit_lifecycle_rules(client)
        assert result.ok is True

    async def test_flat_prefix_format(self) -> None:
        """Some MinIO SDK versions return rules with a flat Prefix key."""
        rules = [{"Prefix": "users/", "Status": "Enabled"}]
        client = _storage_with_rules(rules)
        result = await audit_lifecycle_rules(client)
        assert result.ok is False
        assert "users/" in result.violations[0]


class TestStartupAssertNoLifecycleRules:
    async def test_passes_when_no_violations(self) -> None:
        """AC6: no violations → no sys.exit."""
        client = _storage_with_rules([])
        await startup_assert_no_lifecycle_rules(client)  # must not raise

    async def test_exits_on_violation(self) -> None:
        """AC5: lifecycle rule on users/ → SystemExit(1)."""
        rules = [{"Filter": {"Prefix": "users/"}, "Status": "Enabled"}]
        client = _storage_with_rules(rules)
        with pytest.raises(SystemExit) as exc_info:
            await startup_assert_no_lifecycle_rules(client)
        assert exc_info.value.code == 1
