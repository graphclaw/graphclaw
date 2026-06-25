# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for externalized onboarding prompts and fail-fast loading (FR-ID-001).

Covers:
- OnboardingFSM.get_system_prompt loads sections from onboarding.md in storage
- 1-hour in-process TTL cache (single read across states; reload after expiry)
- Fail-fast on missing file (FileNotFoundError) and missing section (ValueError)
"""

from __future__ import annotations

import textwrap

import pytest

from graphclaw.agent.onboarding import OnboardingFSM, OnboardingState
from graphclaw.infra.storage import StoragePaths

_ONBOARDING_MD = textwrap.dedent(
    """\
    # Onboarding Prompts

    ## WELCOME
    Welcome the user warmly and ask their name.

    ---

    ## PERSONA
    Ask the user about their role and work style.

    ---

    ## DONE
    Onboarding complete.
    """
)


class _CountingStorage:
    """Minimal in-memory storage that counts read() calls."""

    def __init__(self, data: dict[str, bytes]) -> None:
        self._data = data
        self.read_count = 0

    async def read(self, path: str) -> bytes:
        self.read_count += 1
        if path not in self._data:
            raise FileNotFoundError(path)
        return self._data[path]


def _storage_with_prompts(content: str = _ONBOARDING_MD) -> _CountingStorage:
    return _CountingStorage({StoragePaths.system_onboarding_prompts(): content.encode("utf-8")})


class TestOnboardingPromptLoading:
    async def test_loads_section_from_storage(self):
        fsm = OnboardingFSM(_storage_with_prompts())
        prompt = await fsm.get_system_prompt(OnboardingState.WELCOME)
        assert prompt == "Welcome the user warmly and ask their name."

    async def test_file_cached_across_calls_and_states(self):
        storage = _storage_with_prompts()
        fsm = OnboardingFSM(storage)

        await fsm.get_system_prompt(OnboardingState.WELCOME)
        await fsm.get_system_prompt(OnboardingState.WELCOME)
        # Different state reuses the same cached file — still one storage read.
        persona = await fsm.get_system_prompt(OnboardingState.PERSONA)

        assert persona == "Ask the user about their role and work style."
        assert storage.read_count == 1

    async def test_cache_reloads_after_ttl_expiry(self, monkeypatch):
        import graphclaw.agent.onboarding as onboarding_mod

        storage = _storage_with_prompts()
        fsm = OnboardingFSM(storage)
        await fsm.get_system_prompt(OnboardingState.WELCOME)
        assert storage.read_count == 1

        # Force the cached entry past its TTL.
        monkeypatch.setattr(onboarding_mod, "_PROMPT_CACHE_TTL_SECONDS", -1.0)
        await fsm.get_system_prompt(OnboardingState.WELCOME)
        assert storage.read_count == 2

    async def test_missing_file_raises_filenotfound(self):
        fsm = OnboardingFSM(_CountingStorage({}))
        with pytest.raises(FileNotFoundError):
            await fsm.get_system_prompt(OnboardingState.WELCOME)

    async def test_missing_section_raises_valueerror(self):
        # File present but the requested state's section is absent.
        partial = "# Onboarding Prompts\n\n## WELCOME\nHi.\n"
        fsm = OnboardingFSM(_storage_with_prompts(partial))
        with pytest.raises(ValueError, match="WORKING_HOURS"):
            await fsm.get_system_prompt(OnboardingState.WORKING_HOURS)

    async def test_no_storage_raises_runtimeerror(self):
        fsm = OnboardingFSM(storage=None)
        with pytest.raises(RuntimeError):
            await fsm.get_system_prompt(OnboardingState.WELCOME)
