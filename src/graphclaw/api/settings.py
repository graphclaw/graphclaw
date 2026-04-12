"""graphclaw.api.settings — User settings endpoints.

Description
-----------
Provides ``GET /app/v1/settings`` and ``PATCH /app/v1/settings`` for reading
and updating per-user configuration (LLM provider, timezone, channel config),
``GET /app/v1/settings/channels`` for listing configured channels, plus extended
endpoints for profile, scoring-weights, organizations, and LLM keys.

All endpoints require a valid Bearer access token.

Design Patterns
---------------
- StorageClient persistence: Settings are stored as a JSON blob at a
  per-user path in object storage, replacing the previous in-memory stub.
- FileNotFoundError → defaults: When no config file exists for a user, a
  default settings dict is returned so the endpoint is always functional.
- Optimistic patch: ``PATCH`` loads current settings, applies supplied fields,
  and writes back — suitable for low-contention personal config updates.
- GraphStore profile: UserNode is read/updated via GraphStore for profile data.
- Secrets LLM keys: LLM API keys are stored in SecretsClient at a per-user,
  per-provider path; values are never returned to the client.

Public API
----------
- router: ``APIRouter`` for /settings routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, StorageClientDep, GraphStoreDep, SecretsClientDep.
- fastapi: APIRouter, HTTPException, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import (
    CurrentUserDep,
    GraphStoreDep,
    SecretsClientDep,
    StorageClientDep,
)
from graphclaw.infra.storage import StoragePaths
from graphclaw.models.base import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["app-api"])

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _settings_path(user_id: str) -> str:
    return StoragePaths.user_config(user_id)


def _default_settings(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "llm_provider": "litellm",
        "timezone": "UTC",
        "channels": [],
    }


async def _load_settings(user_id: str, storage_client) -> dict[str, Any]:
    """Read settings from storage; return defaults when not yet written."""
    try:
        raw = await storage_client.read(_settings_path(user_id))
        return json.loads(raw.decode())
    except FileNotFoundError:
        return _default_settings(user_id)


async def _save_settings(user_id: str, storage_client, data: dict[str, Any]) -> None:
    """Write settings JSON back to storage."""
    raw = json.dumps(data, default=str).encode()
    await storage_client.write(
        _settings_path(user_id),
        raw,
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> SettingsResponse:
    """Return settings for the authenticated user."""
    data = await _load_settings(user_id, storage_client)
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
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> SettingsResponse:
    """Update settings for the authenticated user."""
    current = await _load_settings(user_id, storage_client)
    if body.llm_provider is not None:
        current["llm_provider"] = body.llm_provider
    if body.timezone is not None:
        current["timezone"] = body.timezone
    await _save_settings(user_id, storage_client, current)
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
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> list[ChannelStatus]:
    """List channel configuration for the authenticated user."""
    data = await _load_settings(user_id, storage_client)
    raw_channels: list[dict[str, Any]] = data.get("channels", [])
    return [ChannelStatus(**ch) for ch in raw_channels]


# ---------------------------------------------------------------------------
# Profile routes — UserNode via GraphStore
# ---------------------------------------------------------------------------


class UserProfileResponse(BaseModel):
    """Response body for GET /settings/profile."""

    user_id: str
    name: str = ""
    email: str = ""
    role: str | None = None
    timezone: str = "UTC"


class UserProfilePatchRequest(BaseModel):
    """Request body for PATCH /settings/profile."""

    name: str | None = None
    timezone: str | None = None
    role: str | None = None


@router.get(
    "/profile",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user profile",
    description="Return the authenticated user's profile from the graph store.",
)
async def get_profile(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> UserProfileResponse:
    """Read the UserNode for the authenticated user."""
    node = await graph_store.get_node(user_id)
    if node is None:
        # Return a minimal profile if not yet provisioned
        return UserProfileResponse(user_id=user_id)
    return UserProfileResponse(
        user_id=user_id,
        name=node.get("name", ""),
        email=node.get("email", ""),
        role=node.get("role"),
        timezone=node.get("timezone", "UTC"),
    )


@router.patch(
    "/profile",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update user profile",
    description="Partially update the authenticated user's profile in the graph store.",
)
async def patch_profile(
    body: UserProfilePatchRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> UserProfileResponse:
    """Update fields on the UserNode for the authenticated user."""
    node = await graph_store.get_node(user_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )
    updates: dict[str, Any] = {"updated_at": utcnow().isoformat()}
    if body.name is not None:
        updates["name"] = body.name
    if body.timezone is not None:
        updates["timezone"] = body.timezone
    if body.role is not None:
        updates["role"] = body.role

    updated = await graph_store.update_node(user_id, updates)
    node = updated or node
    logger.debug("settings: profile updated for user_id=%s", user_id)
    return UserProfileResponse(
        user_id=user_id,
        name=node.get("name", ""),
        email=node.get("email", ""),
        role=node.get("role"),
        timezone=node.get("timezone", "UTC"),
    )


# ---------------------------------------------------------------------------
# Channel activate / deactivate routes
# ---------------------------------------------------------------------------


class ChannelActivateRequest(BaseModel):
    """Optional config payload when activating a channel."""

    config: dict[str, Any] = {}


@router.post(
    "/channels/{ch}/activate",
    response_model=ChannelStatus,
    status_code=status.HTTP_200_OK,
    summary="Activate a channel",
    description=(
        "Add or re-enable a channel for the authenticated user.  If the channel "
        "already exists it is updated to enabled=True."
    ),
)
async def activate_channel(
    ch: str,
    body: ChannelActivateRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> ChannelStatus:
    """Activate (or re-enable) a channel in the user's settings."""
    data = await _load_settings(user_id, storage_client)
    channels: list[dict[str, Any]] = data.get("channels", [])

    # Update existing entry or append new one
    existing = next((c for c in channels if c.get("channel") == ch), None)
    if existing is not None:
        existing["enabled"] = True
        existing["config"] = body.config
    else:
        channels.append({"channel": ch, "enabled": True, "config": body.config})
    data["channels"] = channels
    await _save_settings(user_id, storage_client, data)
    logger.debug("settings: channel '%s' activated for user_id=%s", ch, user_id)
    return ChannelStatus(channel=ch, enabled=True, config=body.config)


@router.delete(
    "/channels/{ch}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a channel",
    description=(
        "Disable a channel for the authenticated user.  The channel entry is "
        "retained with enabled=False so its configuration is preserved."
    ),
)
async def deactivate_channel(
    ch: str,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> None:
    """Disable a channel in the user's settings (soft delete)."""
    data = await _load_settings(user_id, storage_client)
    channels: list[dict[str, Any]] = data.get("channels", [])

    existing = next((c for c in channels if c.get("channel") == ch), None)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{ch}' not found",
        )
    existing["enabled"] = False
    data["channels"] = channels
    await _save_settings(user_id, storage_client, data)
    logger.debug("settings: channel '%s' deactivated for user_id=%s", ch, user_id)


# ---------------------------------------------------------------------------
# Scoring weights routes
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: dict[str, float] = {
    "W1_timeline": 0.25,
    "W2_dependencies": 0.20,
    "W3_critical_path": 0.20,
    "W4_blocker": 0.15,
    "W5_override": 0.10,
    "W6_resource_risk": 0.05,
    "W7_constraint": 0.05,
}


def _weights_path(user_id: str) -> str:
    return StoragePaths.user_scoring_weights(user_id)


async def _load_weights(user_id: str, storage_client: Any) -> dict[str, float]:
    try:
        raw = await storage_client.read(_weights_path(user_id))
        return json.loads(raw.decode())
    except FileNotFoundError:
        return dict(_DEFAULT_WEIGHTS)


async def _save_weights(user_id: str, storage_client: Any, data: dict[str, float]) -> None:
    raw = json.dumps(data, default=str).encode()
    await storage_client.write(_weights_path(user_id), raw, content_type="application/json")


class ScoringWeightsResponse(BaseModel):
    """Response body for GET /settings/scoring-weights."""

    W1_timeline: float = 0.25
    W2_dependencies: float = 0.20
    W3_critical_path: float = 0.20
    W4_blocker: float = 0.15
    W5_override: float = 0.10
    W6_resource_risk: float = 0.05
    W7_constraint: float = 0.05


class ScoringWeightsPatchRequest(BaseModel):
    """Request body for PATCH /settings/scoring-weights."""

    W1_timeline: float | None = None
    W2_dependencies: float | None = None
    W3_critical_path: float | None = None
    W4_blocker: float | None = None
    W5_override: float | None = None
    W6_resource_risk: float | None = None
    W7_constraint: float | None = None


@router.get(
    "/scoring-weights",
    response_model=ScoringWeightsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get scoring weights",
    description="Return the user's current 7-factor scoring weights (W1–W7).",
)
async def get_scoring_weights(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> ScoringWeightsResponse:
    """Read scoring weights from storage; return defaults when absent."""
    data = await _load_weights(user_id, storage_client)
    return ScoringWeightsResponse(**data)


@router.patch(
    "/scoring-weights",
    response_model=ScoringWeightsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update scoring weights",
    description=(
        "Partially update the user's scoring weights.  Only supplied factors are "
        "changed; omitted factors retain their current values."
    ),
)
async def patch_scoring_weights(
    body: ScoringWeightsPatchRequest,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> ScoringWeightsResponse:
    """Apply partial weight updates and persist."""
    current = await _load_weights(user_id, storage_client)
    for field in ScoringWeightsPatchRequest.model_fields:
        value = getattr(body, field, None)
        if value is not None:
            current[field] = value
    await _save_weights(user_id, storage_client, current)
    logger.debug("settings: scoring weights updated for user_id=%s", user_id)
    return ScoringWeightsResponse(**current)


# ---------------------------------------------------------------------------
# Organization routes — via GraphStore
# ---------------------------------------------------------------------------


class OrgResponse(BaseModel):
    """Response body for organization endpoints."""

    org_id: str
    name: str
    domain: str | None = None
    owner_id: str
    member_count: int = 0


class OrgCreateRequest(BaseModel):
    """Request body for POST /settings/organizations."""

    name: str
    domain: str | None = None


class OrgPatchRequest(BaseModel):
    """Request body for PATCH /settings/organizations/{id}."""

    name: str | None = None
    domain: str | None = None


def _org_to_response(node: dict[str, Any]) -> OrgResponse:
    members = node.get("members", [])
    return OrgResponse(
        org_id=node["id"],
        name=node.get("name", ""),
        domain=node.get("domain"),
        owner_id=node.get("owner_id", ""),
        member_count=len(members),
    )


@router.get(
    "/organizations",
    response_model=list[OrgResponse],
    status_code=status.HTTP_200_OK,
    summary="List organizations",
    description="Return all organizations the authenticated user is a member of.",
)
async def list_organizations(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> list[OrgResponse]:
    """List OrganizationNodes where the user is a member or owner."""
    try:
        all_orgs = await graph_store.list_nodes("OrganizationNode")
    except Exception as exc:
        logger.warning("settings: org list failed: %s", exc)
        return []

    result: list[OrgResponse] = []
    for node in all_orgs:
        owner_id = node.get("owner_id", "")
        raw_members = node.get("members", [])
        # AGE returns nested objects as JSON strings — parse them if needed
        parsed_members: list[dict] = []
        for m in raw_members:
            if isinstance(m, str):
                try:
                    import json as _json

                    m = _json.loads(m)
                except Exception:
                    continue
            if isinstance(m, dict):
                parsed_members.append(m)
        member_ids = {m.get("user_id") for m in parsed_members}
        if owner_id == user_id or user_id in member_ids:
            result.append(_org_to_response(node))
    return result


@router.post(
    "/organizations",
    response_model=OrgResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create organization",
    description="Create a new organization owned by the authenticated user.",
)
async def create_organization(
    body: OrgCreateRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> OrgResponse:
    """Create an OrganizationNode owned by the authenticated user."""
    from graphclaw.models.nodes import OrganizationNode

    now = utcnow()
    org_id = f"ORG-{uuid.uuid4().hex[:12]}"
    node = OrganizationNode(
        id=org_id,
        name=body.name,
        domain=body.domain,
        owner_id=user_id,
        members=[],
        created_at=now,
        updated_at=now,
        version=0,
    )
    await graph_store.create_node(node)
    logger.debug("settings: org created org_id=%s owner=%s", org_id, user_id)
    return OrgResponse(
        org_id=org_id,
        name=body.name,
        domain=body.domain,
        owner_id=user_id,
        member_count=0,
    )


@router.patch(
    "/organizations/{org_id}",
    response_model=OrgResponse,
    status_code=status.HTTP_200_OK,
    summary="Update organization",
    description="Update name or domain of an organization owned by the authenticated user.",
)
async def patch_organization(
    org_id: str,
    body: OrgPatchRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> OrgResponse:
    """Partially update an OrganizationNode the user owns."""
    node = await graph_store.get_node(org_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization '{org_id}' not found",
        )
    if node.get("owner_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the organization owner can update it",
        )
    updates: dict[str, Any] = {"updated_at": utcnow().isoformat()}
    if body.name is not None:
        updates["name"] = body.name
    if body.domain is not None:
        updates["domain"] = body.domain

    updated = await graph_store.update_node(org_id, updates)
    node = updated or node
    logger.debug("settings: org updated org_id=%s", org_id)
    return _org_to_response(node)


# ---------------------------------------------------------------------------
# LLM key routes — SecretsClient
# ---------------------------------------------------------------------------

_LLM_KEY_PATH_TEMPLATE = "graphclaw/{user_id}/llm/{provider}"


def _llm_key_path(user_id: str, provider: str) -> str:
    return _LLM_KEY_PATH_TEMPLATE.format(user_id=user_id, provider=provider)


class LLMKeyRequest(BaseModel):
    """Request body for POST /settings/llm-keys."""

    provider: str
    api_key: str


class LLMKeyResponse(BaseModel):
    """Response body confirming an LLM key was stored."""

    provider: str
    stored: bool = True


@router.post(
    "/llm-keys",
    response_model=LLMKeyResponse,
    status_code=status.HTTP_200_OK,
    summary="Store LLM API key",
    description=(
        "Store an LLM provider API key in the secrets backend.  The key value "
        "is never returned; only the provider name is echoed back."
    ),
)
async def store_llm_key(
    body: LLMKeyRequest,
    user_id: CurrentUserDep,
    secrets_client: SecretsClientDep,
) -> LLMKeyResponse:
    """Persist an LLM API key for the authenticated user."""
    key_path = _llm_key_path(user_id, body.provider)
    await secrets_client.set_secret(key_path, body.api_key)
    logger.debug("settings: LLM key stored for user_id=%s provider=%s", user_id, body.provider)
    return LLMKeyResponse(provider=body.provider, stored=True)


@router.delete(
    "/llm-keys/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete LLM API key",
    description="Remove a stored LLM provider API key for the authenticated user.",
)
async def delete_llm_key(
    provider: str,
    user_id: CurrentUserDep,
    secrets_client: SecretsClientDep,
) -> None:
    """Delete a stored LLM API key."""
    key_path = _llm_key_path(user_id, provider)
    try:
        await secrets_client.delete_secret(key_path)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No LLM key found for provider '{provider}'",
        )
    logger.debug("settings: LLM key deleted for user_id=%s provider=%s", user_id, provider)
