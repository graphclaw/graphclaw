"""graphclaw.llm.factory — LLMClient factory function.

Description
-----------
Provides ``create_llm_client``, the single entry-point for constructing
an ``LLMClient`` instance.  Provider SDKs are imported lazily so that
the core library does not require all provider packages to be installed.

Supported providers
-------------------
- ``"litellm"``  — Default.  Routes to any provider via LiteLLM proxy.
- ``"anthropic"`` — Direct Anthropic SDK (``anthropic`` package).
- ``"openai"``   — Direct OpenAI SDK (``openai`` package; install with
                   ``pip install 'graphclaw[openai]'``).

Design Patterns
---------------
- Factory Function: Single function creates concrete implementations
  based on a string key, keeping provider imports lazy.
- Open/Closed: Adding a new provider requires only adding a new case
  here and implementing ``LLMClient`` — no other code changes.

Public API
----------
- create_llm_client: Instantiate the appropriate LLMClient implementation.

Dependencies
------------
- graphclaw.llm.base: LLMClient (TYPE_CHECKING only at module level).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graphclaw.llm.base import LLMClient


def create_llm_client(provider: str = "litellm", **kwargs: Any) -> LLMClient:
    """Instantiate and return an LLMClient for the given provider.

    Parameters
    ----------
    provider:
        One of ``"litellm"`` (default), ``"anthropic"``, or ``"openai"``.
        Raises ``ValueError`` for unknown providers.
    **kwargs:
        Provider-specific constructor arguments.  Common keys:

        - ``api_key`` (str): API key (falls back to env var if omitted).
        - ``default_model`` (str): Model used when no model is specified
          at call time.

    Returns
    -------
    LLMClient
        Concrete implementation for the requested provider.

    Raises
    ------
    ValueError
        If ``provider`` is not recognised.
    RuntimeError
        If the provider's SDK is not installed.

    Examples
    --------
    ::

        # Default — LiteLLM (routes to 100+ providers)
        client = create_llm_client()

        # Anthropic directly
        client = create_llm_client("anthropic", api_key="sk-ant-...")

        # OpenAI directly (requires: pip install 'graphclaw[openai]')
        client = create_llm_client("openai", api_key="sk-...")
    """
    match provider:
        case "litellm":
            from graphclaw.llm.litellm.client import LiteLLMLLMClient

            return LiteLLMLLMClient(**kwargs)

        case "anthropic":
            from graphclaw.llm.anthropic.client import AnthropicLLMClient

            return AnthropicLLMClient(**kwargs)

        case "openai":
            from graphclaw.llm.openai.client import OpenAILLMClient

            return OpenAILLMClient(**kwargs)

        case _:
            raise ValueError(
                f"Unknown LLM provider: {provider!r}. "
                "Supported providers: 'litellm', 'anthropic', 'openai'."
            )
