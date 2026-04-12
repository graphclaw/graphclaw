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
- SkillRegistryService delegation: All persistence is handled by the real
  ``SkillRegistryService`` (storage-backed) rather than in-memory stubs.
  The service is injected per-request via ``SkillRegistryDep``.
- Mapping layer: ``InstalledSkill`` and ``SkillListing`` dataclass objects
  returned by the service are mapped to ``SkillEntry`` Pydantic responses.
- KeyError → 404: Service methods raise ``KeyError`` for missing resources;
  the API layer translates these to HTTP 404.

Public API
----------
- router: ``APIRouter`` for /skills routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, SkillRegistryDep.
- graphclaw.skills.registry_models: SkillSource (dataclass), SkillSourceType.
- fastapi: APIRouter, HTTPException, Query, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, SkillRegistryDep, StorageClientDep
from graphclaw.infra.storage import StoragePaths
from graphclaw.skills.registry_models import SkillSource, SkillSourceType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["app-api"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SkillEntry(BaseModel):
    """An installed skill or available listing."""

    skill_id: str
    skill_name: str
    version: str = "0.1.0"
    description: str = ""
    source_uri: str | None = None
    source_type: str = "local"
    tags: list[str] = []
    enabled: bool = True


class SkillInstallRequest(BaseModel):
    """Request body for POST /app/v1/skills/install."""

    skill_name: str
    source_uri: str
    version: str | None = None


class SkillSourceResponse(BaseModel):
    """A registered skill source."""

    source_uri: str
    source_type: str = "git"
    name: str
    last_fetched_at: str | None = None


class SkillSourceAddRequest(BaseModel):
    """Request body for POST /app/v1/skills/sources."""

    source_type: str
    uri: str
    name: str
    auth_secret_ref: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[SkillEntry],
    status_code=status.HTTP_200_OK,
    summary="List installed skills",
    description="Return all skills installed for the authenticated user.",
)
async def list_skills(
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
) -> list[SkillEntry]:
    """List all installed skills for the authenticated user."""
    installed = await skill_registry.list_installed(user_id)
    return [_installed_to_entry(sk) for sk in installed]


@router.get(
    "/search",
    response_model=list[SkillEntry],
    status_code=status.HTTP_200_OK,
    summary="Search available skills",
    description=(
        "Search for skills available across all registered sources (plus built-in "
        "LOCAL skills) by name, description, or tags."
    ),
)
async def search_skills(
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
    q: str = Query(default="", description="Search query string (name or description)"),
    tags: list[str] = Query(default=[], description="Filter by tags (all must match)"),
) -> list[SkillEntry]:
    """Search available skills across registered sources."""
    listings = await skill_registry.search(
        user_id,
        query=q,
        tags=tags if tags else None,
    )
    return [_listing_to_entry(li) for li in listings]


@router.post(
    "/install",
    response_model=SkillEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Install a skill",
    description="Install a skill from a registered source URI.",
)
async def install_skill(
    body: SkillInstallRequest,
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
) -> SkillEntry:
    """Install a skill for the authenticated user."""
    try:
        installed_skill = await skill_registry.install(
            user_id,
            skill_name=body.skill_name,
            source_uri=body.source_uri,
            version=body.version,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error("skills: install failed skill=%s source=%s: %s", body.skill_name, body.source_uri, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to install skill '{body.skill_name}': {exc}",
        )
    logger.info("skills: installed '%s' for user_id=%s", body.skill_name, user_id)
    return _installed_to_entry(installed_skill)


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Uninstall a skill",
    description="Remove an installed skill by its skill_id.",
)
async def uninstall_skill(
    skill_id: str,
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
) -> None:
    """Uninstall a skill for the authenticated user."""
    try:
        await skill_registry.uninstall(user_id, skill_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found",
        )
    logger.info("skills: uninstalled '%s' for user_id=%s", skill_id, user_id)


@router.get(
    "/sources",
    response_model=list[SkillSourceResponse],
    status_code=status.HTTP_200_OK,
    summary="List registered skill sources",
    description="Return all skill sources registered for the authenticated user.",
)
async def list_sources(
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
) -> list[SkillSourceResponse]:
    """List all skill sources registered for the authenticated user."""
    sources = await skill_registry.list_sources(user_id)
    return [_source_to_response(s) for s in sources]


@router.post(
    "/sources",
    response_model=SkillSourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a skill source",
    description=(
        "Register a new skill source.  Immediately fetches the source index to "
        "validate the URI is reachable."
    ),
)
async def add_source(
    body: SkillSourceAddRequest,
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
) -> SkillSourceResponse:
    """Register a new skill source for the authenticated user."""
    try:
        source_type = SkillSourceType(body.source_type)
    except ValueError:
        valid = [t.value for t in SkillSourceType]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid source_type '{body.source_type}'. Valid values: {valid}",
        )

    source = SkillSource(
        source_type=source_type,
        uri=body.uri,
        name=body.name,
        auth_secret_ref=body.auth_secret_ref,
    )
    try:
        await skill_registry.add_source(user_id, source)
    except Exception as exc:
        logger.error("skills: add_source failed uri=%s: %s", body.uri, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to add source '{body.uri}': {exc}",
        )
    logger.info("skills: added source '%s' for user_id=%s", body.uri, user_id)
    return _source_to_response(source)


@router.delete(
    "/sources/{source_uri:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a skill source",
    description="Deregister a skill source by its URI.  Also uninstalls all skills from that source.",
)
async def remove_source(
    source_uri: str,
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
) -> None:
    """Deregister a skill source and uninstall its skills."""
    sources = await skill_registry.list_sources(user_id)
    if not any(s.uri == source_uri for s in sources):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill source '{source_uri}' not found",
        )
    await skill_registry.remove_source(user_id, source_uri)
    logger.info("skills: removed source '%s' for user_id=%s", source_uri, user_id)


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _installed_to_entry(sk: Any) -> SkillEntry:
    """Convert an ``InstalledSkill`` dataclass to a ``SkillEntry`` response."""
    return SkillEntry(
        skill_id=sk.skill_id,
        skill_name=sk.name,
        version=sk.version,
        description=getattr(sk, "description", ""),
        source_uri=sk.source_uri,
        source_type=sk.source_type.value if hasattr(sk.source_type, "value") else str(sk.source_type),
        tags=list(sk.tags) if sk.tags else [],
        enabled=True,
    )


def _listing_to_entry(li: Any) -> SkillEntry:
    """Convert a ``SkillListing`` dataclass to a ``SkillEntry`` response."""
    return SkillEntry(
        skill_id=f"listing-{li.name}",
        skill_name=li.name,
        version=li.version,
        description=getattr(li, "description", ""),
        source_uri=li.source_uri,
        source_type=li.source_type.value if hasattr(li.source_type, "value") else str(li.source_type),
        tags=list(li.tags) if li.tags else [],
        enabled=True,
    )


def _source_to_response(src: Any) -> SkillSourceResponse:
    """Convert a ``SkillSource`` dataclass to a ``SkillSourceResponse``."""
    last_fetched: str | None = None
    if getattr(src, "last_fetched_at", None) is not None:
        last_fetched = str(src.last_fetched_at)
    return SkillSourceResponse(
        source_uri=src.uri,
        source_type=src.source_type.value if hasattr(src.source_type, "value") else str(src.source_type),
        name=src.name or src.uri,
        last_fetched_at=last_fetched,
    )


# ---------------------------------------------------------------------------
# Wave 5 — Feedback, Workers, Executions, Test
# ---------------------------------------------------------------------------

def _executions_path(user_id: str, skill_id: str) -> str:
    return StoragePaths.skill_executions(user_id, skill_id)


class SkillFeedbackRequest(BaseModel):
    """Request body for POST /skills/{id}/feedback."""

    rating: float  # 0.0 – 1.0
    comment: str | None = None


class SkillFeedbackResponse(BaseModel):
    """Response body confirming feedback was recorded."""

    skill_id: str
    recorded: bool = True


class WorkerStatusOut(BaseModel):
    """Status snapshot for a single skill worker."""

    worker_id: str
    state: str
    current_job_id: str | None = None
    last_heartbeat: datetime | None = None
    jobs_completed: int = 0
    jobs_failed: int = 0


class SkillExecutionOut(BaseModel):
    """A single skill execution record."""

    job_id: str
    skill_name: str
    task_id: str
    session_id: str
    status: str
    output: str = ""
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    tokens_used: int = 0
    cost_usd: float = 0.0


class SkillTestRequest(BaseModel):
    """Request body for POST /skills/{id}/test."""

    input_data: dict[str, Any] = {}
    task_id: str = "TSK-TEST-0000-TST"


class SkillTestResponse(BaseModel):
    """Response confirming a test job was submitted."""

    job_id: str
    skill_id: str
    status: str = "submitted"
    submitted_at: datetime


@router.post(
    "/{skill_id}/feedback",
    response_model=SkillFeedbackResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit skill feedback",
    description=(
        "Record a quality rating (0.0–1.0) for an installed skill.  Updates "
        "the EMA quality score and usage count via ``SkillRegistryService``."
    ),
)
async def submit_skill_feedback(
    skill_id: str,
    body: SkillFeedbackRequest,
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
) -> SkillFeedbackResponse:
    """Record user feedback (quality rating) for an installed skill."""
    try:
        await skill_registry.record_usage(
            user_id,
            skill_id,
            quality_score=body.rating,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found or not installed",
        )
    logger.debug("skills: feedback recorded skill_id=%s rating=%.2f", skill_id, body.rating)
    return SkillFeedbackResponse(skill_id=skill_id, recorded=True)


@router.get(
    "/workers",
    response_model=list[WorkerStatusOut],
    status_code=status.HTTP_200_OK,
    summary="List skill workers",
    description=(
        "Return the status of all skill worker threads in the pool.  Returns "
        "an empty list when no worker pool is initialised."
    ),
)
async def list_workers(
    user_id: CurrentUserDep,
    request: Request,
) -> list[WorkerStatusOut]:
    """Return worker pool status snapshots; gracefully handles absent pool."""
    worker_pool = getattr(request.app.state, "worker_pool", None)
    if worker_pool is None or not hasattr(worker_pool, "get_worker_statuses"):
        return []

    statuses = worker_pool.get_worker_statuses()
    return [
        WorkerStatusOut(
            worker_id=ws.worker_id,
            state=(
                ws.state.value if hasattr(ws.state, "value") else str(ws.state)
            ),
            current_job_id=ws.current_job_id,
            last_heartbeat=ws.last_heartbeat,
            jobs_completed=ws.jobs_completed,
            jobs_failed=ws.jobs_failed,
        )
        for ws in statuses
    ]


@router.get(
    "/{skill_id}/executions",
    response_model=list[SkillExecutionOut],
    status_code=status.HTTP_200_OK,
    summary="List skill executions",
    description=(
        "Return the execution history for an installed skill.  History is "
        "stored by the skill worker at ``skills/executions/{user}/{skill}.json``."
    ),
)
async def list_skill_executions(
    skill_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> list[SkillExecutionOut]:
    """Return stored execution records for the given skill."""
    path = _executions_path(user_id, skill_id)
    try:
        raw = await storage_client.read(path)
        records: list[dict] = json.loads(raw.decode())
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("skills: executions read failed skill_id=%s: %s", skill_id, exc)
        return []
    return [SkillExecutionOut(**r) for r in records]


@router.post(
    "/{skill_id}/test",
    response_model=SkillTestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Test a skill",
    description=(
        "Submit a test job for an installed skill.  Delegates to the worker pool "
        "when available; returns a mock submitted response when absent."
    ),
)
async def test_skill(
    skill_id: str,
    body: SkillTestRequest,
    user_id: CurrentUserDep,
    skill_registry: SkillRegistryDep,
    request: Request,
) -> SkillTestResponse:
    """Submit a test execution for the given skill."""
    from graphclaw.models.base import utcnow

    # Verify skill is installed
    try:
        installed = await skill_registry.list_installed(user_id)
        if not any(s.skill_id == skill_id for s in installed):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill '{skill_id}' not found or not installed",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("skills: test list check failed: %s", exc)

    job_id = f"JOB-{uuid.uuid4().hex[:12]}"
    now = utcnow()

    # Attempt to submit to worker pool if available
    worker_pool = getattr(request.app.state, "worker_pool", None)
    if worker_pool is not None and hasattr(worker_pool, "submit"):
        try:
            from graphclaw.skills.models import SkillJob

            job = SkillJob(
                job_id=job_id,
                skill_name=skill_id,
                task_id=body.task_id,
                session_id=f"test-{uuid.uuid4().hex[:8]}",
                input_data=body.input_data,
                created_at=now,
            )
            await worker_pool.submit(job)
            logger.debug("skills: test job submitted job_id=%s skill_id=%s", job_id, skill_id)
        except Exception as exc:
            logger.warning("skills: test submit failed: %s", exc)

    return SkillTestResponse(
        job_id=job_id,
        skill_id=skill_id,
        status="submitted",
        submitted_at=now,
    )
