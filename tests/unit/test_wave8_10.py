# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for Waves 8, 8.5, and 10 features.

Covers:
- UserDirectory org-scoped search (FR-DIR-001..002)
- OrgTaskIndex upsert + list_for_assignee ACL (FR-XT-001..002)
- MembershipCascade on_member_added / on_member_removed (FR-AK-001)
- DetachmentCascade detach (FR-AD-001)
- DistillationOutbox enqueue idempotency + pending list (FR-RES-001)
- DistillationWorker run_once (FR-RES-001)
- StorageLock acquire/release context manager (FR-RES-003)
- ReplyLineageTracker record + find_task_id (FR-RES-002)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# FR-DIR-001..002: UserDirectory
# ---------------------------------------------------------------------------


class TestUserDirectory:
    """UserDirectory search ACL enforcement."""

    @pytest.mark.asyncio
    async def test_empty_org_ids_returns_empty(self):
        """search() with empty caller_org_ids returns [] without DB query (NFR-004)."""
        from graphclaw.identity.directory import UserDirectory

        pool = MagicMock()
        pool.fetch = AsyncMock()
        directory = UserDirectory(pool)
        result = await directory.search("Alice", [])
        assert result == []
        pool.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_by_user_id_empty_org_ids_returns_empty(self):
        """get_by_user_id() with empty org_ids returns []."""
        from graphclaw.identity.directory import UserDirectory

        pool = MagicMock()
        pool.fetch = AsyncMock()
        directory = UserDirectory(pool)
        result = await directory.get_by_user_id("user-1", [])
        assert result == []
        pool.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_passes_org_ids_to_query(self):
        """search() passes org_ids in SQL query (scoped ACL)."""
        from graphclaw.identity.directory import UserDirectory

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        directory = UserDirectory(pool)
        await directory.search("Alice", ["org-1", "org-2"])
        assert pool.fetch.called
        sql, *args = pool.fetch.call_args[0]
        assert "org_id IN" in sql
        # org_ids should appear in args
        assert "org-1" in args
        assert "org-2" in args

    @pytest.mark.asyncio
    async def test_upsert_executes_insert_on_conflict(self):
        """upsert() calls pool.execute with INSERT ... ON CONFLICT."""
        from graphclaw.identity.directory import DirectoryEntry, UserDirectory

        pool = MagicMock()
        pool.execute = AsyncMock()
        directory = UserDirectory(pool)
        entry = DirectoryEntry(
            user_id="u1",
            org_id="org-1",
            display_name="Alice",
            emails=["alice@ex.com"],
            identities={},
            discoverable_aliases=["alice"],
            visibility_policy="org_default",
        )
        await directory.upsert(entry)
        assert pool.execute.called
        sql = pool.execute.call_args[0][0]
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_remove_calls_delete(self):
        """remove() calls DELETE query."""
        from graphclaw.identity.directory import UserDirectory

        pool = MagicMock()
        pool.execute = AsyncMock()
        directory = UserDirectory(pool)
        await directory.remove("u1", "org-1")
        assert pool.execute.called
        sql = pool.execute.call_args[0][0]
        assert "DELETE FROM user_directory" in sql

    @pytest.mark.asyncio
    async def test_none_pool_returns_empty_gracefully(self):
        """None pool returns [] without error."""
        from graphclaw.identity.directory import UserDirectory

        directory = UserDirectory(None)
        result = await directory.search("Alice", ["org-1"])
        assert result == []


# ---------------------------------------------------------------------------
# FR-XT-001..002: OrgTaskIndex
# ---------------------------------------------------------------------------


class TestOrgTaskIndex:
    """OrgTaskIndex upsert and assignee query ACL."""

    def _make_pool(self, rows=None):
        pool = MagicMock()
        pool.execute = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows or [])
        return pool

    @pytest.mark.asyncio
    async def test_upsert_calls_insert_on_conflict(self):
        """upsert() issues INSERT...ON CONFLICT DO UPDATE."""
        from graphclaw.cross_tenant.task_index import OrgTaskIndex, OrgTaskIndexEntry

        pool = self._make_pool()
        idx = OrgTaskIndex(pool)
        entry = OrgTaskIndexEntry(
            task_id="task-1",
            owner_user_id="user-1",
            org_id="org-1",
            workspace_id=None,
            assignee_linked_user_ids=["user-2"],
            state="IN_PROGRESS",
            deadline=None,
            last_activity_at=None,
            summary_text="Do the thing",
        )
        await idx.upsert(entry)
        assert pool.execute.called
        sql = pool.execute.call_args[0][0]
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_list_for_assignee_empty_org_ids_returns_empty(self):
        """Empty caller_org_ids → [] without DB query (NFR-004)."""
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        pool = self._make_pool()
        idx = OrgTaskIndex(pool)
        result = await idx.list_for_assignee("user-1", [])
        assert result == []
        pool.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_for_assignee_scopes_to_org_ids(self):
        """list_for_assignee() includes org_id IN clause."""
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        pool = self._make_pool([])
        idx = OrgTaskIndex(pool)
        await idx.list_for_assignee("user-1", ["org-1"])
        assert pool.fetch.called
        sql = pool.fetch.call_args[0][0]
        assert "org_id IN" in sql

    @pytest.mark.asyncio
    async def test_none_pool_returns_empty_gracefully(self):
        """None pool returns [] without error."""
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        idx = OrgTaskIndex(None)
        result = await idx.list_for_assignee("user-1", ["org-1"])
        assert result == []

    @pytest.mark.asyncio
    async def test_set_archived_updates_table(self):
        """set_archived() runs UPDATE on org_task_index."""
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        pool = self._make_pool()
        idx = OrgTaskIndex(pool)
        now = datetime.now(timezone.utc)
        await idx.set_archived("task-1", now)
        assert pool.execute.called


# ---------------------------------------------------------------------------
# FR-AK-001: MembershipCascade
# ---------------------------------------------------------------------------


class TestMembershipCascade:
    """MembershipCascade add/remove fan-out."""

    @pytest.mark.asyncio
    async def test_on_member_added_calls_directory_upsert(self):
        """on_member_added() calls directory.upsert with user data."""
        from graphclaw.cascade.membership import MembershipCascade

        store = MagicMock()
        store.get_node = AsyncMock(
            return_value={"id": "user-1", "name": "Alice", "aliases": [], "identities": {}}
        )
        directory = MagicMock()
        directory.upsert = AsyncMock()
        cascade = MembershipCascade(store, directory=directory)
        await cascade.on_member_added("user-1", "org-1")
        assert directory.upsert.called

    @pytest.mark.asyncio
    async def test_on_member_removed_calls_directory_remove(self):
        """on_member_removed() calls directory.remove."""
        from graphclaw.cascade.membership import MembershipCascade

        store = MagicMock()
        store.list_nodes = AsyncMock(return_value=[])
        directory = MagicMock()
        directory.remove = AsyncMock()
        cascade = MembershipCascade(store, directory=directory)
        await cascade.on_member_removed("user-1", "org-1")
        directory.remove.assert_called_once_with("user-1", "org-1")

    @pytest.mark.asyncio
    async def test_on_member_removed_detaches_shadows(self):
        """on_member_removed() flips link_status on ResourceNode shadows."""
        from graphclaw.cascade.membership import MembershipCascade

        store = MagicMock()
        store.list_nodes = AsyncMock(return_value=[{"id": "res-1", "linked_user_id": "user-1"}])
        store.update_node = AsyncMock()
        cascade = MembershipCascade(store, directory=None)
        await cascade.on_member_removed("user-1", "org-1")
        store.update_node.assert_called_once_with("res-1", {"link_status": "detached_org_left"})

    @pytest.mark.asyncio
    async def test_no_directory_does_not_crash(self):
        """Works without directory dependency."""
        from graphclaw.cascade.membership import MembershipCascade

        store = MagicMock()
        store.list_nodes = AsyncMock(return_value=[])
        cascade = MembershipCascade(store, directory=None)
        await cascade.on_member_removed("user-1", "org-1")  # Should not raise


# ---------------------------------------------------------------------------
# FR-AD-001: DetachmentCascade
# ---------------------------------------------------------------------------


class TestDetachmentCascade:
    """DetachmentCascade freeze and archive."""

    @pytest.mark.asyncio
    async def test_detach_updates_link_status(self):
        """detach() calls update_node with link_status=detached_org_left."""
        from graphclaw.cascade.detachment import DetachmentCascade

        store = MagicMock()
        store.update_node = AsyncMock()
        cascade = DetachmentCascade(store)
        await cascade.detach("res-1")
        store.update_node.assert_called_once()
        call_args = store.update_node.call_args[0]
        assert call_args[0] == "res-1"
        assert call_args[1]["link_status"] == "detached_org_left"

    @pytest.mark.asyncio
    async def test_detach_with_storage_archives_snapshot(self):
        """detach() with storage writes a snapshot JSON."""
        from graphclaw.cascade.detachment import DetachmentCascade

        store = MagicMock()
        store.update_node = AsyncMock()
        store.get_node = AsyncMock(return_value={"id": "res-1", "name": "Bob"})

        storage = MagicMock()
        storage.write = AsyncMock()

        cascade = DetachmentCascade(store, storage=storage)
        await cascade.detach("res-1")
        assert storage.write.called
        path_written = storage.write.call_args[0][0]
        assert "res-1" in path_written


# ---------------------------------------------------------------------------
# FR-RES-001: DistillationOutbox + Worker
# ---------------------------------------------------------------------------


class TestDistillationOutbox:
    """DistillationOutbox idempotency."""

    @pytest.mark.asyncio
    async def test_enqueue_stores_in_memory_without_pool(self):
        """Enqueue works without DB pool (in-memory mode)."""
        from graphclaw.distillation.outbox import DistillationOutbox, DistillationWrite

        outbox = DistillationOutbox(pool=None)
        write = DistillationWrite(
            message_id="msg-1",
            target_node_id="node-1",
            target_type="intelligence",
            payload={"line": "test line"},
        )
        result = await outbox.enqueue(write)
        assert result is True
        pending = await outbox.list_pending()
        assert len(pending) == 1
        assert pending[0].message_id == "msg-1"

    @pytest.mark.asyncio
    async def test_mark_processed_removes_from_pending(self):
        """mark_processed clears the entry from pending."""
        from graphclaw.distillation.outbox import DistillationOutbox, DistillationWrite

        outbox = DistillationOutbox(pool=None)
        write = DistillationWrite(
            message_id="msg-2",
            target_node_id="node-1",
            target_type="intelligence",
            payload={},
        )
        await outbox.enqueue(write)
        await outbox.mark_processed(write.id)
        pending = await outbox.list_pending()
        assert all(p.id != write.id for p in pending)

    @pytest.mark.asyncio
    async def test_mark_failed_increments_retry_count(self):
        """mark_failed increments retry_count."""
        from graphclaw.distillation.outbox import DistillationOutbox, DistillationWrite

        outbox = DistillationOutbox(pool=None)
        write = DistillationWrite(
            message_id="msg-3",
            target_node_id="node-1",
            target_type="intelligence",
            payload={},
        )
        await outbox.enqueue(write)
        await outbox.mark_failed(write.id, "some error")
        pending = await outbox.list_pending()
        entry = next(p for p in pending if p.id == write.id)
        assert entry.retry_count == 1
        assert entry.error_detail == "some error"

    @pytest.mark.asyncio
    async def test_enqueue_with_db_pool_deduplication(self):
        """Enqueue with DB pool calls INSERT...ON CONFLICT DO NOTHING."""
        from graphclaw.distillation.outbox import DistillationOutbox, DistillationWrite

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])  # simulate RETURNING nothing (dup)
        outbox = DistillationOutbox(pool=pool)
        write = DistillationWrite(
            message_id="msg-dup",
            target_node_id="node-1",
            target_type="intelligence",
            payload={},
        )
        result = await outbox.enqueue(write)
        assert pool.fetch.called
        sql = pool.fetch.call_args[0][0]
        assert "ON CONFLICT" in sql


class TestDistillationWorker:
    """DistillationWorker processes pending entries."""

    @pytest.mark.asyncio
    async def test_run_once_processes_intelligence_write(self):
        """run_once applies intelligence writes to target nodes."""
        from graphclaw.distillation.outbox import DistillationOutbox, DistillationWrite
        from graphclaw.workers.distillation_worker import DistillationWorker

        outbox = DistillationOutbox(pool=None)
        write = DistillationWrite(
            message_id="msg-4",
            target_node_id="node-A",
            target_type="intelligence",
            payload={"line": "[2026] new intel"},
        )
        await outbox.enqueue(write)

        store = MagicMock()
        store.get_node = AsyncMock(return_value={"id": "node-A", "intelligence": "old intel"})
        store.update_node = AsyncMock()

        worker = DistillationWorker(outbox, store)
        count = await worker.run_once()
        assert count == 1
        store.update_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_once_skips_max_retried_entries(self):
        """Entries at MAX_RETRIES are skipped without processing."""
        from graphclaw.distillation.outbox import DistillationOutbox, DistillationWrite
        from graphclaw.workers.distillation_worker import MAX_RETRIES, DistillationWorker

        outbox = DistillationOutbox(pool=None)
        write = DistillationWrite(
            message_id="msg-exhausted",
            target_node_id="node-B",
            target_type="intelligence",
            payload={"line": "line"},
            retry_count=MAX_RETRIES,
        )
        await outbox.enqueue(write)

        store = MagicMock()
        worker = DistillationWorker(outbox, store)
        count = await worker.run_once()
        assert count == 0
        store.update_node = AsyncMock()
        store.update_node.assert_not_called()


# ---------------------------------------------------------------------------
# FR-RES-003: StorageLock
# ---------------------------------------------------------------------------


class TestStorageLock:
    """StorageLock advisory locking."""

    @pytest.mark.asyncio
    async def test_acquire_release_no_contention(self):
        """acquire/release succeeds when lock file does not exist."""
        from graphclaw.infra.storage_locks import StorageLock

        storage = MagicMock()
        storage.exists = AsyncMock(return_value=False)
        storage.write = AsyncMock()
        storage.delete = AsyncMock()

        lock = StorageLock(storage, owner_id="worker-1")
        await lock.acquire("my/path.md")
        assert storage.write.called
        await lock.release("my/path.md")
        assert storage.delete.called

    @pytest.mark.asyncio
    async def test_context_manager_acquires_and_releases(self):
        """async with lock.lock(...) acquires and releases."""
        from graphclaw.infra.storage_locks import StorageLock

        storage = MagicMock()
        storage.exists = AsyncMock(return_value=False)
        storage.write = AsyncMock()
        storage.delete = AsyncMock()

        lock = StorageLock(storage, owner_id="w1")
        async with lock.lock("path/file.md"):
            assert storage.write.called

        assert storage.delete.called

    @pytest.mark.asyncio
    async def test_stale_lock_is_broken(self):
        """Lock older than TTL is broken and re-acquired."""
        import json  # noqa: PLC0415

        from graphclaw.infra.storage_locks import StorageLock

        stale_payload = json.dumps(
            {"owner": "old-worker", "acquired_at": "2020-01-01T00:00:00+00:00"}
        ).encode()

        storage = MagicMock()
        storage.exists = AsyncMock(return_value=True)
        storage.read = AsyncMock(return_value=stale_payload)
        storage.delete = AsyncMock()
        storage.write = AsyncMock()

        lock = StorageLock(storage, owner_id="new-worker", ttl_seconds=60)
        await lock.acquire("my/path.md")
        assert storage.delete.called  # Stale lock broken
        assert storage.write.called  # New lock written

    @pytest.mark.asyncio
    async def test_timeout_raises_lock_acquisition_error(self):
        """Raises LockAcquisitionError when lock held and max_wait elapsed."""
        import json  # noqa: PLC0415

        from graphclaw.infra.storage_locks import LockAcquisitionError, StorageLock

        fresh_payload = json.dumps(
            {"owner": "other", "acquired_at": datetime.now(timezone.utc).isoformat()}
        ).encode()

        storage = MagicMock()
        storage.exists = AsyncMock(return_value=True)
        storage.read = AsyncMock(return_value=fresh_payload)
        storage.delete = AsyncMock()
        storage.write = AsyncMock()

        lock = StorageLock(
            storage,
            owner_id="w1",
            ttl_seconds=3600,
            poll_interval=0.01,
            max_wait_seconds=0.02,
        )
        with pytest.raises(LockAcquisitionError):
            await lock.acquire("busy/path.md")


# ---------------------------------------------------------------------------
# FR-RES-002: ReplyLineageTracker
# ---------------------------------------------------------------------------


class TestReplyLineageTracker:
    """ReplyLineageTracker record and lookup."""

    @pytest.mark.asyncio
    async def test_record_and_find_task_id(self):
        """find_task_id returns task_id after record()."""
        from graphclaw.inbound.reply_lineage import ReplyLineageTracker

        pool = MagicMock()
        pool.execute = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"task_id": "task-X"}])

        tracker = ReplyLineageTracker(pool)
        await tracker.record("msg-reply-1", "task-X", "inbound", channel="email")
        task_id = await tracker.find_task_id("msg-reply-1")
        assert task_id == "task-X"

    @pytest.mark.asyncio
    async def test_find_task_id_not_found_returns_none(self):
        """find_task_id returns None when row not found."""
        from graphclaw.inbound.reply_lineage import ReplyLineageTracker

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        tracker = ReplyLineageTracker(pool)
        result = await tracker.find_task_id("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_none_pool_returns_none_gracefully(self):
        """None pool returns None without error."""
        from graphclaw.inbound.reply_lineage import ReplyLineageTracker

        tracker = ReplyLineageTracker(None)
        result = await tracker.find_task_id("any")
        assert result is None

    @pytest.mark.asyncio
    async def test_record_with_none_pool_does_not_crash(self):
        """record() with None pool is a no-op."""
        from graphclaw.inbound.reply_lineage import ReplyLineageTracker

        tracker = ReplyLineageTracker(None)
        await tracker.record("m1", "t1", "inbound")  # Should not raise

    @pytest.mark.asyncio
    async def test_get_thread_returns_ordered_records(self):
        """get_thread returns LineageRecord list."""
        from graphclaw.inbound.reply_lineage import ReplyLineageTracker

        now = datetime.now(timezone.utc)
        pool = MagicMock()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "key": "m1",
                    "task_id": "t1",
                    "direction": "inbound",
                    "parent_key": None,
                    "channel": "email",
                    "created_at": now,
                },
            ]
        )
        tracker = ReplyLineageTracker(pool)
        thread = await tracker.get_thread("t1")
        assert len(thread) == 1
        assert thread[0].message_id == "m1"
