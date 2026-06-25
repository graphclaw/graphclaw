# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.policies — REST endpoints for per-user agent policy files.

Description
-----------
Provides GET/PUT endpoints for reading and writing per-user agent policy files
stored in MinIO at ``{user_id}/agents/{agent_id}/policies/{policy_name}.md``
(FR-STORE-002, FR-POL-001).

Endpoints
---------
- ``GET  /app/v1/agents/{agent_id}/policies/{policy_name}``
    Read a policy file.  Returns parsed frontmatter + body + version (etag).
- ``PUT  /app/v1/agents/{agent_id}/policies/{policy_name}``
    Write a policy file.  Validates frontmatter schema; invalidates Redis cache.

All endpoints require a valid Bearer access token.  Users can only access their
own policies (user_id scoped by JWT).

Design Patterns
---------------
- Optimistic concurrency: optional ``expected_version`` field compared against
  current etag; returns 409 Conflict if mismatch.
- Cache invalidation: PUT always invalidates the Redis cache entry (FR-STORE-002 AC3).

Public API
----------
- router: ``APIRouter`` for /agents/{agent_id}/policies routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, StorageClientDep.
- graphclaw.agent.policies.loader: PolicyLoader, PolicyLoadError.
- graphclaw.agent.policies.schemas: POLICY_SCHEMA_MAP.
"""

from __future__ import annotations

import hashlib
import logging

import yaml
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from graphclaw.agent.policies.loader import PolicyLoader, PolicyLoadError
from graphclaw.agent.policies.schemas import POLICY_SCHEMA_MAP
from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.infra.storage import StoragePaths

logger = logging.getLogger(__name__)

router = APIRouter(tags=["policies"])


class PolicyReadResponse(BaseModel):
    frontmatter: dict
    body: str
    version: str  # MD5 etag


class PolicyWriteRequest(BaseModel):
    frontmatter: dict
    body: str
    expected_version: str | None = None


class PolicyWriteResponse(BaseModel):
    version: str


@router.get("/agents/{agent_id}/policies/{policy_name}", response_model=PolicyReadResponse)
async def read_policy(
    agent_id: str,
    policy_name: str,
    request: Request,
    user_id: CurrentUserDep,
    storage: StorageClientDep,
) -> PolicyReadResponse:
    """Read a policy file for the authenticated user's agent."""
    if policy_name not in POLICY_SCHEMA_MAP:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown policy: {policy_name!r}. Valid names: {list(POLICY_SCHEMA_MAP)}",
        )
    redis = getattr(request.app.state, "redis", None)
    loader = PolicyLoader(storage, redis_client=redis)
    try:
        loaded = await loader.load(user_id, agent_id, policy_name)
    except PolicyLoadError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # Re-parse frontmatter as plain dict for response.
    try:
        import yaml as _yaml  # noqa: PLC0415

        fm_text = loaded.raw_bytes.decode("utf-8", errors="replace")
        if fm_text.startswith("---"):
            end = fm_text.find("\n---", 3)
            if end != -1:
                fm_text = fm_text[3:end].strip()
            else:
                fm_text = ""
        fm_dict = _yaml.safe_load(fm_text) or {}
    except Exception:
        fm_dict = {}

    return PolicyReadResponse(
        frontmatter=fm_dict,
        body=loaded.body,
        version=loaded.etag,
    )


@router.put(
    "/agents/{agent_id}/policies/{policy_name}",
    response_model=PolicyWriteResponse,
    status_code=status.HTTP_200_OK,
)
async def write_policy(
    agent_id: str,
    policy_name: str,
    payload: PolicyWriteRequest,
    request: Request,
    user_id: CurrentUserDep,
    storage: StorageClientDep,
) -> PolicyWriteResponse:
    """Write (create or update) a policy file for the authenticated user's agent."""
    if policy_name not in POLICY_SCHEMA_MAP:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown policy: {policy_name!r}. Valid names: {list(POLICY_SCHEMA_MAP)}",
        )

    # Validate frontmatter against the schema.
    schema_cls = POLICY_SCHEMA_MAP[policy_name]
    try:
        schema_cls(**payload.frontmatter)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid frontmatter for {policy_name!r}: {exc}",
        ) from exc

    # Optimistic concurrency: if expected_version provided, check current etag.
    if payload.expected_version is not None:
        redis = getattr(request.app.state, "redis", None)
        loader = PolicyLoader(storage, redis_client=redis)
        try:
            current = await loader.load(user_id, agent_id, policy_name)
            if current.etag != payload.expected_version:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Policy version mismatch (concurrent write).",
                )
        except PolicyLoadError:
            pass  # File doesn't exist yet — allow create.

    # Serialise to YAML frontmatter + body.
    fm_yaml = yaml.dump(payload.frontmatter, default_flow_style=False, allow_unicode=True)
    file_content = f"---\n{fm_yaml}---\n{payload.body}"
    raw_bytes = file_content.encode("utf-8")

    path = StoragePaths.agent_policy(user_id, agent_id, policy_name)
    await storage.write(path, raw_bytes, content_type="text/markdown")

    # Invalidate Redis cache.
    redis = getattr(request.app.state, "redis", None)
    loader = PolicyLoader(storage, redis_client=redis)
    await loader.invalidate(user_id, agent_id, policy_name)

    etag = hashlib.md5(  # noqa: S324 — non-crypto etag
        raw_bytes, usedforsecurity=False
    ).hexdigest()
    logger.info("Policy %s written for user %s / agent %s", policy_name, user_id, agent_id)
    return PolicyWriteResponse(version=etag)
