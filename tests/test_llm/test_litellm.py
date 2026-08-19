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


# ---------------------------------------------------------------------------
# _translate_messages — multi-turn tool call serialization
# ---------------------------------------------------------------------------


def test_translate_messages_basic_user():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    msgs = [LLMMessage(role="user", content="Hello")]
    result = LiteLLMLLMClient._translate_messages(msgs)
    assert result == [{"role": "user", "content": "Hello"}]


def test_translate_messages_preserves_assistant_tool_calls():
    import json

    from graphclaw.llm.base import ToolCall
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    tc = ToolCall(
        id="call_abc123", name="update_task_state", arguments={"task_id": "t1", "new_state": "done"}
    )
    msgs = [
        LLMMessage(role="user", content="Do it"),
        LLMMessage(role="assistant", content="", tool_calls=[tc]),
    ]
    result = LiteLLMLLMClient._translate_messages(msgs)

    assert len(result) == 2
    asst = result[1]
    assert asst["role"] == "assistant"
    assert "tool_calls" in asst
    assert asst["tool_calls"][0]["id"] == "call_abc123"
    assert asst["tool_calls"][0]["type"] == "function"
    assert asst["tool_calls"][0]["function"]["name"] == "update_task_state"
    parsed_args = json.loads(asst["tool_calls"][0]["function"]["arguments"])
    assert parsed_args == {"task_id": "t1", "new_state": "done"}


def test_translate_messages_preserves_tool_call_id():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    msgs = [
        LLMMessage(role="tool", content='{"result": "ok"}', tool_call_id="call_abc123"),
    ]
    result = LiteLLMLLMClient._translate_messages(msgs)
    assert result[0]["tool_call_id"] == "call_abc123"


def test_translate_messages_fallback_uuid_for_empty_tool_call_id():
    from graphclaw.llm.base import ToolCall
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    tc = ToolCall(id="", name="some_tool", arguments={})
    msgs = [LLMMessage(role="assistant", content="", tool_calls=[tc])]
    result = LiteLLMLLMClient._translate_messages(msgs)
    generated_id = result[0]["tool_calls"][0]["id"]
    assert generated_id.startswith("call_")
    assert len(generated_id) > len("call_")


async def test_complete_sends_tool_calls_in_history():
    """When a prior assistant message has tool_calls, complete() must send them to LiteLLM."""
    from graphclaw.llm.base import ToolCall
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    tc = ToolCall(id="call_xyz", name="get_task", arguments={"id": "t1"})
    messages = [
        LLMMessage(role="user", content="show task"),
        LLMMessage(role="assistant", content="", tool_calls=[tc]),
        LLMMessage(role="tool", content='{"title": "My task"}', tool_call_id="call_xyz"),
        LLMMessage(role="user", content="thanks"),
    ]

    mock_resp = _make_litellm_response("Done!")
    with _mock_litellm(mock_resp) as mock_litellm:
        client = LiteLLMLLMClient()
        await client.complete(messages)

    sent = mock_litellm.acompletion.call_args[1]["messages"]
    # assistant turn must carry tool_calls
    assert "tool_calls" in sent[1]
    assert sent[1]["tool_calls"][0]["id"] == "call_xyz"
    # tool turn must carry tool_call_id
    assert sent[2]["tool_call_id"] == "call_xyz"


# ---------------------------------------------------------------------------
# Text-based tool-call fallback (Ollama / Qwen models)
# ---------------------------------------------------------------------------


def test_needs_text_tool_fallback_ollama():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    # Ollama models have native tool-calling support — no text fallback needed
    assert LiteLLMLLMClient._needs_text_tool_fallback("ollama/qwen2.5") is False
    assert LiteLLMLLMClient._needs_text_tool_fallback("ollama/llama3.2") is False
    assert LiteLLMLLMClient._needs_text_tool_fallback("anthropic/claude-sonnet-4-20250514") is False
    assert LiteLLMLLMClient._needs_text_tool_fallback("openai/gpt-4o") is False


def test_parse_text_tool_calls_valid():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    content = '{"tool_call": {"name": "list_available_agents", "arguments": {"capability_filter": "email"}}}'
    result = LiteLLMLLMClient._parse_text_tool_calls(content)
    assert len(result) == 1
    assert result[0].name == "list_available_agents"
    assert result[0].arguments == {"capability_filter": "email"}
    assert result[0].id.startswith("call_")


def test_parse_text_tool_calls_no_args():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    content = '{"tool_call": {"name": "get_current_tasks", "arguments": {}}}'
    result = LiteLLMLLMClient._parse_text_tool_calls(content)
    assert len(result) == 1
    assert result[0].name == "get_current_tasks"


def test_parse_text_tool_calls_plain_text():
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    result = LiteLLMLLMClient._parse_text_tool_calls("Sure, I can help with that!")
    assert result == []


def test_parse_text_tool_calls_schema_echo_not_parsed():
    """A tool schema definition echoed by the model must NOT be mistaken for a tool call."""
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    schema_echo = (
        '{"type":"function","name":"list_available_agents","description":"List all agents..."}'
    )
    result = LiteLLMLLMClient._parse_text_tool_calls(schema_echo)
    assert result == []


async def test_complete_ollama_uses_native_tool_calling():
    """For ollama/ models, tools must be passed natively (not injected as system prompt)."""
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    tool = ToolDefinition(
        name="get_task",
        description="Fetch a task by ID",
        parameters={"type": "object", "properties": {"id": {"type": "string"}}},
    )
    mock_resp = _make_litellm_response("Here is the task.")
    with _mock_litellm(mock_resp) as mock_litellm:
        client = LiteLLMLLMClient(default_model="ollama/qwen2.5")
        await client.complete(
            [LLMMessage(role="user", content="show task t1")],
            tools=[tool],
        )

    kwargs = mock_litellm.acompletion.call_args[1]
    # Native tools kwarg MUST be present for Ollama
    assert "tools" in kwargs
    assert kwargs["tools"][0]["function"]["name"] == "get_task"
    # System message must NOT be injected with tool text
    assert not any(
        "tool_call" in m.get("content", "") and "TOOL_NAME" in m.get("content", "")
        for m in kwargs["messages"]
    )


# ---------------------------------------------------------------------------
# api_base resolution — per-call, not bound to the constructor's default model
#
# A single LiteLLM client serves every LLMRole, so api_base must be derived from
# the *target* model on each call. Binding it at construction leaks in both
# directions (see LiteLLMLLMClient._resolve_api_base).
# ---------------------------------------------------------------------------


async def test_api_base_set_for_ollama_target_when_default_is_hosted(monkeypatch):
    """An ollama/ per-call model must get api_base even on a hosted-default client."""
    # config.app is a cached_property returning a frozen AppConfig (resolved at
    # first access for the process lifetime), so replace the cached instance
    # rather than the env var or a field on the frozen dataclass.
    import dataclasses

    from graphclaw.config import config
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    monkeypatch.setattr(
        config,
        "app",
        dataclasses.replace(config.app, ollama_base_url="http://ollama.test:11434"),
    )
    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        client = LiteLLMLLMClient(default_model="anthropic/claude-sonnet-4-6")
        await client.complete([LLMMessage(role="user", content="hi")], model="ollama/qwen2.5:7b")

    assert mock_litellm.acompletion.call_args[1]["api_base"] == "http://ollama.test:11434"


async def test_api_base_absent_for_hosted_target_when_default_is_ollama(monkeypatch):
    """Regression: the Ollama base URL must never be sent to a hosted provider."""
    # config.app is a cached_property returning a frozen AppConfig (resolved at
    # first access for the process lifetime), so replace the cached instance
    # rather than the env var or a field on the frozen dataclass.
    import dataclasses

    from graphclaw.config import config
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    monkeypatch.setattr(
        config,
        "app",
        dataclasses.replace(config.app, ollama_base_url="http://ollama.test:11434"),
    )
    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        client = LiteLLMLLMClient(default_model="ollama/qwen2.5:7b")
        await client.complete(
            [LLMMessage(role="user", content="hi")], model="anthropic/claude-sonnet-4-6"
        )

    assert "api_base" not in mock_litellm.acompletion.call_args[1]


async def test_explicit_api_base_ctor_arg_wins_for_every_model(monkeypatch):
    """An explicit constructor api_base applies regardless of the target model."""
    # config.app is a cached_property returning a frozen AppConfig (resolved at
    # first access for the process lifetime), so replace the cached instance
    # rather than the env var or a field on the frozen dataclass.
    import dataclasses

    from graphclaw.config import config
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    monkeypatch.setattr(
        config,
        "app",
        dataclasses.replace(config.app, ollama_base_url="http://ollama.test:11434"),
    )
    mock_resp = _make_litellm_response()
    with _mock_litellm(mock_resp) as mock_litellm:
        client = LiteLLMLLMClient(
            default_model="ollama/qwen2.5:7b", api_base="http://proxy.test:4000"
        )
        await client.complete(
            [LLMMessage(role="user", content="hi")], model="anthropic/claude-sonnet-4-6"
        )

    assert mock_litellm.acompletion.call_args[1]["api_base"] == "http://proxy.test:4000"


async def test_stream_resolves_api_base_per_call(monkeypatch):
    """The streaming path must resolve api_base from the target model too."""
    # config.app is a cached_property returning a frozen AppConfig (resolved at
    # first access for the process lifetime), so replace the cached instance
    # rather than the env var or a field on the frozen dataclass.
    import dataclasses

    from graphclaw.config import config
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    monkeypatch.setattr(
        config,
        "app",
        dataclasses.replace(config.app, ollama_base_url="http://ollama.test:11434"),
    )
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = MagicMock()
    chunk.choices[0].delta.content = "hi"
    chunk.choices[0].delta.tool_calls = None
    chunk.choices[0].finish_reason = "stop"

    mock_module = MagicMock()
    mock_module.acompletion = AsyncMock(return_value=_MockLiteLLMStream([chunk]))
    original = sys.modules.get("litellm")
    sys.modules["litellm"] = mock_module
    try:
        client = LiteLLMLLMClient(default_model="anthropic/claude-sonnet-4-6")
        async for _ in client.stream(
            [LLMMessage(role="user", content="hi")], model="ollama/qwen2.5:7b"
        ):
            pass
    finally:
        if original is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = original

    assert mock_module.acompletion.call_args[1]["api_base"] == "http://ollama.test:11434"


def test_api_base_property_reflects_default_model(monkeypatch):
    """The public api_base property describes the client's default model."""
    # config.app is a cached_property returning a frozen AppConfig (resolved at
    # first access for the process lifetime), so replace the cached instance
    # rather than the env var or a field on the frozen dataclass.
    import dataclasses

    from graphclaw.config import config
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    monkeypatch.setattr(
        config,
        "app",
        dataclasses.replace(config.app, ollama_base_url="http://ollama.test:11434"),
    )
    assert LiteLLMLLMClient(default_model="ollama/qwen2.5").api_base == ("http://ollama.test:11434")
    assert LiteLLMLLMClient(default_model="anthropic/claude-sonnet-4-6").api_base is None
