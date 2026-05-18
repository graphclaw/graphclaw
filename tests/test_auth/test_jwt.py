# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_auth.test_jwt — Unit tests for JWTService (RS256).

Description
-----------
Tests for ``JWTService`` token issuance, verification, revocation, and the
``from_env()`` factory method.  A real RSA-2048 key pair is generated once
per module via a module-scoped fixture to keep test runs fast.

Design Patterns
---------------
- Module-scoped RSA fixture: Key generation is expensive; running it once per
  module avoids slow test runs.
- Manual expired token: An expired token is constructed by encoding a payload
  with a past ``exp`` claim directly via ``jose.jwt.encode``.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- cryptography: RSA key pair generation.
- jose: JWT encode/decode (python-jose[cryptography]).
- graphclaw.auth.jwt: JWTService, JWTError (re-exported from jose).
"""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt

from graphclaw.auth.jwt import JWTService

# ---------------------------------------------------------------------------
# Module-level fixtures — RSA key pair generated once for the whole module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_key_pair():
    """Generate a real RSA-2048 key pair once per module."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return priv_pem, pub_pem


@pytest.fixture(scope="module")
def jwt_service(rsa_key_pair):
    """Build a JWTService with real keys and no Redis (in-process revocation)."""
    priv, pub = rsa_key_pair
    return JWTService(private_key=priv, public_key=pub, redis_client=None)


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------


class TestTokenIssuance:
    def test_issue_access_token_returns_non_empty_string(self, jwt_service):
        token = jwt_service.issue_access_token("USER-test-001")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_issue_refresh_token_returns_non_empty_string(self, jwt_service):
        token = jwt_service.issue_refresh_token("USER-test-001")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_access_and_refresh_tokens_are_different(self, jwt_service):
        access = jwt_service.issue_access_token("USER-test-001")
        refresh = jwt_service.issue_refresh_token("USER-test-001")
        assert access != refresh


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


class TestTokenVerification:
    def test_verify_access_token_returns_payload_with_sub(self, jwt_service):
        user_id = "USER-alice-abc123"
        token = jwt_service.issue_access_token(user_id)
        payload = jwt_service.verify_token(token)
        assert payload["sub"] == user_id

    def test_verify_access_token_returns_payload_with_type_access(self, jwt_service):
        token = jwt_service.issue_access_token("USER-test-002")
        payload = jwt_service.verify_token(token)
        assert payload["type"] == "access"

    def test_verify_refresh_token_returns_payload_with_type_refresh(self, jwt_service):
        token = jwt_service.issue_refresh_token("USER-test-002")
        payload = jwt_service.verify_token(token)
        assert payload["type"] == "refresh"

    def test_verify_token_payload_has_required_claims(self, jwt_service):
        token = jwt_service.issue_access_token("USER-test-003")
        payload = jwt_service.verify_token(token)
        for claim in ("jti", "iat", "exp", "sub", "type"):
            assert claim in payload, f"Missing required claim: {claim}"

    def test_verify_tampered_token_raises_jwt_error(self, jwt_service):
        token = jwt_service.issue_access_token("USER-test-004")
        # Tamper by flipping a character in the signature portion
        parts = token.split(".")
        assert len(parts) == 3
        sig = parts[2]
        # Flip first character of signature
        if sig[0] == "a":
            sig = "b" + sig[1:]
        else:
            sig = "a" + sig[1:]
        tampered = ".".join([parts[0], parts[1], sig])
        with pytest.raises(JWTError):
            jwt_service.verify_token(tampered)

    def test_verify_expired_token_raises_jwt_error(self, jwt_service, rsa_key_pair):
        """Construct a token with past exp claim and verify it raises JWTError."""
        priv_pem, _ = rsa_key_pair
        now = int(time.time())
        payload = {
            "sub": "USER-expired-user",
            "jti": "expired-jti-12345",
            "iat": now - 3600,
            "exp": now - 1800,  # expired 30 minutes ago
            "type": "access",
        }
        expired_token = jwt.encode(payload, priv_pem, algorithm="RS256")
        with pytest.raises(JWTError):
            jwt_service.verify_token(expired_token)


# ---------------------------------------------------------------------------
# Token revocation
# ---------------------------------------------------------------------------


class TestTokenRevocation:
    @pytest.mark.asyncio
    async def test_revoke_token_adds_jti_to_revoked_local(self, jwt_service):
        token = jwt_service.issue_access_token("USER-revoke-test-001")
        payload = jwt_service.verify_token(token)
        jti = payload["jti"]

        await jwt_service.revoke_token(token)

        assert jti in jwt_service._revoked_local

    @pytest.mark.asyncio
    async def test_verify_revoked_token_raises_jwt_error(self, jwt_service):
        token = jwt_service.issue_access_token("USER-revoke-test-002")
        await jwt_service.revoke_token(token)

        with pytest.raises(JWTError, match="revoked"):
            jwt_service.verify_token(token)

    @pytest.mark.asyncio
    async def test_revoke_does_not_affect_other_tokens(self, jwt_service):
        token_a = jwt_service.issue_access_token("USER-revoke-test-003")
        token_b = jwt_service.issue_access_token("USER-revoke-test-003")

        await jwt_service.revoke_token(token_a)

        # token_b is a different token with a different jti
        payload_b = jwt_service.verify_token(token_b)
        assert payload_b["sub"] == "USER-revoke-test-003"


# ---------------------------------------------------------------------------
# from_env classmethod
# ---------------------------------------------------------------------------


class TestFromEnv:
    def test_from_env_with_no_env_vars_generates_ephemeral_keys(self, monkeypatch):
        monkeypatch.delenv("JWT_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)

        svc = JWTService.from_env()
        assert svc is not None

    def test_from_env_service_can_issue_and_verify_token(self, monkeypatch):
        monkeypatch.delenv("JWT_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)

        svc = JWTService.from_env()
        token = svc.issue_access_token("USER-from-env-001")
        payload = svc.verify_token(token)
        assert payload["sub"] == "USER-from-env-001"
        assert payload["type"] == "access"

    def test_from_env_with_provided_keys_uses_them(self, monkeypatch, rsa_key_pair):
        priv, pub = rsa_key_pair
        monkeypatch.setenv("JWT_PRIVATE_KEY", priv)
        monkeypatch.setenv("JWT_PUBLIC_KEY", pub)
        monkeypatch.delenv("REDIS_URL", raising=False)

        svc = JWTService.from_env()
        token = svc.issue_access_token("USER-env-key-test")
        payload = svc.verify_token(token)
        assert payload["sub"] == "USER-env-key-test"
