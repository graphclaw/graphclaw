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
- Factory: Callers construct these models from environment variables (via
  Pydantic's model_validate or direct construction) and inject them into
  the concrete infrastructure implementations.

Public API
----------
- StorageConfig: Object storage backend settings (S3/MinIO).
- BrokerConfig: Message broker settings (Redis/SQS).
- LoggingConfig: Async logger settings (service name, buffer, level).

Dependencies
------------
- pydantic: BaseModel for validated settings models.

Notes
-----
These models intentionally carry defaults that match the local Docker Compose
environment (MinIO on port 9000, Redis on port 6379).  Production deployments
override them via environment-specific configuration injection.
"""
from __future__ import annotations

from pydantic import BaseModel


class StorageConfig(BaseModel):
    """Configuration for the object storage backend.

    Attributes:
        backend: Storage backend identifier (``"s3"`` or ``"local"``).
        bucket: Target S3 / MinIO bucket name.
        endpoint_url: Override endpoint for MinIO or S3-compatible servers.
            Set to ``None`` to use the default AWS endpoint.
        region: AWS region (or MinIO region) to use.
    """

    backend: str = "s3"
    bucket: str = "graphclaw"
    endpoint_url: str | None = None
    region: str = "us-east-1"


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
