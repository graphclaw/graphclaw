# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.audit — Audit log query endpoint.

Routes
------
GET /app/v1/admin/audit-log — query the audit log with filters
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audit-log", tags=["admin-api"])

_AUDIT_LOG_PATH = "admin/audit/log.json"


async def _load_json(path: str, storage_client: Any, default: Any = None) -> Any:
    try:
        raw = await storage_client.read(path)
        return json.loads(raw.decode())
    except FileNotFoundError:
        return default if default is not None else []


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """A single audit log entry."""

    event_id: str
    actor_id: str
    action: str
    resource_type: str = ""
    resource_id: str = ""
    timestamp: str
    ip_address: str | None = None
    details: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[AuditEntry],
    status_code=status.HTTP_200_OK,
    summary="Query audit log",
    description=(
        "Return audit log entries with optional filters for actor, action, "
        "resource type, and time window.  Results are ordered most-recent first."
    ),
)
async def query_audit_log(
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
    actor_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditEntry]:
    """Return filtered audit log entries."""
    raw: list[dict] = await _load_json(_AUDIT_LOG_PATH, storage_client, default=[])

    entries = [AuditEntry(**e) for e in (raw if isinstance(raw, list) else [])]

    if actor_id:
        entries = [e for e in entries if e.actor_id == actor_id]
    if action:
        entries = [e for e in entries if e.action == action]
    if resource_type:
        entries = [e for e in entries if e.resource_type == resource_type]

    # Sort most-recent first
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries[:limit]
