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

        assert "elided" in loaded
        # Capped content + elision note — far below the original 500 chars.
        assert len(loaded) < 300

    @pytest.mark.asyncio
    async def test_truncation_keeps_the_newest_content_not_the_stale_head(self, monkeypatch):
        """Regression: distillation appends the newest notes at the END of
        working memory (see distillation.py _append_working_note). A
        head-only content[:cap] truncation kept the stale preamble and
        silently dropped exactly what the agent most recently learned."""
        monkeypatch.setenv("GRAPHCLAW_MEMORY_WORKING_CHAR_CAP", "200")
        old_header = "# Working Memory\n\n## Recent Context\n"
        stale_note = "STALE_NOTE_FROM_LONG_AGO " * 30
        newest_note = "NEWEST_NOTE_JUST_LEARNED"
        content = (old_header + stale_note + newest_note).encode()
        storage = _FakeStorage({_working_key(): content})
        orch = _make_orchestrator(storage)

        loaded = await orch._load_working_memory(_USER)

        assert newest_note in loaded
        assert "# Working Memory" in loaded  # head/preamble also preserved

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

    @pytest.mark.asyncio
    async def test_default_max_topics_none_is_unbounded(self):
        """This index grows unbounded over an agent's lifetime; default
        behaviour (no cap) must be preserved for callers that don't pass one."""
        index = {"topics": {f"topic-{i}": f"desc-{i}" for i in range(30)}}
        storage = _FakeStorage({_semantic_index_key(): json.dumps(index).encode()})
        orch = _make_orchestrator(storage)

        block = await orch._load_semantic_memory_index(_USER)

        for i in range(30):
            assert f"topic-{i}" in block
        assert "more topics" not in block

    @pytest.mark.asyncio
    async def test_max_topics_caps_rendered_lines(self):
        index = {"topics": {f"topic-{i}": f"desc-{i}" for i in range(30)}}
        storage = _FakeStorage({_semantic_index_key(): json.dumps(index).encode()})
        orch = _make_orchestrator(storage)

        block = await orch._load_semantic_memory_index(_USER, max_topics=5)

        for i in range(5):
            assert f"topic-{i}" in block
        assert "(+25 more topics — call read_memory to browse)" in block

    @pytest.mark.asyncio
    async def test_max_topics_applies_to_list_style_index_too(self):
        index = {"topics": [{"name": f"topic-{i}", "description": f"desc-{i}"} for i in range(10)]}
        storage = _FakeStorage({_semantic_index_key(): json.dumps(index).encode()})
        orch = _make_orchestrator(storage)

        block = await orch._load_semantic_memory_index(_USER, max_topics=3)

        assert "(+7 more topics — call read_memory to browse)" in block


# ---------------------------------------------------------------------------
# _build_system_prompt — persona/graph-summary caps and overall budget fitting
#
# Regression coverage for the context-budget work: persona and graph summary
# were previously unbounded strings concatenated directly into the prompt.
# ---------------------------------------------------------------------------


class TestBuildSystemPromptBudget:
    @pytest.mark.asyncio
    async def test_persona_truncated_at_configured_cap(self, monkeypatch):
        from graphclaw.infra.storage import StoragePaths

        monkeypatch.setenv("GRAPHCLAW_CONTEXT_PERSONA_MAX_CHARS", "200")
        profile_path = StoragePaths.agent_profile(_USER, _AGENT)
        storage = _FakeStorage({profile_path: (b"P" * 5000)})
        orch = _make_orchestrator(storage)
        orch._build_graph_summary = AsyncMock(return_value="")
        orch._build_execution_context = AsyncMock(return_value="")

        prompt = await orch._build_system_prompt(_USER)

        assert "## Your Persona" in prompt
        # Truncation marker present, and nowhere near the full 5000 chars.
        assert "truncated" in prompt
        persona_section = prompt.split("## Your Persona")[1].split("## ")[0]
        assert len(persona_section) < 500

    @pytest.mark.asyncio
    async def test_graph_summary_truncated_at_configured_cap(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_GRAPH_SUMMARY_MAX_CHARS", "150")
        storage = _FakeStorage()
        orch = _make_orchestrator(storage)
        orch._build_graph_summary = AsyncMock(return_value="G" * 5000)
        orch._build_execution_context = AsyncMock(return_value="")

        prompt = await orch._build_system_prompt(_USER)

        assert "## Current Task Graph Summary" in prompt
        assert "truncated" in prompt
        summary_section = prompt.split("## Current Task Graph Summary")[1]
        assert len(summary_section) < 500

    @pytest.mark.asyncio
    async def test_response_format_and_header_always_present_under_pressure(self, monkeypatch):
        """Priority-0 sections (header, tool manifest, response format) must
        survive even when every droppable section is deliberately oversized."""
        from graphclaw.infra.storage import StoragePaths

        monkeypatch.setenv("GRAPHCLAW_CONTEXT_MODEL_WINDOW_TOKENS", "2000")
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_RESERVE_OUTPUT_TOKENS", "0")
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_PROMPT_BUDGET_PCT", "100")
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_SYSTEM_BUDGET_PCT", "10")  # tiny system budget
        profile_path = StoragePaths.agent_profile(_USER, _AGENT)
        storage = _FakeStorage({profile_path: (b"P" * 50_000)})
        orch = _make_orchestrator(storage)
        orch._build_graph_summary = AsyncMock(return_value="G" * 50_000)
        orch._build_execution_context = AsyncMock(return_value="")

        prompt = await orch._build_system_prompt(_USER)

        assert "## Response Format (MANDATORY)" in prompt
        assert "## Available Tool Sets" in prompt

    @pytest.mark.asyncio
    async def test_no_caps_configured_persona_passes_through_unbounded(self):
        """Backward compatible: without any GRAPHCLAW_CONTEXT_* override, the
        default caps are generous and a normal-sized persona is untouched."""
        from graphclaw.infra.storage import StoragePaths

        profile_path = StoragePaths.agent_profile(_USER, _AGENT)
        storage = _FakeStorage({profile_path: b"A concise persona."})
        orch = _make_orchestrator(storage)
        orch._build_graph_summary = AsyncMock(return_value="")
        orch._build_execution_context = AsyncMock(return_value="")

        prompt = await orch._build_system_prompt(_USER)

        assert "A concise persona." in prompt
        assert "truncated" not in prompt


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
