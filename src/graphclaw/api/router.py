"""graphclaw.api.router — Aggregated /app/v1/ API router.

Description
-----------
Collects all application-level sub-routers and mounts them under the
``/app/v1`` prefix.  Include ``app_router`` in the FastAPI application
factory to expose all application management endpoints.

Design Patterns
---------------
- Router aggregation: A single top-level router delegates to focused
  sub-routers, keeping each concern (settings, approvals, skills, MCP
  servers, A2A keys) in its own module.

Public API
----------
- app_router: ``APIRouter`` aggregating all /app/v1/ sub-routers.

Dependencies
------------
- graphclaw.api.*: Sub-router modules.
- fastapi: APIRouter (third-party).
"""
from __future__ import annotations

from fastapi import APIRouter

from graphclaw.api.a2a_keys import router as a2a_keys_router
from graphclaw.api.approvals import router as approvals_router
from graphclaw.api.mcp_registry import router as mcp_registry_router
from graphclaw.api.settings import router as settings_router
from graphclaw.api.skill_registry import router as skill_registry_router

app_router = APIRouter(prefix="/app/v1", tags=["app-api"])
app_router.include_router(settings_router)
app_router.include_router(approvals_router)
app_router.include_router(skill_registry_router)
app_router.include_router(mcp_registry_router)
app_router.include_router(a2a_keys_router)
