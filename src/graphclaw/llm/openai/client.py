"""graphclaw.llm.openai.client — LLMClient implementation backed by the OpenAI SDK.

Description
-----------
``OpenAILLMClient`` implements the ``LLMClient`` ABC using the official
``openai`` Python SDK.  OpenAI's message format closely matches the shared
``LLMMessage`` model, so minimal translation is required.

Note
----
Requires the ``openai`` optional dependency:
``pip install 'graphclaw[openai]'``

Design Patterns
---------------
- Adapter: Translates the ``LLMClient`` interface to ``openai.AsyncOpenAI``.
- Lazy Import: ``openai`` is imported inside each method to keep the
  module loadable without the SDK installed.

Public API
----------
- OpenAILLMClient: OpenAI SDK-backed LLMClient implementation.

Dependencies
------------
- graphclaw.llm.base: LLMClient, LLMMessage, LLMResponse, LLMStreamChunk,
  ToolDefinition, ToolCall.
- openai: Official OpenAI Python SDK (>= 1.54.0).
- tiktoken: Token counting for OpenAI models (>= 0.8.0).
"""

from __future__ import annotations

import json
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


class OpenAILLMClient(LLMTraceMixin, LLMClient):
    """LLMClient backed by the OpenAI SDK (GPT models).

    Args:
        api_key: OpenAI API key.  Falls back to the ``OPENAI_API_KEY``
            environment variable when not provided.
        default_model: OpenAI model to use when no model is specified at
            call time (default ``"gpt-4o"``).

    Note:
        Requires ``pip install 'graphclaw[openai]'`` (openai + tiktoken).
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gpt-4o",
        **_: Any,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import openai  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "openai SDK is required for this provider. "
                    "Install it with: pip install 'graphclaw[openai]'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    @staticmethod
    def _translate_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Translate LLMMessage list to OpenAI message format.

        OpenAI uses the same role names as our shared model, so this is
        mostly a dict conversion with tool_call handling.
        """
        result = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            result.append(msg)
        return result

    @staticmethod
    def _translate_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Translate ToolDefinition list to OpenAI function-calling format."""
        return [
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

    @staticmethod
    def _extract_tool_calls(openai_tool_calls: Any) -> list[ToolCall]:
        """Extract tool calls from an OpenAI response message."""
        if not openai_tool_calls:
            return []
        result = []
        for tc in openai_tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return result

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Send a completion request to the OpenAI API."""
        client = self._get_client()
        target_model = model or self._default_model
        oai_messages = self._translate_messages(messages)

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = self._translate_tools(tools)

        t0 = self._now_ms()
        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            self._trace_llm_call(
                provider="openai",
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
            raise RuntimeError(f"OpenAI API call failed: {exc}") from exc

        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = self._extract_tool_calls(choice.message.tool_calls)
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        self._trace_llm_call(
            provider="openai",
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
            stop_reason=choice.finish_reason,
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
        """Stream a completion response from the OpenAI API."""
        client = self._get_client()
        target_model = model or self._default_model
        oai_messages = self._translate_messages(messages)

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = self._translate_tools(tools)

        import json as _json  # noqa: PLC0415

        accumulated_content = ""
        finish_reason = None
        # index → {"id": str, "name": str, "arguments": str}
        tc_chunks: dict[int, dict[str, str]] = {}
        try:
            async with await client.chat.completions.create(**kwargs) as stream:
                async for chunk in stream:
                    choice = chunk.choices[0]
                    delta = choice.delta
                    text_delta = delta.content or ""
                    accumulated_content += text_delta
                    finish_reason = choice.finish_reason

                    # Accumulate tool call deltas
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tc_chunks:
                                tc_chunks[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc_delta.id:
                                tc_chunks[idx]["id"] += tc_delta.id
                            fn = tc_delta.function
                            if fn:
                                if fn.name:
                                    tc_chunks[idx]["name"] += fn.name
                                if fn.arguments:
                                    tc_chunks[idx]["arguments"] += fn.arguments

                    if finish_reason:
                        tool_calls: list[ToolCall] = []
                        for idx in sorted(tc_chunks):
                            tc = tc_chunks[idx]
                            try:
                                args = _json.loads(tc["arguments"]) if tc["arguments"] else {}
                            except (_json.JSONDecodeError, ValueError):
                                args = {}
                            tool_calls.append(
                                ToolCall(id=tc["id"], name=tc["name"], arguments=args)
                            )
                        final_response = LLMResponse(
                            content=accumulated_content,
                            model=target_model,
                            tokens_used=0,
                            prompt_tokens=0,
                            completion_tokens=0,
                            cost_usd=0.0,
                            tool_calls=tool_calls,
                            stop_reason=finish_reason,
                        )
                        yield LLMStreamChunk(
                            content_delta=text_delta,
                            is_final=True,
                            accumulated=final_response,
                        )
                    else:
                        yield LLMStreamChunk(content_delta=text_delta)
        except Exception as exc:
            raise RuntimeError(f"OpenAI stream failed: {exc}") from exc

    async def count_tokens(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
    ) -> int:
        """Count tokens locally using tiktoken."""
        target_model = model or self._default_model
        try:
            import tiktoken  # noqa: PLC0415

            try:
                enc = tiktoken.encoding_for_model(target_model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")

            total = 0
            for m in messages:
                total += len(enc.encode(m.content))
                total += 4  # per-message overhead (role, separators)
            total += 2  # reply primer
            return total
        except ImportError:
            # tiktoken not available — rough estimate
            return sum(len(m.content) // 4 for m in messages)

    async def close(self) -> None:
        """Close the OpenAI async HTTP client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
