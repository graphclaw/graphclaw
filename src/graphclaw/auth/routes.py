"""graphclaw.auth.routes — FastAPI router for OAuth 2.0 + JWT authentication.

Description
-----------
Provides the ``/auth`` route group implementing the full authentication flow:

1. ``GET  /auth/login``     — Redirect the browser to the IdP authorization page.
2. ``GET  /auth/callback``  — Receive the IdP callback, exchange the code for
                               tokens, look up or create a UserNode (stubbed),
                               and return GraphClaw access + refresh tokens.
3. ``POST /auth/refresh``   — Rotate a refresh token; return new token pair.
4. ``POST /auth/logout``    — Revoke a refresh token.
5. ``GET  /auth/me``        — Return the authenticated user's ID (requires
                               Bearer access token).

Design Patterns
---------------
- Router: ``APIRouter(prefix="/auth", tags=["auth"])`` keeps auth routes
  isolated from other route modules and easy to include/exclude.
- Dependency Injection: ``JWTService`` and ``OAuthService`` are injected via
  ``Depends`` to support overriding in tests.
- Graceful degradation: If no OAuth providers are configured, the login and
  callback endpoints return HTTP 503 with an explanatory message.

Public API
----------
- router: ``APIRouter`` instance to include in the FastAPI application.

Dependencies
------------
- graphclaw.auth.jwt: JWTService.
- graphclaw.auth.oauth: OAuthService.
- graphclaw.auth.middleware: require_auth, get_jwt_service.
- fastapi: APIRouter, Depends, HTTPException, Request (third-party).
- fastapi.responses: RedirectResponse (third-party).
- pydantic: BaseModel (third-party).
- jose: JWTError (third-party).
- logging, os: stdlib.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import JWTError
from pydantic import BaseModel

from graphclaw.auth.jwt import JWTService
from graphclaw.auth.middleware import get_current_user_id, get_jwt_service
from graphclaw.auth.oauth import OAuthService
from graphclaw.auth.provisioning import UserProvisioningService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Module-level OAuthService singleton ────────────────────────────────────────

_oauth_service: OAuthService | None = None


def get_oauth_service() -> OAuthService:
    """FastAPI dependency that returns the singleton ``OAuthService``.

    Initialises the service from environment variables on the first call.
    Subsequent calls return the cached instance.

    Returns
    -------
    OAuthService:
        The application-wide OAuth service instance.
    """
    global _oauth_service  # noqa: PLW0603
    if _oauth_service is None:
        logger.debug("OAuthService singleton: initialising from environment")
        _oauth_service = OAuthService.from_env()
    return _oauth_service


async def get_provisioning_service(
    request: Request,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> UserProvisioningService | None:
    """Return a ``UserProvisioningService`` backed by ``app.state`` services.

    Returns ``None`` when the graph store or storage client is not yet
    initialised (e.g. at startup or in tests without a DB).  Callers
    must handle ``None`` gracefully by falling back to token-only issuance.
    """
    graph_store = getattr(request.app.state, "graph_store", None)
    storage_client = getattr(request.app.state, "storage_client", None)
    if graph_store is None or storage_client is None:
        return None
    return UserProvisioningService(
        graph_store=graph_store,
        storage_client=storage_client,
        jwt_service=jwt_service,
    )


# ── Request / Response models ──────────────────────────────────────────────────


class RefreshRequest(BaseModel):
    """Request body for ``POST /auth/refresh``."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Request body for ``POST /auth/logout``."""

    refresh_token: str


class TokenResponse(BaseModel):
    """Response body for token issuance endpoints."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900  # 15 minutes in seconds


# ── Helper: build redirect URI ─────────────────────────────────────────────────


def _build_redirect_uri(provider_name: str) -> str:
    """Build the OAuth callback redirect URI for *provider_name*.

    Reads ``OAUTH_REDIRECT_BASE_URL`` from the environment (defaults to
    ``http://localhost:8000`` for local dev).

    Parameters
    ----------
    provider_name:
        The OAuth provider name (e.g. ``"google"``).

    Returns
    -------
    str:
        Full callback URL, e.g. ``https://api.graphclaw.ai/auth/callback?provider=google``.
    """
    base_url = os.environ.get("OAUTH_REDIRECT_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/auth/callback?provider={provider_name}"


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "/login",
    status_code=302,
    summary="Initiate OAuth 2.0 login",
    description=(
        "Redirects the browser to the chosen identity provider's authorization page. "
        "The ``provider`` query parameter must be one of ``google``, ``github``, "
        "or ``microsoft``.  The IdP must be configured via the corresponding "
        "``OAUTH_<PROVIDER>_CLIENT_ID`` and ``OAUTH_<PROVIDER>_CLIENT_SECRET`` "
        "environment variables."
    ),
)
async def login(
    provider: str,
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    """Redirect to the IdP authorization URL with PKCE + CSRF state.

    Parameters
    ----------
    provider:
        IdP name — one of ``"google"``, ``"github"``, ``"microsoft"``.
    oauth_service:
        ``OAuthService`` injected via ``Depends``.

    Returns
    -------
    RedirectResponse:
        HTTP 302 redirect to the IdP authorization page.

    Raises
    ------
    HTTPException(400):
        If *provider* is not a supported value.
    HTTPException(503):
        If the requested provider is not configured (missing env vars).
    """
    provider = provider.lower().strip()
    if provider not in ("google", "github", "microsoft"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'. Choose: google, github, microsoft.",
        )

    redirect_uri = _build_redirect_uri(provider)

    try:
        authorization_url, state = await oauth_service.get_authorization_url(
            provider_name=provider,
            redirect_uri=redirect_uri,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    logger.info("auth/login: redirecting to %s (state=%s)", provider, state)
    return RedirectResponse(url=authorization_url, status_code=302)


@router.get(
    "/callback",
    response_model=TokenResponse,
    summary="OAuth 2.0 callback — exchange code for tokens",
    description=(
        "Receives the IdP authorization callback, validates the CSRF state, "
        "exchanges the authorization code for an access token, fetches the user "
        "profile, provisions a UserNode if needed, and returns GraphClaw access "
        "+ refresh tokens."
    ),
)
async def callback(
    provider: str,
    code: str,
    state: str,
    jwt_service: JWTService = Depends(get_jwt_service),
    oauth_service: OAuthService = Depends(get_oauth_service),
    provisioning_service: UserProvisioningService | None = Depends(get_provisioning_service),
) -> dict[str, Any]:
    """Exchange OAuth authorization code for GraphClaw JWT tokens.

    Parameters
    ----------
    provider:
        IdP name — one of ``"google"``, ``"github"``, ``"microsoft"``.
    code:
        Authorization code received from the IdP.
    state:
        CSRF state token to validate against the stored value.
    jwt_service:
        ``JWTService`` injected via ``Depends``.
    oauth_service:
        ``OAuthService`` injected via ``Depends``.
    provisioning_service:
        ``UserProvisioningService`` injected via ``Depends``; ``None`` when the
        graph store is not initialised (dev / test without DB).

    Returns
    -------
    dict:
        ``{"access_token", "refresh_token", "token_type", "expires_in"}``.

    Raises
    ------
    HTTPException(400):
        If the state is invalid/expired or code exchange fails.
    HTTPException(503):
        If the provider is not configured.
    """
    provider = provider.lower().strip()
    redirect_uri = _build_redirect_uri(provider)

    try:
        userinfo = await oauth_service.exchange_code(
            provider_name=provider,
            code=code,
            state=state,
            redirect_uri=redirect_uri,
        )
    except ValueError as exc:
        logger.warning("auth/callback: exchange_code failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    provider_name: str = userinfo.get("provider", provider)
    provider_user_id: str = userinfo.get("provider_user_id", "")
    email: str = userinfo.get("email", "")
    name: str = userinfo.get("name", "")
    oauth_subject = f"{provider_name}:{provider_user_id}"

    # ── User provisioning — create/lookup UserNode + WorkspaceNode ───────────
    if provisioning_service is not None:
        try:
            result = await provisioning_service.provision_new_user(
                oauth_subject=oauth_subject,
                email=email,
                display_name=name,
                provider=provider_name,
            )
            logger.info(
                "auth/callback: provisioned user_id=%s is_new=%s provider=%s email=%s",
                result.user_id,
                result.is_new_user,
                provider_name,
                email,
            )
            return {
                "access_token": result.access_token,
                "refresh_token": result.refresh_token,
                "token_type": "bearer",
                "expires_in": 900,
            }
        except Exception as exc:  # noqa: BLE001
            # Log and fall through to token-only issuance so login still works
            # even if provisioning encounters a transient error.
            logger.error(
                "auth/callback: provisioning failed for %s — falling back to token-only: %s",
                email,
                exc,
            )

    # ── Fallback: token-only (no DB, or provisioning error) ──────────────────
    logger.info(
        "auth/callback: token-only mode for provider=%s email=%s platform_user_id=%s",
        provider_name,
        email,
        oauth_subject,
    )
    access_token = jwt_service.issue_access_token(oauth_subject)
    refresh_token = jwt_service.issue_refresh_token(oauth_subject)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 900,
    }


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token",
    description=(
        "Validates the supplied refresh token, revokes it, and issues a fresh "
        "access + refresh token pair.  The old refresh token is immediately "
        "invalidated to prevent reuse (refresh token rotation)."
    ),
)
async def refresh(
    body: RefreshRequest,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> dict[str, Any]:
    """Rotate a refresh token and return a new token pair.

    Parameters
    ----------
    body:
        ``{"refresh_token": str}``.
    jwt_service:
        ``JWTService`` injected via ``Depends``.

    Returns
    -------
    dict:
        ``{"access_token", "refresh_token", "token_type", "expires_in"}``.

    Raises
    ------
    HTTPException(401):
        If the refresh token is invalid, expired, or revoked.
    HTTPException(400):
        If the token is not of type ``"refresh"``.
    """
    try:
        payload = await jwt_service.verify_token_async(body.refresh_token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided token is not a refresh token",
        )

    user_id: str = payload.get("sub", "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing subject claim",
        )

    # Revoke the old refresh token (rotation)
    await jwt_service.revoke_token(body.refresh_token)

    new_access_token = jwt_service.issue_access_token(user_id)
    new_refresh_token = jwt_service.issue_refresh_token(user_id)

    logger.debug("auth/refresh: rotated refresh token for user_id=%s", user_id)

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "expires_in": 900,
    }


@router.post(
    "/logout",
    summary="Logout — revoke refresh token",
    description=(
        "Revokes the supplied refresh token, preventing it from being used to "
        "obtain new access tokens.  Access tokens are short-lived (15 min) and "
        "expire naturally; they cannot be revoked individually via this endpoint."
    ),
)
async def logout(
    body: LogoutRequest,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> dict[str, bool]:
    """Revoke a refresh token.

    Parameters
    ----------
    body:
        ``{"refresh_token": str}``.
    jwt_service:
        ``JWTService`` injected via ``Depends``.

    Returns
    -------
    dict:
        ``{"ok": True}``.

    Notes
    -----
    This endpoint always returns ``{"ok": true}`` even if the token is already
    expired or invalid, to prevent information leakage.
    """
    try:
        await jwt_service.revoke_token(body.refresh_token)
    except Exception as exc:  # noqa: BLE001
        # Log but do not expose internal errors — always return ok
        logger.warning("auth/logout: revoke_token raised unexpectedly: %s", exc)

    logger.debug("auth/logout: refresh token revoked")
    return {"ok": True}


class DevTokenRequest(BaseModel):
    """Request body for ``POST /auth/dev-token`` (development only)."""

    user_id: str = "USER-dev-001"
    role: str = "ADMIN"


@router.post(
    "/dev-token",
    response_model=TokenResponse,
    summary="Issue a dev JWT (development mode only)",
    description=(
        "Issues a real RS256 JWT for the given ``user_id`` without OAuth. "
        "**Only available when ``ENVIRONMENT=development``.**  "
        "Returns HTTP 403 in production."
    ),
    include_in_schema=os.environ.get("ENVIRONMENT", "development") == "development",
)
async def dev_token(
    body: DevTokenRequest,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> dict[str, Any]:
    """Issue a dev access + refresh token pair without OAuth.

    Only works when ``ENVIRONMENT=development``.
    """
    if os.environ.get("ENVIRONMENT", "development") != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev token endpoint is disabled in production",
        )
    access_token = jwt_service.issue_access_token(body.user_id, role=body.role)
    refresh_token = jwt_service.issue_refresh_token(body.user_id)
    logger.info("auth/dev-token: issued token for user_id=%s role=%s", body.user_id, body.role)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 900,
        "user_id": body.user_id,
        "role": body.role,
    }


@router.get(
    "/me",
    summary="Get current authenticated user",
    description=(
        "Returns the user ID of the currently authenticated user. "
        "Requires a valid Bearer access token in the ``Authorization`` header."
    ),
)
async def me(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Return the authenticated user's platform ID.

    Parameters
    ----------
    user_id:
        Platform user ID extracted from the Bearer access token by
        ``get_current_user_id``.

    Returns
    -------
    dict:
        ``{"user_id": str, "token_type": "access"}``.
    """
    return {"user_id": user_id, "token_type": "access"}
