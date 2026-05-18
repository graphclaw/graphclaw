# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.deps — Shared FastAPI dependency providers for the /app/v1/ layer.

Description
-----------
Centralises all FastAPI ``Depends()`` callables used by the cockpit API modules.
Each provider pulls the relevant runtime object from ``request.app.state``, which
is populated by the application factory at startup.  This keeps every endpoint
handler free of import-time coupling to concrete implementations.

Available providers
-------------------
- ``get_graph_store``            → ``GraphStore`` instance (required).
- ``get_query_engine``           → ``GraphQueryEngine`` instance (optional, 503 if absent).
- ``get_scoring_engine``         → ``ScoringEngine`` instance (required).
- ``get_state_machine``          → ``StateMachine`` instance (stateless, always fresh).
- ``get_storage_client``         → ``StorageClient`` instance (required).
- ``get_secrets_client``         → ``SecretsClient`` instance (required).
- ``get_skill_registry_service`` → ``SkillRegistryService`` (storage-backed, per-request).
- ``get_mcp_registry``           → ``MCPRegistry`` (storage-backed, per-request).
- ``get_redis``                  → ``redis.asyncio.Redis`` client (optional, 503 if absent).
- ``require_admin``              → Validates that the authenticated user holds ADMIN or OWNER role.

Design Patterns
---------------
- Dependency Injection: All providers are async callables compatible with
  ``Depends()``; endpoints declare them in their signatures.
- Fail-fast: Missing ``app.state`` attributes raise HTTP 503 immediately so
  callers never receive a ``None`` when a real instance is expected.
- Separation of concerns: Auth is handled by ``require_auth`` (in
  ``graphclaw.auth.middleware``); this module only adds role escalation.

Public API
----------
- get_graph_store, get_query_engine, get_scoring_engine, get_state_machine,
  get_storage_client, get_secrets_client, get_redis, require_admin.

Dependencies
------------
- graphclaw.auth.middleware: require_auth.
- graphclaw.db.base: GraphStore, GraphQueryEngine (third-party ABCs).
- graphclaw.scoring.engine: ScoringEngine.
- graphclaw.state.machine: StateMachine.
- graphclaw.infra.storage: StorageClient.
- graphclaw.infra.secrets: SecretsClient.
- fastapi: Depends, HTTPException, Request, status (third-party).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from graphclaw.auth.middleware import require_auth
from graphclaw.db.base import GraphQueryEngine, GraphStore
from graphclaw.infra.secrets import SecretsClient
from graphclaw.infra.storage import StorageClient
from graphclaw.mcp.registry import MCPRegistry
from graphclaw.scoring.engine import ScoringEngine
from graphclaw.skills.registry import SkillRegistryService
from graphclaw.state.machine import StateMachine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Infrastructure providers
# ---------------------------------------------------------------------------


async def get_graph_store(request: Request) -> GraphStore:
    """Return the ``GraphStore`` bound to this application instance.

    Raises
    ------
    HTTPException(503):
        If ``app.state.graph_store`` is not set (backend not initialised).
    """
    store: GraphStore | None = getattr(request.app.state, "graph_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Graph store is not initialised",
        )
    return store


async def get_query_engine(request: Request) -> GraphQueryEngine:
    """Return the ``GraphQueryEngine`` bound to this application instance.

    Raises
    ------
    HTTPException(503):
        If ``app.state.query_engine`` is not set.
    """
    engine: GraphQueryEngine | None = getattr(request.app.state, "query_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Graph query engine is not initialised",
        )
    return engine


async def get_scoring_engine(request: Request) -> ScoringEngine:
    """Return the ``ScoringEngine`` bound to this application instance.

    Raises
    ------
    HTTPException(503):
        If ``app.state.scoring_engine`` is not set.
    """
    engine: ScoringEngine | None = getattr(request.app.state, "scoring_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scoring engine is not initialised",
        )
    return engine


def get_state_machine() -> StateMachine:
    """Return a fresh ``StateMachine`` instance.

    ``StateMachine`` carries no mutable state between calls so it is safe to
    instantiate per-request.
    """
    return StateMachine()


async def get_storage_client(request: Request) -> StorageClient:
    """Return the ``StorageClient`` bound to this application instance.

    Raises
    ------
    HTTPException(503):
        If ``app.state.storage_client`` is not set.
    """
    client: StorageClient | None = getattr(request.app.state, "storage_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage client is not initialised",
        )
    return client


async def get_secrets_client(request: Request) -> SecretsClient:
    """Return the ``SecretsClient`` bound to this application instance.

    Raises
    ------
    HTTPException(503):
        If ``app.state.secrets_client`` is not set.
    """
    client: SecretsClient | None = getattr(request.app.state, "secrets_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Secrets client is not initialised",
        )
    return client


async def get_agent_graph_store(request: Request) -> GraphStore:
    """Return the ``GraphStore`` instance bound to the **agent_principal**.

    This is the least-privilege store for agent code paths — no DELETE grants.
    Falls back to the default store when a separate agent-principal store is
    not wired (dev environments).

    Raises
    ------
    HTTPException(503):
        If no graph store is available.
    """
    store: GraphStore | None = getattr(request.app.state, "agent_graph_store", None)
    if store is None:
        # Fallback: shared store (dev / single-DSN environments).
        return await get_graph_store(request)
    return store


async def get_admin_graph_store(request: Request) -> GraphStore:
    """Return the ``GraphStore`` instance bound to the **admin_principal**.

    This store has full grants including DELETE.  It must only be used by
    purge workers and admin endpoints.

    Raises
    ------
    HTTPException(503):
        If no admin graph store is available.
    """
    store: GraphStore | None = getattr(request.app.state, "admin_graph_store", None)
    if store is None:
        # Fallback: shared store (dev / single-DSN environments).
        return await get_graph_store(request)
    return store


async def get_skill_registry_service(request: Request) -> SkillRegistryService:
    """Return a ``SkillRegistryService`` wired to the app's StorageClient.

    The secrets client is attached when available so private GitHub sources
    can resolve auth tokens.

    Raises
    ------
    HTTPException(503):
        If ``app.state.storage_client`` is not set.
    """
    storage = await get_storage_client(request)
    secrets = getattr(request.app.state, "secrets_client", None)
    return SkillRegistryService(storage_client=storage, secrets_client=secrets)


async def get_mcp_registry(request: Request) -> MCPRegistry:
    """Return an ``MCPRegistry`` wired to the app's StorageClient.

    MCP server configs are stored as JSON files under each user's MinIO prefix
    (``{user_id}/mcp/servers/{server_id}.json``) rather than in the graph DB.

    Raises
    ------
    HTTPException(503):
        If ``app.state.storage_client`` is not set.
    """
    storage = await get_storage_client(request)
    secrets = getattr(request.app.state, "secrets_client", None)
    return MCPRegistry(storage_client=storage, secrets_client=secrets)


async def get_redis(request: Request):  # type: ignore[return]
    """Return the ``redis.asyncio.Redis`` client, if available.

    Raises
    ------
    HTTPException(503):
        If ``app.state.redis`` is not set (Redis not configured).
    """
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis is not configured",
        )
    return redis


# ---------------------------------------------------------------------------
# Role escalation
# ---------------------------------------------------------------------------

_ADMIN_ROLES = frozenset({"ADMIN", "OWNER"})


async def require_admin(
    user_id: Annotated[str, Depends(require_auth)],
    request: Request,
) -> str:
    """Verify that the authenticated user holds ADMIN or OWNER role.

    Reads the user's role from ``request.state.user_role``, which must be
    populated by the JWT middleware before this dependency runs.

    Parameters
    ----------
    user_id:
        Extracted from the Bearer token by ``require_auth``.
    request:
        FastAPI request; the middleware stores the decoded role on
        ``request.state.user_role``.

    Returns
    -------
    str:
        The authenticated ``user_id``, unchanged, if role check passes.

    Raises
    ------
    HTTPException(403):
        If the user does not hold ADMIN or OWNER role.
    """
    role: str = getattr(request.state, "user_role", "USER")
    if role not in _ADMIN_ROLES:
        logger.warning("deps: admin access denied for user_id=%s role=%s", user_id, role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or Owner role required",
        )
    return user_id


# ---------------------------------------------------------------------------
# Type aliases for cleaner endpoint signatures
# ---------------------------------------------------------------------------

GraphStoreDep = Annotated[GraphStore, Depends(get_graph_store)]
QueryEngineDep = Annotated[GraphQueryEngine, Depends(get_query_engine)]
ScoringEngineDep = Annotated[ScoringEngine, Depends(get_scoring_engine)]
StateMachineDep = Annotated[StateMachine, Depends(get_state_machine)]
StorageClientDep = Annotated[StorageClient, Depends(get_storage_client)]
SecretsClientDep = Annotated[SecretsClient, Depends(get_secrets_client)]
SkillRegistryDep = Annotated[SkillRegistryService, Depends(get_skill_registry_service)]
MCPRegistryDep = Annotated[MCPRegistry, Depends(get_mcp_registry)]
CurrentUserDep = Annotated[str, Depends(require_auth)]
AdminUserDep = Annotated[str, Depends(require_admin)]
AgentGraphStoreDep = Annotated[GraphStore, Depends(get_agent_graph_store)]
AdminGraphStoreDep = Annotated[GraphStore, Depends(get_admin_graph_store)]


async def get_broker(request: Request):  # type: ignore[return]
    """Return the ``MessageBroker`` from app state, or None if not configured."""
    return getattr(request.app.state, "broker", None)


BrokerDep = Annotated[Any, Depends(get_broker)]


# ---------------------------------------------------------------------------
# Wave 0 (FR-AL-001): CallerContext dependency
# ---------------------------------------------------------------------------


async def get_caller_context(
    request: Request,
    user_id: str = Depends(require_auth),
) -> CallerContextDep_type:
    """Build a CallerContext from the authenticated request.

    Provides user_id, org_id (from request state or header), and principal.
    """
    from graphclaw.cross_tenant.acl import CallerContext  # noqa: PLC0415

    org_id: str = getattr(request.state, "org_id", None) or request.headers.get(
        "X-Org-Id", "default"
    )
    principal: str = getattr(request.state, "principal", "agent_principal")
    session_id: str | None = getattr(request.state, "session_id", None)

    return CallerContext(
        user_id=user_id,
        org_id=org_id,
        principal=principal,
        session_id=session_id,
    )


CallerContextDep_type = object  # forward ref placeholder (avoid circular at module load)
CallerContextDep = Annotated[object, Depends(get_caller_context)]
