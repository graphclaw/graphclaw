# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_models.test_org_models — Unit tests for Phase 2 org/workspace models.

Tests OrganizationNode, WorkspaceNode, OrgMember, OrgSettings, and the new
ID generator / validator helpers added in Phase 2.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graphclaw.models.base import (
    ORG_ID_PATTERN,
    WORKSPACE_ID_PATTERN,
    generate_org_id,
    generate_workspace_id,
    validate_org_id,
    validate_workspace_id,
)
from graphclaw.models.enums import (
    EdgeType,
    MembershipStatus,
    OrgRole,
    WorkspaceVisibility,
)
from graphclaw.models.nodes import (
    OrganizationNode,
    OrgMember,
    OrgSettings,
    WorkspaceNode,
)

# ---------------------------------------------------------------------------
# ID pattern / generator tests
# ---------------------------------------------------------------------------


class TestOrgIdPattern:
    def test_valid_org_id(self):
        assert ORG_ID_PATTERN.match("ORG-abc123")

    def test_valid_org_id_with_uuid(self):
        oid = generate_org_id()
        assert ORG_ID_PATTERN.match(oid)

    def test_invalid_org_id_wrong_prefix(self):
        assert not ORG_ID_PATTERN.match("USR-abc123")

    def test_validate_org_id_ok(self):
        oid = generate_org_id()
        assert validate_org_id(oid) == oid

    def test_validate_org_id_raises(self):
        with pytest.raises(ValueError, match="Invalid organization ID"):
            validate_org_id("BAD-id")


class TestWorkspaceIdPattern:
    def test_valid_workspace_id(self):
        assert WORKSPACE_ID_PATTERN.match("WS-abc123")

    def test_valid_workspace_id_with_generator(self):
        wid = generate_workspace_id()
        assert WORKSPACE_ID_PATTERN.match(wid)

    def test_invalid_workspace_id(self):
        assert not WORKSPACE_ID_PATTERN.match("WK-abc")

    def test_validate_workspace_id_ok(self):
        wid = generate_workspace_id()
        assert validate_workspace_id(wid) == wid

    def test_validate_workspace_id_raises(self):
        with pytest.raises(ValueError):
            validate_workspace_id("BADWS-123")


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestPhase2Enums:
    def test_org_role_values(self):
        assert OrgRole.OWNER == "OWNER"
        assert OrgRole.ADMIN == "ADMIN"
        assert OrgRole.MEMBER == "MEMBER"
        assert OrgRole.GUEST == "GUEST"

    def test_membership_status_values(self):
        assert MembershipStatus.ACTIVE == "ACTIVE"
        assert MembershipStatus.INVITED == "INVITED"
        assert MembershipStatus.SUSPENDED == "SUSPENDED"
        assert MembershipStatus.REMOVED == "REMOVED"

    def test_workspace_visibility_values(self):
        assert WorkspaceVisibility.PRIVATE == "PRIVATE"
        assert WorkspaceVisibility.INTERNAL == "INTERNAL"
        assert WorkspaceVisibility.PUBLIC == "PUBLIC"

    def test_new_edge_types_exist(self):
        assert EdgeType.MEMBER_OF == "MEMBER_OF"
        assert EdgeType.ADMIN_OF == "ADMIN_OF"
        assert EdgeType.BELONGS_TO_ORG == "BELONGS_TO_ORG"
        assert EdgeType.SCOPED_TO_WS == "SCOPED_TO_WS"


# ---------------------------------------------------------------------------
# OrgSettings tests
# ---------------------------------------------------------------------------


class TestOrgSettings:
    def test_defaults(self):
        s = OrgSettings()
        assert s.default_workspace_visibility == WorkspaceVisibility.INTERNAL
        assert s.allow_guest_members is False
        assert s.require_approval_for_tasks is False
        assert s.daily_briefing_hour_utc == 8

    def test_custom_settings(self):
        s = OrgSettings(
            default_workspace_visibility=WorkspaceVisibility.PUBLIC,
            allow_guest_members=True,
            daily_briefing_hour_utc=7,
        )
        assert s.default_workspace_visibility == WorkspaceVisibility.PUBLIC
        assert s.allow_guest_members is True
        assert s.daily_briefing_hour_utc == 7


# ---------------------------------------------------------------------------
# OrgMember tests
# ---------------------------------------------------------------------------


class TestOrgMember:
    def test_defaults(self):
        m = OrgMember(user_id="USER-abc")
        assert m.role == OrgRole.MEMBER
        assert m.status == MembershipStatus.ACTIVE
        assert m.joined_at is None

    def test_owner_member(self):
        now = datetime.now(timezone.utc)
        m = OrgMember(user_id="USER-abc", role=OrgRole.OWNER, joined_at=now)
        assert m.role == OrgRole.OWNER
        assert m.joined_at == now


# ---------------------------------------------------------------------------
# OrganizationNode tests
# ---------------------------------------------------------------------------


class TestOrganizationNode:
    def _make(self, **kwargs):
        now = datetime.now(timezone.utc)
        defaults = dict(
            id=generate_org_id(),
            name="Acme Corp",
            owner_id="USER-owner-uuid",
            created_at=now,
            updated_at=now,
        )
        defaults.update(kwargs)
        return OrganizationNode(**defaults)

    def test_valid_org_node(self):
        org = self._make()
        assert org.name == "Acme Corp"
        assert org.members == []
        assert isinstance(org.settings, OrgSettings)

    def test_invalid_id_raises(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError):
            OrganizationNode(
                id="BAD-id",
                name="X",
                owner_id="USER-x",
                created_at=now,
                updated_at=now,
            )

    def test_with_members(self):
        member = OrgMember(user_id="USER-member", role=OrgRole.ADMIN)
        org = self._make(members=[member])
        assert len(org.members) == 1
        assert org.members[0].role == OrgRole.ADMIN

    def test_with_domain(self):
        org = self._make(domain="acme.com")
        assert org.domain == "acme.com"


# ---------------------------------------------------------------------------
# WorkspaceNode tests
# ---------------------------------------------------------------------------


class TestWorkspaceNode:
    def _make(self, **kwargs):
        now = datetime.now(timezone.utc)
        defaults = dict(
            id=generate_workspace_id(),
            org_id=generate_org_id(),
            name="Engineering",
            created_at=now,
            updated_at=now,
        )
        defaults.update(kwargs)
        return WorkspaceNode(**defaults)

    def test_valid_workspace_node(self):
        ws = self._make()
        assert ws.name == "Engineering"
        assert ws.visibility == WorkspaceVisibility.INTERNAL
        assert ws.member_ids == []
        assert ws.is_default is False

    def test_invalid_id_raises(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError):
            WorkspaceNode(
                id="BAD-id",
                org_id=generate_org_id(),
                name="X",
                created_at=now,
                updated_at=now,
            )

    def test_private_workspace(self):
        ws = self._make(visibility=WorkspaceVisibility.PRIVATE)
        assert ws.visibility == WorkspaceVisibility.PRIVATE

    def test_default_workspace_flag(self):
        ws = self._make(is_default=True, task_prefix="ENG")
        assert ws.is_default is True
        assert ws.task_prefix == "ENG"
