# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.llm.routing — Resolves LLMRole -> provider+model, vends role clients.

Description
-----------
``ModelRouter`` is the single place that turns a :class:`~graphclaw.llm.roles.LLMRole`
into a concrete ``(provider, model)`` pair and a usable ``LLMClient``. It owns
exactly one base ``LLMClient`` per distinct *provider* (created lazily via
``create_llm_client`` and cached), and vends each role a cheap
:class:`~graphclaw.llm.role_client.RoleBoundLLMClient` decorator over that
shared base. This is deliberately NOT one client per role — LiteLLM model
strings already carry the provider prefix, so a single LiteLLM client already
reaches every provider; six near-identical client objects would be pure
duplication.

Design Patterns
---------------
- Factory (delegated): ``ModelRouter`` does not construct SDK clients itself;
  it calls ``create_llm_client`` (or an injected factory, for tests) and keeps
  the plugin-architecture invariant that construction only ever goes through
  that one function.
- Decorator: role clients wrap the shared base — see ``role_client.py``.
- Cache: base clients are cached per provider; role clients are cached per role.

Public API
----------
- ModelSpec: resolved (provider, model, api_base) for one role.
- ModelRouter: resolves roles, vends clients, owns the base-client lifecycle.

Dependencies
------------
- graphclaw.config: LLMRoutingConfig, config (routing policy source).
- graphclaw.llm.factory: create_llm_client (default client_factory).
- graphclaw.llm.role_client: RoleBoundLLMClient.
- graphclaw.llm.roles: LLMRole.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from graphclaw.config import LLMRoutingConfig, config
from graphclaw.llm.role_client import RoleBoundLLMClient
from graphclaw.llm.roles import LLMRole

if TYPE_CHECKING:
    from graphclaw.llm.base import LLMClient

logger = logging.getLogger(__name__)

# Providers whose SDK forwards the model string verbatim to the API — a
# LiteLLM-style "<provider>/<model>" prefix must not reach them.
_DIRECT_PROVIDERS = ("anthropic", "openai")
_KNOWN_PREFIXES = ("litellm/", "anthropic/", "openai/", "ollama/", "ollama_chat/")


@dataclass(frozen=True)
class ModelSpec:
    """Resolved provider + model for one role.

    Attributes:
        provider: One of ``"litellm"``, ``"anthropic"``, ``"openai"``.
        model: Provider-native model string (prefix stripped for direct
            providers; see ``ModelRouter.spec_for``).
        api_base: Informational only — for ``ollama/`` models the LiteLLM
            client resolves its own ``api_base`` per call
            (see ``LiteLLMLLMClient._resolve_api_base``); this field exists
            for diagnostics/health output.
    """

    provider: str
    model: str
    api_base: str | None = None


class ModelRouter:
    """Resolves :class:`LLMRole` -> :class:`ModelSpec` and vends role clients.

    Args:
        policy: Role -> provider/model resolution policy. Defaults to
            ``config.llm_routing`` (env-driven).
        default_provider: Provider to use for a role when nothing in the
            policy names one — typically the provider selected at gateway
            startup by ``_select_startup_llm_provider_and_key``.
        api_keys: Optional ``{provider: api_key}`` map used when constructing
            base clients. A missing/empty key is fine for local providers
            (e.g. Ollama via LiteLLM).
        client_factory: Overrides how base clients are constructed. Defaults
            to ``create_llm_client``; tests inject a fake to avoid SDK stubs.
    """

    def __init__(
        self,
        policy: LLMRoutingConfig | None = None,
        *,
        default_provider: str = "litellm",
        api_keys: Mapping[str, str | None] | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._policy = policy if policy is not None else config.llm_routing
        self._default_provider = default_provider
        self._api_keys: dict[str, str | None] = dict(api_keys or {})
        self._client_factory = client_factory
        self._base_clients: dict[str, LLMClient] = {}
        self._role_clients: dict[LLMRole, RoleBoundLLMClient] = {}

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def spec_for(self, role: LLMRole) -> ModelSpec:
        """Resolve *role* to a concrete provider + model, without side effects."""
        role_key = str(role)
        provider = self._policy.provider_for(role_key, default=self._default_provider)
        model = self._policy.model_for(role_key)

        if provider != "litellm" and provider in _DIRECT_PROVIDERS:
            # Direct SDK backends forward `model` verbatim — a LiteLLM-style
            # prefix must be stripped (own prefix) or flagged (foreign prefix).
            own_prefix = f"{provider}/"
            if model.startswith(own_prefix):
                model = model[len(own_prefix) :]
            elif any(model.startswith(p) for p in _KNOWN_PREFIXES):
                logger.warning(
                    "ModelRouter: role=%s resolved provider=%r but model=%r carries "
                    "a different provider's prefix — sending to the SDK as-is, "
                    "which will likely fail",
                    role_key,
                    provider,
                    model,
                )

        api_base = (
            config.app.ollama_base_url
            if model.startswith(("ollama/", "ollama_chat/"))
            else None
        )
        return ModelSpec(provider=provider, model=model, api_base=api_base)

    # ------------------------------------------------------------------
    # Client cache
    # ------------------------------------------------------------------

    def _factory(self) -> Callable[..., Any]:
        if self._client_factory is not None:
            return self._client_factory
        from graphclaw.llm.factory import create_llm_client  # noqa: PLC0415

        return create_llm_client

    def base_client(self, provider: str) -> LLMClient:
        """Lazily create and cache the single base client for *provider*."""
        cached = self._base_clients.get(provider)
        if cached is not None:
            return cached
        factory = self._factory()
        api_key = self._api_keys.get(provider) or None
        client = factory(provider, api_key=api_key)
        self._base_clients[provider] = client
        return client

    def for_role(self, role: LLMRole) -> RoleBoundLLMClient:
        """Return the (cached) role-bound client for *role*."""
        cached = self._role_clients.get(role)
        if cached is not None:
            return cached
        spec = self.spec_for(role)
        inner = self.base_client(spec.provider)
        bound = RoleBoundLLMClient(inner, role, spec)
        self._role_clients[role] = bound
        return bound

    def describe(self) -> dict[str, dict[str, str]]:
        """Return ``{role: {provider, model}}`` for every role — for /health."""
        return {
            str(role): {"provider": spec.provider, "model": spec.model}
            for role in LLMRole
            for spec in (self.spec_for(role),)
        }

    async def aclose(self) -> None:
        """Close each distinct base client exactly once."""
        for provider, client in self._base_clients.items():
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ModelRouter: error closing %s base client: %s", provider, exc)
        self._base_clients.clear()
        self._role_clients.clear()
