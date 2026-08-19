# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_agent.test_episodic_recall — Tests for graphclaw.agent.episodic_recall.

Covers the keyword+recency scoring extracted from
MainOrchestrator._tool_recall_episodic so it can be shared with
SubAgentRunner's system-prompt assembly (see Wave Model-Routing, Step 14).
"""

from __future__ import annotations

import pytest

from graphclaw.agent.episodic_recall import dumps_matches, recall_episodic
from graphclaw.infra.storage import StoragePaths

_USER = "usr-001"
_AGENT = "main"


class _FakeStorage:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self._objects = dict(objects or {})

    async def list_objects(self, prefix: str) -> list[str]:
        return [k for k in self._objects if k.startswith(prefix)]

    async def read(self, path: str) -> bytes:
        if path not in self._objects:
            raise FileNotFoundError(path)
        return self._objects[path]


def _episodic_key(name: str) -> str:
    return f"{StoragePaths.agent_memory_episodic_prefix(_USER, _AGENT)}{name}"


def _archive_key(name: str) -> str:
    return f"{StoragePaths.agent_memory_episodic_archive_prefix(_USER, _AGENT)}{name}"


class TestRecallEpisodic:
    @pytest.mark.asyncio
    async def test_empty_query_returns_no_matches(self):
        storage = _FakeStorage({_episodic_key("2026-01-01-standup.md"): b"notes"})
        matches = await recall_episodic(storage, user_id=_USER, agent_id=_AGENT, query="  ")
        assert matches == []

    @pytest.mark.asyncio
    async def test_no_storage_returns_no_matches(self):
        matches = await recall_episodic(None, user_id=_USER, agent_id=_AGENT, query="standup")
        assert matches == []

    @pytest.mark.asyncio
    async def test_no_entries_returns_no_matches(self):
        storage = _FakeStorage({})
        matches = await recall_episodic(storage, user_id=_USER, agent_id=_AGENT, query="standup")
        assert matches == []

    @pytest.mark.asyncio
    async def test_filename_exact_match_scores_highest(self):
        storage = _FakeStorage(
            {
                _episodic_key("2026-01-01-standup.md"): b"General notes.",
                _episodic_key("2026-01-02-budget-review.md"): b"General notes.",
            }
        )
        matches = await recall_episodic(
            storage, user_id=_USER, agent_id=_AGENT, query="budget review"
        )
        assert matches[0].name == "2026-01-02-budget-review.md"

    @pytest.mark.asyncio
    async def test_content_keyword_boosts_score(self):
        storage = _FakeStorage(
            {
                _episodic_key("2026-01-01-a.md"): b"Discussed the quarterly budget in detail.",
                _episodic_key("2026-01-02-b.md"): b"Unrelated content about lunch plans.",
            }
        )
        matches = await recall_episodic(storage, user_id=_USER, agent_id=_AGENT, query="budget")
        assert matches[0].name == "2026-01-01-a.md"

    @pytest.mark.asyncio
    async def test_archive_entries_excluded(self):
        storage = _FakeStorage(
            {
                _episodic_key("2026-01-01-standup.md"): b"active",
                _archive_key("2026-01-01-standup.md"): b"archived",
            }
        )
        matches = await recall_episodic(storage, user_id=_USER, agent_id=_AGENT, query="standup")
        assert len(matches) == 1
        assert matches[0].content == "active"

    @pytest.mark.asyncio
    async def test_uses_stricter_startswith_not_substring_check(self):
        """Regression: SubAgentRunner used to filter with
        `archive_prefix not in k`, a substring check that could
        misclassify an active key that merely *contains* the archive
        prefix text elsewhere in its path. startswith is correct."""
        storage = _FakeStorage(
            {
                _episodic_key("2026-01-01-archive-notes.md"): b"active entry mentioning archive",
            }
        )
        matches = await recall_episodic(
            storage, user_id=_USER, agent_id=_AGENT, query="archive notes"
        )
        assert len(matches) == 1

    @pytest.mark.asyncio
    async def test_limit_caps_returned_matches(self):
        storage = _FakeStorage(
            {_episodic_key(f"2026-01-0{i}-note.md"): b"note content" for i in range(1, 6)}
        )
        matches = await recall_episodic(
            storage, user_id=_USER, agent_id=_AGENT, query="note", limit=2
        )
        assert len(matches) == 2

    @pytest.mark.asyncio
    async def test_ties_break_toward_newer_filenames(self):
        """All-zero-score entries (no keyword/content hits) still sort by
        filename descending — since episodic files are date-prefixed, this
        is a free 'newest first' fallback with no extra code path."""
        storage = _FakeStorage(
            {
                _episodic_key("2026-01-01-a.md"): b"nothing relevant",
                _episodic_key("2026-03-01-b.md"): b"nothing relevant",
            }
        )
        matches = await recall_episodic(
            storage, user_id=_USER, agent_id=_AGENT, query="zzz_no_match_at_all", limit=2
        )
        assert matches[0].name == "2026-03-01-b.md"

    @pytest.mark.asyncio
    async def test_max_chars_drops_entries_that_would_exceed_budget(self):
        storage = _FakeStorage(
            {
                _episodic_key("2026-01-01-budget-a.md"): b"x" * 100,
                _episodic_key("2026-01-02-budget-b.md"): b"y" * 100,
            }
        )
        matches = await recall_episodic(
            storage, user_id=_USER, agent_id=_AGENT, query="budget", limit=5, max_chars=150
        )
        assert len(matches) == 1

    @pytest.mark.asyncio
    async def test_max_chars_does_not_truncate_mid_content(self):
        """An entry that would exceed the budget is dropped whole, never
        truncated — a half-cut episodic memory would mislead the model."""
        storage = _FakeStorage({_episodic_key("2026-01-01-budget-a.md"): b"z" * 100})
        matches = await recall_episodic(
            storage, user_id=_USER, agent_id=_AGENT, query="budget", max_chars=50
        )
        assert matches == []

    @pytest.mark.asyncio
    async def test_storage_failure_returns_empty_not_raises(self):
        class _BrokenStorage:
            async def list_objects(self, prefix: str) -> list[str]:
                raise RuntimeError("storage down")

        matches = await recall_episodic(
            _BrokenStorage(), user_id=_USER, agent_id=_AGENT, query="anything"
        )
        assert matches == []

    @pytest.mark.asyncio
    async def test_delegated_instructions_as_free_text_query(self):
        """The intended sub-agent usage: pass the delegation instructions
        (plus task_id) directly as the query — no extra classification step."""
        storage = _FakeStorage(
            {
                _episodic_key("2026-01-01-priya-budget.md"): b"Notes on Priya's Q3 budget review.",
                _episodic_key("2026-01-02-unrelated.md"): b"Notes about the office party.",
            }
        )
        matches = await recall_episodic(
            storage,
            user_id=_USER,
            agent_id=_AGENT,
            query="Write an email to Priya about the Q3 budget review TSK-AB-0001-DEL",
        )
        assert matches[0].name == "2026-01-01-priya-budget.md"


class TestDumpsMatches:
    def test_dumps_matches_shape(self):
        from graphclaw.agent.episodic_recall import EpisodicMatch

        matches = [EpisodicMatch(name="a.md", content="hi", score=5.0)]
        assert dumps_matches(matches) == [{"name": "a.md", "content": "hi", "score": 5.0}]

    def test_dumps_matches_empty_list(self):
        assert dumps_matches([]) == []
