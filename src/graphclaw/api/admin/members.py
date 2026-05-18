# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.members — Organization member management endpoints.

Routes
------
GET    /app/v1/admin/members           — list all org members
POST   /app/v1/admin/members/invite    — invite a new member
PATCH  /app/v1/admin/members/{id}      — update member role/status
DELETE /app/v1/admin/members/{id}      — remove a member

All endpoints require ADMIN or OWNER role.  Member records are stored on the
``OrganizationNode`` in the graph store.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, BrokerDep, GraphStoreDep
from graphclaw.infra.broker import MEMBERSHIP_EVENTS
from graphclaw.models.base import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/members", tags=["admin-api"])

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class MemberOut(BaseModel):
    """An organization member entry."""

    user_id: str
    role: str = "MEMBER"
    member_status: str = "ACTIVE"
    email: str = ""
    joined_at: str | None = None


class InviteRequest(BaseModel):
    """Request body for POST /admin/members/invite."""

    email: str
    role: str = "MEMBER"


class MemberPatchRequest(BaseModel):
    """Request body for PATCH /admin/members/{id}."""

    role: str | None = None
    member_status: str | None = None


# ---------------------------------------------------------------------------
# Helper — resolve org for admin user
# ---------------------------------------------------------------------------


async def _get_admin_org(admin_user_id: str, graph_store: Any) -> dict[str, Any] | None:
    """Return the first org owned by or where admin is an ADMIN/OWNER member."""
    try:
        orgs = await graph_store.list_nodes("OrganizationNode")
    except Exception:
        return None
    for org in orgs:
        if org.get("owner_id") == admin_user_id:
            return org
        for m in org.get("members", []):
            if m.get("user_id") == admin_user_id and m.get("role") in ("ADMIN", "OWNER"):
                return org
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[MemberOut],
    status_code=status.HTTP_200_OK,
    summary="List org members",
    description="Return all members of the admin user's organization.",
)
async def list_members(
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
    role: str | None = Query(default=None, description="Filter by role"),
) -> list[MemberOut]:
    org = await _get_admin_org(admin_user_id, graph_store)
    if org is None:
        return []
    members: list[dict] = org.get("members", [])
    if role:
        members = [m for m in members if m.get("role") == role]
    return [
        MemberOut(
            user_id=m.get("user_id", ""),
            role=m.get("role", "MEMBER"),
            member_status=m.get("status", "ACTIVE"),
            email=m.get("email", ""),
            joined_at=str(m["joined_at"]) if m.get("joined_at") else None,
        )
        for m in members
    ]


@router.post(
    "/invite",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a member",
    description="Send an invitation and add the member record to the organization.",
)
async def invite_member(
    body: InviteRequest,
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
    broker: BrokerDep,
) -> MemberOut:
    org = await _get_admin_org(admin_user_id, graph_store)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No organization found for this admin",
        )
    # Build new member record (user_id derived from email for now)
    new_member: dict[str, Any] = {
        "user_id": f"USER-invited-{body.email.split('@')[0][:12]}",
        "email": body.email,
        "role": body.role,
        "status": "INVITED",
        "joined_at": utcnow().isoformat(),
    }
    members: list[dict] = list(org.get("members", []))
    members.append(new_member)
    await graph_store.update_node(org["id"], {"members": members})
    logger.debug("admin/members: invited %s to org %s", body.email, org["id"])
    # Publish membership event for directory indexer sync (FR-DIR-001)
    if broker is not None:
        import json as _json  # noqa: PLC0415

        try:
            await broker.publish(
                MEMBERSHIP_EVENTS,
                _json.dumps(
                    {
                        "event": "member_added",
                        "user_id": new_member["user_id"],
                        "org_id": org["id"],
                    }
                ),
            )
        except Exception as _exc:  # noqa: BLE001
            logger.debug("admin/members: MEMBERSHIP_EVENTS publish failed: %s", _exc)
    return MemberOut(
        user_id=new_member["user_id"],
        role=new_member["role"],
        member_status="INVITED",
        email=body.email,
        joined_at=new_member["joined_at"],
    )


@router.patch(
    "/{member_id}",
    response_model=MemberOut,
    status_code=status.HTTP_200_OK,
    summary="Update member",
    description="Update a member's role or status.",
)
async def update_member(
    member_id: str,
    body: MemberPatchRequest,
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
) -> MemberOut:
    org = await _get_admin_org(admin_user_id, graph_store)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    members: list[dict] = list(org.get("members", []))
    target = next((m for m in members if m.get("user_id") == member_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Member '{member_id}' not found",
        )
    if body.role is not None:
        target["role"] = body.role
    if body.member_status is not None:
        target["status"] = body.member_status
    await graph_store.update_node(org["id"], {"members": members})
    return MemberOut(
        user_id=target["user_id"],
        role=target.get("role", "MEMBER"),
        member_status=target.get("status", "ACTIVE"),
        email=target.get("email", ""),
        joined_at=str(target["joined_at"]) if target.get("joined_at") else None,
    )


@router.delete(
    "/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove member",
    description="Remove a member from the organization.",
)
async def remove_member(
    member_id: str,
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
    broker: BrokerDep,
) -> None:
    org = await _get_admin_org(admin_user_id, graph_store)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    members: list[dict] = list(org.get("members", []))
    updated = [m for m in members if m.get("user_id") != member_id]
    if len(updated) == len(members):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Member '{member_id}' not found",
        )
    await graph_store.update_node(org["id"], {"members": updated})
    logger.debug("admin/members: removed %s from org %s", member_id, org["id"])
    # Publish membership event for directory indexer sync (FR-DIR-001)
    if broker is not None:
        import json as _json  # noqa: PLC0415

        try:
            await broker.publish(
                MEMBERSHIP_EVENTS,
                _json.dumps(
                    {
                        "event": "member_removed",
                        "user_id": member_id,
                        "org_id": org["id"],
                    }
                ),
            )
        except Exception as _exc:  # noqa: BLE001
            logger.debug("admin/members: MEMBERSHIP_EVENTS publish failed: %s", _exc)
