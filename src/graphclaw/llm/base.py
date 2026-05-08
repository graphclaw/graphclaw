# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.llm.base — LLMClient ABC and shared provider-agnostic data models.

Description
-----------
Defines the ``LLMClient`` Abstract Base Class that all LLM provider backends
must implement.  Also defines the frozen dataclasses used to represent
messages, responses, tool definitions, and streaming chunks in a
provider-agnostic way.

Every LLM provider (Anthropic, OpenAI, LiteLLM, ...) translates between
these types and its own SDK-specific formats internally.  Business logic
only ever works with these types.

Design Patterns
---------------
- Abstract Base Class: ``LLMClient`` defines the minimal contract so provider
  backends are interchangeable without changing any business logic.
- Frozen Dataclasses: All data models are frozen (immutable) to prevent
  accidental mutation across async boundaries.
- Strategy: The factory selects the concrete implementation at startup;
  the rest of the system only sees ``LLMClient``.

Public API
----------
- LLMClient: ABC — complete, stream, count_tokens, close.
- LLMMessage: Provider-agnostic chat message.
- LLMResponse: Provider-agnostic completion response.
- ToolDefinition: Function/tool spec (JSON Schema format).
- ToolCall: A single tool invocation returned by the model.
- LLMStreamChunk: One chunk emitted by a streaming response.

Dependencies
------------
- abc: ABC, abstractmethod (stdlib).
- dataclasses: dataclass, field (stdlib).
- collections.abc: AsyncIterator (stdlib).
- typing: Any (stdlib).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMMessage:
    """A single message in a conversation.

    Attributes:
        role: One of ``"system"``, ``"user"``, ``"assistant"``, or ``"tool"``.
        content: The text content of the message.
        tool_call_id: ID of the tool call this message is a response to
            (only used when ``role="tool"``).
        tool_calls: Tool invocations returned by the model in this message
            (only set on ``role="assistant"`` messages that contain tool use).
    """

    role: str
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ToolDefinition:
    """A function/tool that the model can invoke.

    Attributes:
        name: Unique tool identifier (snake_case recommended).
        description: Human-readable description used by the model to decide
            when to call this tool.
        parameters: JSON Schema object describing the tool's arguments.
    """

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation returned by the model.

    Attributes:
        id: Provider-assigned unique ID for this tool call.  Must be echoed
            back in the ``tool_call_id`` of the subsequent ``"tool"`` message.
        name: Name of the tool to invoke.
        arguments: Parsed argument dict (provider translates from JSON string).
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """The result of a non-streaming LLM completion.

    Attributes:
        content: Assistant reply text.
        model: The model identifier that was actually used.
        tokens_used: Total tokens consumed (prompt + completion).
        prompt_tokens: Tokens in the input messages.
        completion_tokens: Tokens in the generated response.
        cost_usd: Estimated cost in US dollars (0.0 if not calculable).
        tool_calls: Tool invocations requested by the model (empty if none).
        stop_reason: Why the model stopped generating:
            ``"end_turn"`` | ``"tool_use"`` | ``"max_tokens"`` | ``None``.
    """

    content: str
    model: str
    tokens_used: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass(frozen=True)
class LLMStreamChunk:
    """One chunk emitted by a streaming response.

    Attributes:
        content_delta: The new text added by this chunk (may be empty).
        is_final: ``True`` on the last chunk only.
        accumulated: Full ``LLMResponse`` set on the final chunk so callers
            can access token counts and cost after streaming completes.
    """

    content_delta: str = ""
    is_final: bool = False
    accumulated: LLMResponse | None = None


# ---------------------------------------------------------------------------
# Abstract Base Class
# ---------------------------------------------------------------------------


class LLMClient(ABC):
    """Abstract base class for all LLM provider backends.

    Implementations must translate between the shared data models above and
    their provider-specific SDK formats.  All methods are async.

    Usage::

        client = create_llm_client("anthropic", api_key="sk-ant-...")
        response = await client.complete(
            [LLMMessage(role="user", content="Hello!")],
            model="claude-sonnet-4-6",
        )
        print(response.content)

    Note:
        Always call ``await client.close()`` when done to release SDK
        connection resources (HTTP sessions, thread pools, etc.).
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request and return the full response.

        Args:
            messages: Conversation history.  System messages are supported
                via ``role="system"``; provider implementations handle
                the necessary format conversions internally.
            model: Provider-specific model identifier.  Falls back to the
                client's configured ``default_model`` when ``None``.
            max_tokens: Maximum tokens to generate in the response.
            temperature: Sampling temperature.  ``0.0`` is deterministic.
            tools: Optional list of tools the model may invoke.

        Returns:
            ``LLMResponse`` with content, token counts, cost, and any
            tool calls requested by the model.

        Raises:
            RuntimeError: If the SDK is not installed or the API call fails.
        """

    @abstractmethod
    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream a chat completion response chunk by chunk.

        Yields ``LLMStreamChunk`` objects.  The final chunk has
        ``is_final=True`` and ``accumulated`` set to the full
        ``LLMResponse``.

        Args:
            messages: Conversation history.
            model: Provider-specific model identifier.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            tools: Optional list of tools the model may invoke.

        Yields:
            ``LLMStreamChunk`` — one per content delta, plus a final chunk.
        """

    @abstractmethod
    async def count_tokens(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
    ) -> int:
        """Count the tokens that would be consumed by the given messages.

        Used for pre-flight context window checks before sending a request.

        Args:
            messages: Conversation history to count.
            model: Provider-specific model identifier.

        Returns:
            Estimated token count (int).
        """

    @abstractmethod
    async def close(self) -> None:
        """Release provider SDK connection resources.

        Always call this when the client is no longer needed to avoid
        resource leaks (open HTTP sessions, thread pools, etc.).
        """
