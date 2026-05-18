# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
GET    /app/v1/agents/{id}/config      — read runtime config.json
PUT    /app/v1/agents/{id}/config      — update runtime config.json
GET    /app/v1/agents/{id}/wiring      — resolved wiring summary
POST   /app/v1/agents/{id}/test        — run a quick test of the agent
GET    /app/v1/agents/delegations      — list active sub-agent delegations
GET    /app/v1/agents/dispatch-plan/{session_id} — list planned dispatch tiers for a session
GET    /app/v1/agents/{id}/plans       — list pending plans from MinIO

Storage layout
--------------
- ``agents/{user_id}/definitions/{agent_id}.json``
- ``agents/{user_id}/definitions/{agent_id}/versions/{version}.json``
- ``{user_id}/agents/{agent_id}/config.json``     (runtime config)
- ``{user_id}/agents/{agent_id}/profile.md``      (runtime profile)
- ``{user_id}/agents/{agent_id}/manifest.json``   (runtime manifest)

Design Patterns
---------------
- StorageClient persistence: Definitions stored as JSON blobs; no graph node
  is created — the canvas is a UI artefact, not a first-class graph entity.
- Version snapshots: Every PATCH creates a versioned copy before writing the
  update.  Versions are read-only (no delete/restore endpoint in this wave).
- Runtime bridge: POST /agents also provisions the runtime agent files so the
  orchestrator can immediately discover and invoke the new agent.
- Slugified IDs: Agent IDs are derived from the human-readable name for
  idempotency and readability (e.g. "research-agent").
- AgentLoop test: POST /{id}/test tries ``app.state.agent_loop.run_cycle()``
  and returns a lightweight summary; falls back gracefully when absent.

Public API
----------
- router: ``APIRouter`` for /agents routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, StorageClientDep.
- graphclaw.infra.storage: StoragePaths.
- fastapi: APIRouter, HTTPException, Query, Request, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from graphclaw.api.deps import CurrentUserDep, MCPRegistryDep, SkillRegistryDep, StorageClientDep
from graphclaw.infra.storage import StoragePaths
from graphclaw.models.base import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["app-api"])

# ---------------------------------------------------------------------------
# Storage path helpers
# ---------------------------------------------------------------------------

_DEF_PATH_TEMPLATE = "agents/{user_id}/definitions/{agent_id}.json"
_VER_PATH_TEMPLATE = "agents/{user_id}/definitions/{agent_id}/versions/{version}.json"
_DEF_PREFIX_TEMPLATE = "agents/{user_id}/definitions/"
_CANVAS_LAYOUT_TEMPLATE = "agents/{user_id}/definitions/canvas-layout.json"


def _def_path(user_id: str, agent_id: str) -> str:
    return _DEF_PATH_TEMPLATE.format(user_id=user_id, agent_id=agent_id)


def _ver_path(user_id: str, agent_id: str, version: str) -> str:
    return _VER_PATH_TEMPLATE.format(user_id=user_id, agent_id=agent_id, version=version)


def _ver_prefix(user_id: str, agent_id: str) -> str:
    return f"agents/{user_id}/definitions/{agent_id}/versions/"


def _def_prefix(user_id: str) -> str:
    return _DEF_PREFIX_TEMPLATE.format(user_id=user_id)


def _canvas_layout_path(user_id: str) -> str:
    return _CANVAS_LAYOUT_TEMPLATE.format(user_id=user_id)


def _slugify(name: str) -> str:
    """Convert a human-readable name to a URL-safe, lowercase slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:40] or "agent"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class AgentConfigSchema(BaseModel):
    """Typed runtime config for an agent (stored at {user_id}/agents/{agent_id}/config.json).

    Fields introduced for canvas wiring:
    - skills: IDs of installed skills this agent may invoke.
    - mcp_servers: IDs of registered MCP servers this agent may call.
    - tool_sets: Named tool-set IDs this agent can load_tool_set().
    - sub_agents: Agent IDs this orchestrator may delegate to.

    Secure-by-default: explicit empty list [] = no access.
    Missing key (None) = all available (backward compatible).
    """

    llm_model: str = "claude-sonnet-4-20250514"
    heartbeat_interval_seconds: int = 60
    execution_timeout_seconds: int = 600
    skills: list[str] | None = Field(default=None)
    mcp_servers: list[str] | None = Field(default=None)
    tool_sets: list[str] | None = Field(default=None)
    sub_agents: list[str] | None = Field(default=None)


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
    agent_id: str | None = None  # If provided, use as-is; otherwise slugify from name.


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


class WiredSkillEntry(BaseModel):
    """A skill wired to an agent."""

    skill_id: str
    skill_name: str
    description: str = ""
    version: str = "0.1.0"
    enabled: bool = True


class WiredMCPServerEntry(BaseModel):
    """An MCP server wired to an agent."""

    server_id: str
    name: str
    transport: str = "http"
    endpoint_url: str | None = None
    trust_tier: str = "GATED"
    enabled: bool = True


class WiredSubAgentEntry(BaseModel):
    """A sub-agent wired for delegation."""

    agent_id: str
    name: str
    description: str = ""


class WiringSummary(BaseModel):
    """Resolved wiring summary for an agent (C10)."""

    agent_id: str
    skills: list[WiredSkillEntry] = []
    mcp_servers: list[WiredMCPServerEntry] = []
    tool_sets: list[str] = []
    sub_agents: list[WiredSubAgentEntry] = []


class AgentDelegationRow(BaseModel):
    """Active sub-agent delegation row for cockpit agent monitor."""

    agent_id: str
    task_id: str
    session_id: str
    status: str
    started_at: datetime | None = None
    last_heartbeat: datetime | None = None
    heartbeat_age_seconds: int | None = None
    duration_seconds: int | None = None


class DispatchPlanJob(BaseModel):
    """Single delegated job inside a dispatch tier."""

    agent_id: str
    task_id: str
    batch_id: str
    status: str


class DispatchPlanTier(BaseModel):
    """One dispatch tier (swim-lane row) for a delegation session."""

    tier: int
    batch_id: str
    total_count: int
    completed_count: int
    status: str
    jobs: list[DispatchPlanJob] = []


class DispatchPlanResponse(BaseModel):
    """Dispatch plan response for a single orchestration session."""

    session_id: str
    tiers: list[DispatchPlanTier] = []


def _seconds_since(value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((utcnow() - value).total_seconds()))


def _runner_status_value(state: Any) -> str:
    value = getattr(state, "value", state)
    if value is None:
        return "UNKNOWN"
    return str(value).upper()


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
    await storage_client.write(_def_path(user_id, agent_id), raw, content_type="application/json")


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
    def_paths = [p for p in all_paths if p.endswith(".json") and "/versions/" not in p]

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
    """Create and persist a new agent definition, then provision runtime files."""
    now = utcnow()

    # Use provided agent_id or slugify from name; append short suffix on collision.
    base_id = body.agent_id or _slugify(body.name)
    agent_id = base_id
    # Deduplicate: if a definition already exists, append a short random suffix.
    if await storage_client.exists(_def_path(user_id, agent_id)):
        agent_id = f"{base_id}-{uuid.uuid4().hex[:6]}"

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

    # Provision runtime agent files so the orchestrator can discover this agent.
    await _provision_runtime_agent(user_id, agent_id, body.name, body.description, storage_client)

    logger.debug("agents: created agent_id=%s for user_id=%s", agent_id, user_id)
    return _dict_to_definition(data)


@router.get(
    "/delegations",
    response_model=list[AgentDelegationRow],
    status_code=status.HTTP_200_OK,
    summary="List active sub-agent delegations",
    description=(
        "Return currently running sub-agent delegations from the in-memory "
        "SubAgentPool runner snapshots. Returns [] when the pool is not initialised."
    ),
)
async def list_agent_delegations(
    user_id: CurrentUserDep,
    request: Request,
) -> list[AgentDelegationRow]:
    """List active delegations for the agent monitor panel."""
    sub_agent_pool = getattr(request.app.state, "sub_agent_pool", None)
    if sub_agent_pool is None or not hasattr(sub_agent_pool, "get_runner_statuses"):
        return []

    now = utcnow()
    rows: list[AgentDelegationRow] = []

    for runner_status in sub_agent_pool.get_runner_statuses():
        agent_id = getattr(runner_status, "agent_id", None)
        task_id = getattr(runner_status, "task_id", None)
        session_id = getattr(runner_status, "session_id", None)
        if not agent_id or not task_id or not session_id:
            continue

        started_at = getattr(runner_status, "started_at", None)
        last_heartbeat = getattr(runner_status, "last_heartbeat", None)
        state = _runner_status_value(getattr(runner_status, "state", None))

        rows.append(
            AgentDelegationRow(
                agent_id=str(agent_id),
                task_id=str(task_id),
                session_id=str(session_id),
                status=state,
                started_at=started_at,
                last_heartbeat=last_heartbeat,
                heartbeat_age_seconds=_seconds_since(last_heartbeat),
                duration_seconds=(
                    max(0, int((now - started_at).total_seconds()))
                    if started_at is not None
                    else None
                ),
            )
        )

    rows.sort(
        key=lambda row: row.started_at.timestamp() if row.started_at is not None else -1,
        reverse=True,
    )
    return rows


@router.get(
    "/dispatch-plan/{session_id}",
    response_model=DispatchPlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Get dispatch plan tiers for a session",
    description=(
        "Return dispatch tier swim-lanes for a delegation session from the "
        "in-memory SubAgentPool planner snapshot. Returns an empty tier list "
        "when the pool is not initialised or no plan is registered."
    ),
)
async def get_dispatch_plan(
    session_id: str,
    user_id: CurrentUserDep,
    request: Request,
) -> DispatchPlanResponse:
    """Return dispatch tier structure and job states for a session."""
    sub_agent_pool = getattr(request.app.state, "sub_agent_pool", None)
    if sub_agent_pool is None or not hasattr(sub_agent_pool, "get_dispatch_plan"):
        return DispatchPlanResponse(session_id=session_id, tiers=[])

    try:
        raw_tiers = sub_agent_pool.get_dispatch_plan(session_id)
    except Exception as exc:
        logger.warning("agents: dispatch plan lookup failed for session %s: %s", session_id, exc)
        return DispatchPlanResponse(session_id=session_id, tiers=[])

    tiers = [DispatchPlanTier.model_validate(tier) for tier in raw_tiers]
    return DispatchPlanResponse(session_id=session_id, tiers=tiers)


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

    # Sync runtime files when name or description changes so Intelligence Hub stays consistent.
    if body.name is not None or body.description is not None:
        try:
            await _provision_runtime_agent(
                user_id, agent_id, data["name"], data.get("description", ""), storage_client
            )
        except Exception as exc:
            logger.warning("agents: runtime sync failed for agent_id=%s: %s", agent_id, exc)

    logger.debug("agents: updated agent_id=%s version=%s", agent_id, new_version)
    return _dict_to_definition(data)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete agent definition",
    description=(
        "Permanently delete an agent canvas definition and its version history. "
        "Pass ``?cleanup_runtime=true`` to also delete the runtime agent files "
        "(profile, config, manifest, memory)."
    ),
)
async def delete_agent(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    cleanup_runtime: bool = Query(default=False, alias="cleanup_runtime"),
) -> None:
    """Delete an agent definition, version history, and optionally runtime files."""
    exists = await storage_client.exists(_def_path(user_id, agent_id))
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )
    # Delete definition
    await storage_client.delete(_def_path(user_id, agent_id))

    # Clean up version history (previously orphaned)
    try:
        ver_paths = await storage_client.list_objects(_ver_prefix(user_id, agent_id))
        for path in ver_paths:
            await storage_client.delete(path)
    except Exception as exc:
        logger.warning("agents: version cleanup failed for %s: %s", agent_id, exc)

    # Optionally delete runtime agent files
    if cleanup_runtime:
        try:
            runtime_prefix = StoragePaths.agent_root(user_id, agent_id)
            runtime_paths = await storage_client.list_objects(runtime_prefix)
            for path in runtime_paths:
                await storage_client.delete(path)
            logger.debug("agents: deleted runtime files for agent_id=%s", agent_id)
        except Exception as exc:
            logger.warning("agents: runtime cleanup failed for %s: %s", agent_id, exc)

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

    def _version_key(v: AgentVersionOut) -> int:
        try:
            return int(v.version)
        except ValueError:
            return 0

    return sorted(versions, key=_version_key)


# ---------------------------------------------------------------------------
# Pending plans
# ---------------------------------------------------------------------------


class PendingPlanTask(BaseModel):
    draft_task_id: str = ""
    title: str = ""
    description: str = ""
    priority: str = ""
    estimated_effort: str = ""


class PendingPlanOut(BaseModel):
    plan_id: str
    goal_title: str = ""
    goal_description: str = ""
    status: str = "DRAFT"
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""
    deadline: str | None = None
    tasks: list[PendingPlanTask] = Field(default_factory=list)


@router.get(
    "/{agent_id}/plans",
    response_model=list[PendingPlanOut],
    status_code=status.HTTP_200_OK,
    summary="List pending plans",
    description=(
        "Return all pending plans for this agent, ordered newest-first.  "
        "Plans with status EXECUTED are excluded."
    ),
)
async def list_pending_plans(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    include_executed: bool = Query(default=False, description="Include EXECUTED plans"),
) -> list[PendingPlanOut]:
    """List pending plans stored in MinIO for the given agent."""
    prefix = f"{StoragePaths.agent_root(user_id, agent_id)}state/pending_plans/"
    try:
        paths = await storage_client.list_objects(prefix)
    except Exception as exc:
        logger.warning("agents: failed to list plans for %s/%s: %s", user_id, agent_id, exc)
        return []

    plans: list[PendingPlanOut] = []
    for path in paths:
        if not path.endswith(".json"):
            continue
        try:
            raw = await storage_client.read(path)
            d = json.loads(raw.decode())
        except Exception as exc:
            logger.warning("agents: failed to read plan %s: %s", path, exc)
            continue

        if not include_executed and d.get("status") == "EXECUTED":
            continue

        tasks_raw = d.get("tasks", [])
        tasks = [
            PendingPlanTask(
                draft_task_id=str(t.get("draft_task_id", "")),
                title=str(t.get("title", t.get("name", ""))),
                description=str(t.get("description", "")),
                priority=str(t.get("priority", "")),
                estimated_effort=str(t.get("estimated_effort", "")),
            )
            for t in tasks_raw
            if isinstance(t, dict)
        ]

        plans.append(
            PendingPlanOut(
                plan_id=str(d.get("plan_id", "")),
                goal_title=str(d.get("goal_title", "")),
                goal_description=str(d.get("goal_description", "")),
                status=str(d.get("status", "DRAFT")),
                revision=int(d.get("revision", 1)),
                created_at=str(d.get("created_at", "")),
                updated_at=str(d.get("updated_at", "")),
                deadline=d.get("deadline") or None,
                tasks=tasks,
            )
        )

    plans.sort(key=lambda p: p.created_at, reverse=True)
    return plans


@router.get(
    "/{agent_id}/config",
    response_model=AgentConfigSchema,
    status_code=status.HTTP_200_OK,
    summary="Get agent runtime config",
    description="Return the runtime config.json for an agent (LLM model, tool wiring).",
)
async def get_agent_config(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AgentConfigSchema:
    """Read the runtime config.json for an agent."""
    config_path = StoragePaths.agent_config(user_id, agent_id)
    try:
        raw = await storage_client.read(config_path)
        data = json.loads(raw.decode())
        return AgentConfigSchema(
            **{k: v for k, v in data.items() if k in AgentConfigSchema.model_fields}
        )
    except FileNotFoundError:
        # Return sensible defaults if config not yet provisioned
        return AgentConfigSchema()
    except Exception as exc:
        logger.warning("agents: config read failed for %s: %s", agent_id, exc)
        return AgentConfigSchema()


@router.put(
    "/{agent_id}/config",
    response_model=AgentConfigSchema,
    status_code=status.HTTP_200_OK,
    summary="Update agent runtime config",
    description="Write the runtime config.json for an agent (LLM model, tool wiring).",
)
async def put_agent_config(
    agent_id: str,
    body: AgentConfigSchema,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> AgentConfigSchema:
    """Write the runtime config.json for an agent."""
    # Accept writes if any of: canvas definition, agent manifest, or existing config file.
    # This allows wiring the orchestrator and intelligence-hub agents that have no canvas def.
    canvas_def_exists = await storage_client.exists(_def_path(user_id, agent_id))
    if not canvas_def_exists:
        manifest_exists = await storage_client.exists(
            StoragePaths.agent_manifest(user_id, agent_id)
        )
        config_exists = await storage_client.exists(StoragePaths.agent_config(user_id, agent_id))
        if not manifest_exists and not config_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent '{agent_id}' not found",
            )
    config_path = StoragePaths.agent_config(user_id, agent_id)
    raw = json.dumps(body.model_dump(exclude_none=False), default=str).encode()
    await storage_client.write(config_path, raw, content_type="application/json")
    logger.debug("agents: config updated for agent_id=%s", agent_id)
    return body


@router.get(
    "/{agent_id}/wiring",
    response_model=WiringSummary,
    status_code=status.HTTP_200_OK,
    summary="Get agent wiring summary",
    description=(
        "Return a resolved wiring summary for an agent: the skills, MCP servers, "
        "tool sets, and sub-agents currently configured in the runtime config.json. "
        "Skill and MCP server entries are enriched with metadata from the registries."
    ),
)
async def get_agent_wiring(
    agent_id: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    skill_registry: SkillRegistryDep,
    mcp_registry: MCPRegistryDep,
) -> WiringSummary:
    """Return resolved wiring metadata for an agent's config.json."""
    # Verify the definition exists
    exists = await storage_client.exists(_def_path(user_id, agent_id))
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )

    # Load config.json to get wired IDs
    config_path = StoragePaths.agent_config(user_id, agent_id)
    try:
        raw = await storage_client.read(config_path)
        config_data = json.loads(raw.decode())
    except FileNotFoundError:
        config_data = {}
    except Exception as exc:
        logger.warning("agents: wiring config read failed for %s: %s", agent_id, exc)
        config_data = {}

    skill_ids: list[str] = config_data.get("skills") or []
    mcp_server_ids: list[str] = config_data.get("mcp_servers") or []
    tool_sets: list[str] = config_data.get("tool_sets") or []
    sub_agent_ids: list[str] = config_data.get("sub_agents") or []

    # Resolve skills from registry
    wired_skills: list[WiredSkillEntry] = []
    if skill_ids:
        try:
            installed = await skill_registry.list_installed(user_id)
            installed_map = {s.skill_id: s for s in installed}
            for sid in skill_ids:
                if sid in installed_map:
                    s = installed_map[sid]
                    wired_skills.append(
                        WiredSkillEntry(
                            skill_id=s.skill_id,
                            skill_name=s.skill_name,
                            description=getattr(s, "description", ""),
                            version=getattr(s, "version", "0.1.0"),
                            enabled=getattr(s, "enabled", True),
                        )
                    )
                else:
                    # ID wired but skill no longer installed — include as orphan
                    wired_skills.append(
                        WiredSkillEntry(skill_id=sid, skill_name=sid, enabled=False)
                    )
        except Exception as exc:
            logger.warning("agents: skill wiring resolution failed: %s", exc)

    # Resolve MCP servers from registry
    wired_mcp: list[WiredMCPServerEntry] = []
    if mcp_server_ids:
        try:
            all_servers = await mcp_registry.list_for_user(user_id, enabled_only=False)
            server_map = {srv.server_id: srv for srv in all_servers}
            for sid in mcp_server_ids:
                if sid in server_map:
                    srv = server_map[sid]
                    wired_mcp.append(
                        WiredMCPServerEntry(
                            server_id=srv.server_id,
                            name=srv.name,
                            transport=str(
                                srv.transport.value
                                if hasattr(srv.transport, "value")
                                else srv.transport
                            ),
                            endpoint_url=getattr(srv, "endpoint_url", None),
                            trust_tier=str(
                                srv.trust_tier.value
                                if hasattr(srv.trust_tier, "value")
                                else srv.trust_tier
                            ),
                            enabled=getattr(srv, "enabled", True),
                        )
                    )
                else:
                    wired_mcp.append(WiredMCPServerEntry(server_id=sid, name=sid, enabled=False))
        except Exception as exc:
            logger.warning("agents: MCP wiring resolution failed: %s", exc)

    # Resolve sub-agents from definitions
    wired_sub_agents: list[WiredSubAgentEntry] = []
    for sub_id in sub_agent_ids:
        try:
            sub_data = await _load_definition(user_id, sub_id, storage_client)
            if sub_data:
                wired_sub_agents.append(
                    WiredSubAgentEntry(
                        agent_id=sub_id,
                        name=sub_data.get("name", sub_id),
                        description=sub_data.get("description", ""),
                    )
                )
            else:
                wired_sub_agents.append(WiredSubAgentEntry(agent_id=sub_id, name=sub_id))
        except Exception as exc:
            logger.warning("agents: sub-agent wiring resolution failed for %s: %s", sub_id, exc)
            wired_sub_agents.append(WiredSubAgentEntry(agent_id=sub_id, name=sub_id))

    return WiringSummary(
        agent_id=agent_id,
        skills=wired_skills,
        mcp_servers=wired_mcp,
        tool_sets=tool_sets,
        sub_agents=wired_sub_agents,
    )


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


# ---------------------------------------------------------------------------
# Runtime agent provisioning helper
# ---------------------------------------------------------------------------


async def _provision_runtime_agent(
    user_id: str,
    agent_id: str,
    name: str,
    description: str,
    storage_client: Any,
) -> None:
    """Provision the runtime files for a newly created agent.

    Creates four files under ``{user_id}/agents/{agent_id}/``:
    - profile.md      — default persona document
    - manifest.json   — capability manifest
    - config.json     — default operational config
    - memory/semantic/knowledge.md — empty semantic memory stub
    """
    now = utcnow().isoformat()

    # profile.md
    profile_content = (
        f"# {name}\n\n"
        f"{description}\n\n"
        f"## Role\n\nSub-agent provisioned via canvas.\n\n"
        f"## Capabilities\n\n- (configure via canvas wiring)\n"
    )
    await storage_client.write(
        StoragePaths.agent_profile(user_id, agent_id),
        profile_content.encode(),
        content_type="text/markdown",
    )

    # manifest.json
    manifest = {
        "agent_id": agent_id,
        "name": name,
        "description": description,
        "source": "user",
        "created_at": now,
    }
    await storage_client.write(
        StoragePaths.agent_manifest(user_id, agent_id),
        json.dumps(manifest, default=str).encode(),
        content_type="application/json",
    )

    # config.json — minimal defaults; caller can PUT to override
    config = AgentConfigSchema()
    await storage_client.write(
        StoragePaths.agent_config(user_id, agent_id),
        json.dumps(config.model_dump(exclude_none=False), default=str).encode(),
        content_type="application/json",
    )

    # memory/semantic/knowledge.md stub
    semantic_path = f"{user_id}/agents/{agent_id}/memory/semantic/knowledge.md"
    await storage_client.write(
        semantic_path,
        b"# Knowledge\n\n(no entries yet)\n",
        content_type="text/markdown",
    )

    logger.debug(
        "agents: provisioned runtime files for agent_id=%s user_id=%s",
        agent_id,
        user_id,
    )
