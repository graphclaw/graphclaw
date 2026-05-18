# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.llm.logging_mixin — LLMTraceMixin for tracing LLM calls.

Mixin class for AnthropicLLMClient, OpenAILLMClient, and LiteLLMLLMClient.
Records every complete() and stream() call to the isolated LLM trace logger
when enabled.

Does nothing (zero overhead) if LLM_TRACE is not set.
"""

from __future__ import annotations

import time
from typing import Any

from graphclaw.infra.logging.context import get_session_id
from graphclaw.infra.logging.llm_trace import get_llm_trace_logger


class LLMTraceMixin:
    """Mixin providing _trace_complete() and _trace_stream() helpers.

    Subclasses call these around LLM SDK invocations. Both methods are no-ops
    when the trace logger is not configured.
    """

    def _trace_llm_call(
        self,
        *,
        provider: str,
        model: str,
        call_type: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        response_content: str,
        response_tool_calls: list[dict[str, Any]],
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: float,
        user_id: str = "",
        error: str | None = None,
    ) -> None:
        """Record one LLM call to the trace logger (no-op if not enabled)."""
        trace_logger = get_llm_trace_logger()
        if trace_logger is None:
            return

        trace_logger.info(
            "llm.trace",
            extra={
                "event_type": "llm.trace",
                "session_id": get_session_id(),
                "user_id": user_id,
                "provider": provider,
                "model": model,
                "call_type": call_type,
                "messages": messages,
                "params": params,
                "response_content": response_content,
                "response_tool_calls": response_tool_calls,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost_usd,
                "latency_ms": round(latency_ms),
                "error": error,
            },
        )

    @staticmethod
    def _now_ms() -> float:
        return time.monotonic() * 1000
