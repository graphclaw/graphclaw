"""graphclaw.infra.config — Infrastructure configuration Pydantic models.

Description
-----------
Defines Pydantic ``BaseModel`` classes for configuring each infrastructure
subsystem: object storage, message broker, and async logging.  These models
serve as structured, validated containers for settings that are typically
loaded from environment variables or a YAML config file.

Design Patterns
---------------
- Value Object: Each config class is a simple, immutable data container with
  sensible defaults for local development.
- Factory: ``StorageConfig.create_client()`` is the single authoritative way
  to instantiate a ``StorageClient``.  It transparently handles both MinIO
  (local dev, ``endpoint_url`` set) and AWS S3 (production, ``endpoint_url``
  is None).  Callers never need to know which backend is in use.

Public API
----------
- StorageConfig: Object storage backend settings (S3/MinIO) + client factory.
- BrokerConfig: Message broker settings (Redis/SQS).
- LoggingConfig: Async logger settings (service name, buffer, level).

Dependencies
------------
- pydantic: BaseModel for validated settings models.

Storage backends
----------------
MinIO (local dev):
    endpoint_url is set to the MinIO API address (e.g. ``http://minio:9000``).
    boto3 routes all S3 API calls to MinIO via that endpoint.
    MinIO is fully S3-compatible so no code changes are needed between envs.

AWS S3 (production):
    endpoint_url is None so boto3 uses the default AWS regional endpoint.
    IAM role credentials are used (no explicit key/secret needed on EC2/ECS).
"""

from __future__ import annotations

import os

from pydantic import BaseModel


class StorageConfig(BaseModel):
    """Configuration for the object storage backend.

    Attributes:
        backend: Storage backend identifier (always ``"s3"`` — covers both
            MinIO and AWS S3 since MinIO is S3-compatible).
        bucket: Target S3 / MinIO bucket name.
        endpoint_url: Override endpoint for MinIO or S3-compatible servers.
            Set to ``None`` for AWS S3 production.
            Set to ``"http://minio:9000"`` (or equivalent) for local MinIO.
        region: AWS region (or MinIO region) to use.

    Factory
    -------
    Use ``create_client()`` to get a ready-to-use ``StorageClient`` instance
    rather than constructing ``S3StorageClient`` directly.
    """

    backend: str = "s3"
    bucket: str = "graphclaw"
    endpoint_url: str | None = None
    region: str = "us-east-1"

    @classmethod
    def from_env(cls) -> StorageConfig:
        """Build a ``StorageConfig`` from standard environment variables.

        Environment variables
        ---------------------
        STORAGE_BUCKET        — bucket name (default: ``"graphclaw"``)
        STORAGE_ENDPOINT_URL  — MinIO endpoint, e.g. ``http://minio:9000``
                                Leave unset for AWS S3.
        STORAGE_REGION        — AWS / MinIO region (default: ``"us-east-1"``)
        """
        return cls(
            bucket=os.environ.get("STORAGE_BUCKET", "graphclaw"),
            endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL"),
            region=os.environ.get("STORAGE_REGION", "us-east-1"),
        )

    def create_client(self):
        """Return an ``S3StorageClient`` configured for this backend.

        When ``endpoint_url`` is set the client points at MinIO (local dev).
        When ``endpoint_url`` is None the client points at AWS S3 (production).
        The same ``S3StorageClient`` class and API is used in both cases
        because MinIO is fully S3-compatible.

        Returns
        -------
        S3StorageClient
            Ready-to-use async storage client.
        """
        from graphclaw.infra.storage import S3StorageClient  # avoid circular import

        return S3StorageClient(
            bucket=self.bucket,
            endpoint_url=self.endpoint_url,
            region=self.region,
        )


class BrokerConfig(BaseModel):
    """Configuration for the message broker backend.

    Attributes:
        backend: Broker backend identifier (``"redis"`` or ``"sqs"``).
        redis_url: Redis connection URL used when backend is ``"redis"``.
    """

    backend: str = "redis"
    redis_url: str = "redis://localhost:6379"


class LoggingConfig(BaseModel):
    """Configuration for the async structured logger.

    Attributes:
        service_name: Identifying label embedded in every log entry.
        buffer_size: Maximum number of log entries to queue before dropping.
        level: Minimum log level to record (e.g. ``"INFO"``, ``"DEBUG"``).
    """

    service_name: str = "graphclaw"
    buffer_size: int = 10_000
    level: str = "INFO"
