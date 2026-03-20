# Adding an LLM Provider

GraphClaw's LLM layer is fully pluggable. Any provider can be added by implementing the `LLMClient` ABC.

## The LLMClient ABC

```python
# src/graphclaw/llm/base.py
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

class LLMClient(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    async def stream(
        self, messages: list[LLMMessage], **kwargs
    ) -> AsyncIterator[LLMStreamChunk]: ...

    @abstractmethod
    async def count_tokens(
        self, messages: list[LLMMessage], *, model: str | None = None
    ) -> int: ...

    @abstractmethod
    async def close(self) -> None: ...
```

## Shared Data Models

All providers communicate using these provider-agnostic types (all are frozen dataclasses):

```python
LLMMessage(role, content, tool_call_id=None, tool_calls=[])
LLMResponse(content, model, tokens_used, prompt_tokens, completion_tokens, cost_usd, tool_calls=[], stop_reason=None)
ToolDefinition(name, description, parameters)  # parameters = JSON Schema dict
ToolCall(id, name, arguments)                   # arguments = dict
LLMStreamChunk(content_delta="", is_final=False, accumulated=None)
```

## Step-by-Step: Add a New Provider

### 1. Create the directory

```
src/graphclaw/llm/
└── myprovider/
    ├── __init__.py
    └── client.py
```

### 2. Implement the ABC

```python
# src/graphclaw/llm/myprovider/client.py
"""LLM client for MyProvider SDK."""
# graphclaw - Apache 2.0 license

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from graphclaw.llm.base import (
    LLMClient, LLMMessage, LLMResponse, LLMStreamChunk, ToolDefinition, ToolCall
)

if TYPE_CHECKING:
    pass


class MyProviderLLMClient(LLMClient):
    """LLMClient implementation for MyProvider."""

    def __init__(self, api_key: str | None = None, default_model: str = "my-model-v1") -> None:
        import myprovider  # lazy import — only required if this backend is used
        self._client = myprovider.AsyncClient(api_key=api_key)
        self._default_model = default_model

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        _model = model or self._default_model

        # Translate LLMMessage list to provider format
        provider_messages = [{"role": m.role, "content": m.content} for m in messages]

        response = await self._client.chat.complete(
            model=_model,
            messages=provider_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        return LLMResponse(
            content=response.choices[0].message.content,
            model=_model,
            tokens_used=response.usage.total_tokens,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            cost_usd=0.0,  # compute from token counts if pricing is known
        )

    async def stream(
        self, messages: list[LLMMessage], **kwargs
    ) -> AsyncIterator[LLMStreamChunk]:
        # Implement streaming if supported
        response = await self.complete(messages, **kwargs)
        yield LLMStreamChunk(content_delta=response.content, is_final=True, accumulated=response)

    async def count_tokens(
        self, messages: list[LLMMessage], *, model: str | None = None
    ) -> int:
        # Use provider's token counting API or estimate locally
        return sum(len(m.content.split()) * 4 // 3 for m in messages)  # rough estimate

    async def close(self) -> None:
        await self._client.close()
```

### 3. Export from `__init__.py`

```python
# src/graphclaw/llm/myprovider/__init__.py
from graphclaw.llm.myprovider.client import MyProviderLLMClient

__all__ = ["MyProviderLLMClient"]
```

### 4. Register in the factory

```python
# src/graphclaw/llm/factory.py  — add to the match block:

case "myprovider":
    from graphclaw.llm.myprovider import MyProviderLLMClient
    return MyProviderLLMClient(**kwargs)
```

### 5. Add tests

```python
# tests/test_llm/test_myprovider.py
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.llm.base import LLMMessage


@pytest.fixture
def mock_myprovider(monkeypatch):
    """Stub the myprovider SDK before import."""
    mock_sdk = MagicMock()
    mock_client = AsyncMock()
    mock_sdk.AsyncClient.return_value = mock_client
    monkeypatch.setitem(sys.modules, "myprovider", mock_sdk)
    return mock_client


@pytest.mark.asyncio
async def test_complete_returns_llm_response(mock_myprovider):
    from graphclaw.llm.myprovider import MyProviderLLMClient

    # Arrange
    mock_myprovider.chat.complete.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Hello!"))],
        usage=MagicMock(total_tokens=50, prompt_tokens=30, completion_tokens=20),
    )
    client = MyProviderLLMClient(api_key="test-key")

    # Act
    response = await client.complete([LLMMessage(role="user", content="Hi")])

    # Assert
    assert response.content == "Hello!"
    assert response.tokens_used == 50
```

### 6. (Optional) Add to `pyproject.toml` optional deps

```toml
[project.optional-dependencies]
myprovider = ["myprovider>=1.0.0"]
```

## Existing Providers

| Provider | Class | Default model | Notes |
|----------|-------|---------------|-------|
| `litellm` | `LiteLLMLLMClient` | `claude-sonnet-4-6` | Default — supports 100+ models via LiteLLM proxy |
| `anthropic` | `AnthropicLLMClient` | `claude-sonnet-4-6` | Direct Anthropic SDK, best for Claude models |
| `openai` | `OpenAILLMClient` | `gpt-4o` | Direct OpenAI SDK; install `graphclaw[openai]` |

## Using the Factory

```python
from graphclaw.llm import create_llm_client

# Default (LiteLLM)
client = create_llm_client()

# Anthropic directly
client = create_llm_client("anthropic", api_key="sk-ant-...")

# OpenAI directly (requires: pip install graphclaw[openai])
client = create_llm_client("openai", api_key="sk-...")

# Custom
client = create_llm_client("myprovider", api_key="...")
```

## Environment Variable Convention

Providers read API keys from environment variables when not passed explicitly:

| Provider | Env var |
|----------|---------|
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `litellm` | Delegates to underlying provider env vars |
