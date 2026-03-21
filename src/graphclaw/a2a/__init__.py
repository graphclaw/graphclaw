"""graphclaw.a2a — Agent-to-Agent (A2A) REST API module.

Description
-----------
Provides the A2A REST API for agent-to-agent communication within the GraphClaw
platform.  External AI agents (or any programmatic caller) authenticate with a
shared secret key (``wg_agent_*`` format) and POST task updates to the canonical
``/api/v1/task-update`` endpoint.

Public API
----------
- A2AKeyManager: API key lifecycle management (generate, rotate, revoke, verify).
- A2AService: Placeholder for future A2A business logic (task routing, etc.).
- router: Combined ``APIRouter`` exposing all A2A management endpoints.

Dependencies
------------
- graphclaw.a2a.key_manager: A2AKeyManager.
- graphclaw.a2a.routes: a2a_router, task_update_router.
"""

from __future__ import annotations

from graphclaw.a2a.key_manager import A2AKeyManager
from graphclaw.a2a.routes import a2a_router, task_update_router

# A2AService is a lightweight placeholder; full business logic lives in the
# orchestrator.  Re-exported here to satisfy the module public API contract.
A2AService = None  # Reserved for Phase 5 orchestrator integration

__all__ = [
    "A2AKeyManager",
    "A2AService",
    "a2a_router",
    "task_update_router",
]
