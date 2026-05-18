# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.auth.oauth — OAuth 2.0 + PKCE helpers for Google, GitHub, and Microsoft.

Description
-----------
Provides ``OAuthProvider`` (configuration dataclass) and ``OAuthService``
(async service class) for completing OAuth 2.0 Authorization Code + PKCE flows
against Google, GitHub, and Microsoft identity providers.

The PKCE extension (RFC 7636) protects the authorization code grant against
interception attacks by binding a ``code_verifier`` secret to the authorization
request via a SHA-256 ``code_challenge``.  The verifier and a CSRF ``state``
token are stored in Redis with a 10-minute TTL and validated during the
callback.

Design Patterns
---------------
- Dataclass: ``OAuthProvider`` is a plain value object holding IdP-specific
  configuration; this keeps ``OAuthService`` provider-agnostic.
- Strategy: ``OAuthService`` dispatches to provider-specific configurations
  looked up by name.  Adding a new IdP requires only registering a new
  ``OAuthProvider`` in the ``_providers`` dict.
- Factory functions: ``GOOGLE_PROVIDER``, ``GITHUB_PROVIDER``,
  ``MICROSOFT_PROVIDER`` read from environment variables and return ``None``
  if the IdP is not configured — callers can check availability cleanly.

Public API
----------
- OAuthProvider: dataclass — IdP configuration.
- OAuthService: async OAuth 2.0 + PKCE service.
  - get_authorization_url(provider_name, redirect_uri) -> tuple[str, str]
  - exchange_code(provider_name, code, state, redirect_uri) -> dict
- GOOGLE_PROVIDER() -> OAuthProvider | None
- GITHUB_PROVIDER() -> OAuthProvider | None
- MICROSOFT_PROVIDER() -> OAuthProvider | None

Dependencies
------------
- httpx: Async HTTP client for token exchange and userinfo fetches.
- authlib: PKCE code_verifier and code_challenge generation utilities.
- redis.asyncio: State and code_verifier storage with TTL.
- os, hashlib, secrets, base64: stdlib.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_OAUTH_STATE_TTL_SECONDS = 10 * 60  # 10 minutes
_REDIS_STATE_PREFIX = "auth:oauth_state:"
_REDIS_VERIFIER_PREFIX = "auth:oauth_verifier:"


# ── OAuthProvider dataclass ────────────────────────────────────────────────────


@dataclass
class OAuthProvider:
    """Configuration for a single OAuth 2.0 identity provider.

    Attributes
    ----------
    name:
        Short identifier used as a URL path segment (e.g. ``"google"``).
    client_id:
        OAuth application client ID registered with the IdP.
    client_secret:
        OAuth application client secret registered with the IdP.
    authorize_url:
        IdP's authorization endpoint URL.
    token_url:
        IdP's token endpoint URL.
    userinfo_url:
        IdP's userinfo endpoint URL (used after token exchange).
    scopes:
        List of OAuth scopes to request (e.g. ``["openid", "email", "profile"]``).
    """

    name: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: list[str] = field(default_factory=list)


# ── Provider factory functions ─────────────────────────────────────────────────


def GOOGLE_PROVIDER() -> OAuthProvider | None:
    """Return Google OAuth provider config from environment variables.

    Environment Variables
    ---------------------
    OAUTH_GOOGLE_CLIENT_ID:
        Google OAuth client ID.
    OAUTH_GOOGLE_CLIENT_SECRET:
        Google OAuth client secret.

    Returns
    -------
    OAuthProvider | None:
        Configured ``OAuthProvider`` or ``None`` if env vars are not set.
    """
    client_id = os.environ.get("OAUTH_GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("OAUTH_GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return OAuthProvider(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://www.googleapis.com/oauth2/v3/userinfo",
        scopes=["openid", "email", "profile"],
    )


def GITHUB_PROVIDER() -> OAuthProvider | None:
    """Return GitHub OAuth provider config from environment variables.

    Environment Variables
    ---------------------
    OAUTH_GITHUB_CLIENT_ID:
        GitHub OAuth application client ID.
    OAUTH_GITHUB_CLIENT_SECRET:
        GitHub OAuth application client secret.

    Returns
    -------
    OAuthProvider | None:
        Configured ``OAuthProvider`` or ``None`` if env vars are not set.
    """
    client_id = os.environ.get("OAUTH_GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("OAUTH_GITHUB_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return OAuthProvider(
        name="github",
        client_id=client_id,
        client_secret=client_secret,
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        scopes=["read:user", "user:email"],
    )


def MICROSOFT_PROVIDER() -> OAuthProvider | None:
    """Return Microsoft OAuth provider config from environment variables.

    Environment Variables
    ---------------------
    OAUTH_MICROSOFT_CLIENT_ID:
        Azure AD application (client) ID.
    OAUTH_MICROSOFT_CLIENT_SECRET:
        Azure AD application client secret.

    Returns
    -------
    OAuthProvider | None:
        Configured ``OAuthProvider`` or ``None`` if env vars are not set.
    """
    client_id = os.environ.get("OAUTH_MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("OAUTH_MICROSOFT_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return OAuthProvider(
        name="microsoft",
        client_id=client_id,
        client_secret=client_secret,
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/oidc/userinfo",
        scopes=["openid", "email", "profile", "User.Read"],
    )


# ── PKCE helpers ───────────────────────────────────────────────────────────────


def _generate_code_verifier() -> str:
    """Generate a PKCE ``code_verifier`` (RFC 7636 §4.1).

    Returns a cryptographically random URL-safe string of 64 characters.
    """
    return secrets.token_urlsafe(64)


def _generate_code_challenge(code_verifier: str) -> str:
    """Generate a PKCE ``code_challenge`` using the S256 method (RFC 7636 §4.2).

    Parameters
    ----------
    code_verifier:
        The plain ``code_verifier`` string.

    Returns
    -------
    str:
        Base64url-encoded SHA-256 hash of *code_verifier* (no padding).
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _generate_state() -> str:
    """Generate a random CSRF state token.

    Returns
    -------
    str:
        URL-safe random string of 32 characters.
    """
    return secrets.token_urlsafe(32)


# ── OAuthService ───────────────────────────────────────────────────────────────


class OAuthService:
    """Async OAuth 2.0 + PKCE service supporting Google, GitHub, and Microsoft.

    Parameters
    ----------
    redis_client:
        An initialised ``redis.asyncio.Redis`` client used to store CSRF state
        and PKCE code verifiers.  When ``None``, an in-process dict is used
        (local dev / testing — state is not shared across replicas).

    Notes
    -----
    Construct via ``OAuthService.from_env()`` or pass a Redis client directly.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis: Any | None = redis_client
        # In-process fallback: maps state_key -> value
        self._state_store: dict[str, str] = {}
        self._verifier_store: dict[str, str] = {}

        # Build provider registry from available env vars
        self._providers: dict[str, OAuthProvider] = {}
        for factory in (GOOGLE_PROVIDER, GITHUB_PROVIDER, MICROSOFT_PROVIDER):
            provider = factory()
            if provider is not None:
                self._providers[provider.name] = provider
                logger.debug("OAuthService: registered provider '%s'", provider.name)
            else:
                # Determine which provider this is for logging
                name = factory.__name__.replace("_PROVIDER", "").lower()
                logger.debug("OAuthService: provider '%s' not configured (env vars not set)", name)

    @classmethod
    def from_env(cls) -> OAuthService:
        """Construct an ``OAuthService`` using REDIS_URL from the environment.

        Environment Variables
        ---------------------
        REDIS_URL:
            Redis connection URL.  If not set, in-process state storage is used.

        Returns
        -------
        OAuthService:
            Configured service instance.
        """
        redis_url = os.environ.get("REDIS_URL", "").strip()
        redis_client: Any | None = None
        if redis_url:
            try:
                import redis.asyncio as aioredis  # noqa: PLC0415

                redis_client = aioredis.from_url(redis_url, decode_responses=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OAuthService: Could not initialise Redis (%s): %s. "
                    "Using in-process state storage.",
                    redis_url,
                    exc,
                )
        else:
            logger.warning(
                "OAuthService: REDIS_URL not set — using in-process state storage. "
                "OAuth state will not be shared across replicas."
            )
        return cls(redis_client=redis_client)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def get_authorization_url(
        self,
        provider_name: str,
        redirect_uri: str,
    ) -> tuple[str, str]:
        """Build the IdP authorization URL with PKCE and CSRF state.

        Parameters
        ----------
        provider_name:
            One of ``"google"``, ``"github"``, ``"microsoft"``.
        redirect_uri:
            The OAuth callback URL registered with the IdP.

        Returns
        -------
        tuple[str, str]:
            ``(authorization_url, state)`` where *state* is the CSRF token that
            must be validated in the callback.

        Raises
        ------
        ValueError:
            If *provider_name* is not a configured provider.
        """
        provider = self._get_provider(provider_name)

        state = _generate_state()
        code_verifier = _generate_code_verifier()
        code_challenge = _generate_code_challenge(code_verifier)

        # Store state and verifier for callback validation
        await self._store_state(state, "1")
        await self._store_verifier(state, code_verifier)

        # Build authorization URL
        scopes_str = " ".join(provider.scopes)
        params: dict[str, str] = {
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes_str,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        # GitHub does not support PKCE natively; omit for GitHub
        if provider_name == "github":
            params.pop("code_challenge", None)
            params.pop("code_challenge_method", None)

        query_string = "&".join(f"{k}={_url_encode(v)}" for k, v in params.items())
        authorization_url = f"{provider.authorize_url}?{query_string}"

        logger.debug(
            "OAuthService: generated authorization URL for provider '%s' state=%s",
            provider_name,
            state,
        )
        return authorization_url, state

    async def exchange_code(
        self,
        provider_name: str,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Validate state, exchange authorization code for tokens, fetch userinfo.

        Parameters
        ----------
        provider_name:
            One of ``"google"``, ``"github"``, ``"microsoft"``.
        code:
            The authorization code received in the callback query parameter.
        state:
            The CSRF state token from the callback query parameter.
        redirect_uri:
            Must exactly match the ``redirect_uri`` used in ``get_authorization_url``.

        Returns
        -------
        dict[str, Any]:
            Normalized user info: ``{"provider", "provider_user_id", "email", "name"}``.

        Raises
        ------
        ValueError:
            If *state* is invalid or expired, if *provider_name* is unknown,
            or if userinfo cannot be fetched.
        """
        provider = self._get_provider(provider_name)

        # Validate CSRF state
        stored = await self._get_state(state)
        if stored is None:
            raise ValueError(f"OAuth state '{state}' is invalid or expired")

        # Retrieve PKCE code_verifier (may be None for GitHub)
        code_verifier = await self._get_verifier(state)

        # Clean up state and verifier from store
        await self._delete_state(state)
        await self._delete_verifier(state)

        # Exchange authorization code for access token
        token_data = await self._exchange_code_for_token(
            provider, code, redirect_uri, code_verifier
        )
        access_token: str = token_data.get("access_token", "")
        if not access_token:
            raise ValueError(
                f"OAuthService: token exchange for '{provider_name}' returned no access_token"
            )

        # Fetch userinfo
        userinfo = await self._fetch_userinfo(provider, access_token)

        # Normalize to common structure
        return self._normalize_userinfo(provider_name, userinfo)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_provider(self, name: str) -> OAuthProvider:
        """Retrieve a configured provider by name, raising ValueError if absent."""
        provider = self._providers.get(name)
        if provider is None:
            available = list(self._providers.keys())
            raise ValueError(
                f"OAuth provider '{name}' is not configured. "
                f"Available providers: {available}. "
                f"Set the corresponding OAUTH_{name.upper()}_CLIENT_ID and "
                f"OAUTH_{name.upper()}_CLIENT_SECRET environment variables."
            )
        return provider

    async def _exchange_code_for_token(
        self,
        provider: OAuthProvider,
        code: str,
        redirect_uri: str,
        code_verifier: str | None,
    ) -> dict[str, Any]:
        """POST to the token endpoint and return the parsed response."""
        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier and provider.name != "github":
            data["code_verifier"] = code_verifier

        headers: dict[str, str] = {"Accept": "application/json"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(provider.token_url, data=data, headers=headers)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                return response.json()
            # GitHub returns form-encoded by default
            from urllib.parse import parse_qs  # noqa: PLC0415

            parsed = parse_qs(response.text)
            return {k: v[0] for k, v in parsed.items()}

    async def _fetch_userinfo(
        self,
        provider: OAuthProvider,
        access_token: str,
    ) -> dict[str, Any]:
        """Fetch user profile data from the IdP's userinfo endpoint."""
        headers: dict[str, str] = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(provider.userinfo_url, headers=headers)
            response.raise_for_status()
            return response.json()

    def _normalize_userinfo(
        self,
        provider_name: str,
        userinfo: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize provider-specific userinfo to a common schema.

        Returns
        -------
        dict with keys: ``provider``, ``provider_user_id``, ``email``, ``name``.
        """
        if provider_name == "google":
            return {
                "provider": "google",
                "provider_user_id": str(userinfo.get("sub", "")),
                "email": userinfo.get("email", ""),
                "name": userinfo.get("name", ""),
            }
        elif provider_name == "github":
            return {
                "provider": "github",
                "provider_user_id": str(userinfo.get("id", "")),
                "email": userinfo.get("email", ""),
                "name": userinfo.get("name") or userinfo.get("login", ""),
            }
        elif provider_name == "microsoft":
            return {
                "provider": "microsoft",
                "provider_user_id": str(userinfo.get("sub", "")),
                "email": userinfo.get("email", ""),
                "name": userinfo.get("name", ""),
            }
        else:
            # Generic fallback
            return {
                "provider": provider_name,
                "provider_user_id": str(userinfo.get("sub") or userinfo.get("id") or ""),
                "email": userinfo.get("email", ""),
                "name": userinfo.get("name", ""),
            }

    # ── State / verifier storage ───────────────────────────────────────────────

    async def _store_state(self, state: str, value: str) -> None:
        key = f"{_REDIS_STATE_PREFIX}{state}"
        if self._redis is not None:
            try:
                await self._redis.set(key, value, ex=_OAUTH_STATE_TTL_SECONDS)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("OAuthService: Redis state store failed: %s", exc)
        self._state_store[key] = value

    async def _get_state(self, state: str) -> str | None:
        key = f"{_REDIS_STATE_PREFIX}{state}"
        if self._redis is not None:
            try:
                return await self._redis.get(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OAuthService: Redis state get failed: %s", exc)
        return self._state_store.get(key)

    async def _delete_state(self, state: str) -> None:
        key = f"{_REDIS_STATE_PREFIX}{state}"
        if self._redis is not None:
            try:
                await self._redis.delete(key)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("OAuthService: Redis state delete failed: %s", exc)
        self._state_store.pop(key, None)

    async def _store_verifier(self, state: str, verifier: str) -> None:
        key = f"{_REDIS_VERIFIER_PREFIX}{state}"
        if self._redis is not None:
            try:
                await self._redis.set(key, verifier, ex=_OAUTH_STATE_TTL_SECONDS)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("OAuthService: Redis verifier store failed: %s", exc)
        self._verifier_store[key] = verifier

    async def _get_verifier(self, state: str) -> str | None:
        key = f"{_REDIS_VERIFIER_PREFIX}{state}"
        if self._redis is not None:
            try:
                return await self._redis.get(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OAuthService: Redis verifier get failed: %s", exc)
        return self._verifier_store.get(key)

    async def _delete_verifier(self, state: str) -> None:
        key = f"{_REDIS_VERIFIER_PREFIX}{state}"
        if self._redis is not None:
            try:
                await self._redis.delete(key)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("OAuthService: Redis verifier delete failed: %s", exc)
        self._verifier_store.pop(key, None)


# ── URL encoding helper ────────────────────────────────────────────────────────


def _url_encode(value: str) -> str:
    """Percent-encode a string for use in a URL query parameter value."""
    from urllib.parse import quote  # noqa: PLC0415

    return quote(value, safe="")
