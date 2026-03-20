"""tests.test_infra.test_byok — Unit tests for BYOKService and supporting types.

Description
-----------
Tests for ``BYOKProvider``, ``BYOKKeyRef``, and ``BYOKService`` as defined in
``graphclaw.infra.byok``.  All ``SecretsClient`` calls are mocked via
``AsyncMock`` so no real secrets backend is needed.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up mocks, calls the service, and asserts
  the expected side-effects or return values.
- Frozen dataclass: ``BYOKKeyRef`` immutability is verified by attempting
  attribute assignment and asserting ``FrozenInstanceError``.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock.
- dataclasses: FrozenInstanceError (Python 3.11+) or AttributeError (3.10).
- graphclaw.infra.byok: BYOKProvider, BYOKKeyRef, BYOKService.
"""
from __future__ import annotations

import dataclasses
import sys

import pytest

# FrozenInstanceError was added in Python 3.11; earlier versions raise AttributeError.
_FROZEN_ERRORS: tuple[type[Exception], ...]
if sys.version_info >= (3, 11):
    _FROZEN_ERRORS = (dataclasses.FrozenInstanceError,)
else:
    _FROZEN_ERRORS = (AttributeError, TypeError)
from unittest.mock import AsyncMock

from graphclaw.infra.byok import BYOKKeyRef, BYOKProvider, BYOKService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(get_secret_side_effect=None, set_secret_return=None):
    secrets = AsyncMock()
    secrets.get_secret = AsyncMock(side_effect=get_secret_side_effect)
    secrets.set_secret = AsyncMock(return_value=set_secret_return)
    secrets.delete_secret = AsyncMock()
    return BYOKService(secrets_client=secrets), secrets


# ---------------------------------------------------------------------------
# BYOKProvider enum
# ---------------------------------------------------------------------------


class TestBYOKProvider:
    def test_has_anthropic_member(self):
        assert BYOKProvider.ANTHROPIC.value == "anthropic"

    def test_has_openai_member(self):
        assert BYOKProvider.OPENAI.value == "openai"

    def test_has_google_member(self):
        assert BYOKProvider.GOOGLE.value == "google"

    def test_has_litellm_member(self):
        assert BYOKProvider.LITELLM.value == "litellm"

    def test_has_exactly_four_members(self):
        assert len(list(BYOKProvider)) == 4


# ---------------------------------------------------------------------------
# BYOKKeyRef
# ---------------------------------------------------------------------------


class TestBYOKKeyRef:
    def test_secret_path_for_anthropic(self):
        ref = BYOKKeyRef(user_id="USER-abc", provider=BYOKProvider.ANTHROPIC)
        assert ref.secret_path == "/workgraph/USER-abc/byok/anthropic"

    def test_secret_path_for_openai(self):
        ref = BYOKKeyRef(user_id="USER-xyz", provider=BYOKProvider.OPENAI)
        assert ref.secret_path == "/workgraph/USER-xyz/byok/openai"

    def test_secret_path_for_google(self):
        ref = BYOKKeyRef(user_id="USER-abc123", provider=BYOKProvider.GOOGLE)
        assert ref.secret_path == "/workgraph/USER-abc123/byok/google"

    def test_secret_path_for_litellm(self):
        ref = BYOKKeyRef(user_id="USER-abc", provider=BYOKProvider.LITELLM)
        assert ref.secret_path == "/workgraph/USER-abc/byok/litellm"

    def test_is_frozen_raises_on_attribute_assignment(self):
        ref = BYOKKeyRef(user_id="USER-abc", provider=BYOKProvider.ANTHROPIC)
        # frozen dataclasses raise FrozenInstanceError (Python 3.11+) or
        # AttributeError (earlier versions) on attribute assignment
        with pytest.raises(_FROZEN_ERRORS):
            ref.user_id = "USER-modified"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BYOKService.register
# ---------------------------------------------------------------------------


class TestBYOKServiceRegister:
    @pytest.mark.asyncio
    async def test_register_calls_set_secret_with_correct_path(self):
        svc, secrets = _make_service()
        ref = await svc.register("USER-abc", BYOKProvider.ANTHROPIC, "sk-ant-123")

        secrets.set_secret.assert_called_once()
        path_arg = secrets.set_secret.call_args[0][0]
        assert path_arg == "/workgraph/USER-abc/byok/anthropic"

    @pytest.mark.asyncio
    async def test_register_calls_set_secret_with_correct_key_value(self):
        svc, secrets = _make_service()
        await svc.register("USER-abc", BYOKProvider.ANTHROPIC, "sk-ant-abc123")

        key_arg = secrets.set_secret.call_args[0][1]
        assert key_arg == "sk-ant-abc123"

    @pytest.mark.asyncio
    async def test_register_returns_byok_key_ref(self):
        svc, secrets = _make_service()
        ref = await svc.register("USER-abc", BYOKProvider.OPENAI, "sk-openai-xyz")

        assert isinstance(ref, BYOKKeyRef)
        assert ref.user_id == "USER-abc"
        assert ref.provider == BYOKProvider.OPENAI


# ---------------------------------------------------------------------------
# BYOKService.get_key
# ---------------------------------------------------------------------------


class TestBYOKServiceGetKey:
    @pytest.mark.asyncio
    async def test_get_key_returns_key_value_when_registered(self):
        svc, secrets = _make_service(get_secret_side_effect=["sk-found-value"])
        result = await svc.get_key("USER-abc", BYOKProvider.ANTHROPIC)
        assert result == "sk-found-value"

    @pytest.mark.asyncio
    async def test_get_key_calls_get_secret_with_correct_path(self):
        svc, secrets = _make_service(get_secret_side_effect=["sk-test"])
        await svc.get_key("USER-abc", BYOKProvider.OPENAI)

        secrets.get_secret.assert_called_once()
        path_arg = secrets.get_secret.call_args[0][0]
        assert path_arg == "/workgraph/USER-abc/byok/openai"

    @pytest.mark.asyncio
    async def test_get_key_returns_none_when_key_error_raised(self):
        svc, secrets = _make_service(get_secret_side_effect=KeyError("not found"))
        result = await svc.get_key("USER-abc", BYOKProvider.ANTHROPIC)
        assert result is None


# ---------------------------------------------------------------------------
# BYOKService.revoke
# ---------------------------------------------------------------------------


class TestBYOKServiceRevoke:
    @pytest.mark.asyncio
    async def test_revoke_calls_delete_secret_with_correct_path(self):
        svc, secrets = _make_service()
        await svc.revoke("USER-abc", BYOKProvider.GOOGLE)

        secrets.delete_secret.assert_called_once()
        path_arg = secrets.delete_secret.call_args[0][0]
        assert path_arg == "/workgraph/USER-abc/byok/google"

    @pytest.mark.asyncio
    async def test_revoke_propagates_key_error_when_secret_not_found(self):
        svc, secrets = _make_service()
        secrets.delete_secret = AsyncMock(side_effect=KeyError("not found"))

        with pytest.raises(KeyError):
            await svc.revoke("USER-abc", BYOKProvider.ANTHROPIC)


# ---------------------------------------------------------------------------
# BYOKService.list_providers
# ---------------------------------------------------------------------------


class TestBYOKServiceListProviders:
    @pytest.mark.asyncio
    async def test_returns_only_registered_providers(self):
        """Only ANTHROPIC and OPENAI have keys; GOOGLE and LITELLM raise KeyError."""
        call_count = [0]

        async def _get_secret(path: str) -> str:
            call_count[0] += 1
            if "anthropic" in path:
                return "sk-ant-key"
            if "openai" in path:
                return "sk-openai-key"
            raise KeyError(path)

        secrets = AsyncMock()
        secrets.get_secret = AsyncMock(side_effect=_get_secret)
        svc = BYOKService(secrets_client=secrets)

        providers = await svc.list_providers("USER-abc")

        assert BYOKProvider.ANTHROPIC in providers
        assert BYOKProvider.OPENAI in providers
        assert BYOKProvider.GOOGLE not in providers
        assert BYOKProvider.LITELLM not in providers

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_keys_registered(self):
        svc, secrets = _make_service(get_secret_side_effect=KeyError("none"))
        providers = await svc.list_providers("USER-abc")
        assert providers == []

    @pytest.mark.asyncio
    async def test_returns_all_providers_when_all_keys_registered(self):
        secrets = AsyncMock()
        secrets.get_secret = AsyncMock(return_value="some-api-key")
        svc = BYOKService(secrets_client=secrets)

        providers = await svc.list_providers("USER-abc")
        assert len(providers) == len(list(BYOKProvider))
