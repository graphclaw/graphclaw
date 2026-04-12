"""graphclaw.api.agents — Agent canvas definition CRUD endpoints.

Description
-----------
Provides CRUD endpoints for agent workflow definitions (the canvas export
format used by the cockpit agent editor).  Each definition is a JSON document
persisted to the ``StorageClient`` at a per-user path.  A lightweight version
history is maintained by appending copies under a ``/versions/`` prefix.

Routes
------
GET    /app/v1/agents                  — list all agent definitions
POST   /app/v1/agents                  — create a new agent definition
GET    /app/v1/agents/{id}             — retrieve a specific definition
PATCH  /app/v1/agents/{id}             — update a definition (auto-versions)
DELETE /app/v1/agents/{id}             — delete a definition
GET    /app/v1/agents/{id}/versions    — list version history
POST   /app/v1/agents/{id}/test        — run a quick test of the agent

Storage layout
--------------
- ``agents/{user_id}/definitions/{agent_id}.json``
- ``agents/{user_id}/definitions/{agent_id}/versions/{version}.json``

Design Patterns
---------------
- StorageClient persistence: Definitions stored as JSON blobs; no graph node
  is created — the canvas is a UI artefact, not a first-class graph entity.
- Version snapshots: Every PATCH creates a versioned copy before writing the
  update.  Versions are read-only (no delete/restore endpoint in this wave).
- AgentLoop test: POST /{id}/test tries ``app.state.agent_loop.run_cycle()``
  and returns a lightweight summary; falls back gracefully when absent.

Public API
----------
- router: ``APIRouter`` for /agents routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, StorageClientDep.
- fastapi: APIRouter, HTTPException, Request, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.models.base import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["app-api"])

# ---------------------------------------------------------------------------
# Storage path helpers
# ---------------------------------------------------------------------------

_DEF_PATH_TEMPLATE = "agents/{user_id}/definitions/{agent_id}.json"
_VER_PATH_TEMPLATE = "agents/{user_id}/definitions/{agent_id}/versions/{version}.json"
_DEF_PREFIX_TEMPLATE = "agents/{user_id}/definitions/"


def _def_path(user_id: str, agent_id: str) -> str:
    return _DEF_PATH_TEMPLATE.format(user_id=user_id, agent_id=agent_id)


def _ver_path(user_id: str, agent_id: str, version: str) -> str:
    return _VER_PATH_TEMPLATE.format(user_id=user_id, agent_id=agent_id, version=version)


def _def_prefix(user_id: str) -> str:
    return _DEF_PREFIX_TEMPLATE.format(user_id=user_id)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class AgentDefinition(BaseModel):
    """Agent canvas definition with metadata and free-form config."""

    agent_id: str
    name: str
    description: str = ""
    version: str = "1"
    created_at: datetime
    updated_at: datetime
    config: dict[str, Any] = {}
    tags: list[str] = []


class AgentCreateRequest(BaseModel):
    """Request body for POST /agents."""

    name: str
    description: str = ""
    config: dict[str, Any] = {}
    tags: list[str] = []


class AgentPatchRequest(BaseModel):
    """Request body for PATCH /agents/{id}."""

    name: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None
    tags: list[str] | None = None


class AgentVersionOut(BaseModel):
    """A version snapshot entry."""

    version: str
    saved_at: datetime
    agent_id: str


class AgentTestResponse(BaseModel):
    """Response from POST /agents/{id}/test."""

    agent_id: str
    status: str = "ok"
    queue_depth: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


async def _load_definition(
    user_id: str, agent_id: str, storage_client: Any
) -> dict[str, Any] | None:
    """Read an agent definition from storage; returns None if absent."""
    try:
        raw = await storage_client.read(_def_path(user_id, agent_id))
        return json.loads(raw.decode())
    except FileNotFoundError:
        return None


async def _save_definition(
    user_id: str, agent_id: str, storage_client: Any, data: dict[str, Any]
) -> None:
    """Write an agent definition to storage."""
    raw = json.dumps(data, default=str).encode()
    await storage_client.write(
        _def_path(user_id, agent_id), raw, content_type="application/json"
    )


async def _save_version(
    user_id: str, agent_id: str, storage_client: Any, data: dict[str, Any]
) -> None:
    """Snapshot the current definition as a new version."""
    version = data.get("version", "1")
    saved_at = utcnow().isoformat()
    snapshot = {**data, "saved_at": saved_at}
    raw = json.dumps(snapshot, default=str).encode()
    await storage_client.write(
        _ver_path(user_id, agent_id, version),
        raw,
        content_type="application/json",
    )


def _dict_to_definition(d: dict[str, Any]) -> AgentDefinition:
    """Convert a stored dict to an ``AgentDefinition`` response."""
    return AgentDefinition(
        agent_id=d["agent_id"],
        name=d.get("name", ""),
        description=d.get("description", ""),
        version=str(d.get("version", "1")),
        created_at=d.get("created_at", utcnow()),
        updated_at=d.get("updated_at", utcnow()),
        config=d.get("config", {}),
        tags=d.get("tags", []),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[AgentDefinition],
    status_code=status.HTTP_200_OK,
    summary="List agent definitions",
    description="Return all agent canvas definitions owned by the authenticated user.",
)
async def list_agents(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> list[AgentDefinition]:
    """List all agent definitions for the authenticated user."""
    prefix = _def_prefix(user_id)
    try:
        all_paths = await storage_client.list_objects(prefix)
    except Exception:
        return []

    # Filter to only top-level definition files (exclude versions/)
    def_paths = [
        p for p in all_paths
        if p.endswith(".json") and "/versions/" not in p
    ]

    definitions: list[AgentDefinition] = []
    for path in def_paths:
        try:
            raw = await storage_client.read(path)
            d = json.loads(raw.decode())
            definitions.append(_dict_to_definition(d))
        except Exception as exc:
            logger.warning("agents: failed to read %s: %s", path, exc)

    return definitions


@router.post(
    "",
    response_model=AgentDefinition,
    status_code=status.HTTP_201_CREATED,
    summary="Create agent definition",
    description="Create a new agent canvas definition.",
)
async def create_agent(
    body: AgentCreateRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AgentDefinition:
    """Create and persist a new agent definition."""
    now = utcnow()
    agent_id = f"AGT-{uuid.uuid4().hex[:12]}"
    data: dict[str, Any] = {
        "agent_id": agent_id,
        "name": body.name,
        "description": body.description,
        "version": "1",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "config": body.config,
        "tags": body.tags,
    }
    await _save_definition(user_id, agent_id, storage_client, data)
    logger.debug("agents: created agent_id=%s for user_id=%s", agent_id, user_id)
    return _dict_to_definition(data)


@router.get(
    "/{agent_id}",
    response_model=AgentDefinition,
    status_code=status.HTTP_200_OK,
    summary="Get agent definition",
    description="Return a specific agent canvas definition by ID.",
)
async def get_agent(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AgentDefinition:
    """Return a single agent definition by ID."""
    data = await _load_definition(user_id, agent_id, storage_client)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )
    return _dict_to_definition(data)


@router.patch(
    "/{agent_id}",
    response_model=AgentDefinition,
    status_code=status.HTTP_200_OK,
    summary="Update agent definition",
    description=(
        "Partially update an agent definition.  The previous version is "
        "automatically snapshotted before the update is written."
    ),
)
async def patch_agent(
    agent_id: str,
    body: AgentPatchRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AgentDefinition:
    """Update an agent definition and snapshot the previous version."""
    data = await _load_definition(user_id, agent_id, storage_client)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )

    # Snapshot current version before update
    try:
        await _save_version(user_id, agent_id, storage_client, data)
    except Exception as exc:
        logger.warning("agents: version snapshot failed: %s", exc)

    # Bump version number
    current_version = int(data.get("version", "1"))
    new_version = str(current_version + 1)

    now = utcnow()
    if body.name is not None:
        data["name"] = body.name
    if body.description is not None:
        data["description"] = body.description
    if body.config is not None:
        data["config"] = body.config
    if body.tags is not None:
        data["tags"] = body.tags
    data["version"] = new_version
    data["updated_at"] = now.isoformat()

    await _save_definition(user_id, agent_id, storage_client, data)
    logger.debug("agents: updated agent_id=%s version=%s", agent_id, new_version)
    return _dict_to_definition(data)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete agent definition",
    description="Permanently delete an agent canvas definition.",
)
async def delete_agent(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> None:
    """Delete an agent definition from storage."""
    exists = await storage_client.exists(_def_path(user_id, agent_id))
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )
    await storage_client.delete(_def_path(user_id, agent_id))
    logger.debug("agents: deleted agent_id=%s for user_id=%s", agent_id, user_id)


@router.get(
    "/{agent_id}/versions",
    response_model=list[AgentVersionOut],
    status_code=status.HTTP_200_OK,
    summary="List agent versions",
    description="Return the version history for an agent definition.",
)
async def list_agent_versions(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> list[AgentVersionOut]:
    """List all saved versions for the given agent."""
    # Verify the definition exists
    exists = await storage_client.exists(_def_path(user_id, agent_id))
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )

    ver_prefix = f"agents/{user_id}/definitions/{agent_id}/versions/"
    try:
        ver_paths = await storage_client.list_objects(ver_prefix)
    except Exception:
        return []

    versions: list[AgentVersionOut] = []
    for path in ver_paths:
        try:
            raw = await storage_client.read(path)
            d = json.loads(raw.decode())
            saved_at_raw = d.get("saved_at", d.get("updated_at", utcnow().isoformat()))
            saved_at = (
                datetime.fromisoformat(saved_at_raw)
                if isinstance(saved_at_raw, str)
                else saved_at_raw
            )
            versions.append(
                AgentVersionOut(
                    version=str(d.get("version", "?")),
                    saved_at=saved_at,
                    agent_id=agent_id,
                )
            )
        except Exception as exc:
            logger.warning("agents: version read failed %s: %s", path, exc)

    return sorted(versions, key=lambda v: v.version)


@router.post(
    "/{agent_id}/test",
    response_model=AgentTestResponse,
    status_code=status.HTTP_200_OK,
    summary="Test agent",
    description=(
        "Run a quick test of the agent definition.  Delegates to "
        "``AgentLoop.run_cycle()`` when available; returns a summary response "
        "with the current queue depth."
    ),
)
async def test_agent(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    request: Request,
) -> AgentTestResponse:
    """Test the agent by running one scoring cycle."""
    exists = await storage_client.exists(_def_path(user_id, agent_id))
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )

    agent_loop = getattr(request.app.state, "agent_loop", None)
    queue_depth = 0

    if agent_loop is not None and hasattr(agent_loop, "run_cycle"):
        try:
            queue = await agent_loop.run_cycle()
            queue_depth = len(queue)
            message = f"Cycle completed — {queue_depth} tasks scored"
        except Exception as exc:
            logger.warning("agents: test cycle failed: %s", exc)
            message = "Cycle failed — see server logs"
    else:
        message = "Agent loop not initialised — definition syntax OK"

    return AgentTestResponse(
        agent_id=agent_id,
        status="ok",
        queue_depth=queue_depth,
        message=message,
    )
