# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_agent.test_result_collector — Unit tests for ResultCollector.

Test ID: GCLAW-TEST-AG-RC-001

Description
-----------
Verifies the ResultCollector's sub-agent result processing, including:
- Task node updates with correct state transitions
- Reading agent output files from MinIO and including in intelligence
- Writing results to agent memory and decisions log
- Graceful degradation when storage unavailable

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up mocks, calls process_agent_result, 
  and asserts on repository updates and storage writes.
- Fake Storage: Uses FakeStorageClient for deterministic testing.

Dependencies
------------
- pytest, pytest-asyncio: Async test runner.
- unittest.mock: AsyncMock, MagicMock.
- graphclaw.agent.result_collector: ResultCollector.
- graphclaw.agent.sub_agent_runner: AgentUpdateEvent.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from graphclaw.agent.result_collector import ResultCollector
from graphclaw.agent.sub_agent_runner import AgentUpdateEvent, AgentUpdateEventType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeStorageClient:
    """In-memory storage for testing."""
    
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
    
    async def read(self, path: str) -> bytes:
        if path not in self.data:
            raise FileNotFoundError(f"Path not found: {path}")
        return self.data[path]
    
    async def write(self, path: str, content: bytes) -> None:
        self.data[path] = content
    
    async def list_prefix(self, prefix: str) -> list[str]:
        return [k for k in self.data if k.startswith(prefix)]


def _make_collector(
    user_id: str = "usr-001",
    agent_id: str = "main",
    storage: object | None = None,
) -> tuple[ResultCollector, AsyncMock]:
    """Return a ResultCollector with mocked dependencies."""
    mock_repo = AsyncMock()
    mock_repo.update_node = AsyncMock()
    
    collector = ResultCollector(
        repo=mock_repo,
        pool=None,  # type: ignore
        user_id=user_id,
        agent_id=agent_id,
        storage=storage,
    )
    return collector, mock_repo


def _make_agent_event(
    agent_id: str = "external-outreach-agent",
    task_id: str = "TSK-001",
    session_id: str = "ses-usr-001-1234567890",
    status: str = "COMPLETED",
    message: str = "Task completed successfully",
    batch_id: str = "",
) -> AgentUpdateEvent:
    """Create an AgentUpdateEvent for testing."""
    return AgentUpdateEvent(
        event_type=AgentUpdateEventType.COMPLETED,
        agent_id=agent_id,
        task_id=task_id,
        session_id=session_id,
        status=status,
        message=message,
        batch_id=batch_id,
    )


# ---------------------------------------------------------------------------
# process_agent_result — Task node updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_agent_result_updates_task_to_needs_review() -> None:
    """Task state set to NEEDS_REVIEW when agent completes successfully."""
    collector, mock_repo = _make_collector()
    event = _make_agent_event(status="COMPLETED", message="Draft ready")
    
    await collector.process_agent_result(event)
    
    mock_repo.update_node.assert_called_once()
    call_args = mock_repo.update_node.call_args[0]
    updates = mock_repo.update_node.call_args[1]["updates"] if len(call_args) == 1 else call_args[1]
    
    assert updates["state"] == "NEEDS_REVIEW"
    assert "Draft ready" in updates["intelligence"]


@pytest.mark.asyncio
async def test_process_agent_result_updates_task_to_blocked_on_failure() -> None:
    """Task state set to BLOCKED when agent fails."""
    collector, mock_repo = _make_collector()
    event = _make_agent_event(status="FAILED", message="Timeout exceeded")
    
    await collector.process_agent_result(event)
    
    updates = mock_repo.update_node.call_args[0][1]
    assert updates["state"] == "BLOCKED"
    assert "Timeout exceeded" in updates["intelligence"]


@pytest.mark.asyncio
async def test_process_agent_result_includes_message_in_intelligence() -> None:
    """Result summary from event.message included in intelligence field."""
    collector, mock_repo = _make_collector()
    event = _make_agent_event(message="Generated 2 email drafts with 95% confidence")
    
    await collector.process_agent_result(event)
    
    updates = mock_repo.update_node.call_args[0][1]
    assert "Generated 2 email drafts" in updates["intelligence"]


# ---------------------------------------------------------------------------
# process_agent_result — Agent output file reading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_agent_result_reads_email_draft_output() -> None:
    """Agent output file (email-draft.md) is read and included in intelligence."""
    storage = FakeStorageClient()
    draft_content = """# Email Draft: John Doe — Follow-up

## Draft A — Concise
To: john@example.com
Subject: Quick check-in

Hi John, just following up on our conversation...
"""
    storage.data["usr-001/agents/external-outreach-agent/output/email-draft.md"] = draft_content.encode()
    
    collector, mock_repo = _make_collector(storage=storage)
    event = _make_agent_event(
        agent_id="external-outreach-agent",
        session_id="ses-usr-001-1234567890",
        message="Email draft generated",
    )
    
    await collector.process_agent_result(event)
    
    updates = mock_repo.update_node.call_args[0][1]
    intelligence = updates["intelligence"]
    
    # Should include both the event message and the output file content
    assert "Email draft generated" in intelligence
    assert "Email Draft: John Doe" in intelligence
    assert "john@example.com" in intelligence


@pytest.mark.asyncio
async def test_process_agent_result_tries_multiple_output_paths() -> None:
    """Tries email-draft.md, result.md, summary.md in order until one succeeds."""
    storage = FakeStorageClient()
    storage.data["usr-001/agents/task-optimizer/output/result.md"] = b"Optimization complete: 20% improvement"
    
    collector, mock_repo = _make_collector(storage=storage)
    event = _make_agent_event(
        agent_id="task-optimizer",
        session_id="ses-usr-001-1234567890",
    )
    
    await collector.process_agent_result(event)
    
    updates = mock_repo.update_node.call_args[0][1]
    assert "20% improvement" in updates["intelligence"]


@pytest.mark.asyncio
async def test_process_agent_result_graceful_when_no_output_file() -> None:
    """No error when agent output file doesn't exist."""
    storage = FakeStorageClient()  # Empty storage
    collector, mock_repo = _make_collector(storage=storage)
    event = _make_agent_event()
    
    await collector.process_agent_result(event)
    
    # Should still update task with just the event message
    mock_repo.update_node.assert_called_once()
    updates = mock_repo.update_node.call_args[0][1]
    assert updates["state"] == "NEEDS_REVIEW"


@pytest.mark.asyncio
async def test_process_agent_result_skips_output_read_when_no_storage() -> None:
    """No storage access attempted when StorageClient is None."""
    collector, mock_repo = _make_collector(storage=None)
    event = _make_agent_event()
    
    await collector.process_agent_result(event)
    
    # Should still update task node
    mock_repo.update_node.assert_called_once()


@pytest.mark.asyncio
async def test_process_agent_result_truncates_intelligence_to_2000_chars() -> None:
    """Intelligence field is capped at 2000 characters."""
    storage = FakeStorageClient()
    long_content = "X" * 3000
    storage.data["usr-001/agents/test-agent/output/result.md"] = long_content.encode()
    
    collector, mock_repo = _make_collector(storage=storage)
    event = _make_agent_event(
        agent_id="test-agent",
        session_id="ses-usr-001-1234567890",
        message="Y" * 100,
    )
    
    await collector.process_agent_result(event)
    
    updates = mock_repo.update_node.call_args[0][1]
    assert len(updates["intelligence"]) == 2000


# ---------------------------------------------------------------------------
# process_agent_result — Memory and decisions log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_agent_result_writes_to_agent_memory() -> None:
    """Sub-agent result is appended to orchestrator's working memory."""
    storage = FakeStorageClient()
    storage.data["usr-001/agents/main/memory/working.md"] = b"# Working Memory\n\nExisting notes..."
    
    collector, mock_repo = _make_collector(storage=storage)
    event = _make_agent_event(
        agent_id="external-outreach-agent",
        task_id="TSK-001",
        batch_id="batch-001",
        message="Email draft ready",
    )
    
    await collector.process_agent_result(event)
    
    memory_content = storage.data["usr-001/agents/main/memory/working.md"].decode()
    assert "Sub-Agent Result: external-outreach-agent" in memory_content
    assert "Task:** TSK-001" in memory_content
    assert "Batch:** batch-001" in memory_content
    assert "Email draft ready" in memory_content


@pytest.mark.asyncio
async def test_process_agent_result_writes_to_decisions_log() -> None:
    """Sub-agent completion is logged to decisions.md."""
    storage = FakeStorageClient()
    
    collector, mock_repo = _make_collector(storage=storage)
    event = _make_agent_event(
        agent_id="external-outreach-agent",
        task_id="TSK-001",
        status="COMPLETED",
    )
    
    await collector.process_agent_result(event)
    
    log_path = "usr-001/agents/main/log/decisions.md"
    assert log_path in storage.data
    
    log_content = storage.data[log_path].decode()
    assert "Sub-agent completed: external-outreach-agent" in log_content
    assert "Task: TSK-001" in log_content
    assert "Status: COMPLETED" in log_content
    assert "Updated task to NEEDS_REVIEW" in log_content


@pytest.mark.asyncio
async def test_process_agent_result_graceful_when_memory_write_fails() -> None:
    """Exception in memory write is swallowed; task update still succeeds."""
    storage = FakeStorageClient()
    # Simulate storage failure by not allowing writes
    storage.write = AsyncMock(side_effect=RuntimeError("Storage full"))
    
    collector, mock_repo = _make_collector(storage=storage)
    event = _make_agent_event()
    
    # Should not raise
    await collector.process_agent_result(event)
    
    # Task update should still have been called
    mock_repo.update_node.assert_called_once()


@pytest.mark.asyncio
async def test_process_agent_result_graceful_when_task_update_fails() -> None:
    """Exception in task update is logged but doesn't crash collector."""
    collector, mock_repo = _make_collector()
    mock_repo.update_node = AsyncMock(side_effect=RuntimeError("DB connection lost"))
    
    event = _make_agent_event()
    
    # Should not raise
    await collector.process_agent_result(event)
