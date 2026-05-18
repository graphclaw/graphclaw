# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.auth.middleware — FastAPI dependency injection for JWT authentication.

Description
-----------
Provides FastAPI ``Depends``-compatible callables for protecting routes with
Bearer token authentication.  The JWT verification is performed by
``JWTService``, which is created lazily from environment variables on first
use and cached as a module-level singleton for the application lifetime.

Design Patterns
---------------
- Singleton: ``_jwt_service`` is a module-level variable initialized once
  (thread-safe for async; FastAPI's event loop is single-threaded).
- Dependency Injection: ``get_current_user_id`` and ``require_auth`` follow
  the FastAPI ``Depends`` protocol — they accept injected dependencies and
  return the authenticated user ID, raising ``HTTPException(401)`` on failure.

Public API
----------
- get_current_user_id: FastAPI dependency — returns authenticated user_id str.
- require_auth: Alias for get_current_user_id (semantic sugar).
- get_jwt_service: FastAPI dependency — returns singleton JWTService.
- init_jwt_service: Call during application startup to pre-warm the singleton.

Dependencies
------------
- graphclaw.auth.jwt: JWTService, JWTError.
- fastapi: Depends, HTTPException, status (third-party).
- fastapi.security: HTTPBearer, HTTPAuthorizationCredentials (third-party).
- logging, os: stdlib.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from graphclaw.auth.jwt import JWTService

logger = logging.getLogger(__name__)

# ── Module-level singleton ─────────────────────────────────────────────────────

_jwt_service: JWTService | None = None

# HTTPBearer scheme — instructs FastAPI/Swagger to send Authorization: Bearer <token>
security = HTTPBearer(auto_error=True)


# ── Singleton management ───────────────────────────────────────────────────────


def get_jwt_service() -> JWTService:
    """FastAPI dependency that returns the singleton ``JWTService``.

    Initialises the service from environment variables on the first call.
    Subsequent calls return the cached instance.

    Returns
    -------
    JWTService:
        The application-wide JWT service instance.

    Notes
    -----
    This function is synchronous so it can be used both as a plain function
    call and as a FastAPI ``Depends`` dependency without requiring ``async``.
    """
    global _jwt_service  # noqa: PLW0603
    if _jwt_service is None:
        logger.debug("JWTService singleton: initialising from environment")
        _jwt_service = JWTService.from_env()
    return _jwt_service


def init_jwt_service(service: JWTService | None = None) -> None:
    """Pre-warm (or replace) the JWT service singleton.

    Parameters
    ----------
    service:
        A pre-built ``JWTService`` instance to use as the singleton.
        When ``None``, calls ``JWTService.from_env()`` to build from env vars.

    Notes
    -----
    Call this during application startup (e.g. in the FastAPI lifespan context)
    to ensure keys are loaded before the first request arrives.  Also useful
    in tests to inject a mock ``JWTService``.
    """
    global _jwt_service  # noqa: PLW0603
    _jwt_service = service or JWTService.from_env()
    logger.debug("JWTService singleton: initialised (pre-warmed)")


def reset_jwt_service() -> None:
    """Clear the singleton — primarily for use in tests.

    After calling this, the next call to ``get_jwt_service()`` will
    re-initialise from environment variables.
    """
    global _jwt_service  # noqa: PLW0603
    _jwt_service = None


# ── FastAPI dependency callables ───────────────────────────────────────────────


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> str:
    """Extract and validate the user_id from a Bearer JWT.

    Parameters
    ----------
    credentials:
        HTTP Authorization header parsed by ``HTTPBearer``.
    jwt_service:
        Singleton ``JWTService`` injected via ``Depends``.

    Returns
    -------
    str:
        The ``sub`` claim from the verified JWT, representing the platform
        user ID.

    Raises
    ------
    HTTPException(401):
        If the token is missing, malformed, expired, revoked, or not of
        type ``"access"``.
    """
    token = credentials.credentials

    try:
        payload: dict[str, Any] = await jwt_service.verify_token_async(token)
    except JWTError as exc:
        logger.debug("get_current_user_id: token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Only access tokens may authenticate API requests
    token_type: str = payload.get("type", "")
    if token_type != "access":
        logger.debug("get_current_user_id: token type '%s' not allowed for API auth", token_type)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> str:
    """Alias for ``get_current_user_id`` — use as ``Depends(require_auth)``.

    Parameters
    ----------
    credentials:
        HTTP Authorization header parsed by ``HTTPBearer``.
    jwt_service:
        Singleton ``JWTService`` injected via ``Depends``.

    Returns
    -------
    str:
        Authenticated platform user ID.

    Raises
    ------
    HTTPException(401):
        If authentication fails for any reason.
    """
    return await get_current_user_id(credentials=credentials, jwt_service=jwt_service)
