# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.cross_tenant.acl — Caller context model for repo-layer ACL (FR-AL-001).

Description
-----------
Provides ``CallerContext``, a lightweight Pydantic model that every public
``GraphStore`` method accepts as an optional parameter.  When
``GRAPHCLAW_NO_DELETE_ENFORCEMENT`` is ``true``, the repo raises
``ACLContextMissingError`` if ``caller_context`` is ``None``.

Design
------
``CallerContext`` carries:
  - ``user_id``: The authenticated user triggering the operation.
  - ``org_id``: The organisation (workspace) scope for cross-tenant isolation.
  - ``principal``: Which DB principal is executing (agent / admin / migration).
  - ``session_id``: Optional request session for distributed tracing.

The ACL guard function ``require_caller_context()`` is called at the top of
each public repo method.  When the feature flag is off it is a no-op so
existing code does not break.  When the flag is on it enforces the contract.

Public API
----------
- CallerContext: Pydantic model carrying audit and ACL metadata.
- require_caller_context: Enforce CallerContext is present (flag-gated).
- system_caller_context: Factory for internal / system-initiated operations.

Dependencies
------------
- graphclaw.db.base: ACLContextMissingError.
- graphclaw.infra.config: AppConfig (reads GRAPHCLAW_NO_DELETE_ENFORCEMENT).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class CallerContext(BaseModel):
    """Carries audit and ACL metadata for every GraphStore call.

    Parameters
    ----------
    user_id:
        Authenticated user or service account triggering the operation.
    org_id:
        Organisation / workspace scope.  Used to enforce cross-tenant isolation.
    principal:
        DB principal name: ``"agent_principal"``, ``"admin_principal"``, or
        ``"migration_principal"``.
    session_id:
        Optional distributed tracing session ID (SES-* format).
    """

    user_id: str = Field(..., description="Authenticated user or service ID.")
    org_id: str = Field(..., description="Organisation / workspace scope.")
    principal: str = Field(
        default="agent_principal",
        description="DB principal: agent_principal | admin_principal | migration_principal.",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional SES-* tracing session ID.",
    )


# ---------------------------------------------------------------------------
# Enforcement helper
# ---------------------------------------------------------------------------


def require_caller_context(caller_context: CallerContext | None) -> None:
    """Raise ACLContextMissingError if caller_context is None and enforcement is on.

    This is a no-op when ``GRAPHCLAW_NO_DELETE_ENFORCEMENT=false`` (the default
    before W0-PR10).  Once the flag is flipped, every public repo call that
    omits caller_context will raise.

    Parameters
    ----------
    caller_context:
        The context provided by the caller.  May be ``None`` in legacy callers.

    Raises
    ------
    ACLContextMissingError:
        When enforcement is active and ``caller_context`` is ``None``.
    """
    if caller_context is not None:
        return  # Fast path: context present, nothing to do.

    # Lazy import to avoid circular dependency at module level.
    from graphclaw.config import AppConfig  # noqa: PLC0415

    config = AppConfig()
    if not config.no_delete_enforcement:
        # Feature flag off — silently allow legacy callers.
        logger.debug("caller_context missing but enforcement is disabled (no-op)")
        return

    from graphclaw.db.base import ACLContextMissingError  # noqa: PLC0415

    raise ACLContextMissingError(
        "caller_context is required when GRAPHCLAW_NO_DELETE_ENFORCEMENT=true. "
        "Pass a CallerContext with user_id, org_id, and principal."
    )


# ---------------------------------------------------------------------------
# System context factory
# ---------------------------------------------------------------------------


def system_caller_context(principal: str = "admin_principal") -> CallerContext:
    """Return a CallerContext suitable for internal/system-triggered operations.

    Use this for gateway startup tasks, migrations, and background jobs that
    have no authenticated end-user.
    """
    return CallerContext(
        user_id="system",
        org_id="system",
        principal=principal,
        session_id=None,
    )
