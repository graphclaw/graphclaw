# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for ProfileSynthesizer behavioral distillation (FR-ID-001).

Covers:
- synthesize_from_onboarding renders Working Style / Key Preferences from LLM JSON
- Fail-fast on LLM error, empty/invalid response, and empty extraction
- PII scrubbing as defense-in-depth
- merge_profile_body replaces behavioral sections while preserving others
- render_profile_sections formatting
"""

from __future__ import annotations

import pytest

from graphclaw.agent.profile_synthesis import (
    ProfileSynthesisError,
    ProfileSynthesizer,
    merge_profile_body,
    render_profile_sections,
)


class _FakeLLM:
    """Fake LLM returning fixed content, or raising when configured to."""

    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.calls: list[dict] = []

    async def complete(
        self, messages, *, model=None, max_tokens=None, temperature=None, tools=None
    ):  # noqa: ARG002
        self.calls.append({"model": model, "messages": messages})
        if self._error is not None:
            raise self._error

        class _Resp:
            content = self._content

        return _Resp()


_CONVO = [
    {"role": "agent", "content": "What's your name?"},
    {"role": "user", "content": "Alex. I like brief updates and only urgent interruptions."},
]


class TestSynthesizeFromOnboarding:
    async def test_renders_both_sections(self):
        llm = _FakeLLM(
            '{"working_style": ["Keep updates brief", "Interrupt only for urgent items"], '
            '"preferences": ["Friendly but professional tone", "Surface blockers first"]}'
        )
        synth = ProfileSynthesizer(llm)
        body = await synth.synthesize_from_onboarding("U1", "main", _CONVO)

        assert "## Working Style" in body
        assert "- Keep updates brief" in body
        assert "## Key Preferences" in body
        assert "- Surface blockers first" in body

    async def test_defers_to_llm_role_classify_default_when_unset(self):
        """With no explicit model, ProfileSynthesizer must pass model=None so
        the underlying LLMClient's LLMRole.CLASSIFY routing default applies —
        not a hardcoded literal."""
        llm = _FakeLLM('{"working_style": ["x"], "preferences": ["y"]}')
        synth = ProfileSynthesizer(llm)
        await synth.synthesize_from_onboarding("U1", "main", _CONVO)
        assert llm.calls[0]["model"] is None

    async def test_explicit_model_override_passes_through(self):
        llm = _FakeLLM('{"working_style": ["x"], "preferences": ["y"]}')
        synth = ProfileSynthesizer(llm, model="ollama/qwen2.5:1.5b")
        await synth.synthesize_from_onboarding("U1", "main", _CONVO)
        assert llm.calls[0]["model"] == "ollama/qwen2.5:1.5b"

    async def test_strips_markdown_code_fence(self):
        llm = _FakeLLM('```json\n{"working_style": ["a"], "preferences": ["b"]}\n```')
        synth = ProfileSynthesizer(llm)
        body = await synth.synthesize_from_onboarding("U1", "main", _CONVO)
        assert "- a" in body and "- b" in body

    async def test_llm_failure_is_failfast(self):
        synth = ProfileSynthesizer(_FakeLLM(error=RuntimeError("timeout")))
        with pytest.raises(ProfileSynthesisError):
            await synth.synthesize_from_onboarding("U1", "main", _CONVO)

    async def test_invalid_json_is_failfast(self):
        synth = ProfileSynthesizer(_FakeLLM("not json at all"))
        with pytest.raises(ProfileSynthesisError):
            await synth.synthesize_from_onboarding("U1", "main", _CONVO)

    async def test_empty_extraction_is_failfast(self):
        synth = ProfileSynthesizer(_FakeLLM('{"working_style": [], "preferences": []}'))
        with pytest.raises(ProfileSynthesisError):
            await synth.synthesize_from_onboarding("U1", "main", _CONVO)

    async def test_no_conversation_is_failfast(self):
        synth = ProfileSynthesizer(_FakeLLM('{"working_style": ["x"], "preferences": ["y"]}'))
        with pytest.raises(ProfileSynthesisError):
            await synth.synthesize_from_onboarding("U1", "main", [])

    async def test_pii_is_scrubbed(self):
        llm = _FakeLLM('{"working_style": ["Call me at 555-123-4567"], "preferences": ["fine"]}')
        synth = ProfileSynthesizer(llm)
        body = await synth.synthesize_from_onboarding("U1", "main", _CONVO)
        assert "555-123-4567" not in body
        assert "[PHONE]" in body


class TestMergeProfileBody:
    def test_replaces_behavioral_sections_preserves_others(self):
        existing = (
            "## Identity\n\n- keep me\n\n"
            "## Working Style\n\n- old style\n\n"
            "## Key Preferences\n\n- old pref\n"
        )
        new = "## Working Style\n\n- new style\n\n## Key Preferences\n\n- new pref"
        merged = merge_profile_body(existing, new)

        assert "- new style" in merged
        assert "- new pref" in merged
        assert "- old style" not in merged
        assert "- old pref" not in merged
        assert "## Identity" in merged
        assert "- keep me" in merged
        # New behavioral sections come first.
        assert merged.index("## Working Style") < merged.index("## Identity")

    def test_empty_existing_body_returns_new(self):
        new = "## Working Style\n\n- only new"
        assert merge_profile_body("", new) == new


class TestRenderProfileSections:
    def test_renders_only_nonempty_sections(self):
        assert render_profile_sections([], ["p1"]) == "## Key Preferences\n\n- p1"
        assert render_profile_sections(["w1"], []) == "## Working Style\n\n- w1"
        assert render_profile_sections([], []) == ""
