"""graphclaw.api.admin.features — Feature flag and policy management endpoints.

Routes
------
GET  /app/v1/admin/features                    — get feature policy
PUT  /app/v1/admin/features                    — replace feature policy
GET  /app/v1/admin/features/channels           — get channel policy
PUT  /app/v1/admin/features/channels           — replace channel policy
GET  /app/v1/admin/features/mcp-allowlist      — get MCP allowlist
PUT  /app/v1/admin/features/mcp-allowlist      — replace MCP allowlist
GET  /app/v1/admin/features/marketplace        — get marketplace policy
PUT  /app/v1/admin/features/marketplace        — replace marketplace policy
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/features", tags=["admin-api"])

_FEATURE_POLICY_PATH = "admin/features/policy.json"
_CHANNEL_POLICY_PATH = "admin/features/channels.json"
_MCP_ALLOWLIST_PATH = "admin/features/mcp_allowlist.json"
_MARKETPLACE_POLICY_PATH = "admin/features/marketplace.json"


async def _load_json(path: str, storage_client: Any, default: Any = None) -> Any:
    try:
        raw = await storage_client.read(path)
        return json.loads(raw.decode())
    except FileNotFoundError:
        return default if default is not None else {}


async def _save_json(path: str, storage_client: Any, data: Any) -> None:
    await storage_client.write(path, json.dumps(data).encode(), content_type="application/json")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FeaturePolicy(BaseModel):
    """Org-level feature flags."""

    enable_agent_canvas: bool = True
    enable_mcp_integration: bool = True
    enable_skill_marketplace: bool = True
    enable_multi_channel: bool = True
    enable_a2a: bool = False
    extra: dict[str, Any] = {}


class ChannelPolicy(BaseModel):
    """Allowed channels and per-channel settings."""

    allowed_channels: list[str] = ["email", "slack", "teams", "whatsapp", "telegram"]
    max_channels_per_user: int = 5
    extra: dict[str, Any] = {}


class MCPAllowlist(BaseModel):
    """Allowlisted MCP server URIs and trust tiers."""

    allowed_servers: list[str] = []
    default_trust_tier: str = "GATED"
    allow_custom_servers: bool = True


class MarketplacePolicy(BaseModel):
    """Skill marketplace access policy."""

    enabled: bool = True
    allow_external_sources: bool = True
    require_approval_for_install: bool = False
    approved_sources: list[str] = []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "", response_model=FeaturePolicy, status_code=status.HTTP_200_OK, summary="Get feature policy"
)
async def get_features(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> FeaturePolicy:
    data = await _load_json(_FEATURE_POLICY_PATH, storage_client)
    return FeaturePolicy(**data) if data else FeaturePolicy()


@router.put(
    "",
    response_model=FeaturePolicy,
    status_code=status.HTTP_200_OK,
    summary="Replace feature policy",
)
async def put_features(
    body: FeaturePolicy, admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> FeaturePolicy:
    await _save_json(_FEATURE_POLICY_PATH, storage_client, body.model_dump())
    return body


@router.get(
    "/channels",
    response_model=ChannelPolicy,
    status_code=status.HTTP_200_OK,
    summary="Get channel policy",
)
async def get_channel_policy(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> ChannelPolicy:
    data = await _load_json(_CHANNEL_POLICY_PATH, storage_client)
    return ChannelPolicy(**data) if data else ChannelPolicy()


@router.put(
    "/channels",
    response_model=ChannelPolicy,
    status_code=status.HTTP_200_OK,
    summary="Replace channel policy",
)
async def put_channel_policy(
    body: ChannelPolicy, admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> ChannelPolicy:
    await _save_json(_CHANNEL_POLICY_PATH, storage_client, body.model_dump())
    return body


@router.get(
    "/mcp-allowlist",
    response_model=MCPAllowlist,
    status_code=status.HTTP_200_OK,
    summary="Get MCP allowlist",
)
async def get_mcp_allowlist(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> MCPAllowlist:
    data = await _load_json(_MCP_ALLOWLIST_PATH, storage_client)
    return MCPAllowlist(**data) if data else MCPAllowlist()


@router.put(
    "/mcp-allowlist",
    response_model=MCPAllowlist,
    status_code=status.HTTP_200_OK,
    summary="Replace MCP allowlist",
)
async def put_mcp_allowlist(
    body: MCPAllowlist, admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> MCPAllowlist:
    await _save_json(_MCP_ALLOWLIST_PATH, storage_client, body.model_dump())
    return body


@router.get(
    "/marketplace",
    response_model=MarketplacePolicy,
    status_code=status.HTTP_200_OK,
    summary="Get marketplace policy",
)
async def get_marketplace_policy(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> MarketplacePolicy:
    data = await _load_json(_MARKETPLACE_POLICY_PATH, storage_client)
    return MarketplacePolicy(**data) if data else MarketplacePolicy()


@router.put(
    "/marketplace",
    response_model=MarketplacePolicy,
    status_code=status.HTTP_200_OK,
    summary="Replace marketplace policy",
)
async def put_marketplace_policy(
    body: MarketplacePolicy, admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> MarketplacePolicy:
    await _save_json(_MARKETPLACE_POLICY_PATH, storage_client, body.model_dump())
    return body
