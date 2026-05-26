# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for chat behavior when no LLM is configured."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.chat import _LLM_NOT_CONFIGURED_MESSAGE, _generate_agent_response
from graphclaw.api.chat import router as chat_router
from graphclaw.api.deps import get_storage_client
from graphclaw.auth.middleware import require_auth


class _InMemoryStorage:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def read(self, path: str) -> bytes:
        if path not in self._data:
            raise FileNotFoundError(path)
        return self._data[path]

    async def write(self, path: str, data: bytes, content_type: str | None = None) -> None:
        self._data[path] = data

    async def delete(self, path: str) -> None:
        self._data.pop(path, None)


@pytest.mark.asyncio
async def test_generate_agent_response_returns_not_configured_when_llm_missing():
    process_chat = AsyncMock(return_value="should-not-be-used")
    agent_loop = SimpleNamespace(llm_client=None, process_chat_message=process_chat)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(agent_loop=agent_loop)),
        state=SimpleNamespace(org_id="default"),
        headers={},
    )

    result = await _generate_agent_response(request, "user-1", "hello")

    assert result == _LLM_NOT_CONFIGURED_MESSAGE
    process_chat.assert_not_awaited()


def test_stream_route_returns_llm_not_configured_run_failed_event():
    app = FastAPI()
    app.include_router(chat_router, prefix="/app/v1")
    app.state.agent_loop = SimpleNamespace(llm_client=None, process_chat_message_stream=AsyncMock())

    storage = _InMemoryStorage()
    app.dependency_overrides[require_auth] = lambda: "user-1"
    app.dependency_overrides[get_storage_client] = lambda: storage

    client = TestClient(app)
    response = client.post("/app/v1/chat/messages/stream", json={"content": "hello"})

    assert response.status_code == 200
    assert "event: run.failed" in response.text
    assert "LLMNotConfigured" in response.text
