# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.onboarding — Onboarding FSM for first-run experience (FR-ID-001).

Description
-----------
Implements the onboarding state machine:
  WELCOME → PERSONA → CHANNELS → WORKING_HOURS → PREFERENCES → POLICIES → DONE

State is persisted in ``profile.md`` YAML frontmatter under
``{user_id}/agents/{agent_id}/profile.md`` via ``StorageClient``.  The FSM
is resumable: if the user quits mid-state, the next session resumes from the
same state.

Design Patterns
---------------
- State Machine: ``OnboardingFSM`` transitions through ordered states.
- Strategy: Each state has its own system-prompt variant (loaded from
  ``gateway/prompts/onboarding/`` on first use).
- Graceful degradation: missing profile.md defaults to ``onboarding_complete: true``
  so existing users are never forced through onboarding (FR-ID-001 AC4).

Public API
----------
- OnboardingState: Enum of valid FSM states.
- OnboardingFSM: State machine + persistence helpers.
- OnboardingFSM.is_onboarding_needed(user_id, agent_id): Check if a user needs onboarding.
- OnboardingFSM.get_state(user_id, agent_id): Return current state.
- OnboardingFSM.advance(user_id, agent_id): Advance to next state.
- OnboardingFSM.complete(user_id, agent_id): Mark onboarding complete.
"""

from __future__ import annotations

import logging
from enum import Enum

import yaml

from graphclaw.infra.storage import StoragePaths

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class OnboardingState(str, Enum):
    """Valid states in the onboarding FSM (FR-ID-001)."""

    WELCOME = "WELCOME"
    PERSONA = "PERSONA"
    CHANNELS = "CHANNELS"
    WORKING_HOURS = "WORKING_HOURS"
    PREFERENCES = "PREFERENCES"
    POLICIES = "POLICIES"
    DONE = "DONE"


_ORDERED_STATES: list[OnboardingState] = [
    OnboardingState.WELCOME,
    OnboardingState.PERSONA,
    OnboardingState.CHANNELS,
    OnboardingState.WORKING_HOURS,
    OnboardingState.PREFERENCES,
    OnboardingState.POLICIES,
    OnboardingState.DONE,
]

_ACTIVE_STATES: set[OnboardingState] = {s for s in _ORDERED_STATES if s != OnboardingState.DONE}

# Per-state allowed tools (FR-ID-001 AC2 — tool allow-lists)
ONBOARDING_TOOL_ALLOWLIST: dict[OnboardingState, list[str]] = {
    OnboardingState.WELCOME: ["set_user_name", "set_agent_name"],
    OnboardingState.PERSONA: ["set_user_name", "set_user_persona", "set_agent_name"],
    OnboardingState.CHANNELS: ["add_user_identity", "set_user_persona"],
    OnboardingState.WORKING_HOURS: ["set_working_hours"],
    OnboardingState.PREFERENCES: ["set_preferences"],
    OnboardingState.POLICIES: ["seed_policy_from_template", "complete_onboarding"],
    OnboardingState.DONE: [],
}

# Default system prompt body (minimal fallback when prompt file not found)
_DEFAULT_PROMPTS: dict[OnboardingState, str] = {
    OnboardingState.WELCOME: (
        "You are greeting a brand-new user for the very first time. This is your only chance to make "
        "a great first impression — be warm, enthusiastic, and personal.\n\n"
        "In your opening message you MUST do ALL of the following in this order:\n"
        "1. Welcome the user with genuine warmth (2-3 sentences). Express excitement to work together.\n"
        "2. Ask for their name: 'First, what's your name?' (keep it short and friendly)\n"
        "3. Ask what they'd like to call you: 'And what would you like to call me? "
        "I'll go by whatever feels right to you — you can always change it later in Settings.'\n\n"
        "Do NOT ask about tasks, projects, or the task graph yet. Focus entirely on the personal "
        "greeting and the two questions above. Use a conversational, friendly tone — not formal or robotic."
    ),
    OnboardingState.PERSONA: ("Ask the user to describe their role and work style."),
    OnboardingState.CHANNELS: (
        "Ask which communication channels the user prefers (email, Telegram, WhatsApp…)."
    ),
    OnboardingState.WORKING_HOURS: ("Ask for the user's working hours and timezone."),
    OnboardingState.PREFERENCES: ("Ask about briefing style preferences and follow-up cadence."),
    OnboardingState.POLICIES: (
        "Explain delegation and escalation policies. Offer to seed defaults."
    ),
    OnboardingState.DONE: "",
}


# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------


class OnboardingFSM:
    """Onboarding FSM — state persisted in profile.md frontmatter (FR-ID-001).

    Parameters
    ----------
    storage:
        ``StorageClient`` implementation for reading/writing profile.md.
    """

    def __init__(self, storage: object) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def is_onboarding_needed(self, user_id: str, agent_id: str = "main") -> bool:
        """Return ``True`` if the user has not yet completed onboarding.

        Missing profile.md → defaults to ``onboarding_complete: true``
        (FR-ID-001 AC4 migration safety).
        """
        frontmatter = await self._load_frontmatter(user_id, agent_id)
        if frontmatter is None:
            # No profile.md → treat existing user as already onboarded (AC4)
            return False
        return not frontmatter.get("onboarding_complete", False)

    async def get_state(self, user_id: str, agent_id: str = "main") -> OnboardingState:
        """Return the current onboarding state for *user_id*."""
        frontmatter = await self._load_frontmatter(user_id, agent_id)
        if frontmatter is None:
            return OnboardingState.WELCOME
        raw_state = frontmatter.get("onboarding_state", OnboardingState.WELCOME.value)
        try:
            return OnboardingState(raw_state)
        except ValueError:
            return OnboardingState.WELCOME

    async def advance(self, user_id: str, agent_id: str = "main") -> OnboardingState:
        """Advance to the next state and persist it.

        Returns the new state.
        """
        current = await self.get_state(user_id, agent_id)
        current_idx = _ORDERED_STATES.index(current)
        if current_idx < len(_ORDERED_STATES) - 1:
            next_state = _ORDERED_STATES[current_idx + 1]
        else:
            next_state = OnboardingState.DONE

        await self._save_state(user_id, agent_id, next_state)
        return next_state

    async def complete(self, user_id: str, agent_id: str = "main") -> None:
        """Write ``onboarding_complete: true`` to profile.md (FR-ID-001 AC3)."""
        await self._save_state(user_id, agent_id, OnboardingState.DONE, mark_complete=True)

    def get_system_prompt(self, state: OnboardingState) -> str:
        """Return the system prompt body for *state*."""
        return _DEFAULT_PROMPTS.get(state, "")

    def get_allowed_tools(self, state: OnboardingState) -> list[str]:
        """Return the tool allow-list for *state*."""
        return ONBOARDING_TOOL_ALLOWLIST.get(state, [])

    def is_active_state(self, state: OnboardingState) -> bool:
        """Return ``True`` if *state* is an active (non-DONE) state."""
        return state in _ACTIVE_STATES

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_frontmatter(self, user_id: str, agent_id: str) -> dict | None:
        """Read profile.md and return its YAML frontmatter dict, or None if missing."""
        path = StoragePaths.agent_profile(user_id, agent_id)
        try:
            raw = await self._storage.read(path)
            content = raw.decode("utf-8", errors="replace")
            return _parse_frontmatter(content)
        except Exception:  # noqa: BLE001
            return None

    async def _save_state(
        self,
        user_id: str,
        agent_id: str,
        state: OnboardingState,
        mark_complete: bool = False,
    ) -> None:
        """Write onboarding state to profile.md frontmatter."""
        path = StoragePaths.agent_profile(user_id, agent_id)
        try:
            raw = await self._storage.read(path)
            content = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            content = ""

        frontmatter, body = _split_frontmatter(content)
        frontmatter["onboarding_state"] = state.value
        if mark_complete or state == OnboardingState.DONE:
            frontmatter["onboarding_complete"] = True

        new_content = _render_profile(frontmatter, body)
        await self._storage.write(path, new_content.encode("utf-8"), "text/markdown")


# ---------------------------------------------------------------------------
# Profile.md YAML helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> dict | None:
    """Parse YAML frontmatter from a ``--- ... ---`` block.

    Returns ``None`` when no frontmatter found (not the same as an empty dict).
    """
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """Split content into (frontmatter_dict, body_str)."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                fm = {}
            return fm, parts[2].lstrip("\n")
    return {}, content


def _render_profile(frontmatter: dict, body: str) -> str:
    """Render profile.md from frontmatter dict and markdown body."""
    fm_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    return f"---\n{fm_str}---\n\n{body}"
