# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for tiered-memory injection into the system prompt + env-var config.

Covers (Wave Tiered-Memory):
- Working memory injected into _build_system_prompt() and capped at the char cap
- Compaction hint when working memory exceeds the configured budget share
- Semantic memory index rendered as a topic list
- GRAPHCLAW_MEMORY_* env vars wire into ContextManager configuration
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.main_orchestrator import MainOrchestrator
from graphclaw.infra.storage import StoragePaths

_USER = "USER-test"
_AGENT = "main"


class _FakeStorage:
    """In-memory StorageClient stand-in."""

    def __init__(self, data: dict[str, bytes] | None = None) -> None:
        self.data: dict[str, bytes] = dict(data or {})

    async def read(self, path: str) -> bytes:
        if path not in self.data:
            raise FileNotFoundError(path)
        return self.data[path]

    async def write(self, path: str, data: bytes, content_type: str = "text/plain") -> None:  # noqa: ARG002
        self.data[path] = data

    async def list_objects(self, prefix: str) -> list[str]:
        return sorted(k for k in self.data if k.startswith(prefix))

    async def exists(self, path: str) -> bool:
        return path in self.data


def _make_orchestrator(storage, *, llm=None) -> MainOrchestrator:
    return MainOrchestrator(
        graph_repo=MagicMock(),
        scoring_engine=MagicMock(),
        state_machine=MagicMock(),
        llm_client=llm,
        storage_client=storage,
        agent_id=_AGENT,
    )


def _working_key() -> str:
    return StoragePaths.agent_memory_working(_USER, _AGENT)


def _semantic_index_key() -> str:
    return StoragePaths.agent_memory_semantic_index(_USER, _AGENT)


# ---------------------------------------------------------------------------
# _build_system_prompt — working memory + semantic index injection
# ---------------------------------------------------------------------------


class TestSystemPromptInjection:
    @pytest.mark.asyncio
    async def test_working_memory_in_system_prompt(self):
        storage = _FakeStorage({_working_key(): b"Remember: standup at 9am."})
        orch = _make_orchestrator(storage)
        # Stub the heavy collaborators so the prompt build stays a unit test.
        orch._build_graph_summary = AsyncMock(return_value="")
        orch._build_execution_context = AsyncMock(return_value="")

        prompt = await orch._build_system_prompt(_USER)

        assert "## Working Memory" in prompt
        assert "standup at 9am" in prompt

    @pytest.mark.asyncio
    async def test_semantic_index_in_system_prompt(self):
        index = {
            "topics": {
                "team-roles": "Who does what on the team",
                "processes": "Standups and retros",
            }
        }
        storage = _FakeStorage({_semantic_index_key(): json.dumps(index).encode()})
        orch = _make_orchestrator(storage)
        orch._build_graph_summary = AsyncMock(return_value="")
        orch._build_execution_context = AsyncMock(return_value="")

        prompt = await orch._build_system_prompt(_USER)

        assert "## Semantic Memory" in prompt
        assert "team-roles: Who does what on the team" in prompt
        assert "processes: Standups and retros" in prompt


# ---------------------------------------------------------------------------
# _load_working_memory — cap + compaction hint
# ---------------------------------------------------------------------------


class TestWorkingMemoryLoader:
    @pytest.mark.asyncio
    async def test_working_memory_truncated_at_cap(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_MEMORY_WORKING_CHAR_CAP", "100")
        storage = _FakeStorage({_working_key(): b"Z" * 500})
        orch = _make_orchestrator(storage)

        loaded = await orch._load_working_memory(_USER)

        assert "[... truncated" in loaded
        # Capped content + truncation note — far below the original 500 chars.
        assert len(loaded) < 300

    @pytest.mark.asyncio
    async def test_compact_hint_when_utilization_high(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_MEMORY_BUDGET_CHARS", "1000")
        monkeypatch.setenv("GRAPHCLAW_MEMORY_COMPACT_THRESHOLD_PCT", "60")
        monkeypatch.setenv("GRAPHCLAW_MEMORY_WORKING_CHAR_CAP", "100000")
        storage = _FakeStorage({_working_key(): b"W" * 700})  # 70% of 1000
        orch = _make_orchestrator(storage)

        loaded = await orch._load_working_memory(_USER)

        assert "WARNING" in loaded
        assert "compact_memory" in loaded

    @pytest.mark.asyncio
    async def test_no_working_memory_returns_empty(self):
        orch = _make_orchestrator(_FakeStorage())
        assert await orch._load_working_memory(_USER) == ""


# ---------------------------------------------------------------------------
# _load_semantic_memory_index — formatting tolerance
# ---------------------------------------------------------------------------


class TestSemanticIndexLoader:
    @pytest.mark.asyncio
    async def test_list_style_index(self):
        index = {"topics": [{"name": "roles", "description": "team roles"}]}
        storage = _FakeStorage({_semantic_index_key(): json.dumps(index).encode()})
        orch = _make_orchestrator(storage)

        block = await orch._load_semantic_memory_index(_USER)

        assert "## Semantic Memory" in block
        assert "roles: team roles" in block

    @pytest.mark.asyncio
    async def test_missing_index_returns_empty(self):
        orch = _make_orchestrator(_FakeStorage())
        assert await orch._load_semantic_memory_index(_USER) == ""

    @pytest.mark.asyncio
    async def test_malformed_index_returns_empty(self):
        storage = _FakeStorage({_semantic_index_key(): b"not json{{"})
        orch = _make_orchestrator(storage)
        assert await orch._load_semantic_memory_index(_USER) == ""


# ---------------------------------------------------------------------------
# Configuration — GRAPHCLAW_MEMORY_* env vars
# ---------------------------------------------------------------------------


class TestMemoryConfig:
    @pytest.mark.asyncio
    async def test_env_var_window_size(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_MEMORY_WINDOW_SIZE", "10")
        orch = _make_orchestrator(_FakeStorage(), llm=MagicMock())
        assert orch._context_manager is not None
        assert orch._context_manager._window_size == 10

    @pytest.mark.asyncio
    async def test_env_var_budget_tokens(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_MEMORY_BUDGET_TOKENS", "50000")
        orch = _make_orchestrator(_FakeStorage(), llm=MagicMock())
        assert orch._context_manager._budget_tokens == 50000

    @pytest.mark.asyncio
    async def test_env_var_summary_threshold(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_MEMORY_SUMMARY_THRESHOLD", "7")
        orch = _make_orchestrator(_FakeStorage(), llm=MagicMock())
        assert orch._context_manager._summary_threshold == 7

    @pytest.mark.asyncio
    async def test_defaults_when_env_unset(self, monkeypatch):
        for var in (
            "GRAPHCLAW_MEMORY_WINDOW_SIZE",
            "GRAPHCLAW_MEMORY_SUMMARY_THRESHOLD",
            "GRAPHCLAW_MEMORY_BUDGET_TOKENS",
        ):
            monkeypatch.delenv(var, raising=False)
        orch = _make_orchestrator(_FakeStorage(), llm=MagicMock())
        assert orch._context_manager._window_size == 20
        assert orch._context_manager._summary_threshold == 30
        assert orch._context_manager._budget_tokens == 80_000

    @pytest.mark.asyncio
    async def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_MEMORY_WINDOW_SIZE", "not-an-int")
        orch = _make_orchestrator(_FakeStorage(), llm=MagicMock())
        assert orch._context_manager._window_size == 20
