# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
- AgentPoolConfig: Sub-agent orchestration pool settings (Phase 5).

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

from pydantic import BaseModel, Field, model_validator


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


class AgentPoolConfig(BaseModel):
    """Configuration for the Phase 5 sub-agent orchestration pool.

    Attributes:
        max_concurrent_agents: Maximum number of SubAgentRunner instances that
            may be active simultaneously.  Jobs beyond this cap remain queued
            in the ``AGENT_JOBS`` broker queue until a slot becomes free.
        subagent_worker_pool_size: Size of the dedicated SkillWorker pool used
            exclusively by sub-agents.  Kept separate from the orchestrator
            pool to prevent resource starvation.
        heartbeat_interval_seconds: How often each SubAgentRunner publishes a
            heartbeat event to ``AGENT_UPDATES``.
        heartbeat_timeout_seconds: Inactivity threshold after which
            AgentHealthMonitor marks the task BLOCKED and triggers escalation.
        subagent_execution_timeout_seconds: Hard timeout for each delegated
            sub-agent run.
        subagent_tool_timeout_seconds: Per-tool timeout for sub-agent calls
            to skills and MCP tools.
        subagent_tool_max_retries: Maximum retry attempts for retry-eligible
            tool calls.
        subagent_retry_backoff_base_ms: Base backoff in milliseconds for
            retry-eligible tool calls.
        subagent_retry_backoff_max_ms: Maximum backoff in milliseconds for
            retry-eligible tool calls.
        subagent_retryable_skills: Explicit allowlist of skill names that are
            safe to retry on transient failures.
        subagent_retryable_mcp_tools: Explicit allowlist of MCP tools that are
            safe to retry on transient failures. Supports either ``tool_name``
            or ``server_id:tool_name`` entries.
    """

    max_concurrent_agents: int = 4
    subagent_worker_pool_size: int = 4
    heartbeat_interval_seconds: int = 60
    heartbeat_timeout_seconds: int = 300
    subagent_execution_timeout_seconds: int = 600
    subagent_tool_timeout_seconds: int = 120
    subagent_tool_max_retries: int = 0
    subagent_retry_backoff_base_ms: int = 200
    subagent_retry_backoff_max_ms: int = 1000
    subagent_retryable_skills: list[str] = Field(default_factory=list)
    subagent_retryable_mcp_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_invariants(self) -> AgentPoolConfig:
        """Validate heartbeat/timeout coherence for stable sub-agent runtime."""
        if self.max_concurrent_agents < 1:
            raise ValueError("GRAPHCLAW_MAX_CONCURRENT_AGENTS must be >= 1")
        if self.subagent_worker_pool_size < 1:
            raise ValueError("GRAPHCLAW_SUBAGENT_WORKER_POOL_SIZE must be >= 1")
        if self.heartbeat_interval_seconds < 10:
            raise ValueError("GRAPHCLAW_AGENT_HEARTBEAT_INTERVAL_SECONDS must be >= 10")
        if self.heartbeat_timeout_seconds < (self.heartbeat_interval_seconds * 2):
            raise ValueError(
                "GRAPHCLAW_AGENT_HEARTBEAT_TIMEOUT_SECONDS must be at least 2x "
                "GRAPHCLAW_AGENT_HEARTBEAT_INTERVAL_SECONDS"
            )
        if self.subagent_tool_timeout_seconds >= self.subagent_execution_timeout_seconds:
            raise ValueError(
                "GRAPHCLAW_SUBAGENT_TOOL_TIMEOUT_SECONDS must be less than "
                "GRAPHCLAW_SUBAGENT_EXECUTION_TIMEOUT_SECONDS"
            )
        if self.subagent_tool_max_retries < 0:
            raise ValueError("GRAPHCLAW_SUBAGENT_TOOL_MAX_RETRIES must be >= 0")
        if self.subagent_retry_backoff_base_ms < 0:
            raise ValueError("GRAPHCLAW_SUBAGENT_RETRY_BACKOFF_BASE_MS must be >= 0")
        if self.subagent_retry_backoff_max_ms < self.subagent_retry_backoff_base_ms:
            raise ValueError(
                "GRAPHCLAW_SUBAGENT_RETRY_BACKOFF_MAX_MS must be >= "
                "GRAPHCLAW_SUBAGENT_RETRY_BACKOFF_BASE_MS"
            )
        return self

    @classmethod
    def from_env(cls) -> AgentPoolConfig:
        """Build an ``AgentPoolConfig`` from environment variables.

        Environment variables
        ---------------------
        GRAPHCLAW_MAX_CONCURRENT_AGENTS      — default 4
        GRAPHCLAW_SUBAGENT_WORKER_POOL_SIZE  — default 4
        GRAPHCLAW_AGENT_HEARTBEAT_INTERVAL_SECONDS — default 60
        GRAPHCLAW_AGENT_HEARTBEAT_TIMEOUT_SECONDS  — default 300
        GRAPHCLAW_SUBAGENT_EXECUTION_TIMEOUT_SECONDS — default 600
        GRAPHCLAW_SUBAGENT_TOOL_TIMEOUT_SECONDS      — default 120
        GRAPHCLAW_SUBAGENT_TOOL_MAX_RETRIES          — default 0
        GRAPHCLAW_SUBAGENT_RETRY_BACKOFF_BASE_MS     — default 200
        GRAPHCLAW_SUBAGENT_RETRY_BACKOFF_MAX_MS      — default 1000
        GRAPHCLAW_SUBAGENT_RETRYABLE_SKILLS          — default "" (comma-separated)
        GRAPHCLAW_SUBAGENT_RETRYABLE_MCP_TOOLS       — default "" (comma-separated)
        """
        retryable_skills_raw = os.environ.get("GRAPHCLAW_SUBAGENT_RETRYABLE_SKILLS", "")
        retryable_mcp_raw = os.environ.get("GRAPHCLAW_SUBAGENT_RETRYABLE_MCP_TOOLS", "")
        return cls(
            max_concurrent_agents=int(os.environ.get("GRAPHCLAW_MAX_CONCURRENT_AGENTS", "4")),
            subagent_worker_pool_size=int(
                os.environ.get("GRAPHCLAW_SUBAGENT_WORKER_POOL_SIZE", "4")
            ),
            heartbeat_interval_seconds=int(
                os.environ.get("GRAPHCLAW_AGENT_HEARTBEAT_INTERVAL_SECONDS", "60")
            ),
            heartbeat_timeout_seconds=int(
                os.environ.get("GRAPHCLAW_AGENT_HEARTBEAT_TIMEOUT_SECONDS", "300")
            ),
            subagent_execution_timeout_seconds=int(
                os.environ.get("GRAPHCLAW_SUBAGENT_EXECUTION_TIMEOUT_SECONDS", "600")
            ),
            subagent_tool_timeout_seconds=int(
                os.environ.get("GRAPHCLAW_SUBAGENT_TOOL_TIMEOUT_SECONDS", "120")
            ),
            subagent_tool_max_retries=int(
                os.environ.get("GRAPHCLAW_SUBAGENT_TOOL_MAX_RETRIES", "0")
            ),
            subagent_retry_backoff_base_ms=int(
                os.environ.get("GRAPHCLAW_SUBAGENT_RETRY_BACKOFF_BASE_MS", "200")
            ),
            subagent_retry_backoff_max_ms=int(
                os.environ.get("GRAPHCLAW_SUBAGENT_RETRY_BACKOFF_MAX_MS", "1000")
            ),
            subagent_retryable_skills=[
                entry.strip() for entry in retryable_skills_raw.split(",") if entry.strip()
            ],
            subagent_retryable_mcp_tools=[
                entry.strip() for entry in retryable_mcp_raw.split(",") if entry.strip()
            ],
        )
