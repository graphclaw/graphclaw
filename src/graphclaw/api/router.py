"""graphclaw.api.router — Aggregated /app/v1/ API router.

Description
-----------
Collects all application-level sub-routers and mounts them under the
``/app/v1`` prefix.  Include ``app_router`` in the FastAPI application
factory to expose all application management endpoints.

Wave status
-----------
Wave 1 (complete)    — graph, scoring, state, events  — core cockpit canvas
Wave 2 (complete)    — approvals, settings, skill_registry, mcp_registry stub→real
Wave 3 (complete)    — chat, config, secrets
Wave 4 (complete)    — settings ext (+11 routes), agent (+6 routes)
Wave 5 (complete)    — skill_registry ext (+4), mcp_registry ext (+2), agents (+7)
Wave 6 (complete)    — admin/* (9 files)
Wave 7 (complete)    — intelligence hub (agent profile, memory, skill authoring)
Wave 8 (active)      — canvas agent config hub (layout, per-agent config, runtime bridge)

Design Patterns
---------------
- Router aggregation: A single top-level router delegates to focused
  sub-routers, keeping each concern in its own module.

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

# ── Existing routers ──────────────────────────────────────────────────────────
from graphclaw.api.a2a_keys import router as a2a_keys_router

# ── Wave 6: Admin panel ───────────────────────────────────────────────────────
from graphclaw.api.admin.router import admin_router

# ── Wave 4: Agent monitor ─────────────────────────────────────────────────────
from graphclaw.api.agent import router as agent_router

# ── Wave 5: Agents canvas, MCP approvals ─────────────────────────────────────
from graphclaw.api.agents import router as agents_router
from graphclaw.api.approvals import router as approvals_router

# ── Wave 8: Agent Canvas Hub ──────────────────────────────────────────────────
from graphclaw.api.canvas import router as canvas_router

# ── Wave 3: Chat, config, secrets ─────────────────────────────────────────────
from graphclaw.api.chat import router as chat_router
from graphclaw.api.compliance import router as compliance_router
from graphclaw.api.config import router as config_router

# ── Wave 1: Core cockpit canvas ───────────────────────────────────────────────
from graphclaw.api.events import router as events_router
from graphclaw.api.graph import router as graph_router

# ── Wave 7: Intelligence Hub ──────────────────────────────────────────────────
from graphclaw.api.intelligence import router as intelligence_router
from graphclaw.api.mcp_registry import mcp_approvals_router
from graphclaw.api.mcp_registry import router as mcp_registry_router
from graphclaw.api.scoring import router as scoring_router
from graphclaw.api.secrets import router as secrets_router
from graphclaw.api.settings import router as settings_router
from graphclaw.api.skill_registry import router as skill_registry_router
from graphclaw.api.state import router as state_router

app_router = APIRouter(prefix="/app/v1", tags=["app-api"])

# Existing
app_router.include_router(settings_router)
app_router.include_router(approvals_router)
app_router.include_router(skill_registry_router)
app_router.include_router(mcp_registry_router)
app_router.include_router(a2a_keys_router)
app_router.include_router(compliance_router)

# Wave 1
app_router.include_router(graph_router)
app_router.include_router(scoring_router)
app_router.include_router(state_router)
app_router.include_router(events_router)

# Wave 3
app_router.include_router(chat_router)
app_router.include_router(config_router)
app_router.include_router(secrets_router)

# Wave 4
app_router.include_router(agent_router)

# Wave 5
app_router.include_router(agents_router)
app_router.include_router(mcp_approvals_router)

# Wave 6
app_router.include_router(admin_router)

# Wave 7
app_router.include_router(intelligence_router)

# Wave 8
app_router.include_router(canvas_router)

# Wave 7 — Identity (FR-ID-002..005)
from graphclaw.api.identity import router as identity_router  # noqa: E402

app_router.include_router(identity_router)

# Wave 1 — Policies (FR-STORE-002)
from graphclaw.api.policies import router as policies_router  # noqa: E402

app_router.include_router(policies_router)
