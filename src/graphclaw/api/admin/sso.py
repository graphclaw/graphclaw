# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.sso — SSO/OIDC/SAML configuration endpoints.

Routes
------
GET   /app/v1/admin/sso          — get SSO configuration
PUT   /app/v1/admin/sso          — replace SSO configuration
POST  /app/v1/admin/sso/test     — test SSO connection
PATCH /app/v1/admin/sso/enforce  — toggle SSO enforcement
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/sso", tags=["admin-api"])

_SSO_CONFIG_PATH = "admin/sso/config.json"


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


class SSOConfig(BaseModel):
    """SSO/OIDC/SAML configuration."""

    provider: str = "none"  # none | google | microsoft | okta | saml | custom
    enabled: bool = False
    enforced: bool = False
    client_id: str = ""
    issuer_url: str = ""
    metadata_url: str = ""
    allowed_domains: list[str] = []
    extra: dict[str, Any] = {}


class SSOTestResponse(BaseModel):
    """Result of testing the SSO connection."""

    reachable: bool = False
    error: str | None = None
    provider: str = ""


class SSOEnforceRequest(BaseModel):
    """Request body for PATCH /admin/sso/enforce."""

    enforced: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=SSOConfig, status_code=status.HTTP_200_OK, summary="Get SSO config")
async def get_sso(admin_user_id: AdminUserDep, storage_client: StorageClientDep) -> SSOConfig:
    data = await _load_json(_SSO_CONFIG_PATH, storage_client)
    return SSOConfig(**data) if data else SSOConfig()


@router.put(
    "", response_model=SSOConfig, status_code=status.HTTP_200_OK, summary="Replace SSO config"
)
async def put_sso(
    body: SSOConfig, admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> SSOConfig:
    await _save_json(_SSO_CONFIG_PATH, storage_client, body.model_dump())
    return body


@router.post(
    "/test",
    response_model=SSOTestResponse,
    status_code=status.HTTP_200_OK,
    summary="Test SSO connection",
    description="Attempt to reach the SSO provider's discovery endpoint.",
)
async def test_sso(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> SSOTestResponse:
    """Test SSO connectivity by probing the issuer URL (graceful degradation)."""
    data = await _load_json(_SSO_CONFIG_PATH, storage_client)
    config = SSOConfig(**data) if data else SSOConfig()
    if not config.enabled or not config.issuer_url:
        return SSOTestResponse(
            reachable=False, error="SSO not configured", provider=config.provider
        )
    # In production this would probe config.issuer_url; here we return a stub.
    return SSOTestResponse(
        reachable=False, error="SSO probe not yet wired to live issuer", provider=config.provider
    )


@router.patch(
    "/enforce",
    response_model=SSOConfig,
    status_code=status.HTTP_200_OK,
    summary="Toggle SSO enforcement",
    description="Enable or disable mandatory SSO login for all org members.",
)
async def toggle_sso_enforce(
    body: SSOEnforceRequest,
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
) -> SSOConfig:
    data = await _load_json(_SSO_CONFIG_PATH, storage_client)
    config = SSOConfig(**data) if data else SSOConfig()
    config.enforced = body.enforced
    await _save_json(_SSO_CONFIG_PATH, storage_client, config.model_dump())
    return config
