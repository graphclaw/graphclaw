"""graphclaw.skills.llm_router — LLM provider routing via LiteLLM.

Description
-----------
Provides ``LLMRouter``, a thin async wrapper around LiteLLM's
``acompletion`` function.  LiteLLM normalises the interface across dozens of
LLM providers (Anthropic, OpenAI, Cohere, Bedrock, etc.), so a single
``complete()`` call here can target any provider by changing the model string
in the ``SkillDefinition``.

Design Patterns
---------------
- Adapter: ``LLMRouter`` adapts the LiteLLM API into the dict-based response
  contract expected by ``SkillWorker.execute``.
- Lazy Import: ``litellm`` is imported inside ``complete()`` so that the
  module can be loaded without LiteLLM present (useful during unit tests where
  the import is mocked).

Public API
----------
- LLMRouter: Routes LLM completion requests via LiteLLM.
- LLMRouter.complete: Send a chat completion request and return a result dict.

Dependencies
------------
- litellm: Multi-provider LLM abstraction library (>= 1.50.0).

Notes
-----
Cost estimation is not yet wired: ``cost_usd`` is always returned as 0.0.
LiteLLM does expose ``litellm.completion_cost()`` which can be plumbed in
when cost tracking is required.
"""
from __future__ import annotations


class LLMRouter:
    """Routes LLM calls to appropriate providers via LiteLLM.

    Args:
        default_model: LiteLLM-compatible model string used when no model is
            specified by the caller (default ``"claude-sonnet-4-20250514"``).

    Usage::

        router = LLMRouter()
        result = await router.complete(
            model="claude-sonnet-4-20250514",
            system_prompt="You are a helpful assistant.",
            user_message="Summarise this task.",
        )
        print(result["content"])
    """

    def __init__(self, default_model: str = "claude-sonnet-4-20250514") -> None:
        self._default_model = default_model

    async def complete(
        self,
        model: str | None = None,
        system_prompt: str = "",
        user_message: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> dict:
        """Send a completion request to the configured LLM provider.

        Builds a two-message conversation (system + user) and calls
        ``litellm.acompletion``.  The response is normalised into a plain
        dict so that callers do not need to understand LiteLLM's response
        objects.

        Args:
            model: LiteLLM model string. Falls back to ``default_model`` if
                ``None`` or empty.
            system_prompt: Content of the system role message.
            user_message: Content of the user role message.
            max_tokens: Maximum tokens in the completion.
            temperature: Sampling temperature (0.0 = deterministic).

        Returns:
            A dict with the following keys:

            - ``content`` (str): The assistant reply text.
            - ``tokens_used`` (int): Total tokens consumed (prompt + completion).
            - ``cost_usd`` (float): Estimated cost in USD (currently 0.0).
            - ``model`` (str): The model string that was used.

        Raises:
            RuntimeError: If ``litellm`` is not installed, or if the underlying
                API call fails for any reason.
        """
        target_model = model or self._default_model

        try:
            import litellm  # noqa: PLC0415  (local import intentional)

            response = await litellm.acompletion(
                model=target_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )

            content: str = response.choices[0].message.content or ""
            usage = response.usage
            tokens_used = (
                (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
                if usage
                else 0
            )

            return {
                "content": content,
                "tokens_used": tokens_used,
                "cost_usd": 0.0,
                "model": target_model,
            }

        except ImportError as exc:
            raise RuntimeError(
                "litellm is required for LLM routing. "
                "Install it with: pip install 'litellm>=1.50.0'"
            ) from exc

        except Exception as exc:
            raise RuntimeError(f"LLM call failed: {exc}") from exc
