"""tests.test_infra.test_log_sinks — Unit tests for sink formatting and storage sink behavior."""

from __future__ import annotations

from graphclaw.infra.sinks.formatting import format_pipe_entry
from graphclaw.infra.sinks.object_storage import ObjectStorageSink
from graphclaw.infra.storage import StorageClient


class InMemoryStorage(StorageClient):
    """Minimal async storage double for sink tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def read(self, path: str) -> bytes:
        if path not in self.objects:
            raise FileNotFoundError(path)
        return self.objects[path]

    async def write(self, path: str, data: bytes, content_type: str = "text/plain") -> None:
        self.objects[path] = data

    async def delete(self, path: str) -> None:
        self.objects.pop(path, None)

    async def list_objects(self, prefix: str) -> list[str]:
        return sorted([key for key in self.objects if key.startswith(prefix)])

    async def exists(self, path: str) -> bool:
        return path in self.objects


def test_format_pipe_entry_uses_fixed_columns_and_extra_json() -> None:
    line = format_pipe_entry(
        {
            "timestamp": "2026-04-19T10:05:00Z",
            "level": "INFO",
            "service": "gateway",
            "event_type": "inbound.processed",
            "session_id": "SES-abc",
            "user_id": "USER-001",
            "task_id": "TSK-001",
            "channel": "email",
            "signal": "status_update",
        }
    )

    parts = line.split("|", 7)
    assert len(parts) == 8
    assert parts[0] == "2026-04-19T10:05:00Z"
    assert parts[1] == "INFO"
    assert parts[2] == "gateway"
    assert parts[3] == "inbound.processed"
    assert parts[4] == "SES-abc"
    assert parts[5] == "USER-001"
    assert parts[6] == "TSK-001"
    assert '"channel":"email"' in parts[7]


async def test_object_storage_sink_writes_user_and_system_paths() -> None:
    storage = InMemoryStorage()
    sink = ObjectStorageSink(storage=storage, log_format="pipe", min_level="INFO")

    entries = [
        {
            "timestamp": "2026-04-19T10:05:00Z",
            "level": "INFO",
            "service": "gateway",
            "event_type": "inbound.processed",
            "session_id": "SES-abc",
            "user_id": "USER-001",
            "task_id": "TSK-001",
            "channel": "email",
        },
        {
            "timestamp": "2026-04-19T10:05:01Z",
            "level": "ERROR",
            "service": "platform",
            "event_type": "runtime.error",
            "session_id": "-",
            "task_id": "-",
            "error": "timeout",
        },
        {
            "timestamp": "2026-04-19T10:05:02Z",
            "level": "DEBUG",
            "service": "gateway",
            "event_type": "debug.event",
            "session_id": "SES-abc",
            "user_id": "USER-001",
        },
    ]

    await sink.write_batch(entries)

    assert "USER-001/logs/gateway/2026-04-19/1000Z.log" in storage.objects
    assert "system/logs/platform/2026-04-19/1000Z.log" in storage.objects

    user_log = storage.objects["USER-001/logs/gateway/2026-04-19/1000Z.log"].decode("utf-8")
    assert "inbound.processed" in user_log
    assert "debug.event" not in user_log

    system_log = storage.objects["system/logs/platform/2026-04-19/1000Z.log"].decode("utf-8")
    assert "runtime.error" in system_log
