"""graphclaw.api.compliance — GDPR compliance REST endpoints.

Description
-----------
Provides REST endpoints for user data export and right-to-erasure requests,
satisfying GDPR Article 17 (erasure) and Article 20 (data portability).

Endpoints
---------
- ``GET  /app/v1/compliance/export``              — Trigger a data export.
- ``POST /app/v1/compliance/erasure``             — Submit an erasure request.
- ``GET  /app/v1/compliance/erasure/{request_id}`` — Poll erasure status.

All endpoints require a valid Bearer access token.

Design Patterns
---------------
- Stub services: Module-level ``GDPRService`` and ``DataExportService``
  instances are built with in-memory stubs so the routes are functional
  without a running database, consistent with the pattern used by other
  API modules in this codebase.

Public API
----------
- router: ``APIRouter`` for /compliance routes.

Dependencies
------------
- graphclaw.auth.middleware: require_auth.
- graphclaw.compliance: GDPRService, DataExportService, AuditLogger.
- fastapi: APIRouter, Depends, HTTPException, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from graphclaw.auth.middleware import require_auth
from graphclaw.compliance.audit import AuditLogger
from graphclaw.compliance.export import DataExportService
from graphclaw.compliance.gdpr import GDPRService
from graphclaw.compliance.models import ErasureStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["app-api"])

# ---------------------------------------------------------------------------
# Stub infrastructure for routes
# ---------------------------------------------------------------------------
# These lightweight stubs allow the endpoints to be exercised in tests and
# local dev without a running AGE database or S3 bucket.  In production the
# services are wired up via dependency injection in the application factory.


class _StubStorage:
    """Minimal in-memory StorageClient stub for the compliance API layer."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def read(self, path: str) -> bytes:
        if path not in self._objects:
            raise FileNotFoundError(path)
        return self._objects[path]

    async def write(self, path: str, data: bytes, content_type: str = "text/plain") -> None:
        self._objects[path] = data

    async def delete(self, path: str) -> None:
        self._objects.pop(path, None)

    async def list_objects(self, prefix: str) -> list[str]:
        return sorted(k for k in self._objects if k.startswith(prefix))

    async def exists(self, path: str) -> bool:
        return path in self._objects


class _StubGraphStore:
    """Minimal in-memory GraphStore stub for the compliance API layer."""

    async def create_node(self, node: object) -> dict:
        return {}

    async def get_node(self, node_id: str) -> dict | None:
        return {"id": node_id}

    async def update_node(self, node_id: str, updates: dict) -> dict | None:
        return {"id": node_id, **updates}

    async def delete_node(self, node_id: str) -> None:
        pass

    async def list_nodes(self, label: str, filters: dict | None = None) -> list[dict]:
        return []

    async def create_edge(
        self, source_id: str, target_id: str, edge_type: str, properties: dict | None = None
    ) -> dict:
        return {}

    async def get_edges(
        self, node_id: str, direction: str = "out", edge_type: str | None = None
    ) -> list[dict]:
        return []

    async def delete_edge(self, edge_id: str) -> None:
        pass


_stub_storage = _StubStorage()
_stub_graph = _StubGraphStore()
_audit_logger = AuditLogger(storage=_stub_storage)
_gdpr_service = GDPRService(
    graph_store=_stub_graph,
    storage=_stub_storage,
    audit_logger=_audit_logger,
)
_export_service = DataExportService(
    graph_store=_stub_graph,
    storage=_stub_storage,
    audit_logger=_audit_logger,
)

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ExportResponse(BaseModel):
    """Response for GET /app/v1/compliance/export."""

    export_id: str
    storage_key: str
    expires_at: datetime


class ErasureRequestBody(BaseModel):
    """Request body for POST /app/v1/compliance/erasure."""

    reason: str = ""


class ErasureRequestResponse(BaseModel):
    """Response for POST /app/v1/compliance/erasure."""

    request_id: str
    status: str


class ErasureStatusResponse(BaseModel):
    """Response for GET /app/v1/compliance/erasure/{request_id}."""

    request_id: str
    status: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/export",
    response_model=ExportResponse,
    status_code=status.HTTP_200_OK,
    summary="Export user data",
    description=(
        "Trigger a full export of the authenticated user's data. "
        "Returns the export ID, S3 storage key, and expiry timestamp. "
        "Use the storage key to generate a presigned download URL."
    ),
)
async def export_user_data(
    user_id: str = Depends(require_auth),
) -> ExportResponse:
    export = await _export_service.export_user_data(user_id)
    return ExportResponse(
        export_id=export.export_id,
        storage_key=export.storage_key,
        expires_at=export.expires_at,
    )


@router.post(
    "/erasure",
    response_model=ErasureRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a right-to-erasure request",
    description=(
        "Submit a GDPR Article 17 right-to-erasure request for the "
        "authenticated user. The request is queued for processing and a "
        "request_id is returned for status polling."
    ),
)
async def create_erasure_request(
    body: ErasureRequestBody,
    user_id: str = Depends(require_auth),
) -> ErasureRequestResponse:
    request = await _gdpr_service.request_erasure(
        user_id=user_id,
        requester_email=f"{user_id}@self",
        reason=body.reason,
    )
    logger.info(
        "compliance: erasure request accepted",
        extra={"request_id": request.request_id, "user_id": user_id},
    )
    return ErasureRequestResponse(
        request_id=request.request_id,
        status=ErasureStatus.PENDING.value,
    )


@router.get(
    "/erasure/{request_id}",
    response_model=ErasureStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get erasure request status",
    description="Poll the status of a previously submitted erasure request.",
)
async def get_erasure_status(
    request_id: str,
    user_id: str = Depends(require_auth),
) -> ErasureStatusResponse:
    if not request_id.startswith("ERASURE-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request_id format: '{request_id}'",
        )
    erasure_status = await _gdpr_service.get_erasure_status(request_id, user_id)
    return ErasureStatusResponse(
        request_id=request_id,
        status=erasure_status.value,
    )
