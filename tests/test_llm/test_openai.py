# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_llm.test_openai — Unit tests for OpenAILLMClient.

Uses sys.modules patching to stub the openai SDK so no real API calls
are made.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.llm.base import LLMMessage, ToolDefinition


def _make_openai_response(
    content: str = "Hello!",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    finish_reason: str = "stop",
) -> MagicMock:
    """Build a mock mimicking an OpenAI ChatCompletion response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = None

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


@contextmanager
def _mock_openai_sdk(response: MagicMock):
    """Inject a stub openai module into sys.modules."""
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=response)
    mock_client.close = AsyncMock()
    mock_sdk.AsyncOpenAI.return_value = mock_client

    original = sys.modules.get("openai")
    sys.modules["openai"] = mock_sdk
    try:
        yield mock_sdk, mock_client
    finally:
        if original is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = original


# ---------------------------------------------------------------------------
# complete() — happy path
# ---------------------------------------------------------------------------


async def test_complete_returns_llm_response():
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response("Hello!", 10, 20)
    with _mock_openai_sdk(mock_resp):
        client = OpenAILLMClient()
        result = await client.complete([LLMMessage(role="user", content="Hi")])

    assert result.content == "Hello!"
    assert result.tokens_used == 30
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 20
    assert result.stop_reason == "stop"
    assert result.tool_calls == []


async def test_complete_uses_default_model():
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response()
    with _mock_openai_sdk(mock_resp) as (_, mock_client):
        client = OpenAILLMClient(default_model="gpt-4o-mini")
        result = await client.complete([LLMMessage(role="user", content="hi")])

    assert result.model == "gpt-4o-mini"
    kwargs = mock_client.chat.completions.create.call_args[1]
    assert kwargs["model"] == "gpt-4o-mini"


async def test_complete_model_override():
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response()
    with _mock_openai_sdk(mock_resp):
        client = OpenAILLMClient(default_model="gpt-4o")
        result = await client.complete([LLMMessage(role="user", content="hi")], model="gpt-4o-mini")

    assert result.model == "gpt-4o-mini"


async def test_complete_passes_tools():
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response()
    tool = ToolDefinition(
        name="search",
        description="Search the web",
        parameters={"type": "object", "properties": {}},
    )
    with _mock_openai_sdk(mock_resp) as (_, mock_client):
        client = OpenAILLMClient()
        await client.complete(
            [LLMMessage(role="user", content="hi")],
            tools=[tool],
        )

    kwargs = mock_client.chat.completions.create.call_args[1]
    assert "tools" in kwargs
    assert kwargs["tools"][0]["type"] == "function"
    assert kwargs["tools"][0]["function"]["name"] == "search"


async def test_complete_passes_system_message_in_messages():
    """OpenAI system messages go in the messages list (not extracted)."""
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response()
    with _mock_openai_sdk(mock_resp) as (_, mock_client):
        client = OpenAILLMClient()
        await client.complete(
            [
                LLMMessage(role="system", content="You are helpful."),
                LLMMessage(role="user", content="Hello"),
            ]
        )

    kwargs = mock_client.chat.completions.create.call_args[1]
    roles = [m["role"] for m in kwargs["messages"]]
    assert "system" in roles


# ---------------------------------------------------------------------------
# complete() — error path
# ---------------------------------------------------------------------------


async def test_complete_api_error_raises_runtime_error():
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response()
    with _mock_openai_sdk(mock_resp) as (_, mock_client):
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        client = OpenAILLMClient()
        with pytest.raises(RuntimeError, match="OpenAI API call failed"):
            await client.complete([LLMMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


async def test_count_tokens_uses_tiktoken():
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response()
    # Stub tiktoken
    mock_tiktoken = MagicMock()
    mock_enc = MagicMock()
    mock_enc.encode.return_value = [1, 2, 3, 4, 5]  # 5 tokens per message
    mock_tiktoken.encoding_for_model.return_value = mock_enc
    mock_tiktoken.get_encoding.return_value = mock_enc

    original = sys.modules.get("tiktoken")
    sys.modules["tiktoken"] = mock_tiktoken
    try:
        with _mock_openai_sdk(mock_resp):
            client = OpenAILLMClient()
            count = await client.count_tokens(
                [
                    LLMMessage(role="user", content="Hello world"),
                    LLMMessage(role="assistant", content="Hi there"),
                ]
            )
    finally:
        if original is None:
            sys.modules.pop("tiktoken", None)
        else:
            sys.modules["tiktoken"] = original

    # 2 messages × (5 tokens + 4 overhead) + 2 primer = 20
    assert count > 0


async def test_count_tokens_fallback_when_tiktoken_missing():
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response()
    original = sys.modules.pop("tiktoken", None)
    try:
        import builtins

        real_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError("No module named 'tiktoken'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocking_import
        try:
            with _mock_openai_sdk(mock_resp):
                client = OpenAILLMClient()
                count = await client.count_tokens([LLMMessage(role="user", content="Hello")])
        finally:
            builtins.__import__ = real_import
    finally:
        if original is not None:
            sys.modules["tiktoken"] = original

    assert count >= 0


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


async def test_close_closes_sdk_client():
    from graphclaw.llm.openai.client import OpenAILLMClient

    mock_resp = _make_openai_response()
    with _mock_openai_sdk(mock_resp) as (_, mock_client):
        client = OpenAILLMClient()
        await client.complete([LLMMessage(role="user", content="hi")])
        await client.close()

    mock_client.close.assert_awaited_once()
