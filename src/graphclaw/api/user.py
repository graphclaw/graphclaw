# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.user — Authenticated user self-service endpoints.

Description
-----------
Provides user-facing REST endpoints that operate on the currently
authenticated user's profile and memberships.

Endpoints
---------
- ``GET  /app/v1/user/orgs``
    Return all organizations the authenticated user belongs to, either as
    owner or as a member.  Reads OrganizationNode vertices from the graph
    and filters in Python for membership.  (FR-UI-002)

Design Patterns
---------------
- Graceful degradation: if the graph has no OrganizationNodes, return an
  empty list rather than an error.
- System caller context is used for the read because this endpoint must
  traverse ALL org nodes — not just ones owned by the calling user.  The
  Python-level filter then enforces user-scoped membership.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, status

from graphclaw.api.deps import CurrentUserDep, GraphStoreDep
from graphclaw.cross_tenant.acl import system_caller_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["user"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


from pydantic import BaseModel  # noqa: E402


class OrgSummary(BaseModel):
    """Lightweight org record for the OrgSwitcher UI. (FR-UI-002)"""

    org_id: str
    name: str
    role: str  # "OWNER" | "ADMIN" | "MEMBER" | "GUEST"
    domain: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member_role(user_id: str, org_props: dict) -> str | None:
    """Return the user's role in this org, or None if not a member.

    Checks ``owner_id`` (→ OWNER) first, then the ``members`` JSON list.
    The ``members`` field may be stored as a JSON string (AGE agtype
    serialisation) or as a Python list already.
    """
    if org_props.get("owner_id") == user_id:
        return "OWNER"

    raw_members = org_props.get("members", [])
    if isinstance(raw_members, str):
        try:
            raw_members = json.loads(raw_members)
        except (json.JSONDecodeError, ValueError):
            raw_members = []

    if not isinstance(raw_members, list):
        return None

    for m in raw_members:
        if not isinstance(m, dict):
            continue
        if m.get("user_id") == user_id:
            status_val = m.get("status", "ACTIVE")
            if status_val == "ACTIVE":
                return str(m.get("role", "MEMBER"))
    return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/orgs",
    response_model=list[OrgSummary],
    status_code=status.HTTP_200_OK,
    summary="List user organisations",
    description=(
        "Return all organisations the authenticated user belongs to "
        "(as owner or active member). (FR-UI-002)"
    ),
)
async def list_user_orgs(
    user_id: CurrentUserDep,
    store: GraphStoreDep,
) -> list[OrgSummary]:
    """Return orgs where the authenticated user is owner or active member."""
    caller = system_caller_context(principal="agent_principal")
    try:
        all_orgs = await store.list_nodes("OrganizationNode", caller_context=caller)
    except Exception as exc:  # noqa: BLE001
        logger.warning("user/orgs: list_nodes failed: %s", exc)
        return []

    result: list[OrgSummary] = []
    for org in all_orgs:
        role = _member_role(user_id, org)
        if role is None:
            continue
        result.append(
            OrgSummary(
                org_id=str(org.get("id", "")),
                name=str(org.get("name", "")),
                role=role,
                domain=org.get("domain"),
            )
        )

    result.sort(key=lambda x: x.name.lower())
    return result
