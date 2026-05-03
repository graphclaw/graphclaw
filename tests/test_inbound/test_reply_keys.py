"""tests.test_inbound.test_reply_keys — FR-OUT-004 / FR-RES-002 unit tests.

Tests ReplyKeyStore dual-write behavior:
  AC1: Redis key written with correct TTL pattern.
  AC2: Postgres reply_lineage row written.
  AC3: Redis failure is non-fatal; Postgres write still occurs.
  AC4: DB failure is non-fatal; Redis write still occurs.
  AC5: Read from Redis returns correct record.
  AC6: Read from DB returns correct record.
"""

from __future__ import annotations

import json

import pytest

from graphclaw.inbound.reply_keys import (
    REDIS_REPLY_KEY_TTL_SECONDS,
    ReplyKeyRecord,
    ReplyKeyStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_record(
    task_id: str = "TSK-001",
    counterparty_id: str = "RES-bob",
    user_id: str = "USER-1",
    channel: str = "telegram",
    thread_id: str = "TG-CHAT-123",
    checkin_id: str = "CHK-001",
) -> ReplyKeyRecord:
    return ReplyKeyRecord(
        task_id=task_id,
        counterparty_id=counterparty_id,
        user_id=user_id,
        channel=channel,
        thread_id=thread_id,
        checkin_id=checkin_id,
    )


class FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.set_calls: list[dict] = []
        self.get_calls: list[str] = []
        self.fail_on_set = False

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.fail_on_set:
            raise ConnectionError("Redis unavailable")
        self._data[key] = value
        self.set_calls.append({"key": key, "value": value, "ex": ex})

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self._data.get(key)


class FakeAsyncCursor:
    def __init__(self) -> None:
        self.execute_calls: list[tuple] = []
        self.fetchrow_calls: list[tuple] = []
        self._fetchrow_result: dict | None = None

    async def execute(self, sql: str, *args) -> None:
        self.execute_calls.append((sql, args))

    async def fetchrow(self, sql: str, *args) -> dict | None:
        self.fetchrow_calls.append((sql, args))
        return self._fetchrow_result


class FakePool:
    def __init__(self, cursor: FakeAsyncCursor | None = None, fail: bool = False) -> None:
        self._cursor = cursor or FakeAsyncCursor()
        self._fail = fail

    def acquire(self):
        return self

    async def __aenter__(self) -> FakeAsyncCursor:
        if self._fail:
            raise ConnectionError("DB unavailable")
        return self._cursor

    async def __aexit__(self, *args) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReplyKeyStoreWrite:
    @pytest.mark.asyncio
    async def test_redis_key_written_with_ttl(self) -> None:
        redis = FakeRedis()
        store = ReplyKeyStore(redis=redis)
        record = make_record()
        await store.write(record, msg_id="MSG-001")
        assert len(redis.set_calls) == 1
        call = redis.set_calls[0]
        assert call["key"] == "checkin:telegram:TG-CHAT-123:MSG-001"
        assert call["ex"] == REDIS_REPLY_KEY_TTL_SECONDS
        # Verify JSON content
        payload = json.loads(call["value"])
        assert payload["task_id"] == "TSK-001"
        assert payload["counterparty_id"] == "RES-bob"

    @pytest.mark.asyncio
    async def test_postgres_written(self) -> None:
        cursor = FakeAsyncCursor()
        pool = FakePool(cursor)
        store = ReplyKeyStore(db_pool=pool)
        record = make_record()
        await store.write(record, msg_id="MSG-001")
        assert len(cursor.execute_calls) == 1
        sql = cursor.execute_calls[0][0]
        assert "reply_lineage" in sql

    @pytest.mark.asyncio
    async def test_redis_failure_nonfatal(self) -> None:
        redis = FakeRedis()
        redis.fail_on_set = True
        cursor = FakeAsyncCursor()
        pool = FakePool(cursor)
        store = ReplyKeyStore(redis=redis, db_pool=pool)
        record = make_record()
        await store.write(record, msg_id="MSG-001")  # should not raise
        # Postgres should still be written
        assert len(cursor.execute_calls) == 1

    @pytest.mark.asyncio
    async def test_db_failure_nonfatal(self) -> None:
        redis = FakeRedis()
        pool = FakePool(fail=True)
        store = ReplyKeyStore(redis=redis, db_pool=pool)
        record = make_record()
        await store.write(record, msg_id="MSG-001")  # should not raise
        # Redis should still be written
        assert len(redis.set_calls) == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_checkin_id_when_msg_id_empty(self) -> None:
        redis = FakeRedis()
        store = ReplyKeyStore(redis=redis)
        record = make_record(checkin_id="CHK-FALLBACK")
        await store.write(record, msg_id="")
        # Key should use checkin_id as msg_id
        assert redis.set_calls[0]["key"] == "checkin:telegram:TG-CHAT-123:CHK-FALLBACK"


class TestReplyKeyStoreRead:
    @pytest.mark.asyncio
    async def test_read_from_redis_returns_record(self) -> None:
        redis = FakeRedis()
        record = make_record()
        redis._data["checkin:telegram:TG-CHAT-123:MSG-001"] = record.to_json()
        store = ReplyKeyStore(redis=redis)
        result = await store.read_from_redis("telegram", "TG-CHAT-123", "MSG-001")
        assert result is not None
        assert result.task_id == "TSK-001"
        assert result.counterparty_id == "RES-bob"

    @pytest.mark.asyncio
    async def test_read_from_redis_returns_none_on_miss(self) -> None:
        redis = FakeRedis()
        store = ReplyKeyStore(redis=redis)
        result = await store.read_from_redis("telegram", "UNKNOWN", "MSG-X")
        assert result is None

    @pytest.mark.asyncio
    async def test_read_from_db_returns_record(self) -> None:
        cursor = FakeAsyncCursor()
        cursor._fetchrow_result = {
            "channel": "email",
            "thread_id": "thread-abc",
            "task_id": "TSK-999",
            "counterparty_id": "RES-alice",
            "user_id": "USER-1",
            "checkin_id": "CHK-X",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        pool = FakePool(cursor)
        store = ReplyKeyStore(db_pool=pool)
        result = await store.read_from_db("email", "thread-abc")
        assert result is not None
        assert result.task_id == "TSK-999"
        assert result.counterparty_id == "RES-alice"

    @pytest.mark.asyncio
    async def test_read_from_db_returns_none_when_no_pool(self) -> None:
        store = ReplyKeyStore()
        result = await store.read_from_db("email", "thread-x")
        assert result is None


class TestReplyKeyRecord:
    def test_to_json_roundtrip(self) -> None:
        record = make_record()
        j = record.to_json()
        restored = ReplyKeyRecord.from_json(j)
        assert restored.task_id == record.task_id
        assert restored.counterparty_id == record.counterparty_id
        assert restored.channel == record.channel
        assert restored.thread_id == record.thread_id

    def test_redis_key_pattern(self) -> None:
        key = ReplyKeyStore.redis_key("email", "thread-123", "msg-456")
        assert key == "checkin:email:thread-123:msg-456"
