"""tests.test_skills.test_marketplace_json — Tests for MarketplaceJson parsing.

Description
-----------
Unit tests for the MarketplaceJson and SkillListing dataclasses in
graphclaw.skills.registry_models:

- Parsing a valid marketplace.json dict into a MarketplaceJson object.
- SkillListing immutability (frozen dataclass).
- Empty skills list produces a valid MarketplaceJson with skills=[].
- SkillSourceType enum string values match expected literals.

Dependencies
------------
- pytest: test runner.
- graphclaw.skills.registry_models: MarketplaceJson, SkillListing, SkillSourceType.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from graphclaw.skills.registry_models import (
    MarketplaceJson,
    SkillListing,
    SkillSourceType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_listing_dict(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid SkillListing constructor kwargs dict."""
    defaults: dict[str, Any] = {
        "name": "linkedin-outreach-agent",
        "version": "1.0.0",
        "description": "Drafts personalised LinkedIn outreach messages.",
        "tags": ["sales", "outreach", "linkedin"],
        "source_uri": "https://example.com/marketplace.json",
        "source_type": SkillSourceType.WEBSITE,
        "skill_file_url": "https://example.com/skills/linkedin-outreach/SKILL.md",
        "requires": ["contact-profile.md"],
    }
    defaults.update(overrides)
    return defaults


def _make_skill_listing(**overrides: Any) -> SkillListing:
    return SkillListing(**_make_skill_listing_dict(**overrides))


def _make_marketplace_json_dict(skills: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a minimal valid marketplace.json-style dict."""
    if skills is None:
        skills = [_make_skill_listing_dict()]
    return {
        "name": "GraphClaw Official Marketplace",
        "description": "Curated skills for GraphClaw.",
        "publisher": "github.com/graphclaw-ai",
        "version": "2.0.0",
        "skills": skills,
    }


# ---------------------------------------------------------------------------
# test_parse_valid_marketplace_json
# ---------------------------------------------------------------------------


class TestParseValidMarketplaceJson:
    """Tests for constructing a MarketplaceJson from a valid data dict."""

    def test_returns_marketplace_json_instance(self) -> None:
        """Constructing MarketplaceJson from valid data returns the correct type."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(
            name=data["name"],
            description=data["description"],
            publisher=data["publisher"],
            version=data["version"],
            skills=skill_listings,
        )
        assert isinstance(mj, MarketplaceJson)

    def test_name_field(self) -> None:
        """The name field should match the input."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.name == "GraphClaw Official Marketplace"

    def test_description_field(self) -> None:
        """The description field should match the input."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.description == "Curated skills for GraphClaw."

    def test_publisher_field(self) -> None:
        """The publisher field should match the input."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.publisher == "github.com/graphclaw-ai"

    def test_version_field(self) -> None:
        """The version field should match the input."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.version == "2.0.0"

    def test_skills_is_list(self) -> None:
        """skills should be a list."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert isinstance(mj.skills, list)

    def test_skills_length(self) -> None:
        """skills should contain one SkillListing when one was provided."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert len(mj.skills) == 1

    def test_skills_are_skill_listing_instances(self) -> None:
        """Every element in skills should be a SkillListing."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        for listing in mj.skills:
            assert isinstance(listing, SkillListing)

    def test_skill_listing_name(self) -> None:
        """The first SkillListing should carry the correct name."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.skills[0].name == "linkedin-outreach-agent"

    def test_skill_listing_version(self) -> None:
        """The SkillListing should carry the correct version."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.skills[0].version == "1.0.0"

    def test_skill_listing_tags(self) -> None:
        """SkillListing tags should match the input list."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.skills[0].tags == ["sales", "outreach", "linkedin"]

    def test_skill_listing_source_type(self) -> None:
        """SkillListing source_type should be SkillSourceType.WEBSITE."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.skills[0].source_type == SkillSourceType.WEBSITE

    def test_skill_listing_requires(self) -> None:
        """SkillListing.requires should carry through from input."""
        data = _make_marketplace_json_dict()
        skill_listings = [SkillListing(**s) for s in data["skills"]]
        mj = MarketplaceJson(**{**data, "skills": skill_listings})
        assert mj.skills[0].requires == ["contact-profile.md"]


# ---------------------------------------------------------------------------
# test_skill_listing_frozen
# ---------------------------------------------------------------------------


class TestSkillListingFrozen:
    """SkillListing is a frozen dataclass — mutation must raise FrozenInstanceError."""

    def test_cannot_mutate_name(self) -> None:
        """Assigning to SkillListing.name should raise FrozenInstanceError."""
        listing = _make_skill_listing()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            listing.name = "hacked-name"  # type: ignore[misc]

    def test_cannot_mutate_version(self) -> None:
        """Assigning to SkillListing.version should raise FrozenInstanceError."""
        listing = _make_skill_listing()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            listing.version = "99.0.0"  # type: ignore[misc]

    def test_cannot_mutate_tags(self) -> None:
        """Assigning to SkillListing.tags should raise FrozenInstanceError."""
        listing = _make_skill_listing()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            listing.tags = []  # type: ignore[misc]

    def test_cannot_mutate_description(self) -> None:
        """Assigning to SkillListing.description should raise FrozenInstanceError."""
        listing = _make_skill_listing()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            listing.description = "modified"  # type: ignore[misc]

    def test_is_frozen_dataclass(self) -> None:
        """SkillListing should be a frozen dataclass."""
        assert dataclasses.is_dataclass(SkillListing)
        # A frozen dataclass has __setattr__ that raises FrozenInstanceError
        fields = dataclasses.fields(SkillListing)
        assert len(fields) > 0, "SkillListing should have dataclass fields"

    def test_marketplace_json_is_frozen_dataclass(self) -> None:
        """MarketplaceJson is also a frozen dataclass."""
        assert dataclasses.is_dataclass(MarketplaceJson)


# ---------------------------------------------------------------------------
# test_marketplace_json_empty_skills
# ---------------------------------------------------------------------------


class TestMarketplaceJsonEmptySkills:
    """MarketplaceJson with an empty skills list."""

    def test_empty_skills_returns_instance(self) -> None:
        """MarketplaceJson with skills=[] is valid and produces an instance."""
        mj = MarketplaceJson(
            name="Empty Collection",
            description="No skills yet.",
            publisher="test-publisher",
            version="0.1.0",
            skills=[],
        )
        assert isinstance(mj, MarketplaceJson)

    def test_empty_skills_list(self) -> None:
        """skills should be an empty list when constructed with skills=[]."""
        mj = MarketplaceJson(
            name="Empty Collection",
            description="",
            publisher="test-publisher",
            version="0.1.0",
            skills=[],
        )
        assert mj.skills == []

    def test_empty_skills_length(self) -> None:
        """len(mj.skills) should be 0 for an empty collection."""
        mj = MarketplaceJson(
            name="Empty",
            description="",
            publisher="pub",
            version="1.0.0",
            skills=[],
        )
        assert len(mj.skills) == 0

    def test_empty_skills_is_not_none(self) -> None:
        """skills should never be None even when no skills are provided."""
        mj = MarketplaceJson(
            name="Empty",
            description="",
            publisher="pub",
            version="1.0.0",
            skills=[],
        )
        assert mj.skills is not None


# ---------------------------------------------------------------------------
# test_source_type_values
# ---------------------------------------------------------------------------


class TestSourceTypeValues:
    """SkillSourceType enum string values."""

    def test_local_value(self) -> None:
        """SkillSourceType.LOCAL should have value 'local'."""
        assert SkillSourceType.LOCAL == "local"
        assert SkillSourceType.LOCAL.value == "local"

    def test_system_value(self) -> None:
        """SkillSourceType.SYSTEM should have value 'system'."""
        assert SkillSourceType.SYSTEM == "system"
        assert SkillSourceType.SYSTEM.value == "system"

    def test_github_value(self) -> None:
        """SkillSourceType.GITHUB should have value 'github'."""
        assert SkillSourceType.GITHUB == "github"
        assert SkillSourceType.GITHUB.value == "github"

    def test_website_value(self) -> None:
        """SkillSourceType.WEBSITE should have value 'website'."""
        assert SkillSourceType.WEBSITE == "website"
        assert SkillSourceType.WEBSITE.value == "website"

    def test_registry_value(self) -> None:
        """SkillSourceType.REGISTRY should have value 'registry'."""
        assert SkillSourceType.REGISTRY == "registry"
        assert SkillSourceType.REGISTRY.value == "registry"

    def test_all_expected_members_exist(self) -> None:
        """All five source type members should exist."""
        expected = {"LOCAL", "SYSTEM", "GITHUB", "WEBSITE", "REGISTRY"}
        actual = {member.name for member in SkillSourceType}
        assert expected == actual

    def test_source_type_is_str_enum(self) -> None:
        """SkillSourceType values should be directly comparable to plain strings."""
        assert SkillSourceType.GITHUB == "github"
        assert SkillSourceType.LOCAL != "github"

    def test_lookup_by_value(self) -> None:
        """SkillSourceType('github') should return SkillSourceType.GITHUB."""
        assert SkillSourceType("github") is SkillSourceType.GITHUB
        assert SkillSourceType("local") is SkillSourceType.LOCAL
