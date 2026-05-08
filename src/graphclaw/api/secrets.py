# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.secrets — Secrets management endpoints.

Description
-----------
Provides REST endpoints for managing application secrets (API keys, tokens,
credentials) stored via the platform ``SecretsClient``.

Endpoints
---------
- ``GET    /app/v1/secrets``              — List secret key names (never values).
- ``GET    /app/v1/secrets/{key}``        — Check whether a key exists.
- ``PUT    /app/v1/secrets/{key}``        — Create or update a secret value.
- ``DELETE /app/v1/secrets/{key}``        — Delete a secret.
- ``POST   /app/v1/secrets/{key}/test``   — Validate a stored secret by key prefix.
- ``GET    /app/v1/secrets/status``       — Health check for the secrets backend.

All endpoints require a valid Bearer access token.

Security design
---------------
- Values are NEVER returned in GET responses; only key names are exposed so
  callers can confirm a secret is present without reading its value.
- The test endpoint validates that a key exists and is non-empty — it does NOT
  make live API calls with the secret, as that is the caller's responsibility.
- Key scoping: Secrets are stored under a user-scoped prefix
  ``graphclaw/{user_id}/{key}`` to prevent cross-user leakage.

Design Patterns
---------------
- SecretsClient delegation: All secret operations delegate to the injected
  ``SecretsClient`` backend (env file, AWS SM, Vault, etc.).
- KeyError → 404: Missing keys raise ``KeyError`` in the client; the API
  translates these to HTTP 404.
- Namespace prefix: Keys are scoped to the authenticated user to prevent
  leakage between users.

Public API
----------
- router: ``APIRouter`` for /secrets routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, SecretsClientDep.
- fastapi: APIRouter, HTTPException, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, SecretsClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/secrets", tags=["secrets"])

# ---------------------------------------------------------------------------
# Key scoping
# ---------------------------------------------------------------------------

# Well-known prefixes the API tracks for listing.
# Since SecretsClient has no list() method, we maintain a secondary index of
# key names in the store itself as a simple JSON list. Each user's index lives
# at the special key f"graphclaw/{user_id}/_index".
_INDEX_SUFFIX = "_index"


def _user_key(user_id: str, key: str) -> str:
    """Return the user-scoped secret key: ``graphclaw/{user_id}/{key}``."""
    return f"graphclaw/{user_id}/{key}"


def _index_key(user_id: str) -> str:
    return _user_key(user_id, _INDEX_SUFFIX)


async def _load_key_index(user_id: str, secrets_client) -> list[str]:
    """Load the list of known secret keys for *user_id*."""
    import json

    try:
        raw = await secrets_client.get_secret(_index_key(user_id))
        return json.loads(raw)
    except (KeyError, Exception):
        return []


async def _save_key_index(user_id: str, secrets_client, keys: list[str]) -> None:
    import json

    await secrets_client.set_secret(_index_key(user_id), json.dumps(sorted(set(keys))))


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SecretKeyEntry(BaseModel):
    """A known secret key (name only — value is never exposed)."""

    key: str
    exists: bool = True


class SecretExistsResponse(BaseModel):
    """Whether a specific key exists in the secrets store."""

    key: str
    exists: bool


class SecretSetRequest(BaseModel):
    """Request body for PUT /app/v1/secrets/{key}."""

    value: str


class SecretTestResult(BaseModel):
    """Result of a secret existence/presence test."""

    key: str
    valid: bool
    detail: str = ""


class SecretsStatusResponse(BaseModel):
    """Health status of the secrets backend."""

    backend: str
    reachable: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    response_model=SecretsStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Secrets backend health check",
    description="Return the reachability status of the configured secrets backend.",
)
async def get_secrets_status(
    user_id: CurrentUserDep,  # noqa: ARG001
    secrets_client: SecretsClientDep,
) -> SecretsStatusResponse:
    """Check whether the secrets backend is reachable."""
    backend_name = type(secrets_client).__name__
    try:
        # Attempt a harmless probe: try to read a known-absent key
        await secrets_client.get_secret(_user_key(user_id, "_health_probe_"))
    except KeyError:
        # KeyError means the backend responded — it's reachable
        return SecretsStatusResponse(
            backend=backend_name, reachable=True, detail="Backend is reachable"
        )
    except Exception as exc:
        logger.warning("secrets: health probe failed: %s", exc)
        return SecretsStatusResponse(backend=backend_name, reachable=False, detail=str(exc))
    return SecretsStatusResponse(backend=backend_name, reachable=True)


@router.get(
    "",
    response_model=list[SecretKeyEntry],
    status_code=status.HTTP_200_OK,
    summary="List secret keys",
    description=(
        "Return the names of all secrets known for the authenticated user.  "
        "Values are never returned — only key names."
    ),
)
async def list_secrets(
    user_id: CurrentUserDep,
    secrets_client: SecretsClientDep,
) -> list[SecretKeyEntry]:
    """List known secret key names for the authenticated user."""
    keys = await _load_key_index(user_id, secrets_client)
    return [SecretKeyEntry(key=k, exists=True) for k in keys]


@router.get(
    "/{key}",
    response_model=SecretExistsResponse,
    status_code=status.HTTP_200_OK,
    summary="Check if a secret exists",
    description="Return whether a secret with the given key exists for the authenticated user.",
)
async def check_secret(
    key: str,
    user_id: CurrentUserDep,
    secrets_client: SecretsClientDep,
) -> SecretExistsResponse:
    """Check whether a specific secret key exists."""
    try:
        await secrets_client.get_secret(_user_key(user_id, key))
        return SecretExistsResponse(key=key, exists=True)
    except KeyError:
        return SecretExistsResponse(key=key, exists=False)
    except Exception as exc:
        logger.error("secrets: check_secret failed key=%s: %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Secrets backend error: {exc}",
        )


@router.put(
    "/{key}",
    response_model=SecretKeyEntry,
    status_code=status.HTTP_200_OK,
    summary="Set a secret value",
    description=(
        "Create or overwrite a secret for the authenticated user.  "
        "The key is added to the user's secret index."
    ),
)
async def set_secret(
    key: str,
    body: SecretSetRequest,
    user_id: CurrentUserDep,
    secrets_client: SecretsClientDep,
) -> SecretKeyEntry:
    """Create or update a secret for the authenticated user."""
    try:
        await secrets_client.set_secret(_user_key(user_id, key), body.value)
    except Exception as exc:
        logger.error("secrets: set_secret failed key=%s: %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to set secret '{key}': {exc}",
        )
    # Update index
    index = await _load_key_index(user_id, secrets_client)
    if key not in index:
        index.append(key)
        await _save_key_index(user_id, secrets_client, index)
    logger.info("secrets: set key=%s for user_id=%s", key, user_id)
    return SecretKeyEntry(key=key, exists=True)


@router.delete(
    "/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a secret",
    description="Remove a secret by key for the authenticated user.",
)
async def delete_secret(
    key: str,
    user_id: CurrentUserDep,
    secrets_client: SecretsClientDep,
) -> None:
    """Delete a secret for the authenticated user."""
    try:
        await secrets_client.delete_secret(_user_key(user_id, key))
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Secret '{key}' not found",
        )
    # Remove from index
    index = await _load_key_index(user_id, secrets_client)
    if key in index:
        index = [k for k in index if k != key]
        await _save_key_index(user_id, secrets_client, index)
    logger.info("secrets: deleted key=%s for user_id=%s", key, user_id)


@router.post(
    "/{key}/test",
    response_model=SecretTestResult,
    status_code=status.HTTP_200_OK,
    summary="Test a secret",
    description=(
        "Verify that a secret is present and non-empty.  "
        "Does not make live API calls with the secret value."
    ),
)
async def test_secret(
    key: str,
    user_id: CurrentUserDep,
    secrets_client: SecretsClientDep,
) -> SecretTestResult:
    """Test that a secret exists and is non-empty."""
    try:
        value = await secrets_client.get_secret(_user_key(user_id, key))
        if not value:
            return SecretTestResult(key=key, valid=False, detail="Secret exists but is empty")
        return SecretTestResult(key=key, valid=True, detail="Secret is present and non-empty")
    except KeyError:
        return SecretTestResult(key=key, valid=False, detail=f"Secret '{key}' not found")
    except Exception as exc:
        logger.error("secrets: test failed key=%s: %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Secrets backend error: {exc}",
        )
