"""graphclaw.api.settings — User settings endpoints.

Description
-----------
Provides ``GET /app/v1/settings`` and ``PATCH /app/v1/settings`` for reading
and updating per-user configuration (LLM provider, timezone, channel config),
and ``GET /app/v1/settings/channels`` for listing configured channels.

All endpoints require a valid Bearer access token.  Settings are currently
stored in-memory as a stub; a graph-backed implementation will replace this in
a future phase.

Design Patterns
---------------
- Dependency injection: ``require_auth`` extracts the authenticated user_id
  from the Bearer token so endpoints never parse tokens themselves.
- Stub storage: A module-level dict simulates per-user settings persistence
  until the graph store integration is implemented.

Public API
----------
- router: ``APIRouter`` for /settings routes.

Dependencies
------------
- graphclaw.auth.middleware: require_auth.
- fastapi: APIRouter, Depends, HTTPException, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from graphclaw.auth.middleware import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["app-api"])

# ── Stub in-memory storage ─────────────────────────────────────────────────────

_user_settings: dict[str, dict[str, Any]] = {}


def _default_settings(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "llm_provider": "litellm",
        "timezone": "UTC",
        "channels": [],
    }


# ── Request / Response models ──────────────────────────────────────────────────


class SettingsResponse(BaseModel):
    """Response body for GET /app/v1/settings."""

    user_id: str
    llm_provider: str = "litellm"
    timezone: str = "UTC"
    channels: list[dict[str, Any]] = []


class SettingsPatchRequest(BaseModel):
    """Request body for PATCH /app/v1/settings."""

    llm_provider: str | None = None
    timezone: str | None = None


class ChannelStatus(BaseModel):
    """A single channel entry with enabled/disabled status."""

    channel: str
    enabled: bool
    config: dict[str, Any] = {}


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user settings",
    description=(
        "Return the authenticated user's application settings including LLM "
        "provider, timezone, and channel configuration."
    ),
)
async def get_settings(
    user_id: str = Depends(require_auth),
) -> SettingsResponse:
    """Return settings for the authenticated user.

    Parameters
    ----------
    user_id:
        Platform user ID extracted from the Bearer access token.

    Returns
    -------
    SettingsResponse:
        Current settings for the user.
    """
    data = _user_settings.get(user_id, _default_settings(user_id))
    return SettingsResponse(**data)


@router.patch(
    "",
    response_model=SettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update user settings",
    description=(
        "Partially update the authenticated user's settings.  Only supplied "
        "fields are changed; omitted fields retain their current values."
    ),
)
async def patch_settings(
    body: SettingsPatchRequest,
    user_id: str = Depends(require_auth),
) -> SettingsResponse:
    """Update settings for the authenticated user.

    Parameters
    ----------
    body:
        Partial settings update.
    user_id:
        Platform user ID extracted from the Bearer access token.

    Returns
    -------
    SettingsResponse:
        Updated settings.
    """
    current = _user_settings.get(user_id, _default_settings(user_id))
    if body.llm_provider is not None:
        current["llm_provider"] = body.llm_provider
    if body.timezone is not None:
        current["timezone"] = body.timezone
    _user_settings[user_id] = current
    logger.debug("settings: updated for user_id=%s", user_id)
    return SettingsResponse(**current)


@router.get(
    "/channels",
    response_model=list[ChannelStatus],
    status_code=status.HTTP_200_OK,
    summary="List configured channels",
    description=(
        "Return all channels configured for the authenticated user with their "
        "enabled/disabled status."
    ),
)
async def list_channels(
    user_id: str = Depends(require_auth),
) -> list[ChannelStatus]:
    """List channel configuration for the authenticated user.

    Parameters
    ----------
    user_id:
        Platform user ID extracted from the Bearer access token.

    Returns
    -------
    list[ChannelStatus]:
        Channel entries.  Empty list until channels are configured.
    """
    data = _user_settings.get(user_id, _default_settings(user_id))
    raw_channels: list[dict[str, Any]] = data.get("channels", [])
    return [ChannelStatus(**ch) for ch in raw_channels]
