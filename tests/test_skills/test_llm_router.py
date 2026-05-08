# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_skills.test_llm_router — Unit tests for graphclaw.skills.llm_router.LLMRouter.

Description
-----------
Verifies that LLMRouter.complete correctly delegates to litellm.acompletion,
returns the normalised response dict, uses the default model when no model
is specified, and raises RuntimeError on failure or missing litellm.

Design Patterns
---------------
- sys.modules Patching: Because litellm is imported inside complete() via a
  lazy ``import litellm`` statement, tests inject a MagicMock into
  ``sys.modules["litellm"]`` rather than patching a module-level name.
- Arrange/Act/Assert: Each test sets up the mock, calls complete(), then
  asserts on the return value or raised exception.

Dependencies
------------
- pytest, pytest-asyncio: Async test runner.
- unittest.mock: AsyncMock, MagicMock, patch.
- sys: Module registry for litellm injection.
- graphclaw.skills.llm_router: LLMRouter under test.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.skills.llm_router import LLMRouter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_litellm_response(
    content: str = "Hello!",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
) -> MagicMock:
    """Build a mock object that mimics a litellm ModelResponse."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


@contextmanager
def _mock_litellm(response: MagicMock):
    """Context manager: inject a fake litellm into sys.modules for the duration."""
    mock_module = MagicMock()
    mock_module.acompletion = AsyncMock(return_value=response)
    # Store existing entry (litellm is actually installed in this project)
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


async def test_complete_calls_litellm() -> None:
    """complete() should call litellm.acompletion with the correct arguments."""
    mock_response = _make_litellm_response("Result text")

    with _mock_litellm(mock_response) as mock_litellm:
        router = LLMRouter()
        await router.complete(
            model="claude-sonnet-4-20250514",
            system_prompt="You are helpful.",
            user_message="Say hello.",
            max_tokens=512,
            temperature=0.0,
        )

        mock_litellm.acompletion.assert_awaited_once()
        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["temperature"] == 0.0
        messages = call_kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Say hello."


async def test_complete_returns_content() -> None:
    """complete() should return a dict with content, tokens_used, cost_usd, model."""
    mock_response = _make_litellm_response(
        content="Summary here.", prompt_tokens=5, completion_tokens=15
    )

    with _mock_litellm(mock_response):
        router = LLMRouter()
        result = await router.complete(model="test-model", user_message="test")

    assert result["content"] == "Summary here."
    assert result["tokens_used"] == 20  # 5 + 15
    assert result["cost_usd"] == 0.0
    assert result["model"] == "test-model"


async def test_complete_default_model() -> None:
    """complete() with no model argument should use the default model."""
    mock_response = _make_litellm_response()

    with _mock_litellm(mock_response) as mock_litellm:
        router = LLMRouter(default_model="my-default-model")
        result = await router.complete()

    assert result["model"] == "my-default-model"
    call_kwargs = mock_litellm.acompletion.call_args[1]
    assert call_kwargs["model"] == "my-default-model"


async def test_complete_custom_model_overrides_default() -> None:
    """Passing model= to complete() must override the default model."""
    mock_response = _make_litellm_response()

    with _mock_litellm(mock_response):
        router = LLMRouter(default_model="default-model")
        result = await router.complete(model="custom-model")

    assert result["model"] == "custom-model"


async def test_complete_none_usage_gives_zero_tokens() -> None:
    """complete() should return tokens_used=0 when the response has no usage."""
    mock_response = _make_litellm_response()
    mock_response.usage = None  # simulate no usage block

    with _mock_litellm(mock_response):
        router = LLMRouter()
        result = await router.complete()

    assert result["tokens_used"] == 0


# ---------------------------------------------------------------------------
# complete() — error paths
# ---------------------------------------------------------------------------


async def test_complete_failure_raises_runtime_error() -> None:
    """complete() should raise RuntimeError when litellm raises an exception."""
    mock_response = _make_litellm_response()

    with _mock_litellm(mock_response) as mock_litellm:
        mock_litellm.acompletion = AsyncMock(side_effect=Exception("API error"))

        router = LLMRouter()
        with pytest.raises(RuntimeError, match="LLM call failed"):
            await router.complete()


async def test_complete_import_error_raises_runtime_error() -> None:
    """complete() should raise RuntimeError with helpful message if litellm is missing."""
    router = LLMRouter()

    # Remove litellm from sys.modules to simulate it not being installed
    original = sys.modules.pop("litellm", None)
    try:
        import builtins

        real_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "litellm":
                raise ImportError("No module named 'litellm'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocking_import
        try:
            with pytest.raises(RuntimeError, match="litellm is required"):
                await router.complete()
        finally:
            builtins.__import__ = real_import
    finally:
        if original is not None:
            sys.modules["litellm"] = original
