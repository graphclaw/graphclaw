# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for Wave 4: FR-CA-001, FR-CA-002, FR-CA-003.

FR-CA-001: Channel-agnostic chat handler signature.
FR-CA-002: Post-turn distillation helper.
FR-CA-003: process_counterparty_turn + tool gating.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from graphclaw.agent.distillation import DistillationHelper, DistillationInput, DistillationResult
from graphclaw.agent.tool_registry import COUNTERPARTY_ALLOWED_TOOL_NAMES, ToolSetRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeLLM:
    """Fake LLM that returns a fixed response."""

    def __init__(self, content: str = '{"task_entry": "test entry", "memory_note": "test note"}'):
        self._content = content
        self.calls: list[dict] = []

    async def complete(
        self, messages, *, model=None, max_tokens=None, temperature=None, tools=None
    ):  # noqa: ARG002
        result = MagicMock()
        result.content = self._content
        result.tool_calls = []
        result.usage = None
        self.calls.append({"messages": messages, "tools": tools})
        return result


class FakeGraphRepo:
    def __init__(self):
        self._intelligence: dict[str, str] = {}

    async def get_node_intelligence(self, node_id: str) -> str | None:
        return self._intelligence.get(node_id)

    async def update_node_intelligence(self, node_id: str, text: str) -> None:
        self._intelligence[node_id] = text


class FakeStorage:
    def __init__(self):
        self._data: dict[str, bytes] = {}

    async def read(self, path: str) -> bytes:
        if path not in self._data:
            raise FileNotFoundError(path)
        return self._data[path]

    async def write(self, path: str, data: bytes, content_type: str = "") -> None:  # noqa: ARG002
        self._data[path] = data


# ---------------------------------------------------------------------------
# FR-CA-001: Channel-agnostic chat handler signature
# ---------------------------------------------------------------------------


class TestFRCA001:
    """FR-CA-001: process_chat_message and process_chat_message_stream accept channel + thread_id."""

    def test_process_chat_message_signature(self) -> None:
        import inspect

        from graphclaw.agent.main_orchestrator import MainOrchestrator

        sig = inspect.signature(MainOrchestrator.process_chat_message)
        params = list(sig.parameters.keys())
        assert "channel" in params, "channel parameter missing from process_chat_message"
        assert "thread_id" in params, "thread_id parameter missing from process_chat_message"

    def test_channel_defaults_to_cockpit(self) -> None:
        import inspect

        from graphclaw.agent.main_orchestrator import MainOrchestrator

        sig = inspect.signature(MainOrchestrator.process_chat_message)
        assert sig.parameters["channel"].default == "cockpit"

    def test_thread_id_defaults_to_none(self) -> None:
        import inspect

        from graphclaw.agent.main_orchestrator import MainOrchestrator

        sig = inspect.signature(MainOrchestrator.process_chat_message)
        assert sig.parameters["thread_id"].default is None

    def test_process_chat_message_stream_signature(self) -> None:
        import inspect

        from graphclaw.agent.main_orchestrator import MainOrchestrator

        sig = inspect.signature(MainOrchestrator.process_chat_message_stream)
        params = list(sig.parameters.keys())
        assert "channel" in params
        assert "thread_id" in params

    def test_process_counterparty_turn_exists(self) -> None:
        from graphclaw.agent.main_orchestrator import MainOrchestrator

        assert hasattr(MainOrchestrator, "process_counterparty_turn")
        assert asyncio.iscoroutinefunction(MainOrchestrator.process_counterparty_turn)


# ---------------------------------------------------------------------------
# FR-CA-002: DistillationHelper
# ---------------------------------------------------------------------------


class TestFRCA002:
    """FR-CA-002: Post-turn distillation writes node intelligence + working memory."""

    @pytest.mark.asyncio
    async def test_distillation_writes_node_intelligence(self) -> None:
        llm = FakeLLM('{"task_entry": "Bob confirmed delivery", "memory_note": null}')
        repo = FakeGraphRepo()
        storage = FakeStorage()
        helper = DistillationHelper(llm=llm, graph_repo=repo, storage=storage)
        inp = DistillationInput(
            user_id="U1",
            agent_id="A1",
            user_text="Did Bob confirm?",
            agent_reply="Yes, TSK-42 confirmed.",
            task_id="TSK-42",
            channel="cockpit",
        )
        result = await helper.distill(inp)
        assert result.action_taken in ("both", "node_updated")
        assert "TSK-42" in repo._intelligence
        assert "Bob confirmed delivery" in repo._intelligence["TSK-42"]

    @pytest.mark.asyncio
    async def test_distillation_writes_memory_note(self) -> None:
        llm = FakeLLM('{"task_entry": null, "memory_note": "User prefers bullet lists"}')
        repo = FakeGraphRepo()
        storage = FakeStorage()
        helper = DistillationHelper(llm=llm, graph_repo=repo, storage=storage)
        inp = DistillationInput(
            user_id="U1",
            agent_id="A1",
            user_text="Use bullet lists please",
            agent_reply="Noted.",
            channel="cockpit",
        )
        result = await helper.distill(inp)
        assert result.action_taken in ("both", "memory_updated")
        # Check context.md was written somewhere
        assert any("working/context.md" in k for k in storage._data)

    @pytest.mark.asyncio
    async def test_distillation_noop_when_no_intelligence(self) -> None:
        llm = FakeLLM('{"task_entry": null, "memory_note": null}')
        repo = FakeGraphRepo()
        storage = FakeStorage()
        helper = DistillationHelper(llm=llm, graph_repo=repo, storage=storage)
        inp = DistillationInput(
            user_id="U1",
            agent_id="A1",
            user_text="Hello",
            agent_reply="Hi!",
            channel="cockpit",
        )
        result = await helper.distill(inp)
        assert result.action_taken == "noop"

    @pytest.mark.asyncio
    async def test_distillation_graceful_on_llm_error(self) -> None:
        class FailLLM:
            async def complete(self, *a, **kw):
                raise RuntimeError("LLM unavailable")

        helper = DistillationHelper(
            llm=FailLLM(), graph_repo=FakeGraphRepo(), storage=FakeStorage()
        )
        inp = DistillationInput(user_id="U1", agent_id="A1", user_text="hi", agent_reply="hey")
        result = await helper.distill(inp)
        assert result.action_taken == "error"
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_distillation_graceful_on_repo_error(self) -> None:
        llm = FakeLLM('{"task_entry": "entry", "memory_note": null}')

        class FailRepo:
            async def get_node_intelligence(self, *a):
                raise RuntimeError("DB down")

            async def update_node_intelligence(self, *a):
                raise RuntimeError("DB down")

        helper = DistillationHelper(llm=llm, graph_repo=FailRepo(), storage=FakeStorage())
        inp = DistillationInput(
            user_id="U1", agent_id="A1", user_text="hi", agent_reply="hey", task_id="TSK-1"
        )
        result = await helper.distill(inp)
        # Should not raise; action will be noop or partial
        assert result.action_taken in ("noop", "error", "memory_updated")

    def test_distillation_result_defaults(self) -> None:
        r = DistillationResult()
        assert r.action_taken == "noop"
        assert r.task_entry is None
        assert r.memory_note is None
        assert r.error is None

    def test_distillation_input_defaults(self) -> None:
        inp = DistillationInput(user_id="U1", agent_id="A1", user_text="hi", agent_reply="hey")
        assert inp.channel == "cockpit"
        assert inp.task_id is None
        assert inp.session_id is None

    @pytest.mark.asyncio
    async def test_distillation_skipped_when_no_storage(self) -> None:
        llm = FakeLLM('{"task_entry": "entry", "memory_note": "note"}')
        helper = DistillationHelper(llm=llm, graph_repo=FakeGraphRepo(), storage=None)
        inp = DistillationInput(user_id="U1", agent_id="A1", user_text="hi", agent_reply="hey")
        result = await helper.distill(inp)
        # memory write skipped, no crash
        assert result.action_taken in ("noop", "node_updated", "error")

    @pytest.mark.asyncio
    async def test_distillation_promotes_semantic_fact(self) -> None:
        from graphclaw.infra.storage import StoragePaths

        llm = FakeLLM(
            '{"task_entry": null, "memory_note": null, '
            '"semantic": {"topic": "owner-profile", "fact": "Abhi is the CEO."}}'
        )
        storage = FakeStorage()
        helper = DistillationHelper(llm=llm, graph_repo=FakeGraphRepo(), storage=storage)
        inp = DistillationInput(
            user_id="U1", agent_id="A1", user_text="I'm the CEO", agent_reply="Noted."
        )
        result = await helper.distill(inp)

        assert result.semantic_topic == "owner-profile"
        topic_path = StoragePaths.agent_memory_semantic_topic("U1", "A1", "owner-profile")
        index_path = StoragePaths.agent_memory_semantic_index("U1", "A1")
        assert b"Abhi is the CEO." in storage._data[topic_path]
        # Index lists the topic so it surfaces in the system prompt + read_memory.
        assert b"owner-profile" in storage._data[index_path]

    @pytest.mark.asyncio
    async def test_distillation_no_semantic_when_null(self) -> None:
        llm = FakeLLM('{"task_entry": null, "memory_note": "note", "semantic": null}')
        storage = FakeStorage()
        helper = DistillationHelper(llm=llm, graph_repo=FakeGraphRepo(), storage=storage)
        inp = DistillationInput(user_id="U1", agent_id="A1", user_text="hi", agent_reply="hey")
        result = await helper.distill(inp)
        assert result.semantic_topic is None
        assert not any("semantic" in k for k in storage._data)


# ---------------------------------------------------------------------------
# FR-CA-003: ToolSetRegistry mode gating + process_counterparty_turn
# ---------------------------------------------------------------------------


class TestFRCA003ToolGating:
    """FR-CA-003: COUNTERPARTY_ALLOWED_TOOL_NAMES filters get_active_tools(mode=...)."""

    def test_counterparty_allowed_tools_defined(self) -> None:
        assert "get_task_details" in COUNTERPARTY_ALLOWED_TOOL_NAMES
        assert "update_task_state" in COUNTERPARTY_ALLOWED_TOOL_NAMES
        assert "escalate_to_owner" in COUNTERPARTY_ALLOWED_TOOL_NAMES

    def test_counterparty_disallows_delegation(self) -> None:
        assert "delegate_to_agent" not in COUNTERPARTY_ALLOWED_TOOL_NAMES

    def test_counterparty_disallows_create_agent(self) -> None:
        assert "create_agent" not in COUNTERPARTY_ALLOWED_TOOL_NAMES

    def test_counterparty_disallows_invoke_skill(self) -> None:
        assert "invoke_skill" not in COUNTERPARTY_ALLOWED_TOOL_NAMES

    def test_counterparty_disallows_call_mcp_tool(self) -> None:
        assert "call_mcp_tool" not in COUNTERPARTY_ALLOWED_TOOL_NAMES

    def test_get_active_tools_default_returns_all(self) -> None:
        reg = ToolSetRegistry()
        reg.reset_session()
        tools = reg.get_active_tools()
        names = {t.name for t in tools}
        # Core tools should all be present
        assert "list_tasks" in names
        assert "get_task_details" in names

    def test_get_active_tools_counterparty_mode_filters(self) -> None:
        reg = ToolSetRegistry()
        reg.reset_session()
        tools = reg.get_active_tools(mode="counterparty_conversation")
        names = {t.name for t in tools}
        # Only allowed tools should be present
        for name in names:
            assert name in COUNTERPARTY_ALLOWED_TOOL_NAMES, (
                f"Tool '{name}' should not be available in counterparty_conversation mode"
            )

    def test_get_active_tools_counterparty_mode_excludes_list_tasks(self) -> None:
        reg = ToolSetRegistry()
        reg.reset_session()
        tools = reg.get_active_tools(mode="counterparty_conversation")
        names = {t.name for t in tools}
        # list_tasks is in core but NOT in counterparty allow-list
        assert "list_tasks" not in names

    def test_get_active_tools_counterparty_excludes_delegation_set(self) -> None:
        reg = ToolSetRegistry()
        reg.activate("delegation")
        tools = reg.get_active_tools(mode="counterparty_conversation")
        names = {t.name for t in tools}
        assert "delegate_to_agent" not in names
        assert "create_agent" not in names


class TestFRCA003CounterpartyTurn:
    """FR-CA-003: process_counterparty_turn signature and basic behavior."""

    def test_process_counterparty_turn_signature(self) -> None:
        import inspect

        from graphclaw.agent.main_orchestrator import MainOrchestrator

        sig = inspect.signature(MainOrchestrator.process_counterparty_turn)
        params = list(sig.parameters.keys())
        assert "user_id" in params
        assert "counterparty_id" in params
        assert "text" in params
        assert "channel" in params
        assert "thread_id" in params
        assert "session_id" in params

    def test_counterparty_turn_is_coroutine(self) -> None:
        from graphclaw.agent.main_orchestrator import MainOrchestrator

        assert asyncio.iscoroutinefunction(MainOrchestrator.process_counterparty_turn)

    def test_distillation_helper_importable(self) -> None:
        from graphclaw.agent.distillation import DistillationHelper  # noqa: F401

        assert True

    def test_distillation_input_importable(self) -> None:
        from graphclaw.agent.distillation import DistillationInput  # noqa: F401

        assert True

    def test_distillation_result_importable(self) -> None:
        from graphclaw.agent.distillation import DistillationResult  # noqa: F401

        assert True
