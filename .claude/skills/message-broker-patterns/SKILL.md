---
name: message-broker-patterns
description: >
  Message broker abstraction for GraphClaw — MessageBroker interface with Redis/BullMQ
  (local dev) and SQS (production) implementations. Use when implementing async job
  queuing, event publishing, skill job dispatch, or status event consumption.
  Triggers on: "message broker", "Redis", "BullMQ", "SQS", "publish", "consume",
  "job queue", "event bus".
---

# Message Broker Patterns

## MessageBroker Interface

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator

class MessageBroker(ABC):
    @abstractmethod
    async def publish(self, queue: str, message: str) -> None:
        """Publish a message to a named queue."""
        ...

    @abstractmethod
    async def consume(self, queue: str) -> AsyncIterator[str]:
        """Yield messages from a named queue (blocking async iterator)."""
        ...

    @abstractmethod
    async def acknowledge(self, queue: str, message_id: str) -> None:
        """Acknowledge successful processing of a message."""
        ...

    @abstractmethod
    async def close(self) -> None: ...
```

## Queue Names

| Queue | Publisher | Consumer | Payload |
|-------|-----------|----------|---------|
| `inbound_messages` | Channel Gateway | Trigger Engine | InboundMessage JSON |
| `trigger_events` | Trigger Engine | Agent Runtime | TriggerEvent JSON |
| `skill_jobs` | Agent Runtime | Skill Worker Pool | SkillJob JSON |
| `status_updates` | Skill Workers | Agent Runtime | StatusUpdate JSON |
| `outbound_messages` | Agent Runtime | Channel Gateway | OutboundMessage JSON |

## Redis Implementation (Local Dev)

```python
import redis.asyncio as redis

class RedisMessageBroker(MessageBroker):
    def __init__(self, url: str = "redis://localhost:6379"):
        self._redis = redis.from_url(url)

    async def publish(self, queue: str, message: str) -> None:
        await self._redis.lpush(queue, message)

    async def consume(self, queue: str) -> AsyncIterator[str]:
        while True:
            _, message = await self._redis.brpop(queue, timeout=5)
            if message:
                yield message.decode()

    async def acknowledge(self, queue: str, message_id: str) -> None:
        pass  # Redis list-based: consumed = acknowledged

    async def close(self) -> None:
        await self._redis.close()
```

## Configuration
```
MESSAGE_BROKER_BACKEND=redis      # local dev
MESSAGE_BROKER_BACKEND=sqs        # production
REDIS_URL=redis://localhost:6379  # for redis backend
```

## Docker Compose Addition
```yaml
redis:
  image: redis:7-alpine
  ports:
    - "6379:6379"
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 5s
    timeout: 3s
    retries: 5
```
