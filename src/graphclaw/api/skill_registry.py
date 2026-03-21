"""graphclaw.api.skill_registry — Skill search and install endpoints.

Description
-----------
Provides REST endpoints for managing the user's installed skills and the
registered skill sources that supply them.

Endpoints
---------
- ``GET    /app/v1/skills``                      — List installed skills.
- ``GET    /app/v1/skills/search``               — Search available skills.
- ``POST   /app/v1/skills/install``              — Install a skill.
- ``DELETE /app/v1/skills/{skill_id}``           — Uninstall a skill.
- ``GET    /app/v1/skills/sources``              — List registered skill sources.
- ``POST   /app/v1/skills/sources``              — Add a new skill source.
- ``DELETE /app/v1/skills/sources/{source_uri}`` — Remove a skill source.

All endpoints require a valid Bearer access token.

Design Patterns
---------------
- Stub storage: Module-level dicts simulate persistence until the graph store
  integration is implemented.
- 404 on missing resource: Unknown skill or source IDs return HTTP 404.

Public API
----------
- router: ``APIRouter`` for /skills routes.

Dependencies
------------
- graphclaw.auth.middleware: require_auth.
- fastapi: APIRouter, Depends, HTTPException, Query, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.auth.middleware import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["app-api"])

# ── Stub in-memory storage ─────────────────────────────────────────────────────

# user_id -> list of installed skill dicts
_installed_skills: dict[str, list[dict[str, Any]]] = {}

# user_id -> list of source dicts
_skill_sources: dict[str, list[dict[str, Any]]] = {}


# ── Request / Response models ──────────────────────────────────────────────────


class SkillEntry(BaseModel):
    """An installed skill."""

    skill_id: str
    skill_name: str
    version: str = "0.1.0"
    source_uri: str | None = None
    tags: list[str] = []
    enabled: bool = True


class SkillInstallRequest(BaseModel):
    """Request body for POST /app/v1/skills/install."""

    skill_name: str
    source_uri: str


class SkillSource(BaseModel):
    """A registered skill source."""

    source_uri: str
    source_type: str = "git"
    name: str


class SkillSourceAddRequest(BaseModel):
    """Request body for POST /app/v1/skills/sources."""

    source_type: str
    uri: str
    name: str


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[SkillEntry],
    status_code=status.HTTP_200_OK,
    summary="List installed skills",
    description="Return all skills installed for the authenticated user.",
)
async def list_skills(
    user_id: str = Depends(require_auth),
) -> list[SkillEntry]:
    skills = _installed_skills.get(user_id, [])
    return [SkillEntry(**s) for s in skills]


@router.get(
    "/search",
    response_model=list[SkillEntry],
    status_code=status.HTTP_200_OK,
    summary="Search available skills",
    description="Search for skills available across registered sources by name or tags.",
)
async def search_skills(
    q: str = Query(default="", description="Search query string"),
    tags: list[str] = Query(default=[], description="Filter by tags"),
    user_id: str = Depends(require_auth),
) -> list[SkillEntry]:
    """Search available skills.

    Stub implementation — returns installed skills filtered by query string.
    Full source-registry search will be implemented in a future phase.
    """
    all_skills = _installed_skills.get(user_id, [])
    if q:
        all_skills = [s for s in all_skills if q.lower() in s.get("skill_name", "").lower()]
    if tags:
        all_skills = [s for s in all_skills if any(t in s.get("tags", []) for t in tags)]
    return [SkillEntry(**s) for s in all_skills]


@router.post(
    "/install",
    response_model=SkillEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Install a skill",
    description="Install a skill from a registered source URI.",
)
async def install_skill(
    body: SkillInstallRequest,
    user_id: str = Depends(require_auth),
) -> SkillEntry:
    skill_id = f"SKL-{uuid4().hex[:12]}"
    entry: dict[str, Any] = {
        "skill_id": skill_id,
        "skill_name": body.skill_name,
        "source_uri": body.source_uri,
        "tags": [],
        "enabled": True,
    }
    _installed_skills.setdefault(user_id, []).append(entry)
    logger.info("skills: installed '%s' for user_id=%s", body.skill_name, user_id)
    return SkillEntry(**entry)


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Uninstall a skill",
    description="Remove an installed skill by its skill_id.",
)
async def uninstall_skill(
    skill_id: str,
    user_id: str = Depends(require_auth),
) -> None:
    skills = _installed_skills.get(user_id, [])
    original_len = len(skills)
    skills[:] = [s for s in skills if s.get("skill_id") != skill_id]
    if len(skills) == original_len:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found",
        )
    logger.info("skills: uninstalled '%s' for user_id=%s", skill_id, user_id)


@router.get(
    "/sources",
    response_model=list[SkillSource],
    status_code=status.HTTP_200_OK,
    summary="List registered skill sources",
    description="Return all skill sources registered for the authenticated user.",
)
async def list_sources(
    user_id: str = Depends(require_auth),
) -> list[SkillSource]:
    sources = _skill_sources.get(user_id, [])
    return [SkillSource(**s) for s in sources]


@router.post(
    "/sources",
    response_model=SkillSource,
    status_code=status.HTTP_201_CREATED,
    summary="Add a skill source",
    description="Register a new skill source for the authenticated user.",
)
async def add_source(
    body: SkillSourceAddRequest,
    user_id: str = Depends(require_auth),
) -> SkillSource:
    entry: dict[str, Any] = {
        "source_uri": body.uri,
        "source_type": body.source_type,
        "name": body.name,
    }
    _skill_sources.setdefault(user_id, []).append(entry)
    logger.info("skills: added source '%s' for user_id=%s", body.uri, user_id)
    return SkillSource(**entry)


@router.delete(
    "/sources/{source_uri:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a skill source",
    description="Deregister a skill source by its URI.",
)
async def remove_source(
    source_uri: str,
    user_id: str = Depends(require_auth),
) -> None:
    sources = _skill_sources.get(user_id, [])
    original_len = len(sources)
    sources[:] = [s for s in sources if s.get("source_uri") != source_uri]
    if len(sources) == original_len:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill source '{source_uri}' not found",
        )
    logger.info("skills: removed source '%s' for user_id=%s", source_uri, user_id)
