# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for MainOrchestrator tiered-memory tools (Wave Tiered-Memory).

Covers:
- read_memory: returns semantic topic content; error on missing topic
- recall_episodic: date match, keyword match, empty store
- compact_memory: archives working context → episodic + replaces working memory
- estimate_memory: per-tier char counts + utilization %
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from graphclaw.agent.main_orchestrator import MainOrchestrator
from graphclaw.infra.storage import StoragePaths

_USER = "USER-test"
_AGENT = "main"


class _FakeStorage:
    """In-memory StorageClient stand-in (read/write/list_objects)."""

    def __init__(self, data: dict[str, bytes] | None = None) -> None:
        self.data: dict[str, bytes] = dict(data or {})

    async def read(self, path: str) -> bytes:
        if path not in self.data:
            raise FileNotFoundError(path)
        return self.data[path]

    async def write(self, path: str, data: bytes, content_type: str = "text/plain") -> None:  # noqa: ARG002
        self.data[path] = data

    async def delete(self, path: str) -> None:
        self.data.pop(path, None)

    async def list_objects(self, prefix: str) -> list[str]:
        return sorted(k for k in self.data if k.startswith(prefix))

    async def exists(self, path: str) -> bool:
        return path in self.data


def _make_orchestrator(storage: _FakeStorage | None) -> MainOrchestrator:
    return MainOrchestrator(
        graph_repo=MagicMock(),
        scoring_engine=MagicMock(),
        state_machine=MagicMock(),
        storage_client=storage,
        agent_id=_AGENT,
    )


def _episodic_key(name: str) -> str:
    return StoragePaths.agent_memory_episodic_entry(_USER, _AGENT, name)


def _working_archive_key(name: str) -> str:
    return StoragePaths.agent_memory_working_archive_entry(_USER, _AGENT, name)


# ---------------------------------------------------------------------------
# read_memory
# ---------------------------------------------------------------------------


class TestReadMemory:
    @pytest.mark.asyncio
    async def test_read_memory_returns_topic_content(self):
        path = StoragePaths.agent_memory_semantic_topic(_USER, _AGENT, "team-roles")
        storage = _FakeStorage({path: b"Alice is PM. Bob is backend."})
        orch = _make_orchestrator(storage)

        result = await orch._execute_tool(_USER, "read_memory", {"topic": "team-roles"})

        assert result["topic"] == "team-roles"
        assert "Alice is PM" in result["content"]

    @pytest.mark.asyncio
    async def test_read_memory_topic_not_found(self):
        storage = _FakeStorage()
        orch = _make_orchestrator(storage)

        result = await orch._execute_tool(_USER, "read_memory", {"topic": "missing"})

        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_read_memory_empty_topic(self):
        orch = _make_orchestrator(_FakeStorage())
        result = await orch._execute_tool(_USER, "read_memory", {"topic": "  "})
        assert "error" in result


# ---------------------------------------------------------------------------
# recall_episodic
# ---------------------------------------------------------------------------


class TestRecallEpisodic:
    @pytest.mark.asyncio
    async def test_recall_episodic_date_match(self):
        storage = _FakeStorage(
            {
                _episodic_key("2026-06-20-compact-sprint.md"): b"Sprint planning notes",
                _episodic_key("2026-06-21-compact-review.md"): b"Design review notes",
                _episodic_key("2026-06-22-compact-retro.md"): b"Retro notes",
            }
        )
        orch = _make_orchestrator(storage)

        result = await orch._execute_tool(_USER, "recall_episodic", {"query": "2026-06-21"})

        assert result["matches"]
        assert result["matches"][0]["name"] == "2026-06-21-compact-review.md"

    @pytest.mark.asyncio
    async def test_recall_episodic_keyword_match(self):
        storage = _FakeStorage(
            {
                _episodic_key("2026-06-20-compact-sprint.md"): b"Discussed Q3 OKRs",
                _episodic_key("2026-06-21-compact-review.md"): b"Reviewed the API redesign",
            }
        )
        orch = _make_orchestrator(storage)

        result = await orch._execute_tool(_USER, "recall_episodic", {"query": "sprint", "limit": 2})

        # The entry whose filename contains 'sprint' should rank first.
        assert result["matches"][0]["name"] == "2026-06-20-compact-sprint.md"

    @pytest.mark.asyncio
    async def test_recall_episodic_content_keyword_boost(self):
        storage = _FakeStorage(
            {
                _episodic_key("2026-06-20-compact-aaa.md"): b"Nothing relevant here.",
                _episodic_key("2026-06-19-compact-bbb.md"): b"We discussed the redesign at length.",
            }
        )
        orch = _make_orchestrator(storage)

        result = await orch._execute_tool(_USER, "recall_episodic", {"query": "redesign"})

        assert result["matches"][0]["name"] == "2026-06-19-compact-bbb.md"

    @pytest.mark.asyncio
    async def test_recall_episodic_excludes_archive(self):
        archive_prefix = StoragePaths.agent_memory_episodic_archive_prefix(_USER, _AGENT)
        storage = _FakeStorage(
            {
                _episodic_key("2026-06-20-compact-sprint.md"): b"active entry",
                f"{archive_prefix}2026-01-01-old.md": b"archived entry",
            }
        )
        orch = _make_orchestrator(storage)

        result = await orch._execute_tool(_USER, "recall_episodic", {"query": "entry"})

        names = [m["name"] for m in result["matches"]]
        assert "2026-06-20-compact-sprint.md" in names
        assert "2026-01-01-old.md" not in names

    @pytest.mark.asyncio
    async def test_recall_episodic_empty(self):
        orch = _make_orchestrator(_FakeStorage())
        result = await orch._execute_tool(_USER, "recall_episodic", {"query": "anything"})
        assert result["matches"] == []


# ---------------------------------------------------------------------------
# compact_memory
# ---------------------------------------------------------------------------


class TestCompactMemory:
    @pytest.mark.asyncio
    async def test_compact_memory_archives_and_replaces(self):
        working_path = StoragePaths.agent_memory_working(_USER, _AGENT)
        storage = _FakeStorage({working_path: b"X" * 1000})
        orch = _make_orchestrator(storage)

        result = await orch._execute_tool(
            _USER,
            "compact_memory",
            {"summary": "Short summary", "session_label": "test"},
        )

        assert result["working_context_replaced"] is True
        assert result["context_before_chars"] == 1000
        assert result["context_after_chars"] == len("Short summary")
        assert result["reduction_pct"] > 0
        # Working memory replaced with the summary.
        assert storage.data[working_path] == b"Short summary"
        # Raw verbatim snapshot goes to the working archive (audit trail).
        archive_key = _working_archive_key(result["archived_as"])
        assert archive_key in storage.data
        assert b"X" * 1000 in storage.data[archive_key]
        # Episodic gets the distilled summary, not the raw context.
        episodic_key = _episodic_key(result["archived_as"])
        assert episodic_key in storage.data
        assert b"Short summary" in storage.data[episodic_key]
        assert b"X" * 1000 not in storage.data[episodic_key]

    @pytest.mark.asyncio
    async def test_compact_memory_noop_when_nothing_to_compact(self):
        # No summary supplied, no LLM, empty working context + no history → no-op
        # (writes nothing rather than producing empty archive files).
        orch = _make_orchestrator(_FakeStorage())
        result = await orch._execute_tool(_USER, "compact_memory", {"summary": ""})
        assert result["working_context_replaced"] is False


# ---------------------------------------------------------------------------
# estimate_memory
# ---------------------------------------------------------------------------


class TestEstimateMemory:
    @pytest.mark.asyncio
    async def test_estimate_memory_returns_breakdown(self):
        working_path = StoragePaths.agent_memory_working(_USER, _AGENT)
        semantic_path = StoragePaths.agent_memory_semantic_topic(_USER, _AGENT, "roles")
        storage = _FakeStorage(
            {
                working_path: b"a" * 100,
                _episodic_key("2026-06-20-compact-x.md"): b"b" * 50,
                semantic_path: b"c" * 25,
            }
        )
        orch = _make_orchestrator(storage)

        result = await orch._execute_tool(_USER, "estimate_memory", {})

        assert result["working_chars"] == 100
        assert result["episodic_chars"] == 50
        assert result["semantic_chars"] == 25
        assert result["total_chars"] == 175
        assert result["utilization_pct"] == round(175 / result["budget_chars"] * 100, 1)

    @pytest.mark.asyncio
    async def test_estimate_memory_empty(self):
        orch = _make_orchestrator(_FakeStorage())
        result = await orch._execute_tool(_USER, "estimate_memory", {})
        assert result["total_chars"] == 0
        assert result["utilization_pct"] == 0.0
