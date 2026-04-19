"""Integration tests for POST /app/v1/chat/messages/stream.

Tests the streaming route against a real FastAPI TestClient with a real
MinIO storage backend and a stub LLM client that generates deterministic
event sequences.

Run with::

    pytest tests/test_api/test_chat_stream.py -m integration

Environment variables:
    STORAGE_BUCKET       — MinIO bucket name (default: graphclaw)
    STORAGE_ENDPOINT_URL — MinIO endpoint (default: http://localhost:9000)
    STORAGE_REGION       — AWS/MinIO region (default: us-east-1)
    AWS_ACCESS_KEY_ID    — MinIO access key (default: minioadmin)
    AWS_SECRET_ACCESS_KEY — MinIO secret key (default: minioadmin)
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator

import pytest

from graphclaw.agent.run_events import RunEventType
from graphclaw.infra.storage import S3StorageClient
from graphclaw.llm.base import LLMClient, LLMResponse, LLMStreamChunk

pytestmark = pytest.mark.integration

BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
REGION = os.getenv("STORAGE_REGION", "us-east-1")
# Credentials are resolved at fixture execution time (not module-import time)
# to avoid env-var pollution from other test modules.
_MINIO_KEY_DEFAULT = "graphclaw"
_MINIO_SECRET_DEFAULT = "graphclaw_dev"


@pytest.fixture(scope="module", autouse=True)
def _force_minio_credentials():
    """Override AWS credentials for this module regardless of test ordering."""
    old_key = os.environ.get("AWS_ACCESS_KEY_ID")
    old_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    # Hardcode the local MinIO root credentials; override takes effect before
    # any fixture in this module creates a boto3 client.
    key = os.environ.get("CHAT_STREAM_TEST_AWS_KEY", _MINIO_KEY_DEFAULT)
    secret = os.environ.get("CHAT_STREAM_TEST_AWS_SECRET", _MINIO_SECRET_DEFAULT)
    os.environ["AWS_ACCESS_KEY_ID"] = key
    os.environ["AWS_SECRET_ACCESS_KEY"] = secret
    yield key, secret
    if old_key is None:
        os.environ.pop("AWS_ACCESS_KEY_ID", None)
    else:
        os.environ["AWS_ACCESS_KEY_ID"] = old_key
    if old_secret is None:
        os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
    else:
        os.environ["AWS_SECRET_ACCESS_KEY"] = old_secret


# ---------------------------------------------------------------------------
# Stub LLM for API tests
# ---------------------------------------------------------------------------


_STUB_REPLY = "Stub reply from streaming route."


class _StubLLMClient(LLMClient):
    def __init__(self, reply: str = _STUB_REPLY) -> None:
        self._reply = reply

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
        parts = [self._reply[:10], self._reply[10:]]
        for p in parts:
            yield LLMStreamChunk(content_delta=p)
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
# Helper: parse raw SSE body into event objects
# ---------------------------------------------------------------------------


def parse_sse_body(body: bytes) -> list[dict]:
    """Parse a raw SSE response body into a list of event dicts."""
    events = []
    text = body.decode()
    frames = text.split("\n\n")
    for frame in frames:
        if not frame.strip():
            continue
        event_type = None
        data = None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if data:
            try:
                parsed = json.loads(data)
                if event_type:
                    parsed["_sse_event"] = event_type
                events.append(parsed)
            except json.JSONDecodeError:
                pass
    return events


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_storage(_force_minio_credentials):
    """Real MinIO storage client — credentials passed explicitly to avoid env-var pollution."""
    key, secret = _force_minio_credentials
    return S3StorageClient(
        bucket=BUCKET,
        endpoint_url=ENDPOINT,
        region=REGION,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
    )


@pytest.fixture
def app_with_stream(real_storage):
    """Build a minimal FastAPI app with the chat router and a stub AgentLoop."""
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from graphclaw.agent.main_orchestrator import MainOrchestrator as AgentLoop
    from graphclaw.api.chat import router as chat_router
    from graphclaw.api.deps import get_storage_client
    from graphclaw.auth.middleware import require_auth
    from graphclaw.scoring.engine import ScoringEngine
    from graphclaw.state.machine import StateMachine

    # AsyncMock so that all async repo calls work in the TestClient
    mock_repo = AsyncMock()
    mock_repo.list_nodes_by_user.return_value = []
    mock_repo.list_nodes.return_value = []
    mock_repo.get_node.return_value = None
    mock_repo.get_edges.return_value = []

    loop = AgentLoop(
        graph_repo=mock_repo,
        scoring_engine=ScoringEngine(),
        state_machine=StateMachine(),
        llm_client=_StubLLMClient(),
        storage_client=real_storage,
    )

    test_user_id = f"test-api-{uuid.uuid4().hex[:8]}"

    app = FastAPI()
    app.include_router(chat_router, prefix="/app/v1")
    app.state.agent_loop = loop

    # Override dependencies
    app.dependency_overrides[require_auth] = lambda: test_user_id
    app.dependency_overrides[get_storage_client] = lambda: real_storage

    client = TestClient(app, raise_server_exceptions=True)
    return client, test_user_id


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


class TestChatStreamRoute:
    def test_returns_event_stream_content_type(self, app_with_stream):
        client, user_id = app_with_stream
        res = client.post(
            "/app/v1/chat/messages/stream",
            json={"content": "Hello"},
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers.get("content-type", "")

    def test_response_contains_run_started(self, app_with_stream):
        client, user_id = app_with_stream
        res = client.post("/app/v1/chat/messages/stream", json={"content": "Test"})
        events = parse_sse_body(res.content)
        event_types = [e.get("event_type") for e in events]
        assert RunEventType.RUN_STARTED in event_types, f"run.started missing in {event_types}"

    def test_response_contains_terminal_event(self, app_with_stream):
        client, user_id = app_with_stream
        res = client.post("/app/v1/chat/messages/stream", json={"content": "Terminate"})
        events = parse_sse_body(res.content)
        event_types = [e.get("event_type") for e in events]
        terminal_found = any(
            et in (RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED) for et in event_types
        )
        assert terminal_found, f"No terminal event in: {event_types}"

    def test_exactly_one_terminal_event(self, app_with_stream):
        client, user_id = app_with_stream
        res = client.post("/app/v1/chat/messages/stream", json={"content": "One terminal"})
        events = parse_sse_body(res.content)
        terminal_count = sum(
            1
            for e in events
            if e.get("event_type") in (RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED)
        )
        assert terminal_count == 1, f"Expected 1 terminal event, got {terminal_count}"

    def test_assistant_delta_events_present(self, app_with_stream):
        client, user_id = app_with_stream
        res = client.post("/app/v1/chat/messages/stream", json={"content": "Stream deltas"})
        events = parse_sse_body(res.content)
        delta_events = [e for e in events if e.get("event_type") == RunEventType.ASSISTANT_DELTA]
        assert len(delta_events) >= 1, "No assistant.delta events in stream response"

    def test_assembled_delta_matches_reply(self, app_with_stream):
        """Concatenating all delta payloads must reconstruct the full reply."""
        client, user_id = app_with_stream
        res = client.post("/app/v1/chat/messages/stream", json={"content": "Full reply check"})
        events = parse_sse_body(res.content)
        assembled = "".join(
            e.get("payload", {}).get("delta", "")
            for e in events
            if e.get("event_type") == RunEventType.ASSISTANT_DELTA
        )
        assert assembled == _STUB_REPLY, f"Assembled: {assembled!r}"

    def test_event_seq_monotonic_in_response(self, app_with_stream):
        client, user_id = app_with_stream
        res = client.post("/app/v1/chat/messages/stream", json={"content": "Seq check"})
        events = parse_sse_body(res.content)
        seqs = [e.get("event_seq") for e in events if e.get("event_seq") is not None]
        assert seqs == sorted(seqs), f"event_seq not monotonic: {seqs}"

    def test_history_persisted_to_storage_after_stream(self, app_with_stream, real_storage):
        """After the stream ends the chat history must be saved to MinIO."""
        import asyncio  # noqa: PLC0415

        client, user_id = app_with_stream
        # Post a unique message so we can identify it in history
        unique_text = f"unique-{uuid.uuid4().hex[:8]}"
        client.post("/app/v1/chat/messages/stream", json={"content": unique_text})

        # Give a moment for the save to complete (TestClient is synchronous but
        # the generator runs to completion before the response body is returned)
        async def _check():
            path = f"agents/{user_id}/chat_history.json"
            try:
                raw = await real_storage.read(path)
                return json.loads(raw.decode())
            except FileNotFoundError:
                return []

        loop = asyncio.new_event_loop()
        history = loop.run_until_complete(_check())
        loop.close()

        user_msgs = [m for m in history if m.get("role") == "user"]
        contents = [m.get("content", "") for m in user_msgs]
        assert unique_text in contents, f"Message not found in history: {contents}"

    def test_no_agent_loop_returns_run_failed_frame(self):
        """When agent_loop is absent the route must return a run.failed SSE frame."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from graphclaw.api.chat import router as chat_router
        from graphclaw.api.deps import get_storage_client
        from graphclaw.auth.middleware import require_auth

        storage = S3StorageClient(bucket=BUCKET, endpoint_url=ENDPOINT, region=REGION)
        test_user_id = f"test-no-loop-{uuid.uuid4().hex[:8]}"

        app = FastAPI()
        app.include_router(chat_router, prefix="/app/v1")
        # No agent_loop on app.state
        app.dependency_overrides[require_auth] = lambda: test_user_id
        app.dependency_overrides[get_storage_client] = lambda: storage

        client = TestClient(app, raise_server_exceptions=True)
        res = client.post("/app/v1/chat/messages/stream", json={"content": "no loop"})

        assert res.status_code == 200
        assert "text/event-stream" in res.headers.get("content-type", "")
        events = parse_sse_body(res.content)
        assert any(e.get("_sse_event") == "run.failed" for e in events)

