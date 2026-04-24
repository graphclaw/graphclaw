"""End-to-end integration test for email inbound processing (DB + broker + storage)."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from graphclaw.agent.event_consumer import AgentEventConsumer
from graphclaw.db.age.connection import create_pool
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.gateway.schemas import InboundMessage
from graphclaw.inbound.intelligence_agent import InboundIntelligenceAgent
from graphclaw.infra.broker import STATUS_UPDATES, RedisMessageBroker
from graphclaw.infra.storage import S3StorageClient, StoragePaths
from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import TaskType
from graphclaw.models.nodes import TaskNode, UserNode

pytestmark = pytest.mark.integration

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STORAGE_ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
STORAGE_REGION = os.getenv("STORAGE_REGION", "us-east-1")
AWS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "graphclaw")
AWS_SECRET = os.getenv("AWS_SECRET_ACCESS_KEY", "graphclaw_dev")


class _StubLLM:
    async def complete(self, *_args, **_kwargs):
        return SimpleNamespace(
            content=json.dumps(
                {
                    "task_entry": "[email | inbound | Sender confirmed the task is complete]",
                    "memory_note": "Sender prefers concise status updates.",
                }
            )
        )


class _StubLoop:
    def __init__(self, graph_repo: AgeGraphStore) -> None:
        self.graph_repo = graph_repo
        self.llm_client = None
        self.agent_id = "main"

    async def run_cycle(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return []

    async def generate_briefing(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return ""

    async def process_chat_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


@pytest_asyncio.fixture
async def repo() -> AgeGraphStore:
    pool = await create_pool(TEST_DSN)
    store = AgeGraphStore(pool)
    try:
        yield store
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_email_inbound_path_writes_db_broker_and_storage(repo: AgeGraphStore) -> None:
    """Email inbound processing should touch DB resolution, broker publish, and storage archive."""
    user_id = "USER-email-e2e"
    task_id = generate_task_id("EM", TaskType.ATOMIC)

    user = UserNode(
        id=user_id,
        name="Email E2E User",
        email="email.e2e@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    task = TaskNode(
        id=task_id,
        task_type=TaskType.ATOMIC,
        title="Deploy API service",
        description="Deploy API service to production",
        created_by=user_id,
        owned_by=user_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    await repo.create_node(user)
    await repo.create_node(task)

    broker = RedisMessageBroker(REDIS_URL)
    redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    await redis_client.delete(STATUS_UPDATES)

    storage = S3StorageClient(
        bucket=STORAGE_BUCKET,
        endpoint_url=STORAGE_ENDPOINT,
        region=STORAGE_REGION,
        aws_access_key_id=AWS_KEY,
        aws_secret_access_key=AWS_SECRET,
    )

    loop = _StubLoop(repo)
    dispatcher = AsyncMock()
    consumer = AgentEventConsumer(
        broker=broker,
        agent_loop=loop,
        dispatcher=dispatcher,
        user_channels={},
        default_user_id=user_id,
        storage=storage,
    )
    consumer._intelligence_agent = InboundIntelligenceAgent(
        llm=_StubLLM(),
        graph_repo=repo,
        storage=storage,
        memory_lock=asyncio.Lock(),
        logger=None,
    )

    inbound = InboundMessage(
        message_id="msg-email-e2e-001",
        channel="email",
        sender="owner@example.com",
        subject="Deployment update",
        body=f"{task_id} is done. Completed successfully.",
        received_at=datetime.now(timezone.utc),
        session_id="SES-email-e2e-001",
    )

    try:
        await consumer._process_raw_inbound(inbound)

        # Broker evidence: status update should be published.
        raw_event = await redis_client.brpop(STATUS_UPDATES, timeout=3)
        assert raw_event is not None, "Expected a status update event in Redis"
        _, payload = raw_event
        status_update = json.loads(payload)
        assert status_update["task_id"] == task_id
        assert status_update["new_state"] == "COMPLETE"

        # Storage evidence: inbox recent and archive entries should be present.
        recent_prefix = StoragePaths.agent_inbox_recent_prefix(user_id, "main")
        archive_prefix = recent_prefix.replace("/recent/", "/archive/")
        recent_entries = await storage.list_objects(recent_prefix)
        archive_entries = await storage.list_objects(archive_prefix)
        assert len(recent_entries) >= 1
        assert len(archive_entries) >= 1

        # DB evidence: intelligence field should include inbound entry.
        intelligence = await repo.get_node_intelligence(task_id)
        assert intelligence is not None
        assert "inbound" in intelligence
    finally:
        await broker.close()
        await redis_client.aclose()
        await repo.delete_node(task_id)
        await repo.delete_node(user_id)
