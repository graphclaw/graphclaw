# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
from graphclaw.llm.logging_mixin import LLMTraceMixin


class LiteLLMLLMClient(LLMTraceMixin, LLMClient):
    """LLMClient backed by LiteLLM (routes to 100+ providers).

    Args:
        default_model: LiteLLM-compatible model string used when no model is
            specified at call time. Supports provider prefixes like:
            - ``anthropic/claude-sonnet-4-20250514``
            - ``openai/gpt-4o``
            - ``ollama/llama3.2`` (requires OLLAMA_API_BASE env var)
            Defaults to value from LITELLM_DEFAULT_MODEL env var or
            ``"claude-sonnet-4-20250514"``.
        api_base: Base URL for the LLM provider API. Auto-configured from
            OLLAMA_API_BASE when using ollama/ models.
    """

    def __init__(self, default_model: str | None = None, api_base: str | None = None, **_: Any) -> None:
        from graphclaw.config import config  # noqa: PLC0415

        self._default_model = default_model or config.app.litellm_default_model
        self._api_base = api_base
        
        # Auto-configure Ollama base URL if using ollama/ model prefix
        if self._default_model.startswith("ollama/") and not self._api_base:
            self._api_base = config.app.ollama_base_url

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
        if self._api_base:
            kwargs["api_base"] = self._api_base
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

        t0 = self._now_ms()
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            self._trace_llm_call(
                provider="litellm",
                model=target_model,
                call_type="complete",
                messages=[{"role": m.role, "content": m.content} for m in messages],
                params={"max_tokens": max_tokens, "temperature": temperature},
                response_content="",
                response_tool_calls=[],
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                latency_ms=self._now_ms() - t0,
                error=str(exc),
            )
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

        self._trace_llm_call(
            provider="litellm",
            model=target_model,
            call_type="complete",
            messages=[{"role": m.role, "content": m.content} for m in messages],
            params={"max_tokens": max_tokens, "temperature": temperature},
            response_content=content,
            response_tool_calls=[{"name": tc.name, "arguments": tc.arguments} for tc in tool_calls],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
            latency_ms=self._now_ms() - t0,
        )

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

        import json as _json  # noqa: PLC0415

        litellm_tools = None
        if tools:
            litellm_tools = [
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

        t0 = self._now_ms()
        try:
            stream_kwargs: dict[str, Any] = {
                "model": target_model,
                "messages": provider_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            }
            if self._api_base:
                stream_kwargs["api_base"] = self._api_base
            if litellm_tools:
                stream_kwargs["tools"] = litellm_tools
            response = await litellm.acompletion(**stream_kwargs)
        except Exception as exc:
            self._trace_llm_call(
                provider="litellm",
                model=target_model,
                call_type="stream",
                messages=[{"role": m.role, "content": m.content} for m in messages],
                params={"max_tokens": max_tokens, "temperature": temperature},
                response_content="",
                response_tool_calls=[],
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                latency_ms=self._now_ms() - t0,
                error=str(exc),
            )
            raise RuntimeError(f"LiteLLM stream call failed: {exc}") from exc

        accumulated_content = ""
        # index → {"id": str, "name": str, "arguments": str}
        tc_chunks: dict[int, dict[str, str]] = {}
        final_tool_calls: list[ToolCall] = []
        try:
            async for chunk in response:
                choice = chunk.choices[0]
                delta = choice.delta
                text_delta = delta.content or ""
                accumulated_content += text_delta
                is_final = choice.finish_reason is not None

                # Accumulate tool call deltas
                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = getattr(tc_delta, "index", 0)
                        if idx not in tc_chunks:
                            tc_chunks[idx] = {"id": "", "name": "", "arguments": ""}
                        if getattr(tc_delta, "id", None):
                            tc_chunks[idx]["id"] += tc_delta.id
                        fn = getattr(tc_delta, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                tc_chunks[idx]["name"] += fn.name
                            if getattr(fn, "arguments", None):
                                tc_chunks[idx]["arguments"] += fn.arguments

                if is_final:
                    final_tool_calls = []
                    for idx in sorted(tc_chunks):
                        tc = tc_chunks[idx]
                        try:
                            args = _json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except (_json.JSONDecodeError, ValueError):
                            args = {}
                        final_tool_calls.append(
                            ToolCall(id=tc["id"], name=tc["name"], arguments=args)
                        )
                    final_response = LLMResponse(
                        content=accumulated_content,
                        model=target_model,
                        tokens_used=0,
                        prompt_tokens=0,
                        completion_tokens=0,
                        cost_usd=0.0,
                        tool_calls=final_tool_calls,
                    )
                    yield LLMStreamChunk(
                        content_delta=text_delta,
                        is_final=True,
                        accumulated=final_response,
                    )
                else:
                    yield LLMStreamChunk(content_delta=text_delta)
        except Exception as exc:
            self._trace_llm_call(
                provider="litellm",
                model=target_model,
                call_type="stream",
                messages=[{"role": m.role, "content": m.content} for m in messages],
                params={"max_tokens": max_tokens, "temperature": temperature},
                response_content=accumulated_content,
                response_tool_calls=[
                    {"name": tc.name, "arguments": tc.arguments} for tc in final_tool_calls
                ],
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                latency_ms=self._now_ms() - t0,
                error=str(exc),
            )
            raise RuntimeError(f"LiteLLM stream failed: {exc}") from exc

        if not final_tool_calls and tc_chunks:
            for idx in sorted(tc_chunks):
                tc = tc_chunks[idx]
                try:
                    args = _json.loads(tc["arguments"]) if tc["arguments"] else {}
                except (_json.JSONDecodeError, ValueError):
                    args = {}
                final_tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))

        self._trace_llm_call(
            provider="litellm",
            model=target_model,
            call_type="stream",
            messages=[{"role": m.role, "content": m.content} for m in messages],
            params={"max_tokens": max_tokens, "temperature": temperature},
            response_content=accumulated_content,
            response_tool_calls=[
                {"name": tc.name, "arguments": tc.arguments} for tc in final_tool_calls
            ],
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=self._now_ms() - t0,
        )

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
