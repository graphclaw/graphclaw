# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_models.test_org_directory_visibility — FR-GRAPH-006 acceptance tests.

Verifies:
  AC1: OrgSettings.directory_visibility defaults to OPEN.
  AC2: All four OrgDirectoryVisibility values are valid.
  AC3: OrganizationNode serialises directory_visibility correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graphclaw.models.enums import OrgDirectoryVisibility
from graphclaw.models.nodes import OrganizationNode, OrgSettings

_NOW = datetime.now(timezone.utc)


class TestOrgDirectoryVisibility:
    def test_enum_values(self) -> None:
        assert OrgDirectoryVisibility.OPEN.value == "open"
        assert OrgDirectoryVisibility.NAME_ONLY.value == "name-only"
        assert OrgDirectoryVisibility.CONSENT_REQUIRED.value == "consent-required"
        assert OrgDirectoryVisibility.INVITATION_ONLY.value == "invitation-only"

    def test_org_settings_default(self) -> None:
        settings = OrgSettings()
        assert settings.directory_visibility == OrgDirectoryVisibility.OPEN

    def test_org_settings_override(self) -> None:
        settings = OrgSettings(directory_visibility=OrgDirectoryVisibility.CONSENT_REQUIRED)
        assert settings.directory_visibility == OrgDirectoryVisibility.CONSENT_REQUIRED

    def test_org_settings_round_trip(self) -> None:
        settings = OrgSettings(directory_visibility=OrgDirectoryVisibility.NAME_ONLY)
        d = settings.model_dump(mode="json")
        assert d["directory_visibility"] == "name-only"
        restored = OrgSettings(**d)
        assert restored.directory_visibility == OrgDirectoryVisibility.NAME_ONLY


class TestOrganizationNodeWithDirectoryVisibility:
    def test_default_directory_visibility(self) -> None:
        org = OrganizationNode(
            id="ORG-test",
            name="TestOrg",
            owner_id="USER-owner",
            created_at=_NOW,
            updated_at=_NOW,
        )
        assert org.settings.directory_visibility == OrgDirectoryVisibility.OPEN

    def test_custom_directory_visibility(self) -> None:
        settings = OrgSettings(directory_visibility=OrgDirectoryVisibility.INVITATION_ONLY)
        org = OrganizationNode(
            id="ORG-private",
            name="PrivateOrg",
            owner_id="USER-owner",
            settings=settings,
            created_at=_NOW,
            updated_at=_NOW,
        )
        assert org.settings.directory_visibility == OrgDirectoryVisibility.INVITATION_ONLY
        d = org.model_dump(mode="json")
        assert d["settings"]["directory_visibility"] == "invitation-only"
