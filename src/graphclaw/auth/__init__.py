# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.auth — OAuth 2.0 + Platform JWT authentication module.

Description
-----------
Provides the complete authentication layer for GraphClaw, covering:
- RS256 JWT issuance and verification (access tokens + refresh tokens).
- OAuth 2.0 + PKCE flows for Google, GitHub, and Microsoft IdPs.
- FastAPI dependency-injection helpers for protecting routes.
- Token revocation via Redis (with in-process fallback for local dev).
- User onboarding provisioning (graph node, S3 prefix, default workspace).

Design Patterns
---------------
- Singleton: JWTService is initialized once from environment variables and
  reused across the application lifetime via the middleware module's lazy init.
- Strategy: OAuthService dispatches to provider-specific configurations,
  making it straightforward to add new IdPs.
- Dependency Injection: require_auth and get_current_user_id are FastAPI
  Depends-compatible callables that extract and validate Bearer tokens.

Public API
----------
- JWTService: RS256 JWT issuance, verification, and revocation.
- require_auth: FastAPI dependency — raises HTTP 401 if token is invalid.
- get_current_user_id: FastAPI dependency — returns the authenticated user_id.
- UserProvisioningService: Atomic user onboarding (graph + storage + tokens).
- ProvisioningResult: Dataclass returned by UserProvisioningService.provision_new_user.

Dependencies
------------
- graphclaw.auth.jwt: JWTService implementation.
- graphclaw.auth.middleware: FastAPI dependency helpers.
- graphclaw.auth.provisioning: UserProvisioningService and ProvisioningResult.
"""

from __future__ import annotations

from graphclaw.auth.jwt import JWTService
from graphclaw.auth.middleware import get_current_user_id, require_auth
from graphclaw.auth.provisioning import ProvisioningResult, UserProvisioningService

__all__ = [
    "JWTService",
    "require_auth",
    "get_current_user_id",
    "UserProvisioningService",
    "ProvisioningResult",
]
