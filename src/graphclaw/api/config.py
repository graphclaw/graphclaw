# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.config — User agent configuration endpoints.

Description
-----------
Provides REST endpoints for managing the per-user agent configuration JSON
blob, which controls agent behaviour such as autonomy levels, scoring weights
overrides, notification preferences, and briefing settings.

Endpoints
---------
- ``GET    /app/v1/config``  — Return the full agent config for the user.
- ``PUT    /app/v1/config``  — Replace the entire config with a new document.
- ``PATCH  /app/v1/config``  — Merge a partial update into the current config.

All endpoints require a valid Bearer access token.

Storage layout
--------------
Config is stored via ``StorageClient`` at ``agents/{user_id}/agent_config.json``.
The schema is intentionally open (``dict[str, Any]``) to allow forward-compatible
evolution without migrations.

Design Patterns
---------------
- StorageClient persistence: Config is a JSON blob; GET reads and returns it
  as-is; PUT overwrites; PATCH deep-merges.
- FileNotFoundError → empty dict: GET and PATCH return ``{}`` for a new user
  so callers receive a valid (if empty) config.
- PUT idempotency: Repeated PUT calls with identical bodies are idempotent.

Public API
----------
- router: ``APIRouter`` for /config routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, StorageClientDep.
- fastapi: APIRouter, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _config_path(user_id: str) -> str:
    return f"agents/{user_id}/agent_config.json"


async def _load_config(user_id: str, storage_client) -> dict[str, Any]:
    try:
        raw = await storage_client.read(_config_path(user_id))
        return json.loads(raw.decode())
    except FileNotFoundError:
        return {}


async def _save_config(user_id: str, storage_client, data: dict[str, Any]) -> None:
    raw = json.dumps(data, default=str).encode()
    await storage_client.write(_config_path(user_id), raw, content_type="application/json")


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *patch* into *base* (patch wins on conflicts)."""
    result = dict(base)
    for key, value in patch.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    """The full agent configuration document."""

    config: dict[str, Any] = {}


class ConfigPutRequest(BaseModel):
    """Request body for PUT /app/v1/config — replaces the entire config."""

    config: dict[str, Any]


class ConfigPatchRequest(BaseModel):
    """Request body for PATCH /app/v1/config — deep-merges into current config."""

    config: dict[str, Any]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Get agent configuration",
    description=(
        "Return the full agent configuration for the authenticated user.  "
        "Returns an empty document for users who have not yet configured their agent."
    ),
)
async def get_config(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> ConfigResponse:
    """Return the agent config document."""
    data = await _load_config(user_id, storage_client)
    return ConfigResponse(config=data)


@router.put(
    "",
    response_model=ConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Replace agent configuration",
    description=(
        "Replace the entire agent configuration with the supplied document.  "
        "All existing settings are overwritten."
    ),
)
async def put_config(
    body: ConfigPutRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> ConfigResponse:
    """Replace the agent config document."""
    await _save_config(user_id, storage_client, body.config)
    logger.info("config: replaced for user_id=%s", user_id)
    return ConfigResponse(config=body.config)


@router.patch(
    "",
    response_model=ConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Partially update agent configuration",
    description=(
        "Deep-merge the supplied partial config into the current config.  "
        "Nested dicts are recursively merged; supplied keys overwrite existing ones."
    ),
)
async def patch_config(
    body: ConfigPatchRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> ConfigResponse:
    """Deep-merge a partial config into the existing config."""
    current = await _load_config(user_id, storage_client)
    merged = _deep_merge(current, body.config)
    await _save_config(user_id, storage_client, merged)
    logger.debug("config: patched for user_id=%s", user_id)
    return ConfigResponse(config=merged)
