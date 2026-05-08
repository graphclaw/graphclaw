# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_llm.test_router_compat — Tests for LLMRouter backward compatibility.

Verifies that LLMRouter continues to return the same dict-based response
contract after being refactored to delegate to LLMClient, and that the
existing LLMRouter test suite contract is preserved.

These tests verify backward compatibility so that SkillWorker continues
to work unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.llm.base import LLMMessage, LLMResponse


def _make_llm_response(
    content: str = "Hello!",
    tokens_used: int = 30,
    cost_usd: float = 0.001,
    model: str = "claude-sonnet-4-20250514",
) -> LLMResponse:
    return LLMResponse(
        content=content,
        model=model,
        tokens_used=tokens_used,
        prompt_tokens=10,
        completion_tokens=20,
        cost_usd=cost_usd,
    )


def _make_mock_llm_client(response: LLMResponse) -> MagicMock:
    mock = MagicMock()
    mock.complete = AsyncMock(return_value=response)
    return mock


# ---------------------------------------------------------------------------
# Backward-compatible dict contract
# ---------------------------------------------------------------------------


async def test_router_returns_content_dict():
    from graphclaw.skills.llm_router import LLMRouter

    mock_client = _make_mock_llm_client(
        _make_llm_response("Summary text", tokens_used=50, cost_usd=0.002)
    )
    router = LLMRouter(llm_client=mock_client)
    result = await router.complete(
        model="claude-sonnet-4-20250514",
        system_prompt="You are helpful.",
        user_message="Summarise this.",
    )

    assert result["content"] == "Summary text"
    assert result["tokens_used"] == 50
    assert result["cost_usd"] == 0.002
    assert result["model"] == "claude-sonnet-4-20250514"


async def test_router_uses_default_model():
    from graphclaw.skills.llm_router import LLMRouter

    mock_client = _make_mock_llm_client(_make_llm_response(model="my-default"))
    router = LLMRouter(default_model="my-default", llm_client=mock_client)
    result = await router.complete()

    assert result["model"] == "my-default"


async def test_router_model_override():
    from graphclaw.skills.llm_router import LLMRouter

    mock_client = _make_mock_llm_client(_make_llm_response(model="override-model"))
    router = LLMRouter(default_model="default", llm_client=mock_client)
    result = await router.complete(model="override-model")

    assert result["model"] == "override-model"


async def test_router_passes_system_and_user_messages():
    from graphclaw.skills.llm_router import LLMRouter

    mock_client = _make_mock_llm_client(_make_llm_response())
    router = LLMRouter(llm_client=mock_client)
    await router.complete(
        system_prompt="System prompt here.",
        user_message="User message here.",
    )

    call_args = mock_client.complete.call_args
    messages: list[LLMMessage] = call_args[0][0]
    assert any(m.role == "system" and "System prompt" in m.content for m in messages)
    assert any(m.role == "user" and "User message" in m.content for m in messages)


async def test_router_passes_max_tokens_and_temperature():
    from graphclaw.skills.llm_router import LLMRouter

    mock_client = _make_mock_llm_client(_make_llm_response())
    router = LLMRouter(llm_client=mock_client)
    await router.complete(max_tokens=512, temperature=0.7)

    kwargs = mock_client.complete.call_args[1]
    assert kwargs["max_tokens"] == 512
    assert kwargs["temperature"] == 0.7


async def test_router_reraises_runtime_error():
    from graphclaw.skills.llm_router import LLMRouter

    mock_client = MagicMock()
    mock_client.complete = AsyncMock(side_effect=RuntimeError("LiteLLM call failed: err"))
    router = LLMRouter(llm_client=mock_client)

    with pytest.raises(RuntimeError, match="LiteLLM call failed"):
        await router.complete()


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


async def test_router_accepts_injected_client():
    """LLMRouter(llm_client=...) should use the injected client."""
    from graphclaw.skills.llm_router import LLMRouter

    mock_client = _make_mock_llm_client(_make_llm_response("Injected!"))
    router = LLMRouter(llm_client=mock_client)
    result = await router.complete()

    assert result["content"] == "Injected!"


async def test_router_default_uses_litellm_provider():
    """LLMRouter() with no client should create LiteLLMLLMClient internally."""
    import sys
    from unittest.mock import AsyncMock, MagicMock

    # Stub litellm so router creation and complete() work
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "litellm response"
    mock_resp.choices[0].message.tool_calls = None
    mock_resp.choices[0].finish_reason = "stop"
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 5
    mock_resp.usage.completion_tokens = 10

    mock_litellm = MagicMock()
    mock_litellm.acompletion = AsyncMock(return_value=mock_resp)

    original = sys.modules.get("litellm")
    sys.modules["litellm"] = mock_litellm
    try:
        from graphclaw.llm.litellm.client import LiteLLMLLMClient
        from graphclaw.skills.llm_router import LLMRouter

        router = LLMRouter()
        assert isinstance(router._client, LiteLLMLLMClient)

        result = await router.complete(user_message="hello")
        assert result["content"] == "litellm response"
    finally:
        if original is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = original
