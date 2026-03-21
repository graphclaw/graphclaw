"""graphclaw.llm.litellm.client — LLMClient implementation backed by LiteLLM.

Description
-----------
``LiteLLMLLMClient`` implements the ``LLMClient`` ABC using LiteLLM as the
provider backend.  LiteLLM normalises the interface across 100+ providers
(Anthropic, OpenAI, Cohere, Bedrock, Vertex AI, Ollama, etc.), so a single
implementation here targets any of them by changing the model string.

This is the default provider used by ``create_llm_client()`` and the
implementation that ``LLMRouter`` previously wrapped directly.

Design Patterns
---------------
- Adapter: Translates the ``LLMClient`` interface to ``litellm.acompletion``.
- Lazy Import: ``litellm`` is imported inside each method so the module can
  be loaded without LiteLLM installed (useful in unit tests with sys.modules
  mocking).

Public API
----------
- LiteLLMLLMClient: LiteLLM-backed LLMClient implementation.

Dependencies
------------
- graphclaw.llm.base: LLMClient, LLMMessage, LLMResponse, LLMStreamChunk,
  ToolDefinition, ToolCall.
- litellm: Multi-provider LLM library (>= 1.50.0).
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


class LiteLLMLLMClient(LLMClient):
    """LLMClient backed by LiteLLM (routes to 100+ providers).

    Args:
        default_model: LiteLLM-compatible model string used when no model is
            specified at call time (default ``"claude-sonnet-4-20250514"``).
    """

    def __init__(self, default_model: str = "claude-sonnet-4-20250514", **_: Any) -> None:
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
        """Send a completion request via LiteLLM."""
        try:
            import litellm  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "litellm is required for this provider. "
                "Install it with: pip install 'litellm>=1.50.0'"
            ) from exc

        target_model = model or self._default_model
        provider_messages = [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": provider_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            raise RuntimeError(f"LiteLLM call failed: {exc}") from exc

        choice = response.choices[0]
        content: str = choice.message.content or ""
        usage = response.usage
        prompt_tokens = (usage.prompt_tokens or 0) if usage else 0
        completion_tokens = (usage.completion_tokens or 0) if usage else 0

        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            import json

            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments or "{}"),
                    )
                )

        stop_reason = getattr(choice, "finish_reason", None)

        return LLMResponse(
            content=content,
            model=target_model,
            tokens_used=prompt_tokens + completion_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
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
        """Stream a completion response via LiteLLM."""
        try:
            import litellm  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "litellm is required for this provider. "
                "Install it with: pip install 'litellm>=1.50.0'"
            ) from exc

        target_model = model or self._default_model
        provider_messages = [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = await litellm.acompletion(
                model=target_model,
                messages=provider_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
        except Exception as exc:
            raise RuntimeError(f"LiteLLM stream call failed: {exc}") from exc

        accumulated_content = ""
        async for chunk in response:
            delta = chunk.choices[0].delta.content or ""
            accumulated_content += delta
            is_final = chunk.choices[0].finish_reason is not None
            if is_final:
                final_response = LLMResponse(
                    content=accumulated_content,
                    model=target_model,
                    tokens_used=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    cost_usd=0.0,
                )
                yield LLMStreamChunk(
                    content_delta=delta,
                    is_final=True,
                    accumulated=final_response,
                )
            else:
                yield LLMStreamChunk(content_delta=delta)

    async def count_tokens(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
    ) -> int:
        """Estimate token count using LiteLLM's token counter."""
        try:
            import litellm  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "litellm is required for this provider. "
                "Install it with: pip install 'litellm>=1.50.0'"
            ) from exc

        target_model = model or self._default_model
        provider_messages = [{"role": m.role, "content": m.content} for m in messages]
        try:
            return litellm.token_counter(model=target_model, messages=provider_messages)
        except Exception:
            # Fall back to rough character-based estimate
            total_chars = sum(len(m.content) for m in messages)
            return total_chars // 4

    async def close(self) -> None:
        """No persistent connections to close for LiteLLM."""
