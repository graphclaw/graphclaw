---
name: storage-abstractions
description: >
  Storage, secrets, and logging abstraction patterns for GraphClaw — StorageClient
  (S3/MinIO), SecretsClient (env_file/AWS SM/Vault), AsyncLogger (structured JSON
  with session_id tracing). Use when implementing storage operations, secrets access,
  or structured logging. Triggers on: "StorageClient", "SecretsClient", "AsyncLogger",
  "MinIO", "S3", "secrets", "structured logging", "session_id".
---

# Storage Abstractions

## StorageClient Interface (PRD Section 26)

```python
from abc import ABC, abstractmethod

class StorageClient(ABC):
    @abstractmethod
    async def read(self, path: str) -> bytes: ...
    @abstractmethod
    async def write(self, path: str, data: bytes, content_type: str = "text/plain") -> None: ...
    @abstractmethod
    async def delete(self, path: str) -> None: ...
    @abstractmethod
    async def list_objects(self, prefix: str) -> list[str]: ...
    @abstractmethod
    async def exists(self, path: str) -> bool: ...
```

### S3/MinIO Implementation
```python
class S3StorageClient(StorageClient):
    def __init__(self, bucket: str, endpoint_url: str | None = None):
        # endpoint_url set for MinIO local, None for real S3
        self._client = boto3.client("s3", endpoint_url=endpoint_url)
```

### S3 File System Layout
```
bucket/
├── agents/{user_id}/
│   ├── main.md              # Agent state file
│   ├── context/             # Loaded context per trigger
│   └── sessions/{session_id}/
├── workspaces/{user_id}/
│   ├── tasks/{task_id}/
│   │   ├── task.md
│   │   ├── status.md        # Skill agent status
│   │   └── output.md        # Skill agent output
│   └── goals/{goal_id}/
└── skills/{user_id}/{skill_id}/
    └── SKILL.md
```

## SecretsClient Interface (PRD Section 31.6)

```python
class SecretsClient(ABC):
    @abstractmethod
    async def get_secret(self, key: str) -> str: ...
    @abstractmethod
    async def set_secret(self, key: str, value: str) -> None: ...
    @abstractmethod
    async def delete_secret(self, key: str) -> None: ...

class EnvFileSecretsClient(SecretsClient):
    """Local dev: reads from .env.local file."""
    async def get_secret(self, key: str) -> str:
        return os.environ[key]  # loaded via python-dotenv
```

### Configuration
```
SECRETS_BACKEND=env_file            # local dev
SECRETS_BACKEND=aws_secrets_manager # production
```

## AsyncLogger (PRD Section 32.4)

```python
class AsyncLogger:
    def __init__(self, service_name: str, buffer_size: int = 10_000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self._flush_interval = 1.0   # seconds
        self._flush_batch_size = 100

    def log(self, level: str, event_type: str, session_id: str, **fields):
        entry = {
            "timestamp": utcnow().isoformat(),
            "level": level,
            "service": self._service_name,
            "event_type": event_type,
            "session_id": session_id,
            **fields,
        }
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            pass  # Drop on full — never block application

    async def _flush_loop(self):
        """Background: flush every 1s or 100 entries."""
        while True:
            batch = []
            try:
                while len(batch) < self._flush_batch_size:
                    batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                pass
            if batch:
                await self._write_batch(batch)
            await asyncio.sleep(self._flush_interval)
```

### session_id Convention
```
SES-{uuid4}  — generated once at trigger entry point
Propagated: InboundMessage → Broker → Agent logs → Skill logs → Outbound
```

### Log Groups
```
/graphclaw/channel-gateway     — IMAP poll, SMTP send
/graphclaw/trigger-engine      — trigger fire, dispatch
/graphclaw/agent-runtime/{uid} — scoring, state transitions
/graphclaw/skill-agents/{uid}  — skill execution, heartbeat
/graphclaw/platform/errors     — unhandled exceptions
/graphclaw/platform/audit      — auth, permission checks
```
