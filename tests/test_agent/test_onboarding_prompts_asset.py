# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Asset-contract tests: the shipped onboarding.md satisfies the FSM (FR-ID-001).

Lives under test_agent (not tests/contract, which is schemathesis-gated) so it runs
in the normal unit suite.

Covers:
- onboarding.md exists in gateway/prompts and is non-trivial
- Every active OnboardingState has a ## section the loader can extract
- WELCOME section reflects its intent (asks for a name, warm/friendly)
- Seeding wires the file to system/prompts/onboarding.md
"""

from __future__ import annotations

import re
from pathlib import Path

import graphclaw
from graphclaw.agent.onboarding import OnboardingState

_PROMPTS_FILE = Path(graphclaw.__file__).parent / "gateway" / "prompts" / "onboarding.md"


def _section(content: str, state: str) -> str | None:
    match = re.search(
        rf"^##[ \t]+{re.escape(state)}[ \t]*\n(.*?)(?=\n---|\n##|\Z)",
        content,
        re.DOTALL | re.MULTILINE,
    )
    return match.group(1).strip() if match else None


def test_onboarding_file_exists_and_substantial():
    assert _PROMPTS_FILE.exists(), f"missing {_PROMPTS_FILE}"
    assert len(_PROMPTS_FILE.read_text(encoding="utf-8")) > 200


def test_all_active_states_have_sections():
    content = _PROMPTS_FILE.read_text(encoding="utf-8")
    for state in OnboardingState:
        if state == OnboardingState.DONE:
            continue  # DONE is optional / may be a trivial closer
        body = _section(content, state.value)
        assert body, f"missing or empty section for state {state.value}"


def test_welcome_section_reflects_intent():
    content = _PROMPTS_FILE.read_text(encoding="utf-8")
    welcome = (_section(content, "WELCOME") or "").lower()
    assert "name" in welcome
    assert "warm" in welcome or "friendly" in welcome


def test_seeding_references_onboarding_file():
    from graphclaw.gateway import seeding
    from graphclaw.infra.storage import StoragePaths

    src = Path(seeding.__file__).read_text(encoding="utf-8")
    assert "onboarding.md" in src
    assert StoragePaths.system_onboarding_prompts() == "system/prompts/onboarding.md"
