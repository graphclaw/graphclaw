---
agent: ws-i-storage-logging
model: sonnet
phase: 1
workstream: WS-I
parallel_with: [WS-F, WS-G]
depends_on: []
skills:
  - storage-abstractions
  - message-broker-patterns
  - graphclaw-docker-dev
  - graphclaw-test-patterns
---

# WS-I: Storage & Logging Infrastructure Agent

## Role
Implement storage, secrets, message broker, and logging abstractions — the
shared infrastructure layer that all Phase 1 services depend on.

## Responsibilities
- StorageClient ABC + S3StorageClient (boto3, MinIO-compatible)
- SecretsClient ABC + EnvFileSecretsClient (local dev)
- MessageBroker ABC + RedisMessageBroker (local dev)
- AsyncLogger with structured JSON, buffered writes, session_id tracing
- Docker Compose additions (Redis, MinIO services)
- Configuration module updates (broker, storage, secrets, logging settings)

## Deliverables
- `src/graphclaw/infra/__init__.py`
- `src/graphclaw/infra/storage.py` — StorageClient ABC + S3StorageClient
- `src/graphclaw/infra/secrets.py` — SecretsClient ABC + EnvFileSecretsClient
- `src/graphclaw/infra/broker.py` — MessageBroker ABC + RedisMessageBroker
- `src/graphclaw/infra/logger.py` — AsyncLogger with flush loop
- `src/graphclaw/infra/config.py` — Infrastructure configuration models
- `docker/docker-compose.yml` — Updated with Redis + MinIO services
- `docker/.env.example` — Updated with new env vars
- `tests/test_infra/test_storage.py` — S3/MinIO integration tests
- `tests/test_infra/test_broker.py` — Redis pub/sub tests
- `tests/test_infra/test_logger.py` — Async logger buffer tests

## Key Patterns
- ABC with concrete implementations selected by env var (Strategy pattern)
- redis.asyncio for non-blocking Redis operations
- boto3 with endpoint_url override for MinIO
- AsyncLogger: asyncio.Queue + background flush loop, drop on full (never block)
- session_id propagation: SES-{uuid4} generated at trigger entry point

## Constraints
- Zero external dependencies beyond boto3, redis, python-dotenv
- All implementations must be fully async
- Storage paths follow S3 layout: bucket/{agents,workspaces,skills}/{user_id}/...
- Logger must never block the application (fire-and-forget semantics)
