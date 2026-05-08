# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from pydantic import ValidationError

from graphclaw.infra.config import AgentPoolConfig


class TestAgentPoolConfigValidation:
    def test_accepts_valid_defaults(self) -> None:
        cfg = AgentPoolConfig()
        assert cfg.max_concurrent_agents == 4
        assert cfg.subagent_tool_max_retries == 0

    def test_rejects_heartbeat_timeout_less_than_two_intervals(self) -> None:
        with pytest.raises(ValidationError, match="HEARTBEAT_TIMEOUT_SECONDS"):
            AgentPoolConfig(
                heartbeat_interval_seconds=60,
                heartbeat_timeout_seconds=119,
            )

    def test_rejects_tool_timeout_not_less_than_execution_timeout(self) -> None:
        with pytest.raises(ValidationError, match="TOOL_TIMEOUT_SECONDS"):
            AgentPoolConfig(
                subagent_execution_timeout_seconds=120,
                subagent_tool_timeout_seconds=120,
            )

    def test_rejects_retry_backoff_max_below_base(self) -> None:
        with pytest.raises(ValidationError, match="RETRY_BACKOFF_MAX_MS"):
            AgentPoolConfig(
                subagent_retry_backoff_base_ms=300,
                subagent_retry_backoff_max_ms=200,
            )


class TestAgentPoolConfigFromEnv:
    def test_parses_retry_lists_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRAPHCLAW_SUBAGENT_RETRYABLE_SKILLS", "skill.a, skill.b")
        monkeypatch.setenv(
            "GRAPHCLAW_SUBAGENT_RETRYABLE_MCP_TOOLS",
            "server-a:tool-x,tool-y",
        )

        cfg = AgentPoolConfig.from_env()

        assert cfg.subagent_retryable_skills == ["skill.a", "skill.b"]
        assert cfg.subagent_retryable_mcp_tools == ["server-a:tool-x", "tool-y"]
