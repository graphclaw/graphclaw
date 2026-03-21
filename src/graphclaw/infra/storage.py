"""graphclaw.infra.storage — StorageClient ABC and S3StorageClient implementation.

Description
-----------
Provides the abstract ``StorageClient`` interface that all object-storage
backends must implement, plus a concrete ``S3StorageClient`` that wraps
boto3 and supports both AWS S3 and MinIO (via ``endpoint_url`` override).
All blocking boto3 calls are wrapped in ``asyncio.to_thread`` so the event
loop is never blocked.

Design Patterns
---------------
- Abstract Base Class: ``StorageClient`` defines the minimal contract so
  production (S3) and test (in-memory stub) backends are interchangeable.
- Adapter: ``S3StorageClient`` adapts the synchronous boto3 API to async.
- Template Method: ``_get_client`` is an internal helper that creates the
  boto3 client on demand, keeping the constructor lightweight.

Public API
----------
- StorageClient: ABC with read, write, delete, list_objects, exists.
- S3StorageClient: boto3/MinIO-backed implementation.

Dependencies
------------
- abc: ABC, abstractmethod.
- asyncio: to_thread for sync-to-async bridge.
- boto3: AWS SDK (also used against MinIO).
- botocore.exceptions: ClientError for 404 detection in exists().

Notes
-----
S3 file layout convention: ``{bucket}/{agents,workspaces,skills}/{user_id}/...``
This module does not enforce the layout — callers are responsible for
building correct path strings.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class StorageClient(ABC):
    """Abstract interface for object storage backends."""

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """Read the object at *path* and return its raw bytes.

        Args:
            path: Object key / path within the bucket.

        Returns:
            Raw bytes of the stored object.

        Raises:
            FileNotFoundError: If the object does not exist.
        """

    @abstractmethod
    async def write(
        self,
        path: str,
        data: bytes,
        content_type: str = "text/plain",
    ) -> None:
        """Write *data* to *path*.

        Args:
            path: Object key / path within the bucket.
            data: Raw bytes to store.
            content_type: MIME type stored as object metadata.
        """

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete the object at *path*.

        Args:
            path: Object key / path within the bucket.
        """

    @abstractmethod
    async def list_objects(self, prefix: str) -> list[str]:
        """List all object keys that start with *prefix*.

        Args:
            prefix: Key prefix to filter by (e.g. ``"agents/USER-abc/"``).

        Returns:
            Sorted list of matching object keys.
        """

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Return True if *path* exists in the bucket.

        Args:
            path: Object key / path within the bucket.
        """


class S3StorageClient(StorageClient):
    """boto3-backed StorageClient supporting AWS S3 and MinIO.

    Args:
        bucket: Name of the S3 bucket to operate on.
        endpoint_url: Override for MinIO or other S3-compatible servers.
            Leave as None to use the default AWS endpoint.
        region: AWS region name (default ``"us-east-1"``).
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._client: object | None = None  # lazy init

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> object:
        """Return (or lazily create) the boto3 S3 client."""
        if self._client is None:
            import boto3  # local import to keep module importable without boto3

            kwargs: dict = {"region_name": self._region}
            if self._endpoint_url is not None:
                kwargs["endpoint_url"] = self._endpoint_url
            self._client = boto3.client("s3", **kwargs)
        return self._client

    # ------------------------------------------------------------------
    # StorageClient interface
    # ------------------------------------------------------------------

    async def read(self, path: str) -> bytes:
        """Read object at *path* from S3/MinIO."""

        def _read() -> bytes:
            client = self._get_client()
            response = client.get_object(Bucket=self._bucket, Key=path)
            return response["Body"].read()

        return await asyncio.to_thread(_read)

    async def write(
        self,
        path: str,
        data: bytes,
        content_type: str = "text/plain",
    ) -> None:
        """Write *data* to *path* in S3/MinIO."""

        def _write() -> None:
            client = self._get_client()
            client.put_object(
                Bucket=self._bucket,
                Key=path,
                Body=data,
                ContentType=content_type,
            )

        await asyncio.to_thread(_write)

    async def delete(self, path: str) -> None:
        """Delete object at *path* from S3/MinIO."""

        def _delete() -> None:
            client = self._get_client()
            client.delete_object(Bucket=self._bucket, Key=path)

        await asyncio.to_thread(_delete)

    async def list_objects(self, prefix: str) -> list[str]:
        """List all keys with *prefix* in S3/MinIO."""

        def _list() -> list[str]:
            client = self._get_client()
            paginator = client.get_paginator("list_objects_v2")
            keys: list[str] = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return sorted(keys)

        return await asyncio.to_thread(_list)

    async def exists(self, path: str) -> bool:
        """Return True if *path* exists in S3/MinIO."""

        def _exists() -> bool:
            from botocore.exceptions import ClientError

            client = self._get_client()
            try:
                client.head_object(Bucket=self._bucket, Key=path)
                return True
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                    return False
                raise

        return await asyncio.to_thread(_exists)
