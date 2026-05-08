# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_llm.test_base — Unit tests for graphclaw.llm.base data models and ABC.

Verifies that all frozen dataclasses instantiate correctly, that field
defaults behave as expected, and that LLMClient cannot be instantiated
directly (abstract methods enforced).
"""

from __future__ import annotations

import pytest

from graphclaw.llm.base import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolCall,
    ToolDefinition,
)

# ---------------------------------------------------------------------------
# LLMMessage
# ---------------------------------------------------------------------------


def test_llm_message_minimal():
    msg = LLMMessage(role="user", content="Hello")
    assert msg.role == "user"
    assert msg.content == "Hello"
    assert msg.tool_call_id is None
    assert msg.tool_calls == []


def test_llm_message_frozen():
    msg = LLMMessage(role="user", content="Hello")
    with pytest.raises(Exception):  # frozen dataclass raises FrozenInstanceError
        msg.role = "assistant"  # type: ignore[misc]


def test_llm_message_all_roles():
    for role in ("system", "user", "assistant", "tool"):
        msg = LLMMessage(role=role, content="text")
        assert msg.role == role


# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------


def test_tool_definition_roundtrip():
    tool = ToolDefinition(
        name="search",
        description="Search the web",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    assert tool.name == "search"
    assert tool.parameters["type"] == "object"


# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------


def test_tool_call_fields():
    tc = ToolCall(id="call-1", name="search", arguments={"query": "python"})
    assert tc.id == "call-1"
    assert tc.arguments["query"] == "python"


# ---------------------------------------------------------------------------
# LLMResponse
# ---------------------------------------------------------------------------


def test_llm_response_minimal():
    r = LLMResponse(
        content="Hello!",
        model="claude-sonnet-4-6",
        tokens_used=50,
        prompt_tokens=30,
        completion_tokens=20,
        cost_usd=0.001,
    )
    assert r.content == "Hello!"
    assert r.tokens_used == 50
    assert r.tool_calls == []
    assert r.stop_reason is None


def test_llm_response_with_tool_calls():
    tc = ToolCall(id="tc-1", name="search", arguments={})
    r = LLMResponse(
        content="",
        model="claude-sonnet-4-6",
        tokens_used=10,
        prompt_tokens=8,
        completion_tokens=2,
        cost_usd=0.0,
        tool_calls=[tc],
        stop_reason="tool_use",
    )
    assert len(r.tool_calls) == 1
    assert r.stop_reason == "tool_use"


# ---------------------------------------------------------------------------
# LLMStreamChunk
# ---------------------------------------------------------------------------


def test_stream_chunk_defaults():
    chunk = LLMStreamChunk()
    assert chunk.content_delta == ""
    assert chunk.is_final is False
    assert chunk.accumulated is None


def test_stream_chunk_final():
    response = LLMResponse(
        content="Done",
        model="gpt-4o",
        tokens_used=10,
        prompt_tokens=5,
        completion_tokens=5,
        cost_usd=0.0,
    )
    chunk = LLMStreamChunk(content_delta="Done", is_final=True, accumulated=response)
    assert chunk.is_final is True
    assert chunk.accumulated.content == "Done"


# ---------------------------------------------------------------------------
# LLMClient ABC cannot be instantiated directly
# ---------------------------------------------------------------------------


def test_llm_client_is_abstract():
    with pytest.raises(TypeError):
        LLMClient()  # type: ignore[abstract]


def test_llm_client_subclass_must_implement_all_methods():
    """A partial implementation should not be instantiable."""

    class PartialClient(LLMClient):
        async def complete(self, messages, **kwargs): ...

        # Missing stream, count_tokens, close

    with pytest.raises(TypeError):
        PartialClient()
