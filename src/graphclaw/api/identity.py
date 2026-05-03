"""graphclaw.api.identity — Identity REST endpoints.

Description
-----------
Exposes identity resolution, alias registration, and resource merge
operations as REST endpoints.  These wrap the same logic used by the
agent identity tool set (FR-ID-002..005).

Endpoints
---------
POST /identity/resolve_user      → ranked resolution candidates
POST /identity/register_alias    → add alias to a node
POST /identity/merge_resource    → merge two duplicate nodes

Dependencies
------------
- graphclaw.identity.resolver: UserResolver
- graphclaw.agent.identity.register_alias: register_alias action
- graphclaw.identity.merger: ResourceMerger
- graphclaw.gateway.deps: get_current_user, get_store, get_storage
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import CallerContextDep, CurrentUserDep, GraphStoreDep, StorageClientDep

router = APIRouter(prefix="/identity", tags=["identity"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ResolveUserRequest(BaseModel):
    query: str
    hints: dict[str, Any] | None = None


class RegisterAliasRequest(BaseModel):
    node_id: str
    alias: str
    source: str = "user"


class MergeResourceRequest(BaseModel):
    keep_id: str
    merge_id: str
    canonical_name: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/resolve_user")
async def resolve_user(
    req: ResolveUserRequest,
    user_id: CurrentUserDep,
    store: GraphStoreDep,
    caller_context: CallerContextDep,
) -> dict[str, Any]:
    """Return ranked identity resolution candidates for a query string."""
    from graphclaw.identity.resolver import UserResolver  # noqa: PLC0415

    resolver = UserResolver(store)
    candidates = await resolver.resolve(
        req.query, user_id, [], req.hints or {}, caller_context=caller_context
    )
    return {
        "candidates": [
            {
                "node_id": c.node_id,
                "source": c.source,
                "confidence": c.confidence,
                "display_name": c.display_name,
                "reason": c.reason,
            }
            for c in candidates
        ]
    }


@router.post("/register_alias")
async def register_alias(
    req: RegisterAliasRequest,
    user_id: CurrentUserDep,
    store: GraphStoreDep,
    caller_context: CallerContextDep,
) -> dict[str, Any]:
    """Add a new alias to a resource or user node."""
    from graphclaw.agent.tools.identity_tools import register_alias as _register  # noqa: PLC0415

    result = await _register(
        req.node_id, req.alias, req.source, user_id, store, caller_context=caller_context
    )
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
    return result


@router.post("/merge_resource")
async def merge_resource(
    req: MergeResourceRequest,
    user_id: CurrentUserDep,
    store: GraphStoreDep,
    storage: StorageClientDep,
    caller_context: CallerContextDep,
) -> dict[str, Any]:
    """Merge two duplicate resource nodes. keep_id becomes canonical."""
    from graphclaw.identity.merger import ResourceMerger  # noqa: PLC0415

    merger = ResourceMerger(store=store, storage=storage)
    try:
        result = await merger.merge(
            req.keep_id, req.merge_id, req.canonical_name, caller_context=caller_context
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "keep_id": result.keep_id,
        "merge_id": result.merge_id,
        "tombstone_id": result.tombstone_id,
        "edges_redirected": result.edges_redirected,
        "aliases_merged": result.aliases_merged,
        "intelligence_lines_merged": result.intelligence_lines_merged,
    }
