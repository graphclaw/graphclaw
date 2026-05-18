# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_llm.test_litellm — Unit tests for LiteLLMLLMClient.

Uses sys.modules patching to stub the litellm SDK so no real API calls
are made.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.llm.base import LLMMessage, ToolDefinition


def _make_litellm_response(
    content: str = "Hello!",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    tool_calls=None,
    finish_reason: str = "stop",
) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls  # None or list

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_litellm_stream_chunk(content: str, finish_reason: str | None = None) -> MagicMock:
    """Build a mock stream chunk matching LiteLLM delta shape."""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = None

    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason

    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


class _MockLiteLLMStream:
    """Async iterable used by stream() tests."""

    def __init__(self, chunks: list[MagicMock]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


@contextmanager
def _mock_litellm(response: MagicMock):
    mock_module = MagicMock()
    mock_module.acompletion = AsyncMock(return_value=response)
    mock_module.token_counter = MagicMock(return_value=42)
    original = sys.modules.get("litellm")
    sys.modules["litellm"] = mock_module
    try:
        yield mock_module
    finally:
        if original is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = original


# ---------------------------------------------------------------------------
# complete() — happy path
# ---------------------------------------------------------------------------


async def test_complete_returns_llm_response():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response("Result", 10, 20)
    with _mock_litellm(mock_resp):
        client = LiteLLMLLMClient()
        result = await client.complete([LLMMessage(role="user", content="Hello")])

    assert result.content == "Result"
    assert result.tokens_used == 30
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 20
    assert result.cost_usd == 0.0
    assert result.tool_calls == []


async def test_complete_uses_default_model():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        client = LiteLLMLLMClient(default_model="my-default")
        result = await client.complete([LMMsg := LLMMessage(role="user", content="hi")])

    assert result.model == "my-default"
    assert mock_litellm.acompletion.call_args[1]["model"] == "my-default"


async def test_complete_model_override():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        client = LiteLLMLLMClient(default_model="default")
        result = await client.complete(
            [LLMMessage(role="user", content="hi")], model="override-model"
        )

    assert result.model == "override-model"


async def test_complete_passes_tools_as_functions():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response()
    tool = ToolDefinition(
        name="search",
        description="Search the web",
        parameters={"type": "object", "properties": {}},
    )
    with _mock_litellm(mock_resp) as mock_litellm:
        client = LiteLLMLLMClient()
        await client.complete(
            [LLMMessage(role="user", content="hi")],
            tools=[tool],
        )

    kwargs = mock_litellm.acompletion.call_args[1]
    assert "tools" in kwargs
    assert kwargs["tools"][0]["function"]["name"] == "search"


async def test_complete_none_usage_gives_zero_tokens():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response()
    mock_resp.usage = None
    with _mock_litellm(mock_resp):
        client = LiteLLMLLMClient()
        result = await client.complete([LLMMessage(role="user", content="hi")])

    assert result.tokens_used == 0


# ---------------------------------------------------------------------------
# complete() — error paths
# ---------------------------------------------------------------------------


async def test_complete_api_error_raises_runtime_error():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        mock_litellm.acompletion = AsyncMock(side_effect=Exception("API down"))
        client = LiteLLMLLMClient()
        with pytest.raises(RuntimeError, match="LiteLLM call failed"):
            await client.complete([LLMMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# stream() — trace parity
# ---------------------------------------------------------------------------


async def test_stream_traces_success_with_accumulated_content():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        mock_litellm.acompletion = AsyncMock(
            return_value=_MockLiteLLMStream(
                [
                    _make_litellm_stream_chunk("Hel"),
                    _make_litellm_stream_chunk("lo", finish_reason="stop"),
                ]
            )
        )

        client = LiteLLMLLMClient()
        client._trace_llm_call = MagicMock()

        chunks = [chunk async for chunk in client.stream([LLMMessage(role="user", content="hi")])]

    assert chunks[-1].is_final is True
    assert chunks[-1].accumulated is not None
    assert chunks[-1].accumulated.content == "Hello"

    client._trace_llm_call.assert_called_once()
    traced = client._trace_llm_call.call_args.kwargs
    assert traced["provider"] == "litellm"
    assert traced["call_type"] == "stream"
    assert traced["response_content"] == "Hello"


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


async def test_count_tokens_delegates_to_litellm():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        mock_litellm.token_counter.return_value = 42
        client = LiteLLMLLMClient()
        count = await client.count_tokens([LLMMessage(role="user", content="Hello world")])

    assert count == 42


async def test_count_tokens_fallback_on_error():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        mock_litellm.token_counter.side_effect = Exception("counter error")
        client = LiteLLMLLMClient()
        # Should return a rough estimate without raising
        count = await client.count_tokens([LLMMessage(role="user", content="Hello world")])

    assert count >= 0


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


async def test_close_is_noop():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    client = LiteLLMLLMClient()
    await client.close()  # Should not raise
