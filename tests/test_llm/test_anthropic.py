# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_llm.test_anthropic — Unit tests for AnthropicLLMClient.

Uses sys.modules patching to stub the anthropic SDK so no real API calls
are made.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.llm.base import LLMMessage, ToolDefinition


def _make_anthropic_response(
    text: str = "Hello!",
    input_tokens: int = 10,
    output_tokens: int = 20,
    stop_reason: str = "end_turn",
) -> MagicMock:
    """Build a mock mimicking anthropic.types.Message."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    response = MagicMock()
    response.content = [text_block]
    response.usage = usage
    response.stop_reason = stop_reason
    return response


@contextmanager
def _mock_anthropic_sdk(response: MagicMock):
    """Inject a stub anthropic module into sys.modules."""
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_client.messages.count_tokens = AsyncMock(return_value=MagicMock(input_tokens=42))
    mock_client.close = AsyncMock()
    mock_sdk.AsyncAnthropic.return_value = mock_client

    original = sys.modules.get("anthropic")
    sys.modules["anthropic"] = mock_sdk
    try:
        yield mock_sdk, mock_client
    finally:
        if original is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = original


# ---------------------------------------------------------------------------
# complete() — happy path
# ---------------------------------------------------------------------------


async def test_complete_returns_llm_response():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response("Hello!", 10, 20)
    with _mock_anthropic_sdk(mock_resp):
        client = AnthropicLLMClient()
        result = await client.complete([LLMMessage(role="user", content="Hi")])

    assert result.content == "Hello!"
    assert result.tokens_used == 30
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 20
    assert result.stop_reason == "end_turn"
    assert result.tool_calls == []


async def test_complete_extracts_system_message():
    """System messages should be passed as Anthropic's top-level system= param."""
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    with _mock_anthropic_sdk(mock_resp) as (_, mock_client):
        client = AnthropicLLMClient()
        await client.complete(
            [
                LLMMessage(role="system", content="You are helpful."),
                LLMMessage(role="user", content="Hello"),
            ]
        )

    kwargs = mock_client.messages.create.call_args[1]
    assert kwargs["system"] == "You are helpful."
    # System message should not appear in messages list
    for msg in kwargs["messages"]:
        assert msg["role"] != "system"


async def test_complete_no_system_message():
    """Without a system message, system= param should not be passed."""
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    with _mock_anthropic_sdk(mock_resp) as (_, mock_client):
        client = AnthropicLLMClient()
        await client.complete([LLMMessage(role="user", content="Hello")])

    kwargs = mock_client.messages.create.call_args[1]
    assert "system" not in kwargs


async def test_complete_passes_tools():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    tool = ToolDefinition(
        name="search",
        description="Search the web",
        parameters={"type": "object", "properties": {}},
    )
    with _mock_anthropic_sdk(mock_resp) as (_, mock_client):
        client = AnthropicLLMClient()
        await client.complete(
            [LLMMessage(role="user", content="hi")],
            tools=[tool],
        )

    kwargs = mock_client.messages.create.call_args[1]
    assert "tools" in kwargs
    assert kwargs["tools"][0]["name"] == "search"
    assert "input_schema" in kwargs["tools"][0]  # Anthropic format


async def test_complete_uses_default_model():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    with _mock_anthropic_sdk(mock_resp) as (_, mock_client):
        client = AnthropicLLMClient(default_model="claude-opus-4-6")
        result = await client.complete([LLMMessage(role="user", content="hi")])

    assert result.model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# complete() — error path
# ---------------------------------------------------------------------------


async def test_complete_api_error_raises_runtime_error():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    with _mock_anthropic_sdk(mock_resp) as (_, mock_client):
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        client = AnthropicLLMClient()
        with pytest.raises(RuntimeError, match="Anthropic API call failed"):
            await client.complete([LLMMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


async def test_count_tokens_uses_sdk():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    with _mock_anthropic_sdk(mock_resp) as (_, mock_client):
        client = AnthropicLLMClient()
        count = await client.count_tokens([LLMMessage(role="user", content="Hello")])

    assert count == 42


async def test_count_tokens_fallback_on_error():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    with _mock_anthropic_sdk(mock_resp) as (_, mock_client):
        mock_client.messages.count_tokens = AsyncMock(side_effect=Exception("error"))
        client = AnthropicLLMClient()
        count = await client.count_tokens([LLMMessage(role="user", content="Hello")])

    assert count >= 0


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


async def test_close_closes_sdk_client():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    with _mock_anthropic_sdk(mock_resp) as (_, mock_client):
        client = AnthropicLLMClient()
        # Trigger lazy client creation
        await client.complete([LLMMessage(role="user", content="hi")])
        await client.close()

    mock_client.close.assert_awaited_once()


async def test_close_without_creating_client_is_noop():
    """close() before any call should not raise."""
    from graphclaw.llm.anthropic.client import AnthropicLLMClient

    mock_resp = _make_anthropic_response()
    with _mock_anthropic_sdk(mock_resp):
        client = AnthropicLLMClient()
        await client.close()  # _client is still None
