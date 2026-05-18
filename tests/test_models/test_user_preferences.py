# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_models.test_user_preferences — FR-GRAPH-005 acceptance tests.

Verifies:
  AC1: UserPreferences defaults include channel_stickiness_hours=48 and
       channel_stickiness_overrides={}.
  AC2: Per-channel stickiness overrides apply correctly.
  AC3: discoverability defaults to ORG_DEFAULT.
  AC4: preferred_channel defaults to 'email'.
"""

from __future__ import annotations

from graphclaw.models.enums import DiscoverabilityLevel
from graphclaw.models.nodes import UserPreferences


class TestUserPreferencesDefaults:
    def test_channel_stickiness_default(self) -> None:
        prefs = UserPreferences()
        assert prefs.channel_stickiness_hours == 48
        assert prefs.channel_stickiness_overrides == {}

    def test_discoverability_default(self) -> None:
        prefs = UserPreferences()
        assert prefs.discoverability == DiscoverabilityLevel.ORG_DEFAULT

    def test_preferred_channel_default(self) -> None:
        prefs = UserPreferences()
        assert prefs.preferred_channel == "email"

    def test_round_trip(self) -> None:
        prefs = UserPreferences(
            discoverability=DiscoverabilityLevel.HIDDEN,
            channel_stickiness_hours=72,
            channel_stickiness_overrides={"email": 168, "telegram": 24},
            preferred_channel="telegram",
        )
        d = prefs.model_dump(mode="json")
        restored = UserPreferences(**d)
        assert restored.discoverability == DiscoverabilityLevel.HIDDEN
        assert restored.channel_stickiness_hours == 72
        assert restored.channel_stickiness_overrides["email"] == 168
        assert restored.channel_stickiness_overrides["telegram"] == 24
        assert restored.preferred_channel == "telegram"


class TestUserPreferencesOverrides:
    def test_per_channel_override(self) -> None:
        """Channel-specific override supercedes the global value."""
        prefs = UserPreferences(
            channel_stickiness_hours=48,
            channel_stickiness_overrides={"email": 168},
        )
        assert prefs.channel_stickiness_overrides.get("email") == 168
        # Telegram uses global default (not overridden).
        assert prefs.channel_stickiness_overrides.get("telegram") is None

    def test_discoverable(self) -> None:
        prefs = UserPreferences(discoverability=DiscoverabilityLevel.DISCOVERABLE)
        assert prefs.discoverability == DiscoverabilityLevel.DISCOVERABLE
