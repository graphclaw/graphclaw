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
- StoragePaths: Static path factory for all multi-tenant storage paths.
- SecretsClient: Abstract base class for secrets management.
- EnvFileSecretsClient: Concrete implementation backed by os.environ / dotenv.
- MessageBroker: Abstract base class for message queue operations.
- RedisMessageBroker: Concrete implementation backed by Redis.
- INBOUND_MESSAGES, TRIGGER_EVENTS, SKILL_JOBS, STATUS_UPDATES, OUTBOUND_MESSAGES: Queue name constants.
- AsyncLogger: Non-blocking structured JSON logger with async flush loop.
- generate_session_id: Generate a SES-{uuid4} session identifier.
- PII-safe log events: AgentToolCallEvent, AgentMessageEvent, etc.
- EmbeddingClient: Async text-to-vector embedding client.
- create_embedding_client: Factory for EmbeddingClient instances.
- StorageConfig, BrokerConfig, LoggingConfig: Pydantic configuration models.

Dependencies
------------
- graphclaw.infra.storage: StorageClient, S3StorageClient, StoragePaths.
- graphclaw.infra.secrets: SecretsClient, EnvFileSecretsClient.
- graphclaw.infra.broker: MessageBroker, RedisMessageBroker, queue name constants.
- graphclaw.infra.logger: AsyncLogger, generate_session_id, PII-safe event classes.
- graphclaw.infra.embeddings: EmbeddingClient, create_embedding_client.
- graphclaw.infra.config: StorageConfig, BrokerConfig, LoggingConfig.

Notes
-----
Import order within this file is intentional: config first (no deps), then
logger, secrets, storage, broker, embeddings so circular imports are avoided.
"""

from __future__ import annotations

from graphclaw.infra.broker import (
    INBOUND_MESSAGES,
    OUTBOUND_MESSAGES,
    SKILL_JOBS,
    STATUS_UPDATES,
    TRIGGER_EVENTS,
    MessageBroker,
    RedisMessageBroker,
)
from graphclaw.infra.config import BrokerConfig, LoggingConfig, StorageConfig
from graphclaw.infra.embeddings import EmbeddingClient, create_embedding_client
from graphclaw.infra.logging.context import generate_session_id
from graphclaw.infra.logging.events import (
    AgentMessageEvent,
    AgentScoringCycleEvent,
    AgentToolCallEvent,
    InboundProcessedEvent,
    IntelligenceUpdateEvent,
    OutboundSentEvent,
)
from graphclaw.infra.secrets import EnvFileSecretsClient, SecretsClient
from graphclaw.infra.storage import S3StorageClient, StorageClient, StoragePaths

__all__ = [
    # Storage
    "StorageClient",
    "S3StorageClient",
    "StoragePaths",
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
    # Logging
    "generate_session_id",
    # PII-safe log events
    "AgentToolCallEvent",
    "AgentMessageEvent",
    "AgentScoringCycleEvent",
    "InboundProcessedEvent",
    "IntelligenceUpdateEvent",
    "OutboundSentEvent",
    # Embeddings
    "EmbeddingClient",
    "create_embedding_client",
    # Config
    "StorageConfig",
    "BrokerConfig",
    "LoggingConfig",
]
