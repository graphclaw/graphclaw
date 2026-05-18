# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.llm — LLM provider abstraction layer.

Exports the public API for the LLM layer:

- ``LLMClient`` ABC
- Shared data models: ``LLMMessage``, ``LLMResponse``, ``ToolDefinition``,
  ``ToolCall``, ``LLMStreamChunk``
- ``create_llm_client`` factory function

Usage::

    from graphclaw.llm import LLMClient, LLMMessage, create_llm_client

    client = create_llm_client("anthropic", api_key="sk-ant-...")
    response = await client.complete(
        [LLMMessage(role="user", content="Hello!")],
    )
"""

from graphclaw.llm.base import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolCall,
    ToolDefinition,
)
from graphclaw.llm.factory import create_llm_client

__all__ = [
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "LLMStreamChunk",
    "ToolCall",
    "ToolDefinition",
    "create_llm_client",
]
