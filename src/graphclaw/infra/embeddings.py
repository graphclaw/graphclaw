# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.infra.embeddings — EmbeddingClient for text-to-vector conversion.

Description
-----------
Provides ``EmbeddingClient``, a lightweight async wrapper around the OpenAI
embeddings API (via ``openai.AsyncOpenAI``).  Supports both single-text and
batch embedding calls.  The model defaults to ``text-embedding-3-small`` but
can be overridden via constructor arg or ``EMBEDDING_MODEL`` environment variable.

Design Patterns
---------------
- Async Wrapper: All HTTP calls to the embeddings API are async by default.
- Factory Function: ``create_embedding_client`` provides DI-friendly construction
  with sensible env-var fallbacks.
- Context Manager: Implements async context manager protocol for ``async with``
  usage, ensuring proper cleanup of the underlying HTTP client.

Public API
----------
- EmbeddingClient: Async client for embedding single texts or batches.
- create_embedding_client: Factory function for DI-friendly instantiation.

Dependencies
------------
- openai: AsyncOpenAI (optional dependency; install with ``pip install 'graphclaw[openai]'``).
- os: For env var lookups.

Examples
--------
Single text embedding::

    client = create_embedding_client()
    await client.start()
    embedding = await client.embed("Hello, world!")
    await client.close()

Batch embedding with context manager::

    async with create_embedding_client() as client:
        embeddings = await client.embed_batch(["text 1", "text 2", "text 3"])
        # embeddings is list[list[float]], one vector per input text
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import AsyncOpenAI


__all__ = ["EmbeddingClient", "create_embedding_client"]


class EmbeddingClient:
    """Async client for converting text to embedding vectors via OpenAI API.

    Args:
        api_key: OpenAI API key. If None, reads from ``OPENAI_API_KEY`` env var.
        model: Embedding model identifier (default: ``text-embedding-3-small``).
            Can be overridden via ``EMBEDDING_MODEL`` env var.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
    ) -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "EmbeddingClient requires an API key. Pass via constructor "
                "or set OPENAI_API_KEY environment variable."
            )

        # Allow env var override for model selection
        self._model = os.getenv("EMBEDDING_MODEL", model)
        self._client: AsyncOpenAI | None = None

    async def __aenter__(self) -> EmbeddingClient:
        """Context manager entry: initialize the HTTP client."""
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        """Context manager exit: cleanup the HTTP client."""
        await self.close()

    async def start(self) -> None:
        """Initialize the underlying OpenAI async HTTP client.

        Safe to call multiple times; subsequent calls are no-ops if already started.
        """
        if self._client is not None:
            return

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package not installed. Install with: pip install 'graphclaw[openai]'"
            ) from exc

        self._client = AsyncOpenAI(api_key=self._api_key)

    async def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text string.

        Args:
            text: Input text to embed. Empty strings return a zero vector.

        Returns:
            Embedding vector as a list of floats (dimensionality depends on model;
            text-embedding-3-small returns 1536 dimensions).

        Raises:
            RuntimeError: If called before ``start()`` or after ``close()``.
        """
        if self._client is None:
            raise RuntimeError("EmbeddingClient not started. Call await client.start() first.")

        if not text.strip():
            # OpenAI API rejects empty strings; return zero vector
            # text-embedding-3-small is 1536-dim; adjust if using other models
            return [0.0] * 1536

        response = await self._client.embeddings.create(
            input=[text],
            model=self._model,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of text strings.

        Args:
            texts: List of input texts. Empty list returns empty list.

        Returns:
            List of embedding vectors, one per input text, preserving input order.

        Raises:
            RuntimeError: If called before ``start()`` or after ``close()``.
        """
        if self._client is None:
            raise RuntimeError("EmbeddingClient not started. Call await client.start() first.")

        if not texts:
            return []

        # Filter out empty strings but track indices to rebuild result in correct order
        valid_indices: list[int] = []
        valid_texts: list[str] = []
        for i, text in enumerate(texts):
            if text.strip():
                valid_indices.append(i)
                valid_texts.append(text)

        # If all texts were empty, return zero vectors for all
        if not valid_texts:
            return [[0.0] * 1536 for _ in texts]

        response = await self._client.embeddings.create(
            input=valid_texts,
            model=self._model,
        )

        # Build result list with embeddings in correct positions, zero vectors for empties
        result: list[list[float]] = [[0.0] * 1536 for _ in texts]
        for idx, embedding_obj in zip(valid_indices, response.data):
            result[idx] = embedding_obj.embedding

        return result

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources.

        Safe to call multiple times.
        """
        if self._client is not None:
            await self._client.close()
            self._client = None


def create_embedding_client(**kwargs: object) -> EmbeddingClient:
    """Factory function to create an EmbeddingClient instance.

    This is the preferred DI-friendly entry point for constructing an
    embedding client. Accepts the same arguments as ``EmbeddingClient.__init__``.

    Args:
        **kwargs: Forwarded to ``EmbeddingClient.__init__``.

    Returns:
        Initialized ``EmbeddingClient`` instance.

    Examples:
        >>> client = create_embedding_client(api_key="sk-...")
        >>> await client.start()
        >>> vec = await client.embed("Hello")
        >>> await client.close()
    """
    return EmbeddingClient(**kwargs)  # type: ignore[arg-type]
