"""graphclaw.infra.secrets — SecretsClient ABC and EnvFileSecretsClient implementation.

Description
-----------
Provides the abstract ``SecretsClient`` interface for retrieving, storing,
and deleting application secrets, along with an ``EnvFileSecretsClient``
concrete implementation that reads from and writes to ``os.environ``.
The env-file backend is the default for local development; production
deployments swap in AWS Secrets Manager, HashiCorp Vault, or similar.

Design Patterns
---------------
- Abstract Base Class: ``SecretsClient`` defines the minimal contract so
  env-file (local), AWS SM, Vault, Azure KV, and GCP SM backends are
  interchangeable.
- Strategy: The backend is selected at application startup and injected
  wherever secrets are needed, keeping business logic backend-agnostic.

Public API
----------
- SecretsClient: ABC with get_secret, set_secret, delete_secret.
- EnvFileSecretsClient: os.environ-backed implementation (loaded via dotenv).

Dependencies
------------
- abc: ABC, abstractmethod.
- os: Environment variable access.
- dotenv: load_dotenv for automatic .env file loading.

Notes
-----
``EnvFileSecretsClient.set_secret`` only mutates the in-process environment;
it does NOT persist changes to a ``.env`` file on disk.  This is intentional
for local development convenience — production backends should use a durable
store.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from dotenv import load_dotenv

# Load .env file into os.environ at import time (no-op if absent).
load_dotenv()


class SecretsClient(ABC):
    """Abstract interface for secrets management backends."""

    @abstractmethod
    async def get_secret(self, key: str) -> str:
        """Retrieve the secret identified by *key*.

        Args:
            key: The secret identifier (e.g. ``"ANTHROPIC_API_KEY"``).

        Returns:
            The secret value as a plain string.

        Raises:
            KeyError: If the secret does not exist.
        """

    @abstractmethod
    async def set_secret(self, key: str, value: str) -> None:
        """Store or update the secret identified by *key*.

        Args:
            key: The secret identifier.
            value: The secret value to store.
        """

    @abstractmethod
    async def delete_secret(self, key: str) -> None:
        """Remove the secret identified by *key*.

        Args:
            key: The secret identifier to remove.

        Raises:
            KeyError: If the secret does not exist.
        """


class EnvFileSecretsClient(SecretsClient):
    """Secrets client backed by ``os.environ`` (loaded via python-dotenv).

    Suitable for local development. ``set_secret`` writes to the current
    process environment only (does not persist to disk).
    """

    async def get_secret(self, key: str) -> str:
        """Return the environment variable named *key*.

        Raises:
            KeyError: If *key* is not present in the environment.
        """
        value = os.environ.get(key)
        if value is None:
            raise KeyError(f"Secret '{key}' not found in environment")
        return value

    async def set_secret(self, key: str, value: str) -> None:
        """Set *key* in the current process environment.

        Note: Change is only visible within the current process; not
        persisted to disk.
        """
        os.environ[key] = value

    async def delete_secret(self, key: str) -> None:
        """Remove *key* from the current process environment.

        Raises:
            KeyError: If *key* is not present in the environment.
        """
        if key not in os.environ:
            raise KeyError(f"Secret '{key}' not found in environment")
        del os.environ[key]
