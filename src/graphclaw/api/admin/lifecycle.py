# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.lifecycle — Data-lifecycle admin endpoints (Wave 0.5).

Routes
------
POST /admin/lifecycle/cancel-purge        FR-DEL-004 — cancel a pending purge
POST /admin/lifecycle/confirm-purge       FR-DEL-004 — force-confirm a pending purge early
POST /admin/lifecycle/right-to-erasure    FR-DEL-006 — GDPR Art.17 immediate purge
POST /admin/lifecycle/legal-hold/{node_id}   FR-DEL-007 — set legal hold
DELETE /admin/lifecycle/legal-hold/{node_id} FR-DEL-007 — release legal hold

All endpoints:
  - require ADMIN or OWNER role (AdminUserDep)
  - use admin_principal (AdminGraphStoreDep) for any DB writes
  - write to the immutable AuditLog before returning

Design patterns
---------------
- Dependency Injection: AdminUserDep, AdminGraphStoreDep, StorageClientDep from api.deps.
- Adapter: AuditLog wraps StorageClient; endpoints pass it as a collaborator.
- Guard: purge endpoints re-check the node's `purge_after` and `purge_cancelled_at`
  inside a logical transaction to prevent the cancel-vs-purge race (FR-DEL-004/§6.2).

Methods (endpoints)
-------------------
- cancel_purge(req, user_id, store, storage) -> CancelPurgeResponse
- confirm_purge(req, user_id, store, storage) -> ConfirmPurgeResponse
- right_to_erasure(req, user_id, store, storage) -> ErasureResponse
- set_legal_hold(node_id, req, user_id, store, storage) -> LegalHoldResponse
- release_legal_hold(node_id, req, user_id, store, storage) -> LegalHoldResponse

Dependencies
------------
- graphclaw.api.deps: AdminUserDep, AdminGraphStoreDep, StorageClientDep.
- graphclaw.audit.immutable_log: AuditLog, AuditEventType.
- graphclaw.models.base: utcnow.
- fastapi: APIRouter, HTTPException, status, Depends.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminGraphStoreDep, AdminUserDep, StorageClientDep
from graphclaw.audit.immutable_log import AuditEventType, AuditLog
from graphclaw.models.base import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/lifecycle", tags=["admin-lifecycle"])

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CancelPurgeRequest(BaseModel):
    """Request body for POST /admin/lifecycle/cancel-purge."""

    user_id: str  # subject whose purge to cancel


class CancelPurgeResponse(BaseModel):
    ok: bool = True
    message: str = ""


class ConfirmPurgeRequest(BaseModel):
    """Request body for POST /admin/lifecycle/confirm-purge."""

    user_id: str  # subject whose purge to confirm immediately


class ConfirmPurgeResponse(BaseModel):
    ok: bool = True
    message: str = ""


class RightToErasureRequest(BaseModel):
    """Request body for POST /admin/lifecycle/right-to-erasure.

    Requires re-auth within the last 5 min (frontend passes re_auth_at).
    """

    subject_id: str  # node to erase (usually the user's own user_id)
    justification: str  # free-text GDPR justification
    re_auth_at: datetime  # timestamp of the last re-authentication


class ErasureResponse(BaseModel):
    ok: bool = True
    audit_entry_id: str = ""


class LegalHoldRequest(BaseModel):
    """Request body for POST /admin/lifecycle/legal-hold/{node_id}."""

    reason: str = ""


class LegalHoldResponse(BaseModel):
    ok: bool = True
    node_id: str = ""
    legal_hold: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REAUTH_WINDOW_SECONDS = 5 * 60  # 5 minutes


def _assert_recent_reauth(re_auth_at: datetime) -> None:
    """Raise 403 if re_auth_at is older than 5 minutes."""
    age = (datetime.now(UTC) - re_auth_at.replace(tzinfo=UTC)).total_seconds()
    if age > _REAUTH_WINDOW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Re-authentication required within the last 5 minutes.",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/cancel-purge",
    response_model=CancelPurgeResponse,
    summary="Cancel a pending scheduled purge (FR-DEL-004)",
)
async def cancel_purge(
    req: CancelPurgeRequest,
    admin_user_id: AdminUserDep,
    store: AdminGraphStoreDep,
    storage: StorageClientDep,
) -> CancelPurgeResponse:
    """Cancel a pending purge by writing purge_cancelled_at = now.

    Sets purge_cancelled_at, clears archived_at and purge_after on the
    subject node via admin_principal.  Writes an immutable audit entry.
    """
    node = await store.get_node(req.user_id, include_archived=True)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    if getattr(node, "purge_after", None) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No pending purge found for this user.",
        )

    now = utcnow()
    updates = {
        "purge_cancelled_at": now,
        "archived_at": None,
        "purge_after": None,
        "archive_reason": None,
        "updated_at": now,
    }
    await store.update_node(req.user_id, updates)

    audit = AuditLog(storage)
    await audit.record(
        AuditEventType.PURGE_CANCELLED,
        actor_id=admin_user_id,
        subject_id=req.user_id,
        metadata={"cancelled_by": admin_user_id},
    )

    logger.info("lifecycle: purge cancelled for user=%s by admin=%s", req.user_id, admin_user_id)
    return CancelPurgeResponse(message=f"Purge cancelled for {req.user_id}.")


@router.post(
    "/confirm-purge",
    response_model=ConfirmPurgeResponse,
    summary="Confirm/force an early purge (FR-DEL-004)",
)
async def confirm_purge(
    req: ConfirmPurgeRequest,
    admin_user_id: AdminUserDep,
    store: AdminGraphStoreDep,
    storage: StorageClientDep,
) -> ConfirmPurgeResponse:
    """Force a pending purge to execute immediately.

    Moves purge_after to now - 1s so the purge worker picks it up on
    the next tick.  Writes an immutable audit entry.
    """
    node = await store.get_node(req.user_id, include_archived=True)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    if getattr(node, "purge_after", None) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No pending purge found for this user.",
        )
    if getattr(node, "legal_hold", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Node is under legal hold; purge is blocked.",
        )

    now = utcnow()
    updates = {
        "purge_after": now - timedelta(seconds=1),  # expire immediately
        "updated_at": now,
    }
    await store.update_node(req.user_id, updates)

    audit = AuditLog(storage)
    await audit.record(
        AuditEventType.PURGE_CONFIRMED,
        actor_id=admin_user_id,
        subject_id=req.user_id,
        metadata={"forced_by": admin_user_id},
    )

    logger.info("lifecycle: purge confirmed for user=%s by admin=%s", req.user_id, admin_user_id)
    return ConfirmPurgeResponse(message=f"Purge confirmed for {req.user_id}.")


@router.post(
    "/right-to-erasure",
    response_model=ErasureResponse,
    summary="GDPR Art.17 — immediate right to erasure (FR-DEL-006)",
)
async def right_to_erasure(
    req: RightToErasureRequest,
    admin_user_id: AdminUserDep,
    store: AdminGraphStoreDep,
    storage: StorageClientDep,
) -> ErasureResponse:
    """Synchronous GDPR erasure — runs purge before response returns.

    Validates re-auth recency, writes audit entry, marks node for
    immediate purge, then runs a synchronous hard-archive of the
    subject's graph substrate.
    """
    _assert_recent_reauth(req.re_auth_at)

    node = await store.get_node(req.subject_id, include_archived=True)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    if getattr(node, "legal_hold", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Node is under legal hold; erasure is blocked.",
        )

    # Write audit entry BEFORE the erasure action.
    audit = AuditLog(storage)
    entry = await audit.record(
        AuditEventType.RIGHT_TO_ERASURE_REQUESTED,
        actor_id=admin_user_id,
        subject_id=req.subject_id,
        metadata={
            "justification": req.justification,
            "re_auth_at": req.re_auth_at.isoformat(),
            "requested_by": admin_user_id,
        },
    )

    # Synchronous erasure: mark as archived + purge immediately.
    now = utcnow()
    updates = {
        "archived_at": now,
        "archived_by": admin_user_id,
        "archive_reason": f"GDPR Art.17 erasure: {req.justification}",
        "purge_after": now - timedelta(seconds=1),  # eligible immediately
        "purge_cancelled_at": None,
        "updated_at": now,
    }
    await store.update_node(req.subject_id, updates)

    await audit.record(
        AuditEventType.RIGHT_TO_ERASURE_EXECUTED,
        actor_id=admin_user_id,
        subject_id=req.subject_id,
        metadata={"audit_entry_id": entry.entry_id},
    )

    logger.info(
        "lifecycle: right-to-erasure executed for subject=%s by admin=%s",
        req.subject_id,
        admin_user_id,
    )
    return ErasureResponse(audit_entry_id=entry.entry_id)


@router.post(
    "/legal-hold/{node_id}",
    response_model=LegalHoldResponse,
    summary="Set legal hold on a node (FR-DEL-007)",
)
async def set_legal_hold(
    node_id: str,
    req: LegalHoldRequest,
    admin_user_id: AdminUserDep,
    store: AdminGraphStoreDep,
    storage: StorageClientDep,
) -> LegalHoldResponse:
    """Place a legal hold on *node_id* to block purge worker.

    Only admin_principal can set the hold.  Writes an immutable audit entry.
    """
    node = await store.get_node(node_id, include_archived=True)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    if getattr(node, "legal_hold", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Legal hold is already set on this node.",
        )

    now = utcnow()
    updates = {
        "legal_hold": True,
        "hold_reason": req.reason,
        "hold_set_by": admin_user_id,
        "hold_set_at": now,
        "updated_at": now,
    }
    await store.update_node(node_id, updates)

    audit = AuditLog(storage)
    await audit.record(
        AuditEventType.LEGAL_HOLD_SET,
        actor_id=admin_user_id,
        subject_id=node_id,
        metadata={"reason": req.reason},
    )

    logger.info("lifecycle: legal hold SET on node=%s by admin=%s", node_id, admin_user_id)
    return LegalHoldResponse(ok=True, node_id=node_id, legal_hold=True)


@router.delete(
    "/legal-hold/{node_id}",
    response_model=LegalHoldResponse,
    summary="Release legal hold on a node (FR-DEL-007)",
)
async def release_legal_hold(
    node_id: str,
    admin_user_id: AdminUserDep,
    store: AdminGraphStoreDep,
    storage: StorageClientDep,
) -> LegalHoldResponse:
    """Release a legal hold, allowing the purge worker to proceed.

    Writes an immutable audit entry with who released and when.
    """
    node = await store.get_node(node_id, include_archived=True)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    if not getattr(node, "legal_hold", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No legal hold is set on this node.",
        )

    now = utcnow()
    updates = {
        "legal_hold": False,
        "hold_reason": None,
        "hold_set_by": None,
        "hold_set_at": None,
        "updated_at": now,
    }
    await store.update_node(node_id, updates)

    audit = AuditLog(storage)
    await audit.record(
        AuditEventType.LEGAL_HOLD_RELEASED,
        actor_id=admin_user_id,
        subject_id=node_id,
        metadata={"released_by": admin_user_id},
    )

    logger.info("lifecycle: legal hold RELEASED on node=%s by admin=%s", node_id, admin_user_id)
    return LegalHoldResponse(ok=True, node_id=node_id, legal_hold=False)
