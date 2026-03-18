"""graphclaw.infra — Shared infrastructure abstractions for GraphClaw.

Description
-----------
Aggregates the core infrastructure abstractions used throughout the GraphClaw
system: storage, secrets management, message brokering, async logging, and
infrastructure-level configuration models. All concrete implementations follow
the Dependency Injection pattern to allow easy swapping in tests and across
deployment environments.

Design Patterns
---------------
- Facade: This package exposes a curated public API from all sub-modules,
  allowing callers to import from ``graphclaw.infra`` rather than individual
  sub-modules.
- Abstract Base Classes: Each infrastructure concern (storage, secrets, broker)
  is defined as an ABC to enforce a consistent interface regardless of backend.

Public API
----------
- StorageClient: Abstract base class for object storage.
- S3StorageClient: Concrete implementation backed by S3/MinIO.
- SecretsClient: Abstract base class for secrets management.
- EnvFileSecretsClient: Concrete implementation backed by os.environ / dotenv.
- MessageBroker: Abstract base class for message queue operations.
- RedisMessageBroker: Concrete implementation backed by Redis.
- INBOUND_MESSAGES, TRIGGER_EVENTS, SKILL_JOBS, STATUS_UPDATES, OUTBOUND_MESSAGES: Queue name constants.
- AsyncLogger: Non-blocking structured JSON logger with async flush loop.
- generate_session_id: Generate a SES-{uuid4} session identifier.
- StorageConfig, BrokerConfig, LoggingConfig: Pydantic configuration models.

Dependencies
------------
- graphclaw.infra.storage: StorageClient, S3StorageClient.
- graphclaw.infra.secrets: SecretsClient, EnvFileSecretsClient.
- graphclaw.infra.broker: MessageBroker, RedisMessageBroker, queue name constants.
- graphclaw.infra.logger: AsyncLogger, generate_session_id.
- graphclaw.infra.config: StorageConfig, BrokerConfig, LoggingConfig.

Notes
-----
Import order within this file is intentional: config first (no deps), then
logger, secrets, storage, broker so circular imports are avoided.
"""
from __future__ import annotations

from graphclaw.infra.broker import (
    INBOUND_MESSAGES,
    MessageBroker,
    OUTBOUND_MESSAGES,
    RedisMessageBroker,
    SKILL_JOBS,
    STATUS_UPDATES,
    TRIGGER_EVENTS,
)
from graphclaw.infra.config import BrokerConfig, LoggingConfig, StorageConfig
from graphclaw.infra.logger import AsyncLogger, generate_session_id
from graphclaw.infra.secrets import EnvFileSecretsClient, SecretsClient
from graphclaw.infra.storage import S3StorageClient, StorageClient

__all__ = [
    # Storage
    "StorageClient",
    "S3StorageClient",
    # Secrets
    "SecretsClient",
    "EnvFileSecretsClient",
    # Broker
    "MessageBroker",
    "RedisMessageBroker",
    "INBOUND_MESSAGES",
    "TRIGGER_EVENTS",
    "SKILL_JOBS",
    "STATUS_UPDATES",
    "OUTBOUND_MESSAGES",
    # Logger
    "AsyncLogger",
    "generate_session_id",
    # Config
    "StorageConfig",
    "BrokerConfig",
    "LoggingConfig",
]
