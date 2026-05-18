# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.skills.llm_router — Backward-compatible LLM routing adapter.

Description
-----------
``LLMRouter`` is a thin adapter over the ``LLMClient`` ABC that preserves
the original dict-based response contract used by ``SkillWorker``.  It
delegates all LLM calls to a configured ``LLMClient`` instance rather than
calling LiteLLM directly.

This allows existing ``SkillWorker`` code and tests to continue working
unchanged while the underlying provider is now fully swappable via the
``LLMClient`` ABC.

Design Patterns
---------------
- Adapter: ``LLMRouter`` adapts the ``LLMClient`` interface to the legacy
  dict-based response contract expected by ``SkillWorker.execute``.
- Backward Compatibility: Constructor accepts an optional ``llm_client``
  to allow injection; falls back to creating a ``LiteLLMLLMClient`` with
  the existing default model, preserving previous behavior.

Public API
----------
- LLMRouter: Routes LLM completion requests via a pluggable ``LLMClient``.
- LLMRouter.complete: Send a chat completion request and return a result dict.

Dependencies
------------
- graphclaw.llm.base: LLMMessage (TYPE_CHECKING).
- graphclaw.llm.factory: create_llm_client.

Notes
-----
``cost_usd`` is forwarded from the underlying ``LLMResponse``.  Cost tracking
requires the provider implementation to calculate it; the default LiteLLM
and Anthropic implementations currently return 0.0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graphclaw.llm.base import LLMClient


class LLMRouter:
    """Routes LLM calls to a pluggable ``LLMClient`` implementation.

    Args:
        default_model: LiteLLM-compatible model string used when no model is
            specified by the caller (default ``"claude-sonnet-4-20250514"``).
        llm_client: Pre-constructed ``LLMClient`` instance to use.  When
            ``None``, a ``LiteLLMLLMClient`` is created with ``default_model``.
        provider: Provider name passed to ``create_llm_client()`` when
            ``llm_client`` is ``None`` (default ``"litellm"``).

    Usage::

        # Default behavior (same as before — LiteLLM under the hood)
        router = LLMRouter()
        result = await router.complete(
            model="claude-sonnet-4-20250514",
            system_prompt="You are a helpful assistant.",
            user_message="Summarise this task.",
        )
        print(result["content"])

        # Swap to Anthropic directly
        from graphclaw.llm import create_llm_client
        router = LLMRouter(llm_client=create_llm_client("anthropic"))
    """

    def __init__(
        self,
        default_model: str = "claude-sonnet-4-20250514",
        llm_client: LLMClient | None = None,
        provider: str = "litellm",
    ) -> None:
        self._default_model = default_model
        if llm_client is not None:
            self._client: LLMClient = llm_client
        else:
            from graphclaw.llm.factory import create_llm_client

            self._client = create_llm_client(provider, default_model=default_model)

    async def complete(
        self,
        model: str | None = None,
        system_prompt: str = "",
        user_message: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Send a completion request to the configured LLM provider.

        Builds a two-message conversation (system + user) and delegates to
        the underlying ``LLMClient``.  The response is returned as a plain
        dict preserving backward compatibility with ``SkillWorker``.

        Args:
            model: Provider model string. Falls back to ``default_model`` if
                ``None`` or empty.
            system_prompt: Content of the system role message.
            user_message: Content of the user role message.
            max_tokens: Maximum tokens in the completion.
            temperature: Sampling temperature (0.0 = deterministic).

        Returns:
            A dict with the following keys:

            - ``content`` (str): The assistant reply text.
            - ``tokens_used`` (int): Total tokens consumed (prompt + completion).
            - ``cost_usd`` (float): Estimated cost in USD.
            - ``model`` (str): The model string that was used.

        Raises:
            RuntimeError: If the underlying provider SDK is not installed
                or if the API call fails.
        """
        from graphclaw.llm.base import LLMMessage

        target_model = model or self._default_model
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_message),
        ]

        response = await self._client.complete(
            messages,
            model=target_model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        return {
            "content": response.content,
            "tokens_used": response.tokens_used,
            "cost_usd": response.cost_usd,
            "model": response.model,
        }
