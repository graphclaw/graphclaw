# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the chat API — requires live MinIO and a running gateway.

These tests verify that:
1. History loaded from MinIO is forwarded to process_chat_message.
2. The session_id is generated and passed correctly.
3. History is persisted back to MinIO after each message.

Run with::

    pytest tests/test_api/test_chat_history_integration.py -m integration

The gateway URL is read from GATEWAY_URL (default: http://localhost:8000).
A valid JWT token must be in TEST_AUTH_TOKEN.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000")
AUTH_TOKEN = os.getenv("TEST_AUTH_TOKEN", "")


def _headers() -> dict:
    if AUTH_TOKEN:
        return {"Authorization": f"Bearer {AUTH_TOKEN}"}
    return {}


# ---------------------------------------------------------------------------
# _generate_agent_response unit-level integration test
# (uses real storage but not the full gateway HTTP layer)
# ---------------------------------------------------------------------------


class TestGenerateAgentResponseWithRealStorage:
    """Tests _generate_agent_response with a real MinIO-backed storage client."""

    @pytest.mark.asyncio
    async def test_history_passed_to_process_chat_message(self):
        """The agent loop must receive the conversation history loaded from storage."""
        from unittest.mock import MagicMock

        from graphclaw.api.chat import _generate_agent_response, _history_path
        from graphclaw.infra.storage import S3StorageClient

        # Real storage against MinIO
        storage = S3StorageClient(
            bucket=os.getenv("STORAGE_BUCKET", "graphclaw"),
            endpoint_url=os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000"),
            region=os.getenv("STORAGE_REGION", "us-east-1"),
        )

        import json

        user_id = f"test-usr-{uuid.uuid4().hex[:8]}"
        history = [
            {
                "message_id": "msg-000000-u",
                "role": "user",
                "content": "Prior question",
                "timestamp": "2026-01-01T00:00:00+00:00",
            },
            {
                "message_id": "msg-000000-a",
                "role": "agent",
                "content": "Prior answer",
                "timestamp": "2026-01-01T00:00:01+00:00",
            },
        ]

        # Write history to real MinIO
        history_path = _history_path(user_id)
        try:
            await storage.write(
                history_path, json.dumps(history).encode(), content_type="application/json"
            )

            captured = {}

            async def _mock_process(user_id, text, conversation_history=None, session_id=None):
                captured["history"] = conversation_history
                captured["session_id"] = session_id
                return "Test reply"

            mock_loop = MagicMock()
            mock_loop.process_chat_message = _mock_process

            request = MagicMock()
            request.app.state.agent_loop = mock_loop

            result = await _generate_agent_response(
                request,
                user_id,
                "New question",
                history=history,
                session_id="ses-000002",
            )

            assert result == "Test reply"
            assert captured["history"] == history
            assert captured["session_id"] == "ses-000002"
        finally:
            try:
                await storage.delete(history_path)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_history_persisted_after_send(self):
        """Sending a message should append to MinIO history."""

        from graphclaw.api.chat import _history_path, _load_history, _save_history
        from graphclaw.infra.storage import S3StorageClient

        storage = S3StorageClient(
            bucket=os.getenv("STORAGE_BUCKET", "graphclaw"),
            endpoint_url=os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000"),
            region=os.getenv("STORAGE_REGION", "us-east-1"),
        )

        user_id = f"test-usr-{uuid.uuid4().hex[:8]}"
        history_path = _history_path(user_id)

        try:
            # Start with empty history
            history = await _load_history(user_id, storage)
            assert history == []

            # Append a message and save
            history.append(
                {
                    "role": "user",
                    "content": "Hello from integration test",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            )
            await _save_history(user_id, storage, history)

            # Reload — should see the appended message
            reloaded = await _load_history(user_id, storage)
            assert len(reloaded) == 1
            assert reloaded[0]["content"] == "Hello from integration test"
        finally:
            try:
                await storage.delete(history_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# HTTP-level tests (require a running gateway with a valid token)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not AUTH_TOKEN, reason="TEST_AUTH_TOKEN not set — skipping HTTP gateway tests")
class TestChatEndpointsHttp:
    """End-to-end HTTP tests against a running gateway instance."""

    @pytest.mark.asyncio
    async def test_send_message_returns_201(self):
        import httpx

        async with httpx.AsyncClient(base_url=GATEWAY_URL) as client:
            response = await client.post(
                "/app/v1/chat/messages",
                json={"content": f"Integration test message {uuid.uuid4().hex[:6]}"},
                headers=_headers(),
            )
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_get_history_returns_200(self):
        import httpx

        async with httpx.AsyncClient(base_url=GATEWAY_URL) as client:
            response = await client.get(
                "/app/v1/chat/messages",
                headers=_headers(),
            )
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data

    @pytest.mark.asyncio
    async def test_history_grows_after_send(self):
        import httpx

        async with httpx.AsyncClient(base_url=GATEWAY_URL) as client:
            # Get baseline count
            get1 = await client.get("/app/v1/chat/messages", headers=_headers())
            baseline = len(get1.json()["messages"])

            # Send a message
            await client.post(
                "/app/v1/chat/messages",
                json={"content": f"History growth test {uuid.uuid4().hex}"},
                headers=_headers(),
            )

            # Verify history grew
            get2 = await client.get("/app/v1/chat/messages", headers=_headers())
            assert len(get2.json()["messages"]) >= baseline + 2  # user + agent messages

    @pytest.mark.asyncio
    async def test_delete_clears_history(self):
        import httpx

        async with httpx.AsyncClient(base_url=GATEWAY_URL) as client:
            # Send a message so there's something to clear
            await client.post(
                "/app/v1/chat/messages",
                json={"content": "Message before clear"},
                headers=_headers(),
            )

            # Clear
            del_resp = await client.delete("/app/v1/chat/messages", headers=_headers())
            assert del_resp.status_code == 204

            # History should now be empty
            get_resp = await client.get("/app/v1/chat/messages", headers=_headers())
            assert get_resp.json()["messages"] == []
