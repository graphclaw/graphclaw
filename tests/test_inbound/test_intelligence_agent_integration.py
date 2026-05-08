# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for InboundIntelligenceAgent with real AGE + MinIO.

Validates that intelligence extraction persists task intelligence to the graph
and appends timestamped working-context notes to object storage.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from graphclaw.db.age.connection import create_pool
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.gateway.schemas import InboundMessage
from graphclaw.inbound.intelligence_agent import InboundIntelligenceAgent
from graphclaw.inbound.models import InboundResult, StatusExtraction, TaskResolution
from graphclaw.infra.storage import S3StorageClient, StoragePaths
from graphclaw.models.enums import ConfidenceLevel, MatchedBy, TaskState

pytestmark = pytest.mark.integration

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
REGION = os.getenv("STORAGE_REGION", "us-east-1")

os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")


class _NodeStub:
    def __init__(self, props: dict):
        self._props = props

    def model_dump(self, **kwargs) -> dict:
        return self._props


@pytest_asyncio.fixture(scope="module")
async def db_pool():
    pool = await create_pool(TEST_DSN)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module")
def storage():
    return S3StorageClient(bucket=BUCKET, endpoint_url=ENDPOINT, region=REGION)


@pytest.mark.asyncio
async def test_process_persists_intelligence_to_graph_and_storage(db_pool, storage):
    repo = AgeGraphStore(db_pool)

    user_id = f"usr-intel-{uuid.uuid4().hex[:8]}"
    agent_id = "main"
    task_id = f"TSK-INT-{uuid.uuid4().hex[:8]}-AT"

    await repo.create_node(
        _NodeStub(
            {
                "id": task_id,
                "task_type": "ATOMIC",
                "title": "Intelligence Integration Task",
                "description": "Task for inbound intelligence integration test",
                "state": "ACTIVE",
                "owned_by": user_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )

    llm = AsyncMock()
    llm.complete = AsyncMock(
        return_value=type(
            "Resp",
            (),
            {
                "content": json.dumps(
                    {
                        "task_entry": "email | inbound | blocker cleared and ready to proceed",
                        "memory_note": "User prefers concise status updates with clear outcomes",
                    }
                )
            },
        )()
    )

    agent = InboundIntelligenceAgent(
        llm=llm,
        graph_repo=repo,
        storage=storage,
        memory_lock=asyncio.Lock(),
    )

    inbound = InboundMessage(
        message_id=f"msg-{uuid.uuid4().hex[:8]}",
        channel="email",
        sender="integration@example.com",
        subject="Status update",
        body="Task is unblocked and work can continue.",
        received_at=datetime.now(timezone.utc),
        session_id=f"SES-{uuid.uuid4().hex[:8]}",
    )
    resolution = InboundResult(
        message_id=inbound.message_id,
        session_id=inbound.session_id,
        resolution=TaskResolution(
            task_id=task_id,
            matched_by=MatchedBy.TASK_ID,
            confidence=ConfidenceLevel.HIGH,
            score=1.0,
            matched_text=task_id,
        ),
        status=StatusExtraction(signal="IN_PROGRESS", suggested_state=TaskState.IN_PROGRESS),
    )

    try:
        result = await agent.process(
            inbound=inbound,
            resolution=resolution,
            agent_id=agent_id,
            user_id=user_id,
        )

        assert result.action_taken == "both"

        intelligence_text = await repo.get_node_intelligence(task_id)
        assert "blocker cleared" in intelligence_text

        context_path = StoragePaths.agent_memory_working(user_id, agent_id)
        context_text = (await storage.read(context_path)).decode("utf-8")
        last_line = [line for line in context_text.splitlines() if line][-1]
        payload = json.loads(last_line)
        assert payload["source"] == "inbound_intelligence"
        assert payload["note"] == "User prefers concise status updates with clear outcomes"
        assert "timestamp" in payload
    finally:
        await repo.delete_node(task_id)
        try:
            await storage.delete(StoragePaths.agent_memory_working(user_id, agent_id))
        except Exception:  # noqa: BLE001
            pass
