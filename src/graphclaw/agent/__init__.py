# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""GraphClaw agent package.

Exports MainOrchestrator (and backward-compatible AgentLoop alias).
"""

from __future__ import annotations

from graphclaw.agent.main_orchestrator import MainOrchestrator

AgentLoop = MainOrchestrator

__all__ = ["MainOrchestrator", "AgentLoop"]
