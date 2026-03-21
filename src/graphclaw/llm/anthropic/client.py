"""graphclaw.llm.anthropic.client — LLMClient implementation backed by the Anthropic SDK.

Description
-----------
``AnthropicLLMClient`` implements the ``LLMClient`` ABC using the official
``anthropic`` Python SDK.  It handles the Anthropic-specific format
differences (system messages as a top-level parameter, tool_use content
blocks, etc.) and translates them to and from the provider-agnostic types.

Design Patterns
---------------
- Adapter: Translates the ``LLMClient`` interface to ``anthropic.AsyncAnthropic``.
- Lazy Import: ``anthropic`` is imported inside each method to keep the
  module loadable without the SDK installed.

Public API
----------
- AnthropicLLMClient: Anthropic SDK-backed LLMClient implementation.

Dependencies
------------
- graphclaw.llm.base: LLMClient, LLMMessage, LLMResponse, LLMStreamChunk,
  ToolDefinition, ToolCall.
- anthropic: Official Anthropic Python SDK (>= 0.40.0).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from graphclaw.llm.base import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolCall,
    ToolDefinition,
)


class AnthropicLLMClient(LLMClient):
    """LLMClient backed by the Anthropic SDK (Claude models).

    Args:
        api_key: Anthropic API key.  Falls back to the ``ANTHROPIC_API_KEY``
            environment variable when not provided.
        default_model: Claude model to use when no model is specified at
            call time (default ``"claude-sonnet-4-6"``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "claude-sonnet-4-6",
        **_: Any,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._client: Any = None  # Lazy — created on first use

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "anthropic SDK is required for this provider. "
                    "Install it with: pip install 'anthropic>=0.40.0'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    @staticmethod
    def _split_messages(
        messages: list[LLMMessage],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Separate system message from conversation messages.

        Anthropic requires the system prompt as a top-level parameter,
        not in the messages list.
        """
        system = ""
        conversation: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system = m.content
            else:
                conversation.append({"role": m.role, "content": m.content})
        return system, conversation

    @staticmethod
    def _translate_tools(
        tools: list[ToolDefinition],
    ) -> list[dict[str, Any]]:
        """Translate ToolDefinition list to Anthropic's tool format."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    @staticmethod
    def _extract_tool_calls(content_blocks: list[Any]) -> list[ToolCall]:
        """Extract tool_use blocks from Anthropic response content."""
        tool_calls = []
        for block in content_blocks:
            if getattr(block, "type", None) == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=args))
        return tool_calls

    @staticmethod
    def _extract_text(content_blocks: list[Any]) -> str:
        """Extract plain text from Anthropic response content blocks."""
        parts = []
        for block in content_blocks:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts)

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Send a completion request to the Anthropic API."""
        client = self._get_client()
        target_model = model or self._default_model
        system, conversation = self._split_messages(messages)

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": conversation,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._translate_tools(tools)

        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            raise RuntimeError(f"Anthropic API call failed: {exc}") from exc

        content = self._extract_text(response.content)
        tool_calls = self._extract_tool_calls(response.content)
        prompt_tokens = response.usage.input_tokens
        completion_tokens = response.usage.output_tokens

        return LLMResponse(
            content=content,
            model=target_model,
            tokens_used=prompt_tokens + completion_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream a completion response from the Anthropic API."""
        client = self._get_client()
        target_model = model or self._default_model
        system, conversation = self._split_messages(messages)

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": conversation,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._translate_tools(tools)

        accumulated_content = ""
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    accumulated_content += text
                    yield LLMStreamChunk(content_delta=text)
                final = await stream.get_final_message()
        except Exception as exc:
            raise RuntimeError(f"Anthropic stream failed: {exc}") from exc

        prompt_tokens = final.usage.input_tokens
        completion_tokens = final.usage.output_tokens
        final_response = LLMResponse(
            content=accumulated_content,
            model=target_model,
            tokens_used=prompt_tokens + completion_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
            stop_reason=final.stop_reason,
        )
        yield LLMStreamChunk(
            content_delta="",
            is_final=True,
            accumulated=final_response,
        )

    async def count_tokens(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
    ) -> int:
        """Count tokens using the Anthropic token-counting API."""
        client = self._get_client()
        target_model = model or self._default_model
        system, conversation = self._split_messages(messages)

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": conversation,
        }
        if system:
            kwargs["system"] = system

        try:
            response = await client.messages.count_tokens(**kwargs)
            return response.input_tokens
        except Exception:
            # Fall back to rough estimate
            total_chars = sum(len(m.content) for m in messages)
            return total_chars // 4

    async def close(self) -> None:
        """Close the Anthropic async HTTP client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
