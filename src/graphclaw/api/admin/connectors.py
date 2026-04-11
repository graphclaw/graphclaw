"""graphclaw.api.admin.connectors — External connector management endpoints.

Routes
------
GET  /app/v1/admin/connectors              — list configured connectors
POST /app/v1/admin/connectors              — add a connector
POST /app/v1/admin/connectors/{id}/sync    — trigger a manual sync
GET  /app/v1/admin/connectors/{id}/health  — check connector health
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, StorageClientDep
from graphclaw.models.base import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/connectors", tags=["admin-api"])

_CONNECTORS_PATH = "admin/connectors/registry.json"


async def _load_connectors(storage_client: Any) -> list[dict]:
    try:
        raw = await storage_client.read(_CONNECTORS_PATH)
        data = json.loads(raw.decode())
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []


async def _save_connectors(storage_client: Any, data: list[dict]) -> None:
    await storage_client.write(
        _CONNECTORS_PATH, json.dumps(data, default=str).encode(), content_type="application/json"
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ConnectorOut(BaseModel):
    """A registered external connector."""

    connector_id: str
    name: str
    type: str  # jira | asana | notion | google_calendar | outlook | custom
    enabled: bool = True
    last_synced_at: str | None = None
    health: str = "unknown"
    config: dict[str, Any] = {}


class ConnectorCreateRequest(BaseModel):
    """Request body for POST /admin/connectors."""

    name: str
    type: str
    config: dict[str, Any] = {}


class SyncResponse(BaseModel):
    """Response from triggering a manual sync."""

    connector_id: str
    sync_id: str
    status: str = "triggered"
    triggered_at: str


class ConnectorHealthResponse(BaseModel):
    """Health check response for a connector."""

    connector_id: str
    reachable: bool = False
    last_synced_at: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[ConnectorOut],
    status_code=status.HTTP_200_OK,
    summary="List connectors",
)
async def list_connectors(admin_user_id: AdminUserDep, storage_client: StorageClientDep) -> list[ConnectorOut]:
    data = await _load_connectors(storage_client)
    return [ConnectorOut(**c) for c in data]


@router.post(
    "",
    response_model=ConnectorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add connector",
)
async def create_connector(
    body: ConnectorCreateRequest,
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
) -> ConnectorOut:
    connectors = await _load_connectors(storage_client)
    connector_id = f"CONN-{uuid.uuid4().hex[:12]}"
    now = utcnow().isoformat()
    record: dict[str, Any] = {
        "connector_id": connector_id,
        "name": body.name,
        "type": body.type,
        "enabled": True,
        "last_synced_at": None,
        "health": "unknown",
        "config": body.config,
        "created_at": now,
    }
    connectors.append(record)
    await _save_connectors(storage_client, connectors)
    logger.debug("admin/connectors: created %s type=%s", connector_id, body.type)
    return ConnectorOut(**record)


@router.post(
    "/{connector_id}/sync",
    response_model=SyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger connector sync",
)
async def sync_connector(
    connector_id: str,
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
) -> SyncResponse:
    connectors = await _load_connectors(storage_client)
    target = next((c for c in connectors if c.get("connector_id") == connector_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector '{connector_id}' not found",
        )
    now = utcnow().isoformat()
    target["last_synced_at"] = now
    await _save_connectors(storage_client, connectors)
    return SyncResponse(
        connector_id=connector_id,
        sync_id=f"SYNC-{uuid.uuid4().hex[:8]}",
        status="triggered",
        triggered_at=now,
    )


@router.get(
    "/{connector_id}/health",
    response_model=ConnectorHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Connector health",
)
async def get_connector_health(
    connector_id: str,
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
) -> ConnectorHealthResponse:
    connectors = await _load_connectors(storage_client)
    target = next((c for c in connectors if c.get("connector_id") == connector_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector '{connector_id}' not found",
        )
    return ConnectorHealthResponse(
        connector_id=connector_id,
        reachable=False,
        last_synced_at=target.get("last_synced_at"),
        error="Health probe not yet wired to live connector",
    )
