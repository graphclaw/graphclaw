# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for post-onboarding profile updates and completion synthesis (FR-ID-001).

Covers:
- update_profile_from_conversation appends to / creates the right section
- Behavioral-only routing (no structured data); profile-not-found + empty guards
- complete_onboarding synthesizes the profile then marks complete (fail-fast)
"""

from __future__ import annotations

import yaml

from graphclaw.agent.tools.onboarding_tools import complete_onboarding
from graphclaw.agent.tools.profile_tools import update_profile_from_conversation
from graphclaw.infra.storage import StoragePaths

_USER = "USER-test"
_PROFILE_PATH = StoragePaths.agent_profile(_USER, "main")


class _MemStorage:
    def __init__(self, data: dict[str, bytes] | None = None) -> None:
        self._data = dict(data or {})

    async def read(self, path: str) -> bytes:
        if path not in self._data:
            raise FileNotFoundError(path)
        return self._data[path]

    async def write(self, path: str, data: bytes, content_type: str = "text/plain") -> None:  # noqa: ARG002
        self._data[path] = data

    def text(self, path: str) -> str:
        return self._data[path].decode("utf-8")


class _FakeLLM:
    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self._content = content
        self._error = error

    async def complete(
        self, messages, *, model=None, max_tokens=None, temperature=None, tools=None
    ):  # noqa: ARG002
        if self._error is not None:
            raise self._error

        class _Resp:
            content = self._content

        return _Resp()


def _profile(frontmatter: dict, body: str) -> bytes:
    fm = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    return f"---\n{fm}---\n\n{body}".encode()


# ---------------------------------------------------------------------------
# update_profile_from_conversation
# ---------------------------------------------------------------------------


class TestUpdateProfile:
    async def test_appends_to_existing_section(self):
        storage = _MemStorage(
            {_PROFILE_PATH: _profile({"agent_name": "Max"}, "## Key Preferences\n\n- warm tone\n")}
        )
        result = await update_profile_from_conversation(
            _USER, instruction="only urgent interruptions", section="preferences", storage=storage
        )
        assert result["updated"] is True
        text = storage.text(_PROFILE_PATH)
        assert "- warm tone" in text
        assert "- only urgent interruptions" in text
        # Frontmatter preserved.
        assert "agent_name: Max" in text

    async def test_creates_missing_section(self):
        storage = _MemStorage({_PROFILE_PATH: _profile({}, "## Identity\n\n- keep\n")})
        result = await update_profile_from_conversation(
            _USER, instruction="be concise", section="working_style", storage=storage
        )
        assert result["updated"] is True
        text = storage.text(_PROFILE_PATH)
        assert "## Working Style" in text
        assert "- be concise" in text
        assert "## Identity" in text

    async def test_invalid_section_defaults_to_preferences(self):
        storage = _MemStorage({_PROFILE_PATH: _profile({}, "body\n")})
        result = await update_profile_from_conversation(
            _USER, instruction="x", section="not_a_section", storage=storage
        )
        assert result["updated"] is True
        assert result["section"] == "preferences"

    async def test_empty_instruction_rejected(self):
        storage = _MemStorage({_PROFILE_PATH: _profile({}, "body\n")})
        result = await update_profile_from_conversation(_USER, instruction="  ", storage=storage)
        assert result["updated"] is False

    async def test_profile_not_found(self):
        result = await update_profile_from_conversation(
            _USER, instruction="be concise", storage=_MemStorage()
        )
        assert result["updated"] is False
        assert "complete onboarding" in result["error"].lower()


# ---------------------------------------------------------------------------
# complete_onboarding (synthesis + completion)
# ---------------------------------------------------------------------------

_HISTORY = [
    {"role": "user", "content": "I'm Alex; I like brief updates, urgent-only interruptions."},
]


class TestCompleteOnboarding:
    async def test_synthesizes_then_marks_complete(self):
        storage = _MemStorage(
            {_PROFILE_PATH: _profile({"onboarding_state": "POLICIES"}, "## Identity\n\n- Alex\n")}
        )
        llm = _FakeLLM(
            '{"working_style": ["Brief updates"], "preferences": ["Urgent-only interruptions"]}'
        )
        result = await complete_onboarding(
            _USER, storage=storage, llm_client=llm, conversation_history=_HISTORY
        )
        assert result == {"completed": True, "profile_synthesized": True}

        text = storage.text(_PROFILE_PATH)
        assert "onboarding_complete: true" in text
        assert "## Working Style" in text
        assert "- Brief updates" in text
        assert "## Identity" in text  # preserved

    async def test_synthesis_failure_blocks_completion(self):
        storage = _MemStorage(
            {_PROFILE_PATH: _profile({"onboarding_state": "POLICIES"}, "## Identity\n\n- Alex\n")}
        )
        llm = _FakeLLM(error=RuntimeError("LLM down"))
        result = await complete_onboarding(
            _USER, storage=storage, llm_client=llm, conversation_history=_HISTORY
        )
        assert result["completed"] is False
        # Onboarding NOT marked complete; user can retry.
        text = storage.text(_PROFILE_PATH)
        assert "onboarding_complete: true" not in text

    async def test_completes_without_synthesis_when_no_llm(self):
        storage = _MemStorage({_PROFILE_PATH: _profile({"onboarding_state": "POLICIES"}, "body\n")})
        result = await complete_onboarding(_USER, storage=storage)
        assert result == {"completed": True, "profile_synthesized": False}
        assert "onboarding_complete: true" in storage.text(_PROFILE_PATH)

    async def test_storage_required(self):
        result = await complete_onboarding(_USER)
        assert result["completed"] is False
