# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.reconciliation — Cross-tenant index rebuild endpoint (FR-AE-001).

Routes
------
POST /app/v1/admin/cross-tenant/rebuild — trigger a full org_task_index rebuild

All endpoints require ADMIN role.

Design Patterns
---------------
- Thin controller: delegates to OrgTaskIndexReconciler; returns summary.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, GraphStoreDep
from graphclaw.cross_tenant.reconciler import OrgTaskIndexReconciler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/cross-tenant", tags=["admin-api"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RebuildRequest(BaseModel):
    """Optional body for rebuild — scopes the reconciliation to one org."""

    org_id: str | None = None


class RebuildResponse(BaseModel):
    """Reconciliation run summary."""

    started_at: str
    finished_at: str | None
    tasks_scanned: int
    rows_upserted: int
    rows_unchanged: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/rebuild",
    response_model=RebuildResponse,
    status_code=status.HTTP_200_OK,
    summary="Rebuild org task index",
    description=(
        "Trigger a full or org-scoped sync of org_task_index against the AGE graph. "
        "Returns a reconciliation summary. (FR-AE-001)"
    ),
)
async def rebuild_org_task_index(
    body: RebuildRequest,
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> RebuildResponse:
    """Run a full reconciliation of the org_task_index."""
    # Retrieve OrgTaskIndex from app state; gracefully degrade when absent.
    task_index = getattr(request.app.state, "org_task_index", None)
    if task_index is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="org_task_index not available — check service configuration.",
        )

    reconciler = OrgTaskIndexReconciler(
        store=graph_store,
        task_index=task_index,
        org_id=body.org_id,
    )

    logger.info(
        "admin/reconciliation: rebuild triggered by %s org_id=%s",
        admin_user_id,
        body.org_id or "all",
    )

    try:
        result = await reconciler.run()
    except Exception as exc:  # noqa: BLE001
        logger.error("admin/reconciliation: run failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reconciliation failed: {exc}",
        ) from exc

    d: dict[str, Any] = result.to_dict()
    return RebuildResponse(
        started_at=d["started_at"],
        finished_at=d["finished_at"],
        tasks_scanned=d["tasks_scanned"],
        rows_upserted=d["rows_upserted"],
        rows_unchanged=d["rows_unchanged"],
        errors=d["errors"],
    )
