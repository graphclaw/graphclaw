"""graphclaw.api.admin.org_lifecycle — Organization archive endpoints (FR-DEL-009).

Routes
------
POST /admin/org-lifecycle/archive         Archive an org (does NOT touch member UserNodes)
POST /admin/org-lifecycle/cancel-archive  Cancel a pending org archive

Design
------
Archiving an OrganizationNode:
  1. Sets archived_at / purge_after on the OrganizationNode.
  2. Sets archived_at on every WorkspaceNode under the org.
  3. Does NOT touch member UserNodes — they remain fully intact.
  4. Writes an immutable audit entry.

Per FR-DEL-009:
  AC1: Archiving ORG-X with members [USER-1, USER-2] leaves both UserNodes intact.
  AC2: Workspaces under ORG-X are archived but tasks remain readable to admin.

Design patterns
---------------
- Dependency Injection: AdminUserDep, AdminGraphStoreDep, StorageClientDep.
- Guard: organisation is verified to exist before archive.

Methods
-------
- archive_org(req, admin_user_id, store, storage) -> OrgArchiveResponse
- cancel_org_archive(req, admin_user_id, store, storage) -> OrgArchiveResponse

Dependencies
------------
- graphclaw.api.deps: AdminUserDep, AdminGraphStoreDep, StorageClientDep.
- graphclaw.audit.immutable_log: AuditLog, AuditEventType.
- graphclaw.models.base: utcnow.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminGraphStoreDep, AdminUserDep, StorageClientDep
from graphclaw.audit.immutable_log import AuditEventType, AuditLog
from graphclaw.models.base import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/org-lifecycle", tags=["admin-org-lifecycle"])

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class OrgArchiveRequest(BaseModel):
    """Request body for POST /admin/org-lifecycle/archive."""

    org_id: str
    reason: str = ""
    purge_after_hours: int = 24 * 7  # default 7-day grace period for orgs


class CancelOrgArchiveRequest(BaseModel):
    org_id: str


class OrgArchiveResponse(BaseModel):
    ok: bool = True
    org_id: str = ""
    workspaces_archived: int = 0
    members_untouched: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/archive",
    response_model=OrgArchiveResponse,
    summary="Archive an organization (FR-DEL-009)",
)
async def archive_org(
    req: OrgArchiveRequest,
    admin_user_id: AdminUserDep,
    store: AdminGraphStoreDep,
    storage: StorageClientDep,
) -> OrgArchiveResponse:
    """Archive an org and all its workspaces — member UserNodes remain intact.

    Acceptance criteria (FR-DEL-009):
    - AC1: Members [USER-1, USER-2] are NOT archived.
    - AC2: Workspaces are archived (tasks remain readable by admin).
    """
    org = await store.get_node(req.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found.")
    if getattr(org, "archived_at", None) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organisation is already archived.",
        )

    now = utcnow()
    purge_after = now + timedelta(hours=req.purge_after_hours)

    # Archive the org node.
    await store.update_node(
        req.org_id,
        {
            "archived_at": now,
            "archived_by": admin_user_id,
            "archive_reason": req.reason or "org_archive",
            "purge_after": purge_after,
            "updated_at": now,
        },
    )

    # Archive all workspaces under this org — do NOT touch UserNodes.
    workspaces_archived = 0
    try:
        workspaces = await store.list_nodes(
            "WorkspaceNode", filters={"org_id": req.org_id}
        )
        for ws in workspaces:
            ws_id = getattr(ws, "id", None)
            if ws_id and getattr(ws, "archived_at", None) is None:
                await store.update_node(
                    ws_id,
                    {
                        "archived_at": now,
                        "archived_by": admin_user_id,
                        "archive_reason": f"org_archive:{req.org_id}",
                        "purge_after": purge_after,
                        "updated_at": now,
                    },
                )
                workspaces_archived += 1
    except Exception:  # noqa: BLE001
        logger.exception("org_archive: error archiving workspaces for org=%s", req.org_id)

    # Count members (to confirm they are untouched).
    member_list = getattr(org, "members", []) or []
    members_untouched = len(member_list)

    # Immutable audit entry.
    audit = AuditLog(storage)
    await audit.record(
        AuditEventType.ORG_ARCHIVED,
        actor_id=admin_user_id,
        subject_id=req.org_id,
        metadata={
            "reason": req.reason,
            "workspaces_archived": workspaces_archived,
            "members_untouched": members_untouched,
        },
    )

    logger.info(
        "org_archive: archived org=%s by admin=%s workspaces=%d members_untouched=%d",
        req.org_id,
        admin_user_id,
        workspaces_archived,
        members_untouched,
    )
    return OrgArchiveResponse(
        org_id=req.org_id,
        workspaces_archived=workspaces_archived,
        members_untouched=members_untouched,
        message=f"Organisation {req.org_id} archived.",
    )


@router.post(
    "/cancel-archive",
    response_model=OrgArchiveResponse,
    summary="Cancel a pending org archive (FR-DEL-009)",
)
async def cancel_org_archive(
    req: CancelOrgArchiveRequest,
    admin_user_id: AdminUserDep,
    store: AdminGraphStoreDep,
    storage: StorageClientDep,
) -> OrgArchiveResponse:
    """Cancel a pending org archive by clearing archive fields."""
    org = await store.get_node(req.org_id, include_archived=True)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found.")
    if getattr(org, "archived_at", None) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organisation is not archived.",
        )

    now = utcnow()
    await store.update_node(
        req.org_id,
        {
            "archived_at": None,
            "archived_by": None,
            "archive_reason": None,
            "purge_after": None,
            "purge_cancelled_at": now,
            "updated_at": now,
        },
    )

    audit = AuditLog(storage)
    await audit.record(
        AuditEventType.ORG_ARCHIVE_CANCELLED,
        actor_id=admin_user_id,
        subject_id=req.org_id,
        metadata={"cancelled_by": admin_user_id},
    )

    logger.info("org_archive: cancelled for org=%s by admin=%s", req.org_id, admin_user_id)
    return OrgArchiveResponse(
        org_id=req.org_id,
        message=f"Organisation {req.org_id} archive cancelled.",
    )
