# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_infra.test_embeddings — Unit tests for EmbeddingClient.

Description
-----------
Verifies EmbeddingClient construction, lifecycle (start/stop/context manager),
single and batch embedding calls, empty-string handling, and the
create_embedding_client factory.  All OpenAI API calls are mocked so tests run
without a live API key.

Design Patterns
---------------
- Mock Injection: ``AsyncOpenAI`` is patched at the module level so no real HTTP
  calls are made; the mock response structure mirrors the actual OpenAI SDK shape.
- Arrange/Act/Assert: Each test sets up mocks, exercises one behaviour, and
  asserts the result.

Dependencies
------------
- pytest, pytest-asyncio: Async test runner.
- unittest.mock: AsyncMock, MagicMock, patch.
- graphclaw.infra.embeddings: EmbeddingClient, create_embedding_client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.infra.embeddings import EmbeddingClient, create_embedding_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_openai_response(vectors: list[list[float]]) -> MagicMock:
    """Construct a mock object matching the OpenAI embeddings response shape."""
    response = MagicMock()
    response.data = [MagicMock(embedding=v) for v in vectors]
    return response


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """ValueError raised when no API key is provided and env var is unset."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        EmbeddingClient(api_key=None)


def test_construction_with_explicit_key() -> None:
    """EmbeddingClient can be constructed with an explicit api_key."""
    client = EmbeddingClient(api_key="sk-test-key")
    assert client._api_key == "sk-test-key"


def test_construction_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """EmbeddingClient reads OPENAI_API_KEY from environment when no key passed."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
    client = EmbeddingClient()
    assert client._api_key == "sk-env-key"


def test_construction_env_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """EMBEDDING_MODEL env var overrides default model."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-ada-002")
    client = EmbeddingClient()
    assert client._model == "text-embedding-ada-002"


# ---------------------------------------------------------------------------
# start() / close() lifecycle
# ---------------------------------------------------------------------------


async def test_start_initialises_openai_client() -> None:
    """start() creates the underlying AsyncOpenAI client."""
    client = EmbeddingClient(api_key="sk-test")
    mock_oai = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_oai) as MockOpenAI:
        await client.start()
        MockOpenAI.assert_called_once_with(api_key="sk-test")
    await client.close()


async def test_start_is_idempotent() -> None:
    """Calling start() twice does not create a second client."""
    client = EmbeddingClient(api_key="sk-test")
    mock_oai = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_oai) as MockOpenAI:
        await client.start()
        await client.start()
        MockOpenAI.assert_called_once()
    await client.close()


async def test_close_sets_client_to_none() -> None:
    """close() calls underlying client.close() and sets _client to None."""
    client = EmbeddingClient(api_key="sk-test")
    mock_oai = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_oai):
        await client.start()
        assert client._client is not None
        await client.close()
        assert client._client is None
        mock_oai.close.assert_called_once()


async def test_close_safe_when_not_started() -> None:
    """close() is safe to call before start() — no exception raised."""
    client = EmbeddingClient(api_key="sk-test")
    await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# embed() — single text
# ---------------------------------------------------------------------------


async def test_embed_raises_if_not_started() -> None:
    """embed() raises RuntimeError if called before start()."""
    client = EmbeddingClient(api_key="sk-test")
    with pytest.raises(RuntimeError, match="not started"):
        await client.embed("hello world")


async def test_embed_returns_vector() -> None:
    """embed() returns the embedding vector from the API response."""
    client = EmbeddingClient(api_key="sk-test")
    expected = [0.1, 0.2, 0.3]
    mock_oai = AsyncMock()
    mock_oai.embeddings.create = AsyncMock(return_value=_make_openai_response([expected]))
    with patch("openai.AsyncOpenAI", return_value=mock_oai):
        await client.start()
        result = await client.embed("hello world")

    assert result == expected


async def test_embed_empty_string_returns_zero_vector() -> None:
    """embed() returns a zero vector for empty/whitespace-only text without API call."""
    client = EmbeddingClient(api_key="sk-test")
    mock_oai = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_oai):
        await client.start()
        result = await client.embed("   ")

    assert all(v == 0.0 for v in result)
    assert len(result) == 1536
    mock_oai.embeddings.create.assert_not_called()


# ---------------------------------------------------------------------------
# embed_batch() — multiple texts
# ---------------------------------------------------------------------------


async def test_embed_batch_raises_if_not_started() -> None:
    """embed_batch() raises RuntimeError if called before start()."""
    client = EmbeddingClient(api_key="sk-test")
    with pytest.raises(RuntimeError, match="not started"):
        await client.embed_batch(["hello", "world"])


async def test_embed_batch_empty_list() -> None:
    """embed_batch([]) returns empty list without API call."""
    client = EmbeddingClient(api_key="sk-test")
    mock_oai = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_oai):
        await client.start()
        result = await client.embed_batch([])

    assert result == []
    mock_oai.embeddings.create.assert_not_called()


async def test_embed_batch_returns_vectors_in_order() -> None:
    """embed_batch() preserves input order in the returned vectors."""
    client = EmbeddingClient(api_key="sk-test")
    vec1 = [0.1, 0.2]
    vec2 = [0.3, 0.4]
    mock_oai = AsyncMock()
    mock_oai.embeddings.create = AsyncMock(return_value=_make_openai_response([vec1, vec2]))
    with patch("openai.AsyncOpenAI", return_value=mock_oai):
        await client.start()
        result = await client.embed_batch(["text one", "text two"])

    assert result[0] == vec1
    assert result[1] == vec2


async def test_embed_batch_skips_empty_strings() -> None:
    """embed_batch() sends only non-empty texts to API; empties get zero vectors."""
    client = EmbeddingClient(api_key="sk-test")
    vec_for_valid = [0.5, 0.6]
    mock_oai = AsyncMock()
    mock_oai.embeddings.create = AsyncMock(return_value=_make_openai_response([vec_for_valid]))
    with patch("openai.AsyncOpenAI", return_value=mock_oai):
        await client.start()
        # texts[0] is empty, texts[1] is valid
        result = await client.embed_batch(["", "valid text"])

    assert all(v == 0.0 for v in result[0])  # empty → zero vector
    assert result[1] == vec_for_valid
    # API called with only the valid text
    call_args = mock_oai.embeddings.create.call_args
    assert call_args[1]["input"] == ["valid text"]


async def test_embed_batch_all_empty() -> None:
    """embed_batch() with all empty strings returns all-zero vectors without API call."""
    client = EmbeddingClient(api_key="sk-test")
    mock_oai = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_oai):
        await client.start()
        result = await client.embed_batch(["", "  ", ""])

    assert len(result) == 3
    for vec in result:
        assert all(v == 0.0 for v in vec)
    mock_oai.embeddings.create.assert_not_called()


# ---------------------------------------------------------------------------
# Context manager protocol
# ---------------------------------------------------------------------------


async def test_context_manager_starts_and_closes() -> None:
    """async with EmbeddingClient calls start() and close() automatically."""
    mock_oai = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_oai):
        async with EmbeddingClient(api_key="sk-test") as client:
            assert client._client is not None
    assert client._client is None


# ---------------------------------------------------------------------------
# create_embedding_client factory
# ---------------------------------------------------------------------------


def test_create_embedding_client_returns_instance() -> None:
    """create_embedding_client() returns an EmbeddingClient instance."""
    client = create_embedding_client(api_key="sk-factory-key")
    assert isinstance(client, EmbeddingClient)
    assert client._api_key == "sk-factory-key"
