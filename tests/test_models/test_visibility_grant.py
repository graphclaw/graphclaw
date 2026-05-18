# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_models.test_visibility_grant — Unit tests for VisibilityGrantNode.

Description
-----------
Tests for ``VisibilityGrantNode``, ``generate_grant_id``, ``validate_grant_id``,
``GRANT_ID_PATTERN``, ``VisibilityScope``, ``EdgeType.GRANTS_ACCESS_TO``, and
the ``BaseNode.version`` field as defined in the Phase 3 model layer.

Design Patterns
---------------
- Arrange/Act/Assert: Each test is self-contained and uses no mocks.
- Pydantic ValidationError: Used to assert rejection of invalid IDs.

Dependencies
------------
- pytest: Test runner.
- pydantic: ValidationError for invalid model construction.
- graphclaw.models.base: generate_grant_id, validate_grant_id, GRANT_ID_PATTERN.
- graphclaw.models.nodes: VisibilityGrantNode.
- graphclaw.models.enums: EdgeType, VisibilityScope.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from graphclaw.models.base import (
    GRANT_ID_PATTERN,
    generate_grant_id,
    generate_task_id,
    validate_grant_id,
)
from graphclaw.models.enums import EdgeType, TaskType, VisibilityScope
from graphclaw.models.nodes import TaskNode, VisibilityGrantNode

NOW = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# generate_grant_id / validate_grant_id / GRANT_ID_PATTERN
# ---------------------------------------------------------------------------


class TestGrantIdHelpers:
    def test_generate_grant_id_matches_pattern(self):
        gid = generate_grant_id()
        assert GRANT_ID_PATTERN.match(gid), f"Generated ID did not match pattern: {gid}"

    def test_generate_grant_id_starts_with_grant_prefix(self):
        gid = generate_grant_id()
        assert gid.startswith("GRANT-")

    def test_grant_id_pattern_matches_valid(self):
        assert GRANT_ID_PATTERN.match("GRANT-abc-123")
        assert GRANT_ID_PATTERN.match("GRANT-xyz")
        assert GRANT_ID_PATTERN.match("GRANT-abc123def456")

    def test_grant_id_pattern_rejects_tsk_prefix(self):
        assert not GRANT_ID_PATTERN.match("TSK-AB-0001-ATM")

    def test_grant_id_pattern_rejects_user_prefix(self):
        assert not GRANT_ID_PATTERN.match("USER-abc-123")

    def test_grant_id_pattern_rejects_empty_string(self):
        assert not GRANT_ID_PATTERN.match("")

    def test_validate_grant_id_returns_value_for_valid(self):
        result = validate_grant_id("GRANT-abc-123")
        assert result == "GRANT-abc-123"

    def test_validate_grant_id_raises_for_invalid(self):
        with pytest.raises(ValueError, match="grant"):
            validate_grant_id("TSK-xxx")

    def test_validate_grant_id_raises_for_wrong_prefix(self):
        with pytest.raises(ValueError):
            validate_grant_id("GRT-abc-123")


# ---------------------------------------------------------------------------
# VisibilityGrantNode construction
# ---------------------------------------------------------------------------


def _make_grant(**kwargs):
    defaults = dict(
        id=generate_grant_id(),
        grantor_user_id="USER-alice-001",
        granted_to_user_id="USER-bob-002",
        target_node_id=generate_task_id("AB", TaskType.ATOMIC),
        target_node_type="TaskNode",
        scope=VisibilityScope.VIEWER,
        reason="Shared by alice",
        created_at=NOW,
        updated_at=NOW,
    )
    defaults.update(kwargs)
    return VisibilityGrantNode(**defaults)


class TestVisibilityGrantNode:
    def test_creates_successfully_with_required_fields(self):
        node = _make_grant()
        assert node.grantor_user_id == "USER-alice-001"
        assert node.granted_to_user_id == "USER-bob-002"

    def test_version_field_defaults_to_zero(self):
        node = _make_grant()
        assert node.version == 0

    def test_revoked_at_defaults_to_none(self):
        node = _make_grant()
        assert node.revoked_at is None

    def test_revoked_by_defaults_to_none(self):
        node = _make_grant()
        assert node.revoked_by is None

    def test_scope_defaults_to_viewer(self):
        node = _make_grant()
        assert node.scope == VisibilityScope.VIEWER

    def test_explicit_scope_editor(self):
        node = _make_grant(scope=VisibilityScope.EDITOR)
        assert node.scope == VisibilityScope.EDITOR

    def test_explicit_scope_owner(self):
        node = _make_grant(scope=VisibilityScope.OWNER)
        assert node.scope == VisibilityScope.OWNER

    def test_invalid_grant_id_raises_validation_error(self):
        with pytest.raises((ValidationError, ValueError)):
            _make_grant(id="TSK-AB-0001-ATM")

    def test_revoked_at_none_is_active(self):
        """A grant with revoked_at=None is considered active by convention."""
        node = _make_grant(revoked_at=None)
        assert node.revoked_at is None

    def test_setting_revoked_at_records_revocation(self):
        node = _make_grant(revoked_at=NOW, revoked_by="USER-alice-001")
        assert node.revoked_at == NOW
        assert node.revoked_by == "USER-alice-001"

    def test_target_node_type_stored(self):
        node = _make_grant(target_node_type="GoalNode")
        assert node.target_node_type == "GoalNode"

    def test_granted_at_field_is_set(self):
        node = _make_grant()
        # granted_at has a default_factory so it is always set
        assert node.granted_at is not None


# ---------------------------------------------------------------------------
# VisibilityScope enum
# ---------------------------------------------------------------------------


class TestVisibilityScope:
    def test_has_viewer_member(self):
        assert VisibilityScope.VIEWER.value == "VIEWER"

    def test_has_editor_member(self):
        assert VisibilityScope.EDITOR.value == "EDITOR"

    def test_has_owner_member(self):
        assert VisibilityScope.OWNER.value == "OWNER"

    def test_three_members_total(self):
        assert len(list(VisibilityScope)) == 3


# ---------------------------------------------------------------------------
# EdgeType.GRANTS_ACCESS_TO
# ---------------------------------------------------------------------------


class TestEdgeTypeGrantsAccessTo:
    def test_grants_access_to_exists(self):
        assert EdgeType.GRANTS_ACCESS_TO is not None

    def test_grants_access_to_value(self):
        assert EdgeType.GRANTS_ACCESS_TO.value == "GRANTS_ACCESS_TO"


# ---------------------------------------------------------------------------
# BaseNode.version field on TaskNode
# ---------------------------------------------------------------------------


class TestBaseNodeVersionField:
    def test_task_node_version_defaults_to_zero(self):
        node = TaskNode(
            id=generate_task_id("AB", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Test",
            description="Test task",
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.version == 0

    def test_task_node_version_can_be_set_explicitly(self):
        node = TaskNode(
            id=generate_task_id("AB", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Test",
            description="Test task",
            created_at=NOW,
            updated_at=NOW,
            version=5,
        )
        assert node.version == 5
