# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_agent.test_stream_parity — Streaming-path parity with the
non-streaming agentic loop: stuck-loop detection and post-turn distillation.

Before this fix, only MainOrchestrator.process_chat_message (non-streaming)
had a stuck-loop guard and called _run_distillation. The streaming path
(process_chat_message_stream — the cockpit's primary UI) had neither: a
looping model would burn all 15 iterations, and memory was never written
from the primary chat surface.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.main_orchestrator import MainOrchestrator as AgentLoop
from graphclaw.llm.base import LLMClient, LLMResponse, LLMStreamChunk, ToolCall
from graphclaw.state.machine import StateMachine


class _StuckToolLLM(LLMClient):
    """Streams the same tool call forever — triggers the stuck-loop guard."""

    def __init__(self) -> None:
        self.call_count = 0

    async def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None):
        raise NotImplementedError

    async def stream(self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None):
        self.call_count += 1
        tc = ToolCall(id=f"tc-{self.call_count}", name="fake_tool", arguments={"x": 1})
        response = LLMResponse(
            content="",
            model="fake",
            tokens_used=1,
            prompt_tokens=1,
            completion_tokens=0,
            cost_usd=0.0,
            tool_calls=[tc],
        )
        yield LLMStreamChunk(content_delta="", is_final=False)
        yield LLMStreamChunk(is_final=True, accumulated=response)

    async def count_tokens(self, messages, *, model=None):
        return 1

    async def close(self):
        return None


class _TextOnlyLLM(LLMClient):
    """Streams a single text reply with no tool calls."""

    async def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None):
        raise NotImplementedError

    async def stream(self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None):
        response = LLMResponse(
            content="Hello!",
            model="fake",
            tokens_used=1,
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=0.0,
        )
        yield LLMStreamChunk(content_delta="Hello!", is_final=False)
        yield LLMStreamChunk(is_final=True, accumulated=response)

    async def count_tokens(self, messages, *, model=None):
        return 1

    async def close(self):
        return None


def _make_loop(llm: LLMClient) -> AgentLoop:
    repo = AsyncMock()
    repo._pool = None
    repo.list_nodes_by_user = AsyncMock(return_value=[])
    engine = MagicMock()
    engine.cache = MagicMock()
    return AgentLoop(
        graph_repo=repo,
        scoring_engine=engine,
        state_machine=StateMachine(),
        llm_client=llm,
        storage_client=None,  # no storage -> _run_distillation is a real no-op unless mocked
    )


class TestStreamingStuckLoopGuard:
    @pytest.mark.asyncio
    async def test_streaming_loop_aborts_on_repeated_identical_tool_call(self):
        llm = _StuckToolLLM()
        loop = _make_loop(llm)

        events = [
            event async for event in loop.process_chat_message_stream(user_id="USER-1", text="hi")
        ]

        assert events[-1].event_type == "run.failed"
        assert events[-1].payload.error_class == "StuckToolLoop"
        # Aborted well before the 15-iteration cap — at most 3 LLM calls.
        assert llm.call_count <= 3

    @pytest.mark.asyncio
    async def test_streaming_loop_does_not_abort_on_varying_tool_calls(self):
        """Sanity check: the guard must not misfire on genuinely different
        tool calls — only on 3 consecutive IDENTICAL ones."""

        class _VaryingLLM(LLMClient):
            def __init__(self) -> None:
                self.call_count = 0

            async def complete(self, *a, **kw):
                raise NotImplementedError

            async def stream(
                self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None
            ):
                self.call_count += 1
                if self.call_count <= 2:
                    tc = ToolCall(
                        id=f"tc-{self.call_count}",
                        name="fake_tool",
                        arguments={"round": self.call_count},
                    )
                    response = LLMResponse(
                        content="",
                        model="fake",
                        tokens_used=1,
                        prompt_tokens=1,
                        completion_tokens=0,
                        cost_usd=0.0,
                        tool_calls=[tc],
                    )
                else:
                    response = LLMResponse(
                        content="Done.",
                        model="fake",
                        tokens_used=1,
                        prompt_tokens=1,
                        completion_tokens=1,
                        cost_usd=0.0,
                    )
                yield LLMStreamChunk(is_final=True, accumulated=response)

            async def count_tokens(self, messages, *, model=None):
                return 1

            async def close(self):
                return None

        llm = _VaryingLLM()
        loop = _make_loop(llm)

        events = [
            event async for event in loop.process_chat_message_stream(user_id="USER-1", text="hi")
        ]

        assert events[-1].event_type == "run.completed"


class TestStreamingDistillationParity:
    @pytest.mark.asyncio
    async def test_text_only_completion_schedules_distillation(self):
        """Regression: the streaming path never called _run_distillation at
        all, so the cockpit (streaming) chat surface never wrote memory."""
        llm = _TextOnlyLLM()
        loop = _make_loop(llm)
        loop._run_distillation = AsyncMock()

        events = [
            event
            async for event in loop.process_chat_message_stream(
                user_id="USER-1", text="hello there", channel="cockpit"
            )
        ]

        assert events[-1].event_type == "run.completed"
        loop._run_distillation.assert_called_once()
        call_kwargs = loop._run_distillation.call_args.kwargs
        assert call_kwargs["user_id"] == "USER-1"
        assert call_kwargs["text"] == "hello there"
        assert call_kwargs["reply"] == "Hello!"
        assert call_kwargs["channel"] == "cockpit"

    @pytest.mark.asyncio
    async def test_stuck_loop_abort_does_not_schedule_distillation(self):
        """No coherent reply exists when the loop aborts on a stuck tool
        call — distillation must not fire for an aborted turn."""
        llm = _StuckToolLLM()
        loop = _make_loop(llm)
        loop._run_distillation = AsyncMock()

        _ = [event async for event in loop.process_chat_message_stream(user_id="USER-1", text="hi")]

        loop._run_distillation.assert_not_called()
