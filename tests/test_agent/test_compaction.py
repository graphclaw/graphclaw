# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared working-memory compaction (graphclaw.agent.compaction).

Covers:
- compact with caller-supplied summary: raw → working/archive, summary → episodic
- compact with NO summary auto-generates one via the LLM from working + history
- no-op when there is nothing to compact (no summary, empty context + history)
- distinct content guarantee (archive != episodic) — Fix Q5
"""

from __future__ import annotations

import json

import pytest

from graphclaw.agent.compaction import compact_working_memory
from graphclaw.infra.storage import StoragePaths

_USER = "USER-test"
_AGENT = "main"


class _FakeStorage:
    def __init__(self, data: dict[str, bytes] | None = None) -> None:
        self.data: dict[str, bytes] = dict(data or {})

    async def read(self, path: str) -> bytes:
        if path not in self.data:
            raise FileNotFoundError(path)
        return self.data[path]

    async def write(self, path: str, data: bytes, content_type: str = "text/plain") -> None:  # noqa: ARG002
        self.data[path] = data

    async def list_objects(self, prefix: str) -> list[str]:
        return sorted(k for k in self.data if k.startswith(prefix))


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    """Records the prompt it received and returns a canned summary."""

    def __init__(self, content: str = "- Goal: ship enterprise tier") -> None:
        self._content = content
        self.calls: list[list] = []

    async def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):  # noqa: ARG002
        self.calls.append(messages)
        return _FakeLLMResponse(self._content)


def _working_key() -> str:
    return StoragePaths.agent_memory_working(_USER, _AGENT)


def _archive_key(name: str) -> str:
    return StoragePaths.agent_memory_working_archive_entry(_USER, _AGENT, name)


def _episodic_key(name: str) -> str:
    return StoragePaths.agent_memory_episodic_entry(_USER, _AGENT, name)


class TestExplicitSummary:
    @pytest.mark.asyncio
    async def test_raw_to_archive_summary_to_episodic(self):
        storage = _FakeStorage({_working_key(): b"R" * 500})
        result = await compact_working_memory(
            storage=storage,
            user_id=_USER,
            agent_id=_AGENT,
            llm=None,
            summary="Concise summary",
            session_label="ses",
        )

        assert result.working_context_replaced is True
        assert result.summary_generated is False
        # Raw snapshot lands in the working archive.
        assert b"R" * 500 in storage.data[_archive_key(result.archived_as)]
        # Distilled summary lands in episodic — and NOT the raw bytes (Q5 distinct).
        episodic = storage.data[_episodic_key(result.archived_as)]
        assert b"Concise summary" in episodic
        assert b"R" * 500 not in episodic
        # Live working context replaced with the summary.
        assert storage.data[_working_key()] == b"Concise summary"


class TestAutoGenerate:
    @pytest.mark.asyncio
    async def test_summary_generated_from_working_and_history(self):
        history = [
            {"role": "user", "content": "My goal is to ship the enterprise tier by September."},
            {"role": "agent", "content": "Got it — tracking that."},
        ]
        storage = _FakeStorage(
            {
                _working_key(): b"Existing working note.",
                StoragePaths.chat_history(_USER): json.dumps(history).encode(),
            }
        )
        llm = _FakeLLM(content="- Goal: ship enterprise tier by September")

        result = await compact_working_memory(
            storage=storage,
            user_id=_USER,
            agent_id=_AGENT,
            llm=llm,
            summary=None,
            session_label="auto",
        )

        assert result.working_context_replaced is True
        assert result.summary_generated is True
        assert len(llm.calls) == 1
        # The summariser saw both the working context and the recent history.
        user_msg = llm.calls[0][1].content
        assert "Existing working note." in user_msg
        assert "ship the enterprise tier" in user_msg
        # Episodic + working get the generated summary; archive keeps the raw.
        assert b"ship enterprise tier" in storage.data[_episodic_key(result.archived_as)]
        assert storage.data[_working_key()] == b"- Goal: ship enterprise tier by September"
        assert b"Existing working note." in storage.data[_archive_key(result.archived_as)]


class TestNoOp:
    @pytest.mark.asyncio
    async def test_noop_when_nothing_to_compact(self):
        storage = _FakeStorage()
        result = await compact_working_memory(
            storage=storage,
            user_id=_USER,
            agent_id=_AGENT,
            llm=None,
            summary=None,
        )
        assert result.working_context_replaced is False
        assert result.archived_as == ""
        # Nothing written.
        assert storage.data == {}
