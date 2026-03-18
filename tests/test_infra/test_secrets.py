"""tests.test_infra.test_secrets — Unit tests for SecretsClient / EnvFileSecretsClient.

Description
-----------
Tests for the ``EnvFileSecretsClient`` implementation.  Each test manipulates
``os.environ`` directly and cleans up after itself using pytest fixtures to
avoid cross-test pollution.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up environment state, exercises the
  client, and asserts the expected outcome.
- Fixture Cleanup: Tests that add or mutate env vars restore the original
  state via ``monkeypatch`` or explicit ``del`` in teardown.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- graphclaw.infra.secrets: EnvFileSecretsClient under test.
"""
from __future__ import annotations

import os

import pytest

from graphclaw.infra.secrets import EnvFileSecretsClient, SecretsClient


# ---------------------------------------------------------------------------
# test_get_secret_from_env
# ---------------------------------------------------------------------------


async def test_get_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET_KEY", "super-secret-value")
    client = EnvFileSecretsClient()

    result = await client.get_secret("MY_SECRET_KEY")

    assert result == "super-secret-value"


# ---------------------------------------------------------------------------
# test_get_secret_missing_raises
# ---------------------------------------------------------------------------


async def test_get_secret_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NONEXISTENT_KEY_XYZ", raising=False)
    client = EnvFileSecretsClient()

    with pytest.raises(KeyError, match="NONEXISTENT_KEY_XYZ"):
        await client.get_secret("NONEXISTENT_KEY_XYZ")


# ---------------------------------------------------------------------------
# test_set_secret_updates_env
# ---------------------------------------------------------------------------


async def test_set_secret_updates_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEW_SECRET_KEY", raising=False)
    client = EnvFileSecretsClient()

    await client.set_secret("NEW_SECRET_KEY", "brand-new-value")

    assert os.environ.get("NEW_SECRET_KEY") == "brand-new-value"
    # Cleanup
    monkeypatch.delenv("NEW_SECRET_KEY", raising=False)


async def test_set_secret_overwrites_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OVERWRITE_KEY", "old-value")
    client = EnvFileSecretsClient()

    await client.set_secret("OVERWRITE_KEY", "new-value")

    assert os.environ.get("OVERWRITE_KEY") == "new-value"


# ---------------------------------------------------------------------------
# test_delete_secret_removes_env
# ---------------------------------------------------------------------------


async def test_delete_secret_removes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DELETABLE_KEY", "to-be-deleted")
    client = EnvFileSecretsClient()

    await client.delete_secret("DELETABLE_KEY")

    assert "DELETABLE_KEY" not in os.environ


async def test_delete_secret_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_DELETE_KEY", raising=False)
    client = EnvFileSecretsClient()

    with pytest.raises(KeyError, match="MISSING_DELETE_KEY"):
        await client.delete_secret("MISSING_DELETE_KEY")


# ---------------------------------------------------------------------------
# test_secrets_client_is_abstract
# ---------------------------------------------------------------------------


def test_secrets_client_is_abstract() -> None:
    with pytest.raises(TypeError):
        SecretsClient()  # type: ignore[abstract]
