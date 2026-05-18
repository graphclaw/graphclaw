# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_skills.test_models — Unit tests for graphclaw.skills.models.

Description
-----------
Verifies that all skill domain models construct correctly with their default
values, that enum members carry the expected string values, and that
explicit field values round-trip through Pydantic validation.

Design Patterns
---------------
- Arrange/Assert: Each test constructs a model instance and asserts field
  values.  No I/O or mocking is required.

Dependencies
------------
- pytest: Test runner.
- datetime: Timestamps for SkillJob / SkillResult.
- graphclaw.skills.models: All models under test.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graphclaw.skills.models import (
    HeartbeatConfig,
    SkillDefinition,
    SkillJob,
    SkillResult,
    SkillStatus,
    ThreadState,
    WorkerStatus,
)


def _utc(*args, **kwargs) -> datetime:
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# SkillStatus enum
# ---------------------------------------------------------------------------


def test_skill_status_values() -> None:
    """SkillStatus members must have the expected string values."""
    assert SkillStatus.IDLE == "IDLE"
    assert SkillStatus.RUNNING == "RUNNING"
    assert SkillStatus.COMPLETED == "COMPLETED"
    assert SkillStatus.FAILED == "FAILED"
    assert SkillStatus.TIMEOUT == "TIMEOUT"


# ---------------------------------------------------------------------------
# ThreadState enum
# ---------------------------------------------------------------------------


def test_thread_state_values() -> None:
    """ThreadState members must have the expected string values."""
    assert ThreadState.SPAWNING == "SPAWNING"
    assert ThreadState.RUNNING == "RUNNING"
    assert ThreadState.WAITING == "WAITING"
    assert ThreadState.COMPLETED == "COMPLETED"
    assert ThreadState.FAILED == "FAILED"
    assert ThreadState.TIMED_OUT == "TIMED_OUT"


# ---------------------------------------------------------------------------
# SkillDefinition
# ---------------------------------------------------------------------------


def test_skill_definition_defaults() -> None:
    """SkillDefinition fields that have defaults should use them when omitted."""
    skill = SkillDefinition(name="test-skill")

    assert skill.name == "test-skill"
    assert skill.description == ""
    assert skill.version == "1.0.0"
    assert skill.model == "claude-sonnet-4-20250514"
    assert skill.max_tokens == 4096
    assert skill.temperature == 0.0
    assert skill.system_prompt == ""
    assert skill.tools == []
    assert skill.tags == []
    assert skill.timeout_seconds == 300


def test_skill_definition_explicit_fields() -> None:
    """SkillDefinition should store all explicitly provided values."""
    skill = SkillDefinition(
        name="summariser",
        description="Summarises text",
        version="2.1.0",
        model="gpt-4o",
        max_tokens=2048,
        temperature=0.7,
        system_prompt="You are a summariser.",
        tools=["search", "read_file"],
        tags=["nlp", "summary"],
        timeout_seconds=120,
    )

    assert skill.name == "summariser"
    assert skill.description == "Summarises text"
    assert skill.version == "2.1.0"
    assert skill.model == "gpt-4o"
    assert skill.max_tokens == 2048
    assert skill.temperature == 0.7
    assert skill.system_prompt == "You are a summariser."
    assert skill.tools == ["search", "read_file"]
    assert skill.tags == ["nlp", "summary"]
    assert skill.timeout_seconds == 120


# ---------------------------------------------------------------------------
# SkillJob
# ---------------------------------------------------------------------------


def test_skill_job_creation() -> None:
    """SkillJob should accept all fields and apply defaults for optional ones."""
    now = _utc(2026, 3, 18, 10, 0)
    job = SkillJob(
        job_id="job-001",
        skill_name="summariser",
        task_id="TSK-AB-0001-ATM",
        session_id="SES-abc",
        created_at=now,
    )

    assert job.job_id == "job-001"
    assert job.skill_name == "summariser"
    assert job.task_id == "TSK-AB-0001-ATM"
    assert job.session_id == "SES-abc"
    assert job.input_data == {}
    assert job.priority == 0
    assert job.created_at == now
    assert job.timeout_seconds == 300


def test_skill_job_with_priority_and_input() -> None:
    """SkillJob should store non-default priority and input_data."""
    now = _utc(2026, 3, 18, 10, 0)
    job = SkillJob(
        job_id="job-002",
        skill_name="analyser",
        task_id="TSK-AB-0002-ATM",
        session_id="SES-xyz",
        input_data={"text": "hello world"},
        priority=5,
        created_at=now,
        timeout_seconds=60,
    )

    assert job.input_data == {"text": "hello world"}
    assert job.priority == 5
    assert job.timeout_seconds == 60


# ---------------------------------------------------------------------------
# SkillResult
# ---------------------------------------------------------------------------


def test_skill_result_completed() -> None:
    """SkillResult with COMPLETED status should store output and zero error."""
    started = _utc(2026, 3, 18, 10, 0)
    completed = _utc(2026, 3, 18, 10, 1)

    result = SkillResult(
        job_id="job-001",
        skill_name="summariser",
        task_id="TSK-AB-0001-ATM",
        session_id="SES-abc",
        status=SkillStatus.COMPLETED,
        output="Summary text",
        started_at=started,
        completed_at=completed,
        tokens_used=150,
        cost_usd=0.002,
    )

    assert result.status == SkillStatus.COMPLETED
    assert result.output == "Summary text"
    assert result.error is None
    assert result.tokens_used == 150
    assert result.cost_usd == 0.002


def test_skill_result_failed() -> None:
    """SkillResult with FAILED status should store the error message."""
    started = _utc(2026, 3, 18, 10, 0)
    completed = _utc(2026, 3, 18, 10, 0, 5)

    result = SkillResult(
        job_id="job-002",
        skill_name="analyser",
        task_id="TSK-AB-0002-ATM",
        session_id="SES-xyz",
        status=SkillStatus.FAILED,
        error="API rate limit exceeded",
        started_at=started,
        completed_at=completed,
    )

    assert result.status == SkillStatus.FAILED
    assert result.output == ""
    assert result.error == "API rate limit exceeded"
    assert result.tokens_used == 0
    assert result.cost_usd == 0.0


def test_skill_result_timeout() -> None:
    """SkillResult with TIMEOUT status should have appropriate error text."""
    now = _utc(2026, 3, 18, 10, 0)
    result = SkillResult(
        job_id="job-003",
        skill_name="slow-skill",
        task_id="TSK-AB-0003-ATM",
        session_id="SES-def",
        status=SkillStatus.TIMEOUT,
        error="Execution timed out",
        started_at=now,
        completed_at=now,
    )

    assert result.status == SkillStatus.TIMEOUT
    assert result.error == "Execution timed out"


# ---------------------------------------------------------------------------
# WorkerStatus
# ---------------------------------------------------------------------------


def test_worker_status_creation() -> None:
    """WorkerStatus should accept worker_id and state with sensible defaults."""
    status = WorkerStatus(
        worker_id="worker-000",
        state=ThreadState.SPAWNING,
    )

    assert status.worker_id == "worker-000"
    assert status.state == ThreadState.SPAWNING
    assert status.current_job_id is None
    assert status.last_heartbeat is None
    assert status.jobs_completed == 0
    assert status.jobs_failed == 0


def test_worker_status_running_with_job() -> None:
    """WorkerStatus should store a current_job_id when in RUNNING state."""
    now = _utc(2026, 3, 18, 10, 0)
    status = WorkerStatus(
        worker_id="worker-001",
        state=ThreadState.RUNNING,
        current_job_id="job-abc",
        last_heartbeat=now,
        jobs_completed=10,
        jobs_failed=2,
    )

    assert status.current_job_id == "job-abc"
    assert status.last_heartbeat == now
    assert status.jobs_completed == 10
    assert status.jobs_failed == 2


# ---------------------------------------------------------------------------
# HeartbeatConfig
# ---------------------------------------------------------------------------


def test_heartbeat_config_defaults() -> None:
    """HeartbeatConfig defaults should match the specification."""
    config = HeartbeatConfig()

    assert config.interval_seconds == 300.0
    assert config.timeout_seconds == 900.0
    assert config.max_respawn_attempts == 3


def test_heartbeat_config_custom() -> None:
    """HeartbeatConfig should accept custom tuning values."""
    config = HeartbeatConfig(
        interval_seconds=60.0,
        timeout_seconds=180.0,
        max_respawn_attempts=5,
    )

    assert config.interval_seconds == 60.0
    assert config.timeout_seconds == 180.0
    assert config.max_respawn_attempts == 5
