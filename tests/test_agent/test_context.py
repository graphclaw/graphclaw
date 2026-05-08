# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.agent.context — ContextManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.context import CompressedContext, ContextManager
from graphclaw.llm.base import LLMMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm(summary_text: str = "Summary of prior conversation."):
    """Build a mock LLMClient that returns a canned summary."""
    llm = AsyncMock()
    response = MagicMock()
    response.content = summary_text
    llm.complete = AsyncMock(return_value=response)
    llm.count_tokens = AsyncMock(return_value=1000)  # well under budget by default
    return llm


def _history(n: int, roles_alternate: bool = True) -> list[dict]:
    """Build n history entries alternating user/agent."""
    entries = []
    for i in range(n):
        role = "user" if (i % 2 == 0 or not roles_alternate) else "agent"
        entries.append({"role": role, "content": f"Message {i}"})
    return entries


def _history_with_nodes(nodes: list[str]) -> list[dict]:
    """History entries that mention given node IDs."""
    entries = []
    for i, node_id in enumerate(nodes):
        entries.append({"role": "user", "content": f"Please work on {node_id}"})
        entries.append({"role": "agent", "content": f"Working on {node_id} now."})
    return entries


# ---------------------------------------------------------------------------
# CompressedContext defaults
# ---------------------------------------------------------------------------


class TestCompressedContextDefaults:
    def test_defaults(self):
        ctx = CompressedContext()
        assert ctx.session_state_block == ""
        assert ctx.summary_block == ""
        assert ctx.recent_messages == []
        assert ctx.collapsed_tool_calls == ""
        assert not ctx.compression_applied
        assert ctx.original_count == 0
        assert ctx.compressed_count == 0


# ---------------------------------------------------------------------------
# Empty history
# ---------------------------------------------------------------------------


class TestEmptyHistory:
    @pytest.mark.asyncio
    async def test_empty_history_returns_empty_context(self):
        llm = _make_llm()
        cm = ContextManager(llm)

        ctx = await cm.compress([])

        assert ctx.session_state_block == ""
        assert ctx.recent_messages == []
        assert not ctx.compression_applied
        assert ctx.original_count == 0


# ---------------------------------------------------------------------------
# Sliding window (short history — no compression)
# ---------------------------------------------------------------------------


class TestSlidingWindow:
    @pytest.mark.asyncio
    async def test_short_history_kept_verbatim(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = _history(5)

        ctx = await cm.compress(history)

        assert len(ctx.recent_messages) == 5
        assert not ctx.compression_applied

    @pytest.mark.asyncio
    async def test_window_size_respected(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=5)
        history = _history(15)

        ctx = await cm.compress(history)

        # Only the last 5 are kept verbatim in recent_messages
        assert len(ctx.recent_messages) == 5
        # Older 10 entries are collapsed (compression_applied = True)
        assert ctx.compression_applied


# ---------------------------------------------------------------------------
# Role remapping  "agent" → "assistant"
# ---------------------------------------------------------------------------


class TestRoleRemapping:
    @pytest.mark.asyncio
    async def test_agent_role_remapped_to_assistant(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "agent", "content": "Hi back"},
        ]

        ctx = await cm.compress(history)
        roles = [m.role for m in ctx.recent_messages]

        assert "agent" not in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_user_role_unchanged(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [{"role": "user", "content": "Question"}]

        ctx = await cm.compress(history)
        assert ctx.recent_messages[0].role == "user"

    @pytest.mark.asyncio
    async def test_empty_content_entries_skipped(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "Real message"},
            {"role": "agent", "content": ""},  # empty — should be skipped
        ]

        ctx = await cm.compress(history)
        assert len(ctx.recent_messages) == 1


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


class TestEntityExtraction:
    @pytest.mark.asyncio
    async def test_task_ids_extracted(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "What about TSK-AB-001-AT?"},
            {"role": "agent", "content": "TSK-AB-001-AT is ACTIVE."},
        ]

        ctx = await cm.compress(history)

        assert "TSK-AB-001-AT" in ctx.session_state_block

    @pytest.mark.asyncio
    async def test_goal_ids_extracted(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "Focus on GOAL-XY-123"},
        ]

        ctx = await cm.compress(history)
        assert "GOAL-XY-123" in ctx.session_state_block

    @pytest.mark.asyncio
    async def test_state_associated_with_task(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "agent", "content": "TSK-AB-001-AT is now COMPLETE."},
        ]

        ctx = await cm.compress(history)
        # Entity register should show COMPLETE state
        assert "COMPLETE" in ctx.session_state_block

    @pytest.mark.asyncio
    async def test_no_nodes_returns_empty_block(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "Just a general question"},
        ]

        ctx = await cm.compress(history)
        assert ctx.session_state_block == ""


# ---------------------------------------------------------------------------
# Tool-call collapse
# ---------------------------------------------------------------------------


class TestToolCallCollapse:
    @pytest.mark.asyncio
    async def test_older_turns_collapsed(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=3, summary_threshold=100)
        # 10 entries — last 3 are in window, first 7 are older
        history = _history(10)

        ctx = await cm.compress(history)

        assert ctx.collapsed_tool_calls != ""
        assert ctx.compression_applied


# ---------------------------------------------------------------------------
# Rolling LLM summary
# ---------------------------------------------------------------------------


class TestRollingSummary:
    @pytest.mark.asyncio
    async def test_summary_called_when_older_exceeds_threshold(self):
        llm = _make_llm("The user discussed task TSK-001-AT.")
        cm = ContextManager(llm, window_size=5, summary_threshold=3)
        # 20 entries → 15 older (> threshold=3) → summary triggered
        history = _history(20)

        ctx = await cm.compress(history)

        assert ctx.summary_block == "The user discussed task TSK-001-AT."
        llm.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_summary_not_called_when_below_threshold(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=15, summary_threshold=30)
        # 20 entries → only 5 older (< threshold=30) → no summary
        history = _history(20)

        await cm.compress(history)

        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_summary_failure_falls_back_gracefully(self):
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        llm.count_tokens = AsyncMock(return_value=1000)
        cm = ContextManager(llm, window_size=5, summary_threshold=3)
        history = _history(20)

        # Should not raise
        ctx = await cm.compress(history)
        assert isinstance(ctx, CompressedContext)


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_builds_flat_message_list(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext(
            session_state_block="## Session State\n- TSK-001: ACTIVE",
            recent_messages=[
                LLMMessage(role="user", content="Hello"),
                LLMMessage(role="assistant", content="Hi"),
            ],
        )

        messages = cm.build_messages(ctx)

        roles = [m.role for m in messages]
        # session_state_block emits user+assistant pair, then recent messages
        assert roles[0] == "user"
        assert roles[1] == "assistant"
        assert "Hello" in messages[-2].content
        assert "Hi" in messages[-1].content

    def test_summary_block_inserted_before_recent(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext(
            summary_block="Prior: user created tasks.",
            recent_messages=[LLMMessage(role="user", content="Current")],
        )

        messages = cm.build_messages(ctx)

        # Find summary block position
        summary_idx = next(i for i, m in enumerate(messages) if "Prior:" in (m.content or ""))
        current_idx = next(i for i, m in enumerate(messages) if "Current" in (m.content or ""))
        assert summary_idx < current_idx

    def test_empty_context_returns_empty_list(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext()

        messages = cm.build_messages(ctx)
        assert messages == []

    def test_collapsed_tool_calls_inserted(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext(
            collapsed_tool_calls="[tool: create_task → TSK-001 created]",
            recent_messages=[LLMMessage(role="user", content="Continue")],
        )

        messages = cm.build_messages(ctx)
        contents = [m.content for m in messages]
        assert any("create_task" in (c or "") for c in contents)
