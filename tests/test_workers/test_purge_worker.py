# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_workers — Unit tests for purge worker + heartbeat.

Tests cover:
- WorkerHeartbeat.beat() writes to Redis with correct TTL.
- WorkerHeartbeat.last_seen() returns the last record.
- WorkerHeartbeat no-ops when Redis is None.
- PurgeWorker.run_once() returns empty result when lock is held.
- PurgeWorker._is_eligible() logic for all gate conditions.
- PurgeWorker.run_once() purges eligible nodes and writes audit.
- PurgeWorker skips nodes with legal_hold=True.
- PurgeWorker skips nodes with purge_cancelled_at set.
- PurgeWorker skips nodes with purge_after in future.
- PurgeWorker handles delete failure gracefully (failed_count increments).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from graphclaw.workers.heartbeat import HeartbeatRecord, WorkerHeartbeat
from graphclaw.workers.purge_worker import PurgeWorker

# ---------------------------------------------------------------------------
# WorkerHeartbeat tests
# ---------------------------------------------------------------------------


class TestWorkerHeartbeat:
    async def test_beat_writes_to_redis(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock()
        hb = WorkerHeartbeat(redis=redis, interval_seconds=3600)
        record = await hb.beat("purge_worker")
        redis.set.assert_called_once()
        key_arg = redis.set.call_args[0][0]
        assert "purge_worker" in key_arg
        assert isinstance(record, HeartbeatRecord)

    async def test_beat_ttl_is_2_5x_interval(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock()
        hb = WorkerHeartbeat(redis=redis, interval_seconds=3600)
        await hb.beat("test_worker")
        kwargs = redis.set.call_args[1]
        assert kwargs.get("ex") == int(3600 * 2.5)

    async def test_beat_no_ops_when_redis_none(self) -> None:
        hb = WorkerHeartbeat(redis=None)
        record = await hb.beat("purge_worker")
        assert isinstance(record, HeartbeatRecord)

    async def test_last_seen_returns_record(self) -> None:
        record = HeartbeatRecord(
            worker_name="purge_worker",
            beat_at=datetime.now(UTC),
        )
        redis = MagicMock()
        redis.get = AsyncMock(return_value=record.model_dump_json())
        hb = WorkerHeartbeat(redis=redis)
        result = await hb.last_seen("purge_worker")
        assert result is not None
        assert result.worker_name == "purge_worker"

    async def test_last_seen_returns_none_when_key_absent(self) -> None:
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        hb = WorkerHeartbeat(redis=redis)
        result = await hb.last_seen("purge_worker")
        assert result is None

    async def test_last_seen_returns_none_when_redis_none(self) -> None:
        hb = WorkerHeartbeat(redis=None)
        result = await hb.last_seen("purge_worker")
        assert result is None

    async def test_beat_silently_ignores_redis_error(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(side_effect=RuntimeError("Redis down"))
        hb = WorkerHeartbeat(redis=redis)
        record = await hb.beat("purge_worker")  # should not raise
        assert isinstance(record, HeartbeatRecord)


# ---------------------------------------------------------------------------
# PurgeWorker helpers
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str = "TASK-001",
    purge_after: datetime | None = None,
    legal_hold: bool = False,
    purge_cancelled_at: datetime | None = None,
    archived_at: datetime | None = None,
) -> MagicMock:
    node = MagicMock()
    node.id = node_id
    node.purge_after = purge_after or (datetime.now(UTC) - timedelta(hours=1))
    node.legal_hold = legal_hold
    node.purge_cancelled_at = purge_cancelled_at
    node.archived_at = archived_at or (datetime.now(UTC) - timedelta(hours=25))
    return node


def _make_store(candidates=None):
    store = MagicMock()
    store.list_nodes = AsyncMock(return_value=candidates or [])
    store.delete_node = AsyncMock()
    return store


def _make_storage():
    storage = MagicMock()
    storage.read = AsyncMock(side_effect=FileNotFoundError)
    storage.write = AsyncMock()
    return storage


def _make_redis(lock_acquired=True):
    redis = MagicMock()
    redis.set = AsyncMock(return_value=1 if lock_acquired else None)
    redis.delete = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    return redis


# ---------------------------------------------------------------------------
# PurgeWorker._is_eligible tests
# ---------------------------------------------------------------------------


class TestIsEligible:
    def _now(self):
        return datetime.now(UTC)

    def test_eligible_node(self) -> None:
        node = _make_node()
        assert PurgeWorker._is_eligible(node, self._now()) is True

    def test_ineligible_future_purge_after(self) -> None:
        node = _make_node(purge_after=datetime.now(UTC) + timedelta(hours=10))
        assert PurgeWorker._is_eligible(node, self._now()) is False

    def test_ineligible_legal_hold(self) -> None:
        node = _make_node(legal_hold=True)
        assert PurgeWorker._is_eligible(node, self._now()) is False

    def test_ineligible_cancelled(self) -> None:
        node = _make_node(purge_cancelled_at=datetime.now(UTC))
        assert PurgeWorker._is_eligible(node, self._now()) is False

    def test_ineligible_no_purge_after(self) -> None:
        node = _make_node()
        node.purge_after = None
        assert PurgeWorker._is_eligible(node, self._now()) is False


# ---------------------------------------------------------------------------
# PurgeWorker.run_once tests
# ---------------------------------------------------------------------------


class TestPurgeWorkerRunOnce:
    async def test_returns_empty_result_when_lock_held(self) -> None:
        store = _make_store()
        redis = _make_redis(lock_acquired=False)
        worker = PurgeWorker(store=store, storage=_make_storage(), redis=redis)
        result = await worker.run_once()
        assert result.purged_count == 0
        store.list_nodes.assert_not_called()

    async def test_purges_eligible_node(self) -> None:
        node = _make_node()
        store = _make_store(candidates=[node])
        storage = _make_storage()
        worker = PurgeWorker(store=store, storage=storage, redis=None)
        result = await worker.run_once()
        store.delete_node.assert_called_once_with(node.id)
        assert result.purged_count == 1
        assert result.failed_count == 0

    async def test_writes_audit_entry_per_purged_node(self) -> None:
        node = _make_node()
        store = _make_store(candidates=[node])
        storage = _make_storage()
        worker = PurgeWorker(store=store, storage=storage, redis=None)
        await worker.run_once()
        storage.write.assert_called()
        written = storage.write.call_args[0][1].decode()
        assert "purge_executed" in written

    async def test_skips_legal_hold_node(self) -> None:
        node = _make_node(legal_hold=True)
        store = _make_store(candidates=[node])
        worker = PurgeWorker(store=store, storage=_make_storage(), redis=None)
        result = await worker.run_once()
        store.delete_node.assert_not_called()
        assert result.skipped_count == 1

    async def test_skips_cancelled_node(self) -> None:
        node = _make_node(purge_cancelled_at=datetime.now(UTC))
        store = _make_store(candidates=[node])
        worker = PurgeWorker(store=store, storage=_make_storage(), redis=None)
        result = await worker.run_once()
        store.delete_node.assert_not_called()
        assert result.skipped_count == 1

    async def test_records_failure_on_delete_error(self) -> None:
        node = _make_node()
        store = _make_store(candidates=[node])
        store.delete_node = AsyncMock(side_effect=RuntimeError("DB error"))
        worker = PurgeWorker(store=store, storage=_make_storage(), redis=None)
        result = await worker.run_once()
        assert result.failed_count == 1
        assert len(result.errors) == 1

    async def test_multiple_nodes_purged(self) -> None:
        nodes = [_make_node(f"TASK-{i:03d}") for i in range(5)]
        store = _make_store(candidates=nodes)
        worker = PurgeWorker(store=store, storage=_make_storage(), redis=None)
        result = await worker.run_once()
        assert result.purged_count == 5
        assert result.candidates_found == 5

    async def test_acquires_and_releases_redis_lock(self) -> None:
        node = _make_node()
        store = _make_store(candidates=[node])
        redis = _make_redis(lock_acquired=True)
        worker = PurgeWorker(store=store, storage=_make_storage(), redis=redis)
        await worker.run_once()
        # Lock acquired (set called with nx=True) and released (delete called)
        set_calls = [c for c in redis.set.call_args_list if c[1].get("nx")]
        assert len(set_calls) >= 1
        redis.delete.assert_called()
