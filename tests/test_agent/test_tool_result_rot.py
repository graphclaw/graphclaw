# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_agent.test_tool_result_rot — Tests for MainOrchestrator's tool-result
truncation and cross-iteration pruning (_tool_result_message / _prune_tool_results).

Before this fix, every role="tool" message appended to the agentic-loop
conversation was raw, unbounded `json.dumps(tool_result)`, never pruned across
the loop's 15 iterations. On a 32k-token local model this alone could exceed
the context window well before the loop terminated.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from graphclaw.agent.main_orchestrator import MainOrchestrator as AgentLoop
from graphclaw.llm.base import LLMMessage, ToolCall
from graphclaw.state.machine import StateMachine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loop() -> AgentLoop:
    repo = AsyncMock()
    repo._pool = None
    engine = MagicMock()
    engine.cache = MagicMock()
    return AgentLoop(graph_repo=repo, scoring_engine=engine, state_machine=StateMachine())


# ---------------------------------------------------------------------------
# _tool_result_message
# ---------------------------------------------------------------------------


class TestToolResultMessage:
    def test_short_result_passes_through_unchanged(self):
        loop = _make_loop()
        msg = loop._tool_result_message("tc-1", "get_task", {"id": "TSK-1", "state": "ACTIVE"})

        assert msg.role == "tool"
        assert msg.tool_call_id == "tc-1"
        assert json.loads(msg.content) == {"id": "TSK-1", "state": "ACTIVE"}

    def test_large_result_is_truncated_with_visible_marker(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_TOOL_RESULT_MAX_CHARS", "200")
        loop = _make_loop()
        big_result = {"data": "x" * 5000}

        msg = loop._tool_result_message("tc-1", "get_task_details", big_result)

        assert len(msg.content) < 5000
        assert "truncated" in msg.content
        assert "narrower args" in msg.content

    def test_result_content_is_valid_json_dumps_shape(self):
        loop = _make_loop()
        msg = loop._tool_result_message("tc-1", "list_tasks", [{"id": "TSK-1"}, {"id": "TSK-2"}])
        assert json.loads(msg.content) == [{"id": "TSK-1"}, {"id": "TSK-2"}]


# ---------------------------------------------------------------------------
# _prune_tool_results
# ---------------------------------------------------------------------------


class TestPruneToolResults:
    def _messages_with_n_tool_calls(self, loop: AgentLoop, n: int) -> list[LLMMessage]:
        messages: list[LLMMessage] = [LLMMessage(role="system", content="sys")]
        for i in range(n):
            tc = ToolCall(id=f"tc-{i}", name=f"tool_{i}", arguments={})
            messages.append(LLMMessage(role="assistant", content="", tool_calls=[tc]))
            messages.append(
                loop._tool_result_message(f"tc-{i}", f"tool_{i}", {"n": i, "payload": "y" * 500})
            )
        return messages

    def test_never_deletes_a_tool_message(self):
        loop = _make_loop()
        messages = self._messages_with_n_tool_calls(loop, 5)
        before = len(messages)

        loop._prune_tool_results(messages)

        assert len(messages) == before

    def test_keeps_last_n_verbatim(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_TOOL_RESULT_KEEP_RECENT", "2")
        loop = _make_loop()
        messages = self._messages_with_n_tool_calls(loop, 5)
        original_contents = [m.content for m in messages if m.role == "tool"]

        loop._prune_tool_results(messages)

        tool_messages = [m for m in messages if m.role == "tool"]
        # Last 2 unchanged (still full JSON, not digested)
        assert tool_messages[-1].content == original_contents[-1]
        assert tool_messages[-2].content == original_contents[-2]
        # Older ones digested
        for m in tool_messages[:-2]:
            assert m.content.startswith("[tool result elided")

    def test_digest_preserves_tool_call_id(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_TOOL_RESULT_KEEP_RECENT", "1")
        loop = _make_loop()
        messages = self._messages_with_n_tool_calls(loop, 3)
        tool_call_ids_before = [m.tool_call_id for m in messages if m.role == "tool"]

        loop._prune_tool_results(messages)

        tool_call_ids_after = [m.tool_call_id for m in messages if m.role == "tool"]
        assert tool_call_ids_after == tool_call_ids_before

    def test_digest_recovers_tool_name_from_preceding_assistant_message(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_TOOL_RESULT_KEEP_RECENT", "0")
        loop = _make_loop()
        messages = self._messages_with_n_tool_calls(loop, 1)

        loop._prune_tool_results(messages)

        tool_msg = next(m for m in messages if m.role == "tool")
        assert "tool_0" in tool_msg.content

    def test_idempotent_on_already_digested_messages(self, monkeypatch):
        """Running prune twice must not re-wrap an already-digested message."""
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_TOOL_RESULT_KEEP_RECENT", "0")
        loop = _make_loop()
        messages = self._messages_with_n_tool_calls(loop, 2)

        loop._prune_tool_results(messages)
        once = [m.content for m in messages if m.role == "tool"]
        loop._prune_tool_results(messages)
        twice = [m.content for m in messages if m.role == "tool"]

        assert once == twice

    def test_zero_keep_recent_digests_everything(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_TOOL_RESULT_KEEP_RECENT", "0")
        loop = _make_loop()
        messages = self._messages_with_n_tool_calls(loop, 3)

        loop._prune_tool_results(messages)

        for m in messages:
            if m.role == "tool":
                assert m.content.startswith("[tool result elided")

    def test_non_tool_messages_untouched(self):
        loop = _make_loop()
        messages = self._messages_with_n_tool_calls(loop, 2)
        system_before = messages[0]

        loop._prune_tool_results(messages)

        assert messages[0] is system_before
