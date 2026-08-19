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

import json
import uuid
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

# Models that do not support OpenAI function-calling API natively and require
# the text-based tool-call fallback instead.
# Ollama models are NOT listed here — Ollama passes function-calling natively
# to models that support it (e.g. qwen2.5, llama3.1). The text fallback is
# reserved for providers/models that genuinely lack tool-call schema support.
_TEXT_TOOL_PREFIXES: tuple[str, ...] = ()


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
        api_base: Base URL for the LLM provider API. When omitted, it is
            resolved *per call* from OLLAMA_API_BASE for ``ollama/`` models and
            left unset for everything else — see ``_resolve_api_base``.

    Note:
        A single instance may serve many different models (one per
        :class:`~graphclaw.llm.roles.LLMRole`), so ``api_base`` must never be
        bound to the constructor's ``default_model``. Doing so breaks both
        directions: an ``ollama/`` call on a hosted-default client would get no
        ``api_base``, and a hosted call on an ollama-default client would have
        the Ollama URL sent to Anthropic/OpenAI.
    """

    def __init__(
        self, default_model: str | None = None, api_base: str | None = None, **_: Any
    ) -> None:
        from graphclaw.config import config  # noqa: PLC0415

        self._default_model = default_model or config.app.litellm_default_model
        # Explicit constructor override — wins over per-call resolution.
        self._api_base_explicit = api_base

    @property
    def api_base(self) -> str | None:
        """``api_base`` that applies to this client's *default* model.

        Retained for introspection and diagnostics. Call paths must use
        :meth:`_resolve_api_base` with the actual target model instead.
        """
        return self._resolve_api_base(self._default_model)

    def _resolve_api_base(self, target_model: str) -> str | None:
        """Resolve the API base URL for a specific target model.

        An explicit constructor argument always wins. Otherwise only
        ``ollama/``/``ollama_chat/`` models get a base URL; hosted providers
        must not receive one.
        """
        if self._api_base_explicit:
            return self._api_base_explicit
        if target_model.startswith(("ollama/", "ollama_chat/")):
            from graphclaw.config import config  # noqa: PLC0415

            return config.app.ollama_base_url
        return None

    # ------------------------------------------------------------------
    # Helpers — message translation
    # ------------------------------------------------------------------

    @staticmethod
    def _translate_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Translate LLMMessages to OpenAI/LiteLLM wire format.

        Preserves tool_calls on assistant messages and tool_call_id on tool
        result messages — both are required for valid multi-turn tool-use
        conversations with any OpenAI-compatible provider.
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.role == "assistant" and m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            if m.role == "tool" and m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            out.append(msg)
        return out

    # ------------------------------------------------------------------
    # Helpers — text-based tool-call fallback for models without native
    # function-calling support (e.g. llama3.2 via Ollama)
    # ------------------------------------------------------------------

    @staticmethod
    def _needs_text_tool_fallback(model: str) -> bool:
        return any(model.startswith(p) for p in _TEXT_TOOL_PREFIXES)

    @staticmethod
    def _build_tool_system_block(tools: list[ToolDefinition]) -> str:
        """Return a system-prompt block that teaches the model to call tools."""
        lines = [
            "You have access to the following tools.",
            "To call a tool, respond with ONLY a JSON object on a single line — no other text:",
            '{"tool_call": {"name": "TOOL_NAME", "arguments": {ARGS}}}',
            "",
            "Available tools:",
        ]
        for t in tools:
            param_names = list((t.parameters or {}).get("properties", {}).keys())
            sig = ", ".join(param_names) if param_names else ""
            lines.append(f"- {t.name}({sig}): {t.description}")
        lines += [
            "",
            "If no tool is needed, reply in plain text.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _translate_messages_text_tools(
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
    ) -> list[dict[str, Any]]:
        """Translate messages for models using the text-based tool fallback.

        - Injects the tool catalogue as a system message prefix.
        - Converts assistant messages that previously called tools into plain
          text so the model sees what was done.
        - Converts role=tool result messages into role=user messages so the
          model receives the results without needing native tool support.
        """
        tool_block = LiteLLMLLMClient._build_tool_system_block(tools)
        out: list[dict[str, Any]] = []

        # Prepend (or merge into existing) system message
        injected = False
        for m in messages:
            if m.role == "system" and not injected:
                out.append({"role": "system", "content": f"{tool_block}\n\n{m.content}"})
                injected = True
            elif m.role == "assistant" and m.tool_calls:
                # Render prior tool invocations as plain text so the model
                # understands what it previously requested.
                calls_text = "\n".join(
                    f'{{"tool_call": {{"name": "{tc.name}", "arguments": {json.dumps(tc.arguments)}}}}}'
                    for tc in m.tool_calls
                )
                text = (m.content + "\n" + calls_text).strip() if m.content else calls_text
                out.append({"role": "assistant", "content": text})
            elif m.role == "tool":
                # Convert tool results to user messages
                out.append({"role": "user", "content": f"Tool result: {m.content}"})
            else:
                out.append({"role": m.role, "content": m.content})

        if not injected:
            out.insert(0, {"role": "system", "content": tool_block})

        return out

    @staticmethod
    def _parse_text_tool_calls(content: str) -> list[ToolCall]:
        """Extract tool calls from a model response that uses text-based JSON format."""
        # Find the start of a {"tool_call": ...} object in the content
        marker = '"tool_call"'
        idx = content.find(marker)
        if idx == -1:
            return []
        # Walk backwards to find the opening brace of the enclosing object
        start = content.rfind("{", 0, idx)
        if start == -1:
            return []
        # Walk forward tracking brace depth to extract the full JSON object
        depth = 0
        end = -1
        for i, ch in enumerate(content[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end == -1:
            return []
        try:
            obj = json.loads(content[start:end])
            tc = obj.get("tool_call", {})
            name = tc.get("name", "")
            if not name:
                return []
            return [
                ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=name,
                    arguments=tc.get("arguments") or {},
                )
            ]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    # ------------------------------------------------------------------
    # LLMClient interface
    # ------------------------------------------------------------------

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
        use_text_tools = tools and self._needs_text_tool_fallback(target_model)

        if use_text_tools:
            provider_messages = self._translate_messages_text_tools(messages, tools)  # type: ignore[arg-type]
        else:
            provider_messages = self._translate_messages(messages)

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": provider_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        resolved_api_base = self._resolve_api_base(target_model)
        if resolved_api_base:
            kwargs["api_base"] = resolved_api_base
        if tools and not use_text_tools:
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
        if use_text_tools:
            tool_calls = self._parse_text_tool_calls(content)
            if tool_calls:
                content = ""
        elif choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id or f"call_{uuid.uuid4().hex[:8]}",
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
        use_text_tools = tools and self._needs_text_tool_fallback(target_model)

        if use_text_tools:
            provider_messages = self._translate_messages_text_tools(messages, tools)  # type: ignore[arg-type]
        else:
            provider_messages = self._translate_messages(messages)

        import json as _json  # noqa: PLC0415

        litellm_tools = None
        if tools and not use_text_tools:
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
            resolved_api_base = self._resolve_api_base(target_model)
            if resolved_api_base:
                stream_kwargs["api_base"] = resolved_api_base
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

                # Accumulate tool call deltas (native tool-calling path)
                if not use_text_tools and hasattr(delta, "tool_calls") and delta.tool_calls:
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
                    if use_text_tools:
                        final_tool_calls = self._parse_text_tool_calls(accumulated_content)
                        if final_tool_calls:
                            accumulated_content = ""
                    else:
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

        if not use_text_tools and not final_tool_calls and tc_chunks:
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
        provider_messages = self._translate_messages(messages)
        try:
            return litellm.token_counter(model=target_model, messages=provider_messages)
        except Exception:
            # Fall back to rough character-based estimate
            total_chars = sum(len(m.content) for m in messages)
            return total_chars // 4

    async def close(self) -> None:
        """No persistent connections to close for LiteLLM."""
