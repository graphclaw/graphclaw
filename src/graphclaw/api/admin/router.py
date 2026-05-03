"""graphclaw.api.admin.router — Aggregated admin sub-router.

Collects all admin API sub-routers and exposes a single ``admin_router``
for inclusion in the top-level ``app_router``.
"""

from __future__ import annotations

from fastapi import APIRouter

from graphclaw.api.admin.audit import router as audit_router
from graphclaw.api.admin.connectors import router as connectors_router
from graphclaw.api.admin.features import router as features_router
from graphclaw.api.admin.guardrails import router as guardrails_router
from graphclaw.api.admin.infra import router as infra_router
from graphclaw.api.admin.judge import router as judge_router
from graphclaw.api.admin.lifecycle import router as lifecycle_router
from graphclaw.api.admin.llm import router as llm_router
from graphclaw.api.admin.members import router as members_router
from graphclaw.api.admin.sso import router as sso_router

admin_router = APIRouter()

admin_router.include_router(members_router)
admin_router.include_router(features_router)
admin_router.include_router(llm_router)
admin_router.include_router(judge_router)
admin_router.include_router(guardrails_router)
admin_router.include_router(sso_router)
admin_router.include_router(audit_router)
admin_router.include_router(infra_router)
admin_router.include_router(connectors_router)
admin_router.include_router(lifecycle_router)
