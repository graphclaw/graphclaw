"""graphclaw.infra.secrets — SecretsClient ABC and concrete implementations.

Description
-----------
Provides the abstract ``SecretsClient`` interface for retrieving, storing,
and deleting application secrets, along with concrete implementations for
multiple backends:

- ``EnvFileSecretsClient``: reads from and writes to ``os.environ`` (local dev).
- ``AWSSecretsClient``: backed by AWS Secrets Manager (via boto3).
- ``HashiCorpVaultClient``: backed by HashiCorp Vault KV v2 (via httpx).

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
- AWSSecretsClient: AWS Secrets Manager-backed implementation.
- HashiCorpVaultClient: HashiCorp Vault KV v2-backed implementation (httpx).

Dependencies
------------
- abc: ABC, abstractmethod.
- os: Environment variable access.
- dotenv: load_dotenv for automatic .env file loading.
- httpx: Async HTTP client for HashiCorpVaultClient.

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


class AWSSecretsClient(SecretsClient):
    """Secrets client backed by AWS Secrets Manager.

    Uses ``boto3`` (via ``asyncio.to_thread``) for async-safe operation.
    Secrets are stored as plaintext strings or JSON in AWS Secrets Manager.
    For JSON secrets, pass ``json_key`` to extract a specific field.

    Environment Variables (or IAM role credentials):
        AWS_REGION / AWS_DEFAULT_REGION — AWS region (default: ``"us-east-1"``).
        AWS_ACCESS_KEY_ID               — AWS access key (optional if using IAM role).
        AWS_SECRET_ACCESS_KEY           — AWS secret key (optional if using IAM role).
        AWS_SESSION_TOKEN               — Session token (for temporary credentials).

    Args:
        region: AWS region name (falls back to ``AWS_REGION`` env var).
        secret_prefix: Optional prefix prepended to all secret names
            (e.g. ``"graphclaw/prod/"``).
    """

    def __init__(
        self,
        region: str | None = None,
        secret_prefix: str = "",
    ) -> None:
        self._region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self._prefix = secret_prefix
        self._client: object | None = None  # lazy-init boto3 client

    def _get_client(self) -> object:
        """Lazily create and return the boto3 secretsmanager client."""
        if self._client is None:
            try:
                import boto3  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for AWSSecretsClient. "
                    "Install it with: pip install 'boto3>=1.34'"
                ) from exc
            self._client = boto3.client("secretsmanager", region_name=self._region)
        return self._client

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get_secret(self, key: str) -> str:
        """Retrieve a secret value from AWS Secrets Manager.

        Args:
            key: The secret name (will be prefixed with ``secret_prefix`` if set).

        Returns:
            The secret value as a plain string.

        Raises:
            KeyError: If the secret does not exist.
            RuntimeError: If boto3 is not installed.
        """
        import asyncio  # noqa: PLC0415

        client = self._get_client()
        full_key = self._full_key(key)

        def _fetch() -> str:
            try:
                import botocore.exceptions  # noqa: PLC0415

                response = client.get_secret_value(SecretId=full_key)  # type: ignore[attr-defined]
                return response.get("SecretString") or ""
            except Exception as exc:  # noqa: BLE001
                # Map ResourceNotFoundException to KeyError
                if "ResourceNotFoundException" in type(exc).__name__:
                    raise KeyError(f"Secret '{full_key}' not found in AWS Secrets Manager") from exc
                raise

        return await asyncio.to_thread(_fetch)

    async def set_secret(self, key: str, value: str) -> None:
        """Create or update a secret in AWS Secrets Manager.

        Args:
            key: The secret name (will be prefixed with ``secret_prefix`` if set).
            value: The secret value (stored as plaintext string).
        """
        import asyncio  # noqa: PLC0415

        client = self._get_client()
        full_key = self._full_key(key)

        def _put() -> None:
            try:
                client.put_secret_value(SecretId=full_key, SecretString=value)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                # Secret may not exist yet — create it
                client.create_secret(Name=full_key, SecretString=value)  # type: ignore[attr-defined]

        await asyncio.to_thread(_put)

    async def delete_secret(self, key: str) -> None:
        """Schedule a secret for deletion in AWS Secrets Manager.

        AWS Secrets Manager uses a soft-delete with a recovery window
        (default: 30 days).

        Args:
            key: The secret name (will be prefixed with ``secret_prefix`` if set).

        Raises:
            KeyError: If the secret does not exist.
        """
        import asyncio  # noqa: PLC0415

        client = self._get_client()
        full_key = self._full_key(key)

        def _delete() -> None:
            try:
                client.delete_secret(  # type: ignore[attr-defined]
                    SecretId=full_key,
                    RecoveryWindowInDays=30,
                )
            except Exception as exc:  # noqa: BLE001
                if "ResourceNotFoundException" in type(exc).__name__:
                    raise KeyError(f"Secret '{full_key}' not found in AWS Secrets Manager") from exc
                raise

        await asyncio.to_thread(_delete)


class HashiCorpVaultClient(SecretsClient):
    """Secrets client backed by HashiCorp Vault KV v2 secrets engine.

    Communicates with Vault exclusively via its HTTP API using ``httpx``
    (no ``hvac`` SDK dependency).  All three operations — get, set, and
    delete — map directly to the KV v2 REST endpoints:

    - GET  ``{vault_addr}/v1/{mount_path}/data/{key}``   → read secret
    - POST ``{vault_addr}/v1/{mount_path}/data/{key}``   → write secret
    - DELETE ``{vault_addr}/v1/{mount_path}/metadata/{key}`` → hard delete

    Secrets are stored as ``{"data": {"value": "<secret>"}}`` under the KV
    v2 data path so that all versions share the same logical key format.

    Vault Enterprise namespaces are supported via the ``X-Vault-Namespace``
    header.

    Environment Variables
    ---------------------
    VAULT_ADDR
        Vault server URL (default: ``http://localhost:8200``).
    VAULT_TOKEN
        Vault authentication token.
    VAULT_NAMESPACE
        Optional Vault Enterprise namespace (e.g. ``"admin"``).

    Args:
        vault_addr: Vault server base URL.  Falls back to the ``VAULT_ADDR``
            environment variable, then ``http://localhost:8200``.
        token: Vault token for authentication.  Falls back to the
            ``VAULT_TOKEN`` environment variable.
        mount_path: KV v2 secrets engine mount path (default ``"secret"``).
        namespace: Vault Enterprise namespace.  Falls back to the
            ``VAULT_NAMESPACE`` environment variable.  Omit for OSS Vault.
    """

    def __init__(
        self,
        vault_addr: str | None = None,
        token: str | None = None,
        mount_path: str = "secret",
        namespace: str | None = None,
    ) -> None:
        self._vault_addr = (
            vault_addr
            or os.environ.get("VAULT_ADDR", "http://localhost:8200")
        ).rstrip("/")
        self._token = token or os.environ.get("VAULT_TOKEN", "")
        self._mount_path = mount_path.strip("/")
        self._namespace = namespace or os.environ.get("VAULT_NAMESPACE")

    def _headers(self) -> dict[str, str]:
        """Build the common Vault request headers."""
        headers: dict[str, str] = {"X-Vault-Token": self._token}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        return headers

    def _data_url(self, key: str) -> str:
        """Return the KV v2 *data* endpoint URL for *key*."""
        return f"{self._vault_addr}/v1/{self._mount_path}/data/{key.lstrip('/')}"

    def _metadata_url(self, key: str) -> str:
        """Return the KV v2 *metadata* endpoint URL for *key*."""
        return f"{self._vault_addr}/v1/{self._mount_path}/metadata/{key.lstrip('/')}"

    async def get_secret(self, key: str) -> str:
        """Retrieve the latest version of a secret from Vault KV v2.

        Args:
            key: Secret path relative to the mount (e.g. ``"graphclaw/api_key"``).

        Returns:
            The string stored in ``data.data.value``.

        Raises:
            KeyError: If the secret path does not exist (HTTP 404).
            RuntimeError: If Vault returns any other non-2xx status.
        """
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient() as client:
            response = await client.get(
                self._data_url(key),
                headers=self._headers(),
            )

        if response.status_code == 404:
            raise KeyError(f"Secret '{key}' not found in Vault")
        if not response.is_success:
            raise RuntimeError(
                f"Vault get_secret failed for '{key}': "
                f"HTTP {response.status_code} — {response.text}"
            )

        payload = response.json()
        try:
            return payload["data"]["data"]["value"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Vault response for '{key}' missing expected 'data.data.value' field"
            ) from exc

    async def set_secret(self, key: str, value: str) -> None:
        """Write (or update) a secret in Vault KV v2.

        Vault KV v2 automatically versions on every write.  The ``value``
        field inside the ``data`` map is the only field written.

        Args:
            key: Secret path relative to the mount.
            value: The secret value to store.

        Raises:
            RuntimeError: If Vault returns a non-2xx status.
        """
        import httpx  # noqa: PLC0415

        body = {"data": {"value": value}}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self._data_url(key),
                headers=self._headers(),
                json=body,
            )

        if not response.is_success:
            raise RuntimeError(
                f"Vault set_secret failed for '{key}': "
                f"HTTP {response.status_code} — {response.text}"
            )

    async def delete_secret(self, key: str) -> None:
        """Permanently delete all versions and metadata for a secret.

        Uses the ``metadata`` endpoint, which performs a hard delete of all
        versions (equivalent to ``vault kv metadata delete``).

        Args:
            key: Secret path relative to the mount.

        Raises:
            KeyError: If the secret path does not exist (HTTP 404).
            RuntimeError: If Vault returns any other non-2xx status.
        """
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                self._metadata_url(key),
                headers=self._headers(),
            )

        if response.status_code == 404:
            raise KeyError(f"Secret '{key}' not found in Vault")
        if not response.is_success:
            raise RuntimeError(
                f"Vault delete_secret failed for '{key}': "
                f"HTTP {response.status_code} — {response.text}"
            )


__all__ = [
    "SecretsClient",
    "EnvFileSecretsClient",
    "AWSSecretsClient",
    "HashiCorpVaultClient",
]
