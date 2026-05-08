# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.skills.registry_models — Data models for the Skill Registry v2.

Description
-----------
Defines the data transfer objects used by ``SkillRegistryService`` to
represent skill sources, available (listed) skills, and installed skills.

All types are plain dataclasses with no business logic — they are the
vocabulary shared between the registry service, the API layer, and the
CLI.

Design Patterns
---------------
- Value Objects: ``SkillListing`` and ``MarketplaceJson`` are frozen
  dataclasses that cannot be mutated after construction.
- Mutable State: ``SkillSource`` and ``InstalledSkill`` are regular
  (non-frozen) dataclasses because their fields are updated over time
  (e.g. ``last_fetched_at``, ``usage_count``).

Public API
----------
- SkillSourceType: Enum of supported skill source backends.
- SkillSource: Registered source entry (GitHub, marketplace URL, local).
- SkillListing: An available skill discovered from a source (not yet installed).
- InstalledSkill: A skill that has been downloaded and cached locally.
- MarketplaceJson: Parsed marketplace.json payload from a remote source.

Dependencies
------------
- dataclasses: dataclass, field.
- datetime: datetime type for timestamp fields.
- enum: Enum base class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SkillSourceType(str, Enum):
    """Supported skill source backends."""

    LOCAL = "local"  # src/graphclaw/skills/definitions/ (built-in)
    SYSTEM = "system"  # platform-provided skills (read-only)
    GITHUB = "github"  # github.com/<owner>/<repo>[/path]
    WEBSITE = "website"  # URL returning marketplace.json
    REGISTRY = "registry"  # future: graphclaw.ai/registry


@dataclass
class SkillSource:
    """A registered source from which skills can be discovered and installed.

    Attributes
    ----------
    source_type:
        Backend type that determines how ``SkillRegistryService`` fetches
        the skill index.
    uri:
        Canonical identifier for the source — a GitHub URL, marketplace
        JSON URL, or local filesystem path.
    name:
        Optional human-readable label for the source.
    auth_secret_ref:
        Key name in the ``SecretsClient`` that holds the auth credential
        (e.g. GitHub personal access token for private repos).
    last_fetched_at:
        timezone.utc datetime of the most recent successful index fetch.
    fetch_interval_hours:
        How often (in hours) the source should be re-fetched.
    """

    source_type: SkillSourceType
    uri: str
    name: str = ""
    auth_secret_ref: str | None = None
    last_fetched_at: datetime | None = None
    fetch_interval_hours: int = 24


@dataclass(frozen=True)
class SkillListing:
    """A skill available from a source — not yet installed.

    Attributes
    ----------
    name:
        Unique skill identifier within its source (e.g.
        ``"linkedin-outreach-agent"``).
    version:
        Semantic version string declared in the skill's SKILL.md or
        marketplace.json.
    description:
        Short human-readable description.
    tags:
        Classification tags used for search and filtering.
    source_uri:
        URI of the ``SkillSource`` this listing came from.
    source_type:
        Backend type of the originating source.
    skill_file_url:
        Direct URL (or filesystem path) to the SKILL.md file for this
        skill.
    requires:
        Names of input files the skill requires to run (e.g.
        ``["contact-profile.md"]``).
    """

    name: str
    version: str
    description: str
    tags: list[str]
    source_uri: str
    source_type: SkillSourceType
    skill_file_url: str
    requires: list[str] = field(default_factory=list)


@dataclass
class InstalledSkill:
    """A skill that has been downloaded and cached in storage.

    Attributes
    ----------
    skill_id:
        Unique identifier for this installation (e.g. ``"skill-abc123"``).
    name:
        Skill name as declared in its SKILL.md.
    version:
        Version at time of installation.
    description:
        Short description copied from the SkillListing.
    skill_file_path:
        Path in ``StorageClient`` where the cached SKILL.md is stored.
    source_uri:
        URI of the source this skill was installed from.
    source_type:
        Backend type of the originating source.
    installed_at:
        timezone.utc datetime when the skill was installed.
    last_used_at:
        timezone.utc datetime of the most recent execution, or ``None`` if never
        executed.
    usage_count:
        Total number of times this skill has been executed.
    avg_quality_score:
        Exponential moving average (alpha=0.2) of quality scores recorded
        via ``SkillRegistryService.record_usage``.
    tags:
        Classification tags copied from the SkillListing.
    """

    skill_id: str
    name: str
    version: str
    description: str
    skill_file_path: str
    source_uri: str
    source_type: SkillSourceType
    installed_at: datetime
    last_used_at: datetime | None = None
    usage_count: int = 0
    avg_quality_score: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketplaceJson:
    """Parsed marketplace.json payload from a remote skill source.

    Attributes
    ----------
    name:
        Display name of the skill collection.
    description:
        Short description of what the collection provides.
    publisher:
        Publisher identifier (e.g. ``"github.com/my-org"``).
    version:
        Version of the marketplace index itself.
    skills:
        List of ``SkillListing`` objects parsed from the ``"skills"`` array
        in the marketplace.json file.
    """

    name: str
    description: str
    publisher: str
    version: str
    skills: list[SkillListing]


__all__ = [
    "SkillSourceType",
    "SkillSource",
    "SkillListing",
    "InstalledSkill",
    "MarketplaceJson",
]
