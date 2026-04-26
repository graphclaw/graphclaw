"""graphclaw.api.intelligence — Intelligence Hub endpoints.

Description
-----------
Provides REST endpoints for the Intelligence Hub feature set:

1. **Agent profile** — Read/write the orchestrating agent's persona, goals,
   and working style stored as a Markdown document in object storage.

2. **Agent memory** — Full CRUD over the three memory tiers (working context,
   episodic session summaries, semantic topic files).  Includes a
   ``/compact`` operation that summarises oversized context to manage
   memory growth.

3. **Skill authoring** — Create, read, update, fork, validate, and import
   user-authored SKILL.md definitions.  Authored skills are stored under the
   user's own ``skills/authored/`` prefix in object storage and can later be
   installed via the regular SkillRegistryService.

All endpoints require a valid Bearer access token.  Paths are resolved through
``StoragePaths`` to guarantee multi-tenant isolation at the ``{user_id}/``
prefix level.

Endpoints
---------
Agent profile:
- GET    /app/v1/intelligence/agents/{agent_id}/profile
- PUT    /app/v1/intelligence/agents/{agent_id}/profile

Agent memory:
- GET    /app/v1/intelligence/agents/{agent_id}/memory/working
- PUT    /app/v1/intelligence/agents/{agent_id}/memory/working
- POST   /app/v1/intelligence/agents/{agent_id}/memory/compact
- GET    /app/v1/intelligence/agents/{agent_id}/memory/episodic
- GET    /app/v1/intelligence/agents/{agent_id}/memory/episodic/{entry_name}
- DELETE /app/v1/intelligence/agents/{agent_id}/memory/episodic/{entry_name}
- GET    /app/v1/intelligence/agents/{agent_id}/memory/semantic
- GET    /app/v1/intelligence/agents/{agent_id}/memory/semantic/{topic}
- PUT    /app/v1/intelligence/agents/{agent_id}/memory/semantic/{topic}
- DELETE /app/v1/intelligence/agents/{agent_id}/memory/semantic/{topic}

Skill authoring:
- GET    /app/v1/intelligence/skills/authored
- POST   /app/v1/intelligence/skills/authored
- GET    /app/v1/intelligence/skills/authored/{skill_id}
- PUT    /app/v1/intelligence/skills/authored/{skill_id}
- DELETE /app/v1/intelligence/skills/authored/{skill_id}
- POST   /app/v1/intelligence/skills/authored/{skill_id}/fork
- POST   /app/v1/intelligence/skills/validate
- POST   /app/v1/intelligence/skills/import

Design Patterns
---------------
- StoragePaths: All path construction goes through the central path registry.
- FileNotFoundError → 404: Storage reads that raise ``FileNotFoundError`` are
  translated to HTTP 404 so callers get a meaningful error.
- Compact: The ``/compact`` operation replaces the working context with a
  user-supplied summary and appends the original to episodic memory for
  archival before discarding.

Public API
----------
- router: ``APIRouter`` for /intelligence routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, StorageClientDep.
- graphclaw.infra.storage: StoragePaths.
- graphclaw.skills.parser: SkillParser (for validate endpoint).
- fastapi: APIRouter, HTTPException, UploadFile, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.infra.storage import StoragePaths
from graphclaw.models.base import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


def _utcnow_str() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_str() -> str:
    return utcnow().strftime("%Y-%m-%d")


def _normalize_skill_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def _extract_skill_metadata(skill_id: str, content: str) -> tuple[str, str, str]:
    """Parse SKILL.md metadata, falling back to safe defaults on errors."""
    try:
        from graphclaw.skills.parser import SkillParser

        parsed = SkillParser().parse(content)
        name = (parsed.name or skill_id).strip() or skill_id
        description = (parsed.description or "").strip()
        version = (parsed.version or "0.1.0").strip() or "0.1.0"
        return name, description, version
    except Exception:  # noqa: BLE001
        return skill_id, "", "0.1.0"


def _authored_skill_response(skill_id: str, path: str, content: str) -> AuthoredSkillResponse:
    name, description, version = _extract_skill_metadata(skill_id, content)
    return AuthoredSkillResponse(
        skill_id=skill_id,
        name=name,
        description=description,
        version=version,
        content=content,
        path=path,
        updated_at=_utcnow_str(),
    )


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class AgentProfileResponse(BaseModel):
    """Response body for agent profile endpoints."""

    agent_id: str
    content: str
    updated_at: str | None = None


class AgentProfileUpdateRequest(BaseModel):
    """Request body for PUT .../profile."""

    content: str


class MemoryContentResponse(BaseModel):
    """Generic memory content response."""

    agent_id: str
    memory_type: str
    key: str
    content: str


class MemoryListEntry(BaseModel):
    """One entry in a list of memory objects."""

    key: str
    path: str


class MemoryListResponse(BaseModel):
    """Response for listing memory objects."""

    agent_id: str
    memory_type: str
    entries: list[MemoryListEntry]


class MemoryWriteRequest(BaseModel):
    """Request body for writing a memory document."""

    content: str


class CompactRequest(BaseModel):
    """Request body for POST .../memory/compact.

    The caller supplies the compacted summary.  The original working context
    is archived to episodic memory before being replaced.
    """

    summary: str
    session_label: str | None = None


class CompactResponse(BaseModel):
    """Response confirming a compact operation."""

    agent_id: str
    archived_as: str
    working_context_replaced: bool = True


class AuthoredSkillEntry(BaseModel):
    """Metadata for a user-authored skill."""

    skill_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    content: str | None = None
    path: str


class AuthoredSkillResponse(BaseModel):
    """Response for a single authored skill."""

    skill_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    content: str
    path: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AuthoredSkillCreateRequest(BaseModel):
    """Request body for creating a new authored skill."""

    skill_id: str | None = None
    name: str | None = None
    description: str | None = None
    version: str | None = None
    content: str


class AuthoredSkillUpdateRequest(BaseModel):
    """Request body for updating an authored skill."""

    content: str
    name: str | None = None
    description: str | None = None
    version: str | None = None


class SkillValidateRequest(BaseModel):
    """Request body for the validate endpoint."""

    content: str


class SkillValidateResponse(BaseModel):
    """Response from the validate endpoint."""

    valid: bool
    errors: list[str] = []
    parsed: dict[str, Any] = {}


class ForkResponse(BaseModel):
    """Response for a fork operation."""

    original_skill_id: str
    forked_skill_id: str
    skill_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    content: str
    path: str


class AuthoredSkillForkRequest(BaseModel):
    """Optional request body for forking a skill."""

    name: str | None = None


# ---------------------------------------------------------------------------
# Agent profile endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/profile",
    response_model=AgentProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get agent profile",
    description=(
        "Return the Markdown profile document for the specified agent.  "
        "Returns a default empty profile when none has been written yet."
    ),
)
async def get_agent_profile(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AgentProfileResponse:
    """Read agent profile.md from object storage."""
    path = StoragePaths.agent_profile(user_id, agent_id)
    try:
        raw = await storage_client.read(path)
        content = raw.decode()
    except FileNotFoundError:
        content = f"# Agent: {agent_id}\n\nNo profile defined yet.\n"
    return AgentProfileResponse(agent_id=agent_id, content=content)


@router.put(
    "/agents/{agent_id}/profile",
    response_model=AgentProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update agent profile",
    description=(
        "Write or replace the Markdown profile document for the specified agent.  "
        "The profile defines the agent's persona, goals, and working style."
    ),
)
async def update_agent_profile(
    agent_id: str,
    body: AgentProfileUpdateRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AgentProfileResponse:
    """Write agent profile.md to object storage."""
    path = StoragePaths.agent_profile(user_id, agent_id)
    await storage_client.write(path, body.content.encode(), content_type="text/markdown")
    logger.info("intelligence: profile updated agent_id=%s user_id=%s", agent_id, user_id)
    return AgentProfileResponse(agent_id=agent_id, content=body.content, updated_at=_utcnow_str())


# ---------------------------------------------------------------------------
# Agent memory — working context
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/memory/working",
    response_model=MemoryContentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get working context",
    description=(
        "Return the current-session working context for the agent.  "
        "This is a scratchpad overwritten by the agent loop on each cycle."
    ),
)
async def get_working_context(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> MemoryContentResponse:
    """Read working/context.md from object storage."""
    path = StoragePaths.agent_memory_working(user_id, agent_id)
    try:
        raw = await storage_client.read(path)
        content = raw.decode()
    except FileNotFoundError:
        content = ""
    return MemoryContentResponse(
        agent_id=agent_id, memory_type="working", key="context.md", content=content
    )


@router.put(
    "/agents/{agent_id}/memory/working",
    response_model=MemoryContentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update working context",
    description=(
        "Write or replace the working context document.  Use this to manually "
        "inject context for the agent or to correct erroneous content."
    ),
)
async def update_working_context(
    agent_id: str,
    body: MemoryWriteRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> MemoryContentResponse:
    """Write working/context.md to object storage."""
    path = StoragePaths.agent_memory_working(user_id, agent_id)
    await storage_client.write(path, body.content.encode(), content_type="text/markdown")
    logger.info("intelligence: working context updated agent_id=%s user_id=%s", agent_id, user_id)
    return MemoryContentResponse(
        agent_id=agent_id, memory_type="working", key="context.md", content=body.content
    )


# ---------------------------------------------------------------------------
# Agent memory — compact
# ---------------------------------------------------------------------------


@router.post(
    "/agents/{agent_id}/memory/compact",
    response_model=CompactResponse,
    status_code=status.HTTP_200_OK,
    summary="Compact working context",
    description=(
        "Archive the current working context to episodic memory and replace it "
        "with a compact summary supplied by the caller.  Use this when the working "
        "context has grown too large or stale.  The original is preserved in "
        "episodic memory before being discarded."
    ),
)
async def compact_working_context(
    agent_id: str,
    body: CompactRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> CompactResponse:
    """Archive working context and replace with compact summary."""
    working_path = StoragePaths.agent_memory_working(user_id, agent_id)

    # Archive current working context to episodic memory
    today = _today_str()
    session_label = body.session_label or uuid.uuid4().hex[:8]
    entry_name = f"{today}-compact-{session_label}.md"
    episodic_path = StoragePaths.agent_memory_episodic_entry(user_id, agent_id, entry_name)
    working_archive_path = StoragePaths.agent_memory_working_archive_entry(
        user_id, agent_id, entry_name
    )

    try:
        original = await storage_client.read(working_path)
        archive_content = (
            f"# Compacted Context — {today}\n\n"
            f"*Session: {session_label}*\n\n"
            f"## Original Working Context\n\n" + original.decode()
        )
        await storage_client.write(
            episodic_path, archive_content.encode(), content_type="text/markdown"
        )
        await storage_client.write(
            working_archive_path,
            archive_content.encode(),
            content_type="text/markdown",
        )
    except FileNotFoundError:
        pass  # Nothing to archive — working context was empty

    # Replace working context with the supplied summary
    await storage_client.write(working_path, body.summary.encode(), content_type="text/markdown")
    logger.info(
        "intelligence: compact done agent_id=%s archived_as=%s user_id=%s",
        agent_id,
        entry_name,
        user_id,
    )
    return CompactResponse(
        agent_id=agent_id,
        archived_as=entry_name,
        working_context_replaced=True,
    )


# ---------------------------------------------------------------------------
# Agent memory — episodic
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/memory/episodic",
    response_model=MemoryListResponse,
    status_code=status.HTTP_200_OK,
    summary="List episodic memory entries",
    description="Return all episodic memory entries (session summaries) for the agent.",
)
async def list_episodic_memory(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> MemoryListResponse:
    """List episodic memory entries from object storage."""
    prefix = StoragePaths.agent_memory_episodic_prefix(user_id, agent_id)
    all_keys = await storage_client.list_objects(prefix)
    entries = [MemoryListEntry(key=k.split("/")[-1], path=k) for k in all_keys if k.endswith(".md")]
    return MemoryListResponse(agent_id=agent_id, memory_type="episodic", entries=entries)


@router.get(
    "/agents/{agent_id}/memory/episodic/{entry_name}",
    response_model=MemoryContentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get episodic memory entry",
    description="Return the content of one episodic memory entry.",
)
async def get_episodic_entry(
    agent_id: str,
    entry_name: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> MemoryContentResponse:
    """Read one episodic memory entry."""
    path = StoragePaths.agent_memory_episodic_entry(user_id, agent_id, entry_name)
    try:
        raw = await storage_client.read(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Episodic entry '{entry_name}' not found",
        )
    return MemoryContentResponse(
        agent_id=agent_id, memory_type="episodic", key=entry_name, content=raw.decode()
    )


@router.delete(
    "/agents/{agent_id}/memory/episodic/{entry_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete episodic memory entry",
    description="Permanently delete one episodic memory entry.",
)
async def delete_episodic_entry(
    agent_id: str,
    entry_name: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> None:
    """Delete one episodic memory entry from object storage."""
    path = StoragePaths.agent_memory_episodic_entry(user_id, agent_id, entry_name)
    await storage_client.delete(path)
    logger.info(
        "intelligence: episodic entry deleted agent_id=%s entry=%s user_id=%s",
        agent_id,
        entry_name,
        user_id,
    )


# ---------------------------------------------------------------------------
# Agent memory — semantic
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/memory/semantic",
    response_model=MemoryListResponse,
    status_code=status.HTTP_200_OK,
    summary="List semantic memory topics",
    description="Return all semantic memory topic names for the agent.",
)
async def list_semantic_memory(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> MemoryListResponse:
    """List semantic memory topics from object storage."""
    prefix = StoragePaths.agent_memory_semantic_prefix(user_id, agent_id)
    all_keys = await storage_client.list_objects(prefix)
    entries = [
        MemoryListEntry(
            key=k.split("/")[-1].removesuffix(".md"),
            path=k,
        )
        for k in all_keys
        if k.endswith(".md")
    ]
    return MemoryListResponse(agent_id=agent_id, memory_type="semantic", entries=entries)


@router.get(
    "/agents/{agent_id}/memory/semantic/{topic}",
    response_model=MemoryContentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get semantic memory topic",
    description="Return the content of one semantic memory topic.",
)
async def get_semantic_topic(
    agent_id: str,
    topic: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> MemoryContentResponse:
    """Read one semantic memory topic file."""
    path = StoragePaths.agent_memory_semantic_topic(user_id, agent_id, topic)
    try:
        raw = await storage_client.read(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Semantic topic '{topic}' not found",
        )
    return MemoryContentResponse(
        agent_id=agent_id, memory_type="semantic", key=topic, content=raw.decode()
    )


@router.put(
    "/agents/{agent_id}/memory/semantic/{topic}",
    response_model=MemoryContentResponse,
    status_code=status.HTTP_200_OK,
    summary="Write semantic memory topic",
    description=(
        "Create or replace a semantic memory topic.  Topics should use "
        "lowercase slugs (e.g. ``users``, ``projects``, ``patterns``)."
    ),
)
async def write_semantic_topic(
    agent_id: str,
    topic: str,
    body: MemoryWriteRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> MemoryContentResponse:
    """Write one semantic memory topic file."""
    path = StoragePaths.agent_memory_semantic_topic(user_id, agent_id, topic)
    await storage_client.write(path, body.content.encode(), content_type="text/markdown")
    logger.info(
        "intelligence: semantic topic written agent_id=%s topic=%s user_id=%s",
        agent_id,
        topic,
        user_id,
    )
    return MemoryContentResponse(
        agent_id=agent_id, memory_type="semantic", key=topic, content=body.content
    )


@router.delete(
    "/agents/{agent_id}/memory/semantic/{topic}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete semantic memory topic",
    description="Permanently delete a semantic memory topic.",
)
async def delete_semantic_topic(
    agent_id: str,
    topic: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> None:
    """Delete one semantic memory topic file."""
    path = StoragePaths.agent_memory_semantic_topic(user_id, agent_id, topic)
    await storage_client.delete(path)
    logger.info(
        "intelligence: semantic topic deleted agent_id=%s topic=%s user_id=%s",
        agent_id,
        topic,
        user_id,
    )


# ---------------------------------------------------------------------------
# Skill authoring endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/skills/authored",
    response_model=list[AuthoredSkillEntry],
    status_code=status.HTTP_200_OK,
    summary="List authored skills",
    description="Return all skills authored or forked by the authenticated user.",
)
async def list_authored_skills(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> list[AuthoredSkillEntry]:
    """List all user-authored SKILL.md files in object storage."""
    prefix = StoragePaths.skill_authored_prefix(user_id)
    all_keys = await storage_client.list_objects(prefix)
    results: list[AuthoredSkillEntry] = []
    for key in all_keys:
        if key.endswith("/SKILL.md"):
            # Extract skill_id from path: {user_id}/skills/authored/{skill_id}/SKILL.md
            parts = key.split("/")
            if len(parts) >= 4:
                skill_id = parts[-2]
                try:
                    raw = await storage_client.read(key)
                    content = raw.decode()
                except FileNotFoundError:
                    continue
                name, description, version = _extract_skill_metadata(skill_id, content)
                results.append(
                    AuthoredSkillEntry(
                        skill_id=skill_id,
                        name=name,
                        version=version,
                        description=description,
                        content=content,
                        path=key,
                        updated_at=_utcnow_str(),
                    )
                )
    results.sort(key=lambda item: item.skill_id)
    return results


@router.post(
    "/skills/authored",
    response_model=AuthoredSkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create authored skill",
    description=(
        "Create a new user-authored skill.  Supply the full SKILL.md content "
        "in the request body.  A ``skill_id`` is auto-generated if not provided."
    ),
)
async def create_authored_skill(
    body: AuthoredSkillCreateRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AuthoredSkillResponse:
    """Create a new authored skill in object storage."""
    requested_id = body.skill_id
    if not requested_id and body.name:
        requested_id = _normalize_skill_id(body.name)

    skill_id = requested_id or f"authored-{uuid.uuid4().hex[:10]}"
    path = StoragePaths.skill_authored(user_id, skill_id)

    if await storage_client.exists(path):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Authored skill '{skill_id}' already exists",
        )

    await storage_client.write(path, body.content.encode(), content_type="text/markdown")
    logger.info("intelligence: authored skill created skill_id=%s user_id=%s", skill_id, user_id)
    return _authored_skill_response(skill_id=skill_id, path=path, content=body.content)


@router.get(
    "/skills/authored/{skill_id}",
    response_model=AuthoredSkillResponse,
    status_code=status.HTTP_200_OK,
    summary="Get authored skill",
    description="Return the SKILL.md content for one user-authored skill.",
)
async def get_authored_skill(
    skill_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AuthoredSkillResponse:
    """Read an authored SKILL.md from object storage."""
    path = StoragePaths.skill_authored(user_id, skill_id)
    try:
        raw = await storage_client.read(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Authored skill '{skill_id}' not found",
        )
    content = raw.decode()
    return _authored_skill_response(skill_id=skill_id, path=path, content=content)


@router.put(
    "/skills/authored/{skill_id}",
    response_model=AuthoredSkillResponse,
    status_code=status.HTTP_200_OK,
    summary="Update authored skill",
    description="Replace the SKILL.md content for an existing user-authored skill.",
)
async def update_authored_skill(
    skill_id: str,
    body: AuthoredSkillUpdateRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AuthoredSkillResponse:
    """Overwrite an authored SKILL.md in object storage."""
    path = StoragePaths.skill_authored(user_id, skill_id)
    await storage_client.write(path, body.content.encode(), content_type="text/markdown")
    logger.info("intelligence: authored skill updated skill_id=%s user_id=%s", skill_id, user_id)
    return _authored_skill_response(skill_id=skill_id, path=path, content=body.content)


@router.delete(
    "/skills/authored/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete authored skill",
    description="Permanently delete a user-authored skill.",
)
async def delete_authored_skill(
    skill_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> None:
    """Delete an authored SKILL.md from object storage."""
    path = StoragePaths.skill_authored(user_id, skill_id)
    await storage_client.delete(path)
    logger.info("intelligence: authored skill deleted skill_id=%s user_id=%s", skill_id, user_id)


@router.post(
    "/skills/authored/{skill_id}/fork",
    response_model=ForkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Fork an authored skill",
    description=(
        "Create a copy of an existing authored skill under a new skill_id.  "
        "Use this to create a personal variant of a built-in or installed skill."
    ),
)
async def fork_authored_skill(
    skill_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    body: AuthoredSkillForkRequest | None = None,
) -> ForkResponse:
    """Copy an authored skill to a new fork ID."""
    source_path = StoragePaths.skill_authored(user_id, skill_id)
    try:
        raw = await storage_client.read(source_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Authored skill '{skill_id}' not found — cannot fork",
        )

    preferred_id = _normalize_skill_id((body.name if body else "") or "")
    fork_id = preferred_id or f"{skill_id}-fork-{uuid.uuid4().hex[:6]}"
    fork_path = StoragePaths.skill_authored(user_id, fork_id)
    if await storage_client.exists(fork_path):
        fork_id = f"{fork_id}-{uuid.uuid4().hex[:6]}"
        fork_path = StoragePaths.skill_authored(user_id, fork_id)

    await storage_client.write(fork_path, raw, content_type="text/markdown")
    logger.info(
        "intelligence: skill forked original=%s fork=%s user_id=%s",
        skill_id,
        fork_id,
        user_id,
    )
    content = raw.decode()
    name, description, version = _extract_skill_metadata(fork_id, content)
    return ForkResponse(
        original_skill_id=skill_id,
        forked_skill_id=fork_id,
        skill_id=fork_id,
        name=name,
        version=version,
        description=description,
        content=content,
        path=fork_path,
    )


@router.post(
    "/skills/validate",
    response_model=SkillValidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate SKILL.md content",
    description=(
        "Parse and validate a SKILL.md document without persisting it.  "
        "Returns the parsed fields on success or a list of errors on failure."
    ),
)
async def validate_skill_content(
    body: SkillValidateRequest,
    user_id: CurrentUserDep,
) -> SkillValidateResponse:
    """Parse SKILL.md content and return validation result."""
    from graphclaw.skills.parser import SkillParser  # local import avoids circular deps

    parser = SkillParser()
    try:
        defn = parser.parse(body.content)
        return SkillValidateResponse(
            valid=True,
            parsed={
                "name": defn.name,
                "description": defn.description,
                "version": defn.version,
                "model": defn.model,
                "tags": defn.tags,
                "timeout_seconds": defn.timeout_seconds,
                "max_tokens": defn.max_tokens,
            },
        )
    except (ValueError, Exception) as exc:
        return SkillValidateResponse(valid=False, errors=[str(exc)])


@router.post(
    "/skills/import",
    response_model=AuthoredSkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import SKILL.md file",
    description=(
        "Upload a SKILL.md file and store it as an authored skill.  The "
        "``skill_id`` is derived from the ``name`` field in the SKILL.md "
        "frontmatter.  Returns HTTP 422 if the file is not a valid SKILL.md."
    ),
)
async def import_skill_file(
    file: UploadFile,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AuthoredSkillResponse:
    """Accept a SKILL.md upload and store it as an authored skill."""
    from graphclaw.skills.parser import SkillParser

    raw = await file.read()
    content = raw.decode()

    parser = SkillParser()
    try:
        defn = parser.parse(content)
    except (ValueError, Exception) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid SKILL.md: {exc}",
        )

    skill_id = defn.name
    path = StoragePaths.skill_authored(user_id, skill_id)

    await storage_client.write(path, raw, content_type="text/markdown")
    logger.info("intelligence: skill imported skill_id=%s user_id=%s", skill_id, user_id)
    return _authored_skill_response(skill_id=skill_id, path=path, content=content)
