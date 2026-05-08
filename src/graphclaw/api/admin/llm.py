# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.llm — LLM provider, key, and budget management endpoints.

Routes
------
GET  /app/v1/admin/llm/providers    — list configured LLM providers
PUT  /app/v1/admin/llm/providers    — update provider config
POST /app/v1/admin/llm/keys         — store an org-level LLM API key
DELETE /app/v1/admin/llm/keys/{provider} — remove an org-level LLM API key
GET  /app/v1/admin/llm/budget       — get current budget config
PUT  /app/v1/admin/llm/budget       — update budget config
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, SecretsClientDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/llm", tags=["admin-api"])

_PROVIDERS_PATH = "admin/llm/providers.json"
_BUDGET_PATH = "admin/llm/budget.json"
_LLM_KEY_PREFIX = "graphclaw/org/llm/"


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


class LLMProviderConfig(BaseModel):
    """Configuration for a single LLM provider."""

    provider: str
    model: str = "default"
    enabled: bool = True
    priority: int = 0
    extra: dict[str, Any] = {}


class LLMProvidersConfig(BaseModel):
    """Org-level LLM provider configuration."""

    providers: list[LLMProviderConfig] = []
    default_provider: str = "anthropic"


class LLMKeyRequest(BaseModel):
    """Request body for storing an org-level LLM API key."""

    provider: str
    api_key: str


class LLMKeyResponse(BaseModel):
    """Response confirming an org-level LLM key was stored."""

    provider: str
    stored: bool = True


class BudgetConfig(BaseModel):
    """Org-level LLM spending budget configuration."""

    daily_limit_usd: float = 100.0
    monthly_limit_usd: float = 2000.0
    alert_threshold_pct: float = 0.8
    cost_anomaly_sigma: float = 3.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/providers",
    response_model=LLMProvidersConfig,
    status_code=status.HTTP_200_OK,
    summary="Get LLM providers",
)
async def get_providers(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> LLMProvidersConfig:
    data = await _load_json(_PROVIDERS_PATH, storage_client)
    return LLMProvidersConfig(**data) if data else LLMProvidersConfig()


@router.put(
    "/providers",
    response_model=LLMProvidersConfig,
    status_code=status.HTTP_200_OK,
    summary="Update LLM providers",
)
async def put_providers(
    body: LLMProvidersConfig, admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> LLMProvidersConfig:
    await _save_json(_PROVIDERS_PATH, storage_client, body.model_dump())
    return body


@router.post(
    "/keys",
    response_model=LLMKeyResponse,
    status_code=status.HTTP_200_OK,
    summary="Store org LLM key",
)
async def store_llm_key(
    body: LLMKeyRequest, admin_user_id: AdminUserDep, secrets_client: SecretsClientDep
) -> LLMKeyResponse:
    await secrets_client.set_secret(f"{_LLM_KEY_PREFIX}{body.provider}", body.api_key)
    logger.debug("admin/llm: stored key for provider=%s", body.provider)
    return LLMKeyResponse(provider=body.provider)


@router.delete(
    "/keys/{provider}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete org LLM key"
)
async def delete_llm_key(
    provider: str, admin_user_id: AdminUserDep, secrets_client: SecretsClientDep
) -> None:
    try:
        await secrets_client.delete_secret(f"{_LLM_KEY_PREFIX}{provider}")
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No key for provider '{provider}'"
        )


@router.get(
    "/budget",
    response_model=BudgetConfig,
    status_code=status.HTTP_200_OK,
    summary="Get budget config",
)
async def get_budget(admin_user_id: AdminUserDep, storage_client: StorageClientDep) -> BudgetConfig:
    data = await _load_json(_BUDGET_PATH, storage_client)
    return BudgetConfig(**data) if data else BudgetConfig()


@router.put(
    "/budget",
    response_model=BudgetConfig,
    status_code=status.HTTP_200_OK,
    summary="Update budget config",
)
async def put_budget(
    body: BudgetConfig, admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> BudgetConfig:
    await _save_json(_BUDGET_PATH, storage_client, body.model_dump())
    return body
