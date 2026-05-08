# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.infra.byok — Bring-Your-Own-Key (BYOK) LLM key management.

Description
-----------
Allows users to register personal LLM API keys (Anthropic, OpenAI, Google,
or LiteLLM-compatible providers).  Keys are stored in the secrets backend
under a per-user namespace::

    /workgraph/USER-{id}/byok/{provider}

The orchestrating agent and skill agents retrieve the key at runtime —
never at container startup — so a compromised container does not expose
all users' keys simultaneously.

If no BYOK key is registered for a given user/provider combination, the
platform's shared LLM key is used as the fallback.

Design Patterns
---------------
- Enum: ``BYOKProvider`` enumerates the supported LLM providers.
- Dataclass (frozen): ``BYOKKeyRef`` is an immutable value object carrying
  the user ID, provider, and resolved secret path.
- Service class: ``BYOKService`` wraps the ``SecretsClient`` ABC so the
  BYOK logic is independent of the secrets backend.

Public API
----------
- BYOKProvider: Enum of supported providers.
- BYOKKeyRef: Frozen dataclass representing a reference to a stored key.
- BYOKService: Async service for registering, retrieving, revoking, and
  listing BYOK keys.

Dependencies
------------
- graphclaw.infra.secrets: SecretsClient ABC.
- dataclasses: dataclass, field.
- enum: Enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from graphclaw.infra.secrets import SecretsClient


class BYOKProvider(Enum):
    """Enumeration of LLM providers that support BYOK key registration.

    Values correspond to the provider identifier strings used in the
    secrets path and in the LLM factory ``create_llm_client(provider=...)``.
    """

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    LITELLM = "litellm"


@dataclass(frozen=True)
class BYOKKeyRef:
    """Immutable reference to a user's BYOK key stored in the secrets backend.

    Attributes:
        user_id: The platform user identifier (e.g. ``"USER-abc123"``).
        provider: The LLM provider this key belongs to.
        secret_path: The resolved path used to read/write the key in the
            secrets backend.  Computed from *user_id* and *provider* — do
            not set manually.

    Example::

        ref = BYOKKeyRef(user_id="USER-abc123", provider=BYOKProvider.ANTHROPIC)
        # ref.secret_path == "/workgraph/USER-abc123/byok/anthropic"
    """

    user_id: str
    provider: BYOKProvider

    @property
    def secret_path(self) -> str:
        """Return the canonical secrets-backend path for this key reference."""
        return f"/workgraph/{self.user_id}/byok/{self.provider.value}"


class BYOKService:
    """Service for managing per-user Bring-Your-Own-Key (BYOK) LLM credentials.

    Wraps a ``SecretsClient`` to provide a typed, per-user interface for
    registering, retrieving, revoking, and listing BYOK LLM API keys.

    Keys are stored at the path::

        /workgraph/{user_id}/byok/{provider}

    so that each user's key is isolated in the secrets backend.

    Args:
        secrets_client: An initialised ``SecretsClient`` instance.  The
            service delegates all storage operations to this client, making
            the BYOK logic backend-agnostic.

    Example::

        service = BYOKService(secrets_client=HashiCorpVaultClient())
        ref = await service.register("USER-abc123", BYOKProvider.OPENAI, "sk-...")
        key = await service.get_key("USER-abc123", BYOKProvider.OPENAI)
    """

    def __init__(self, secrets_client: SecretsClient) -> None:
        self._secrets = secrets_client

    def _ref(self, user_id: str, provider: BYOKProvider) -> BYOKKeyRef:
        """Build a ``BYOKKeyRef`` for the given user/provider pair."""
        return BYOKKeyRef(user_id=user_id, provider=provider)

    async def register(
        self,
        user_id: str,
        provider: BYOKProvider,
        api_key: str,
    ) -> BYOKKeyRef:
        """Store a user's LLM API key in the secrets backend.

        If a key already exists for this user/provider pair it is
        overwritten (the secrets backend handles versioning if supported).

        Args:
            user_id: The platform user identifier.
            provider: The LLM provider the key belongs to.
            api_key: The raw API key string to store.

        Returns:
            A ``BYOKKeyRef`` describing where the key was stored.
        """
        ref = self._ref(user_id, provider)
        await self._secrets.set_secret(ref.secret_path, api_key)
        return ref

    async def get_key(
        self,
        user_id: str,
        provider: BYOKProvider,
    ) -> str | None:
        """Retrieve a user's registered LLM API key.

        Returns ``None`` if the user has not registered a key for the
        given provider (i.e. the secret does not exist).

        Args:
            user_id: The platform user identifier.
            provider: The LLM provider to look up.

        Returns:
            The API key string, or ``None`` if not registered.
        """
        ref = self._ref(user_id, provider)
        try:
            return await self._secrets.get_secret(ref.secret_path)
        except KeyError:
            return None

    async def revoke(self, user_id: str, provider: BYOKProvider) -> None:
        """Delete a user's registered LLM API key from the secrets backend.

        This is a permanent removal.  After revocation the platform's
        shared key will be used for this user/provider combination.

        Args:
            user_id: The platform user identifier.
            provider: The LLM provider whose key should be revoked.

        Raises:
            KeyError: If no key is registered for this user/provider pair.
        """
        ref = self._ref(user_id, provider)
        await self._secrets.delete_secret(ref.secret_path)

    async def list_providers(self, user_id: str) -> list[BYOKProvider]:
        """Return the list of providers for which the user has a registered key.

        Iterates over all ``BYOKProvider`` values and attempts to retrieve
        each key; providers where ``get_key`` returns a non-``None`` value
        are included in the result.

        Args:
            user_id: The platform user identifier.

        Returns:
            A list of ``BYOKProvider`` values for which the user has an
            active key.  Returns an empty list if no keys are registered.
        """
        registered: list[BYOKProvider] = []
        for provider in BYOKProvider:
            key = await self.get_key(user_id, provider)
            if key is not None:
                registered.append(provider)
        return registered


__all__ = [
    "BYOKProvider",
    "BYOKKeyRef",
    "BYOKService",
]
