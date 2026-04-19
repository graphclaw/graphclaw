"""Integration tests for AgentLoop.process_chat_message_stream.

Tests use real PostgreSQL+AGE and MinIO backends.  No stubs or mocks for
infrastructure.  The LLM client is stubbed via a minimal async generator
implementation to produce deterministic event sequences without API costs.

Run with::

    pytest tests/test_agent/test_loop_stream_integration.py -m integration

Environment variables:
    TEST_DATABASE_URL   — PostgreSQL DSN (default: postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw)
    STORAGE_BUCKET      — MinIO bucket name (default: graphclaw)
    STORAGE_ENDPOINT_URL — MinIO endpoint (default: http://localhost:9000)
    STORAGE_REGION      — AWS/MinIO region (default: us-east-1)
    AWS_ACCESS_KEY_ID   — MinIO access key (default: minioadmin)
    AWS_SECRET_ACCESS_KEY — MinIO secret key (default: minioadmin)
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from graphclaw.agent.main_orchestrator import MainOrchestrator as AgentLoop
from graphclaw.agent.run_events import RunEventType
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.db.connection import create_pool
from graphclaw.infra.storage import S3StorageClient
from graphclaw.infra.user_events import InMemoryUserEventPublisher
from graphclaw.llm.base import LLMClient, LLMResponse, LLMStreamChunk
from graphclaw.scoring.engine import ScoringEngine
from graphclaw.state.machine import StateMachine

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Connection constants
# ---------------------------------------------------------------------------

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
REGION = os.getenv("STORAGE_REGION", "us-east-1")

os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")


# ---------------------------------------------------------------------------
# Stub LLM clients
# ---------------------------------------------------------------------------


class _TextOnlyLLMClient(LLMClient):
    """LLM stub that streams a fixed text reply in chunks with no tool calls."""

    def __init__(self, reply: str = "I see your tasks.", chunks: int = 3) -> None:
        self._reply = reply
        self._chunks = chunks

    async def complete(
        self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None
    ) -> LLMResponse:
        return LLMResponse(
            content=self._reply,
            model="stub",
            tokens_used=10,
            prompt_tokens=8,
            completion_tokens=2,
            cost_usd=0.0,
            tool_calls=[],
            stop_reason="end_turn",
        )

    async def stream(
        self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None
    ) -> AsyncIterator[LLMStreamChunk]:
        # Split reply into `chunks` equal parts
        reply = self._reply
        size = max(1, len(reply) // self._chunks)
        parts = [reply[i : i + size] for i in range(0, len(reply), size)]
        for part in parts:
            yield LLMStreamChunk(content_delta=part)
        final = LLMResponse(
            content=reply,
            model="stub",
            tokens_used=10,
            prompt_tokens=8,
            completion_tokens=2,
            cost_usd=0.0,
            tool_calls=[],
            stop_reason="end_turn",
        )
        yield LLMStreamChunk(content_delta="", is_final=True, accumulated=final)

    async def count_tokens(self, messages, *, model=None) -> int:
        return 10

    async def close(self) -> None:
        pass


class _ToolCallLLMClient(LLMClient):
    """LLM stub that first requests a single tool call, then gives a text reply."""

    def __init__(self, tool_name: str = "list_tasks", reply: str = "Done.") -> None:
        self._tool_name = tool_name
        self._reply = reply
        self._call_count = 0

    async def complete(
        self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None
    ) -> LLMResponse:
        from graphclaw.llm.base import ToolCall  # noqa: PLC0415

        self._call_count += 1
        if self._call_count == 1:
            tc = ToolCall(
                id=f"tc-{uuid.uuid4().hex[:8]}", name=self._tool_name, arguments={"user_id": "stub"}
            )
            return LLMResponse(
                content="",
                model="stub",
                tokens_used=15,
                prompt_tokens=12,
                completion_tokens=3,
                cost_usd=0.0,
                tool_calls=[tc],
                stop_reason="tool_use",
            )
        return LLMResponse(
            content=self._reply,
            model="stub",
            tokens_used=10,
            prompt_tokens=8,
            completion_tokens=2,
            cost_usd=0.0,
            tool_calls=[],
            stop_reason="end_turn",
        )

    async def stream(
        self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None
    ) -> AsyncIterator[LLMStreamChunk]:
        from graphclaw.llm.base import ToolCall  # noqa: PLC0415

        self._call_count += 1
        if self._call_count == 1:
            # First call: emit a tool-use response (no text delta, final chunk carries tool_calls)
            tc = ToolCall(
                id=f"tc-{uuid.uuid4().hex[:8]}", name=self._tool_name, arguments={"user_id": "stub"}
            )
            final = LLMResponse(
                content="",
                model="stub",
                tokens_used=15,
                prompt_tokens=12,
                completion_tokens=3,
                cost_usd=0.0,
                tool_calls=[tc],
                stop_reason="tool_use",
            )
            yield LLMStreamChunk(content_delta="", is_final=True, accumulated=final)
        else:
            # Second call: emit text reply
            for part in [self._reply[:5], self._reply[5:]]:
                yield LLMStreamChunk(content_delta=part)
            final = LLMResponse(
                content=self._reply,
                model="stub",
                tokens_used=10,
                prompt_tokens=8,
                completion_tokens=2,
                cost_usd=0.0,
                tool_calls=[],
                stop_reason="end_turn",
            )
            yield LLMStreamChunk(content_delta="", is_final=True, accumulated=final)

    async def count_tokens(self, messages, *, model=None) -> int:
        return 10

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def db_pool():
    pool = await create_pool(TEST_DSN)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module")
def storage():
    return S3StorageClient(bucket=BUCKET, endpoint_url=ENDPOINT, region=REGION)


def _make_loop(db_pool, storage, llm_client: LLMClient) -> AgentLoop:
    repo = AgeGraphStore(db_pool)
    scoring_engine = ScoringEngine()
    state_machine = StateMachine()
    return AgentLoop(
        graph_repo=repo,
        scoring_engine=scoring_engine,
        state_machine=state_machine,
        llm_client=llm_client,
        storage_client=storage,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProcessChatMessageStreamTextOnly:
    """Verify stream events for a simple text-only LLM reply."""

    @pytest.mark.asyncio
    async def test_emits_run_started_and_completed(self, db_pool, storage):
        loop = _make_loop(db_pool, storage, _TextOnlyLLMClient("Hello world!", chunks=4))
        user_id = f"stream-test-{uuid.uuid4().hex[:8]}"

        events = []
        async for event in loop.process_chat_message_stream(
            user_id=user_id,
            text="Hi there",
            session_id="ses-test-001",
        ):
            events.append(event)

        event_types = [e.event_type for e in events]
        assert RunEventType.RUN_STARTED in event_types, "run.started missing"
        assert RunEventType.RUN_COMPLETED in event_types, "run.completed missing"

    @pytest.mark.asyncio
    async def test_exactly_one_terminal_event(self, db_pool, storage):
        loop = _make_loop(db_pool, storage, _TextOnlyLLMClient("Response text", chunks=3))
        user_id = f"stream-test-{uuid.uuid4().hex[:8]}"

        terminal_count = 0
        terminal_types = {RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED}
        async for event in loop.process_chat_message_stream(user_id=user_id, text="Test"):
            if event.event_type in terminal_types:
                terminal_count += 1

        assert terminal_count == 1, f"Expected exactly 1 terminal event, got {terminal_count}"

    @pytest.mark.asyncio
    async def test_event_seq_is_monotonic(self, db_pool, storage):
        loop = _make_loop(db_pool, storage, _TextOnlyLLMClient("Monotonic test", chunks=5))
        user_id = f"stream-test-{uuid.uuid4().hex[:8]}"

        seqs = []
        async for event in loop.process_chat_message_stream(
            user_id=user_id, text="Check monotonic"
        ):
            seqs.append(event.event_seq)

        assert seqs == sorted(seqs), f"event_seq not monotonic: {seqs}"
        assert seqs[0] == 0, f"First seq should be 0, got {seqs[0]}"

    @pytest.mark.asyncio
    async def test_assistant_delta_events_emitted(self, db_pool, storage):
        reply = "Delta streaming test response"
        loop = _make_loop(db_pool, storage, _TextOnlyLLMClient(reply, chunks=4))
        user_id = f"stream-test-{uuid.uuid4().hex[:8]}"

        deltas = []
        async for event in loop.process_chat_message_stream(user_id=user_id, text="Stream me"):
            if event.event_type == RunEventType.ASSISTANT_DELTA:
                payload = event.payload
                if hasattr(payload, "delta"):
                    deltas.append(payload.delta)

        assert len(deltas) >= 1, "No assistant.delta events emitted"
        assembled = "".join(deltas)
        assert assembled == reply, f"Assembled text mismatch: {assembled!r} != {reply!r}"

    @pytest.mark.asyncio
    async def test_all_events_share_same_run_id(self, db_pool, storage):
        loop = _make_loop(db_pool, storage, _TextOnlyLLMClient("Same run", chunks=2))
        user_id = f"stream-test-{uuid.uuid4().hex[:8]}"

        run_ids: set[str] = set()
        async for event in loop.process_chat_message_stream(user_id=user_id, text="One run"):
            run_ids.add(event.run_id)

        assert len(run_ids) == 1, f"Multiple run_ids detected: {run_ids}"

    @pytest.mark.asyncio
    async def test_user_id_consistent_across_events(self, db_pool, storage):
        loop = _make_loop(db_pool, storage, _TextOnlyLLMClient("User ID test", chunks=2))
        user_id = f"stream-uid-{uuid.uuid4().hex[:8]}"

        async for event in loop.process_chat_message_stream(user_id=user_id, text="Check user_id"):
            assert event.user_id == user_id, f"user_id mismatch: {event.user_id}"

    @pytest.mark.asyncio
    async def test_publisher_receives_same_events(self, db_pool, storage):
        """InMemoryUserEventPublisher must collect the same events as the iterator."""
        publisher = InMemoryUserEventPublisher()
        loop = _make_loop(db_pool, storage, _TextOnlyLLMClient("Publisher test", chunks=3))
        user_id = f"stream-pub-{uuid.uuid4().hex[:8]}"

        iterator_events = []
        async for event in loop.process_chat_message_stream(
            user_id=user_id,
            text="Publisher sync",
            publisher=publisher,
        ):
            iterator_events.append(event)

        published = publisher.events_for(user_id)
        # Every event yielded must also have been published
        assert len(published) == len(iterator_events)
        for i, (yielded, pub) in enumerate(zip(iterator_events, published)):
            assert yielded.event_seq == pub.event_seq, f"Seq mismatch at index {i}"
            assert yielded.event_type == pub.event_type


class TestProcessChatMessageStreamWithToolCall:
    """Verify stream events when the LLM makes a tool call."""

    @pytest.mark.asyncio
    async def test_tool_started_emitted(self, db_pool, storage):
        loop = _make_loop(db_pool, storage, _ToolCallLLMClient("list_tasks", "Task list done."))
        user_id = f"stream-tool-{uuid.uuid4().hex[:8]}"

        tool_started = []
        async for event in loop.process_chat_message_stream(user_id=user_id, text="List my tasks"):
            if event.event_type == RunEventType.TOOL_STARTED:
                tool_started.append(event)

        assert len(tool_started) >= 1, "tool.started was not emitted"

    @pytest.mark.asyncio
    async def test_tool_completed_or_failed_after_started(self, db_pool, storage):
        """Every tool.started must be followed by tool.completed or tool.failed."""
        loop = _make_loop(db_pool, storage, _ToolCallLLMClient("list_tasks", "Got tasks."))
        user_id = f"stream-tool-seq-{uuid.uuid4().hex[:8]}"

        events = []
        async for event in loop.process_chat_message_stream(
            user_id=user_id, text="What tasks do I have?"
        ):
            events.append(event)

        tool_starts = [e for e in events if e.event_type == RunEventType.TOOL_STARTED]
        tool_ends = [
            e
            for e in events
            if e.event_type in (RunEventType.TOOL_COMPLETED, RunEventType.TOOL_FAILED)
        ]
        assert len(tool_starts) == len(tool_ends), (
            f"Mismatch: {len(tool_starts)} starts vs {len(tool_ends)} ends"
        )

    @pytest.mark.asyncio
    async def test_terminal_event_still_emitted_after_tool(self, db_pool, storage):
        loop = _make_loop(db_pool, storage, _ToolCallLLMClient("get_task_details", "Here you go."))
        user_id = f"stream-term-{uuid.uuid4().hex[:8]}"

        terminal = [
            e
            for e in [
                event
                async for event in loop.process_chat_message_stream(
                    user_id=user_id, text="Tell me about task details"
                )
            ]
            if e.event_type in (RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED)
        ]
        assert len(terminal) == 1, "Expected exactly one terminal event"


class TestProcessChatMessageStreamHistory:
    """Verify history is properly built from conversation_history param."""

    @pytest.mark.asyncio
    async def test_run_with_history_still_terminates(self, db_pool, storage):
        """Passing conversation history must not break the stream termination."""
        loop = _make_loop(db_pool, storage, _TextOnlyLLMClient("With history", chunks=2))
        user_id = f"stream-hist-{uuid.uuid4().hex[:8]}"

        history = [
            {"role": "user", "content": "Prior message"},
            {"role": "agent", "content": "Prior reply"},
        ]
        terminal_found = False
        async for event in loop.process_chat_message_stream(
            user_id=user_id,
            text="Follow-up question",
            conversation_history=history,
            session_id="ses-hist-001",
        ):
            if event.event_type in (RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED):
                terminal_found = True

        assert terminal_found, "Stream did not terminate when history was provided"

