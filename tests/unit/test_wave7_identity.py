# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for Wave 7 identity features (FR-ID-001..005).

Covers:
- OnboardingFSM state transitions, persistence, tool allow-lists (FR-ID-001)
- UserResolver candidate ranking, exact/fuzzy confidence (FR-ID-002)
- CreatePersonFSM disambiguate → pick existing (alias-drift) / new (FR-ID-003)
- ResourceMerger alias dedup, intelligence merge, tombstone (FR-ID-004)
- register_alias dedup prevention (FR-ID-005)
- IdentityDriftDetector drift detection and auto-register (FR-RES-004)
"""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# FR-ID-001: OnboardingFSM
# ---------------------------------------------------------------------------


class TestOnboardingFSM:
    """OnboardingFSM state transitions and profile.md persistence."""

    def _make_storage(self, initial_content: str = "") -> MagicMock:
        storage = MagicMock()
        storage.read = AsyncMock(return_value=initial_content.encode("utf-8"))
        storage.write = AsyncMock()
        storage.exists = AsyncMock(return_value=bool(initial_content))
        return storage

    def _make_storage_not_found(self) -> MagicMock:
        storage = MagicMock()
        storage.read = AsyncMock(side_effect=FileNotFoundError("not found"))
        storage.write = AsyncMock()
        storage.exists = AsyncMock(return_value=False)
        return storage

    @pytest.mark.asyncio
    async def test_is_onboarding_needed_missing_file_returns_false(self):
        """Missing profile.md → onboarding NOT needed (migration safety AC4)."""
        from graphclaw.agent.onboarding import OnboardingFSM

        storage = self._make_storage_not_found()
        fsm = OnboardingFSM(storage)
        result = await fsm.is_onboarding_needed("user-1", "main")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_onboarding_needed_fresh_profile_returns_true(self):
        """Profile with onboarding_state WELCOME → needed."""
        from graphclaw.agent.onboarding import OnboardingFSM

        content = textwrap.dedent("""\
            ---
            onboarding_state: WELCOME
            onboarding_complete: false
            ---
            # Profile
        """)
        storage = self._make_storage(content)
        fsm = OnboardingFSM(storage)
        result = await fsm.is_onboarding_needed("user-1", "main")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_onboarding_needed_complete_returns_false(self):
        """Profile with onboarding_complete: true → NOT needed."""
        from graphclaw.agent.onboarding import OnboardingFSM

        content = textwrap.dedent("""\
            ---
            onboarding_state: DONE
            onboarding_complete: true
            ---
            # Profile
        """)
        storage = self._make_storage(content)
        fsm = OnboardingFSM(storage)
        result = await fsm.is_onboarding_needed("user-1", "main")
        assert result is False

    @pytest.mark.asyncio
    async def test_advance_state_progresses(self):
        """advance() writes the next state into profile.md."""
        from graphclaw.agent.onboarding import OnboardingFSM, OnboardingState

        content = textwrap.dedent("""\
            ---
            onboarding_state: WELCOME
            onboarding_complete: false
            ---
        """)
        storage = self._make_storage(content)
        fsm = OnboardingFSM(storage)
        new_state = await fsm.advance("user-1", "main")
        assert new_state == OnboardingState.PERSONA
        assert storage.write.called

    @pytest.mark.asyncio
    async def test_complete_sets_flag(self):
        """complete() writes onboarding_complete: true."""
        from graphclaw.agent.onboarding import OnboardingFSM

        content = textwrap.dedent("""\
            ---
            onboarding_state: DONE
            onboarding_complete: false
            ---
        """)
        storage = self._make_storage(content)
        fsm = OnboardingFSM(storage)
        await fsm.complete("user-1", "main")
        call_args = storage.write.call_args
        written = call_args[0][1].decode("utf-8")
        assert "onboarding_complete: true" in written

    def test_get_allowed_tools_welcome_state(self):
        """WELCOME state only allows set_user_name."""
        from graphclaw.agent.onboarding import OnboardingFSM, OnboardingState

        storage = MagicMock()
        fsm = OnboardingFSM(storage)
        tools = fsm.get_allowed_tools(OnboardingState.WELCOME)
        assert "set_user_name" in tools

    def test_get_system_prompt_returns_string(self):
        """Active states return non-empty prompts; DONE may be empty."""
        from graphclaw.agent.onboarding import OnboardingFSM, OnboardingState

        storage = MagicMock()
        fsm = OnboardingFSM(storage)
        active_states = [s for s in OnboardingState if s != OnboardingState.DONE]
        for state in active_states:
            prompt = fsm.get_system_prompt(state)
            assert isinstance(prompt, str)
            assert len(prompt) > 0, f"State {state} has empty prompt"


# ---------------------------------------------------------------------------
# FR-ID-002: UserResolver
# ---------------------------------------------------------------------------


class TestUserResolver:
    """UserResolver candidate ranking."""

    def _make_store(self, users: list[dict], resources: list[dict] = None) -> MagicMock:
        store = MagicMock()

        async def list_nodes(label: str, filters: dict = None, **kwargs) -> list[dict]:
            if label == "UserNode":
                return users
            if label == "ResourceNode":
                return resources or []
            return []

        store.list_nodes = list_nodes
        return store

    @pytest.mark.asyncio
    async def test_exact_name_match_high_confidence(self):
        """Exact name match returns confidence >= 0.9."""
        from graphclaw.identity.resolver import UserResolver

        users = [{"id": "user-1", "name": "Alice Smith", "aliases": []}]
        store = self._make_store(users)
        resolver = UserResolver(store)
        candidates = await resolver.resolve("Alice Smith", "caller-1", [], {})
        assert len(candidates) >= 1
        top = candidates[0]
        assert top.node_id == "user-1"
        assert top.confidence >= 0.9

    @pytest.mark.asyncio
    async def test_fuzzy_name_match_lower_confidence(self):
        """Fuzzy name match returns confidence < 0.95."""
        from graphclaw.identity.resolver import UserResolver

        users = [{"id": "user-1", "name": "Alice Smith", "aliases": []}]
        store = self._make_store(users)
        resolver = UserResolver(store)
        candidates = await resolver.resolve("Alice Smth", "caller-1", [], {})
        assert len(candidates) >= 1
        top = candidates[0]
        assert top.confidence < 0.95

    @pytest.mark.asyncio
    async def test_exact_alias_match_returns_full_confidence(self):
        """Exact alias match returns confidence 1.0."""
        from graphclaw.identity.resolver import UserResolver

        users = [
            {
                "id": "user-1",
                "name": "Bob Jones",
                "aliases": [{"value": "bob@example.com", "source": "email"}],
            }
        ]
        store = self._make_store(users)
        resolver = UserResolver(store)
        candidates = await resolver.resolve("bob@example.com", "caller-1", [], {})
        assert len(candidates) >= 1
        assert candidates[0].confidence == 1.0

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        """No match returns empty list."""
        from graphclaw.identity.resolver import UserResolver

        users = [{"id": "user-1", "name": "Alice Smith", "aliases": []}]
        store = self._make_store(users)
        resolver = UserResolver(store)
        candidates = await resolver.resolve("zxqjkw", "caller-1", [], {})
        assert candidates == []

    @pytest.mark.asyncio
    async def test_candidates_sorted_by_confidence_descending(self):
        """Candidates sorted descending by confidence."""
        from graphclaw.identity.resolver import UserResolver

        users = [
            {"id": "u1", "name": "Alice Smith", "aliases": []},
            {"id": "u2", "name": "Alice Jones", "aliases": []},
        ]
        store = self._make_store(users)
        resolver = UserResolver(store)
        candidates = await resolver.resolve("Alice", "caller-1", [], {})
        confidences = [c.confidence for c in candidates]
        assert confidences == sorted(confidences, reverse=True)


# ---------------------------------------------------------------------------
# FR-ID-003: CreatePersonFSM
# ---------------------------------------------------------------------------


class TestCreatePersonFSM:
    """CreatePersonFSM dialogue flow."""

    def _make_store(self, created_id: str = "res-001") -> MagicMock:
        store = MagicMock()
        store.create_node = AsyncMock(return_value={"id": created_id})
        store.update_node = AsyncMock(return_value={"id": created_id})
        return store

    def test_initial_state_with_candidates_is_disambiguate(self):
        """FSM starts in DISAMBIGUATE when candidates are provided."""
        from graphclaw.agent.identity.create_person import CreatePersonFSM, CreatePersonState

        store = self._make_store()
        candidates = [{"id": "res-001", "name": "Alice Smith"}]
        fsm = CreatePersonFSM(store, "caller-1", candidates=candidates)
        assert fsm.state == CreatePersonState.DISAMBIGUATE

    @pytest.mark.asyncio
    async def test_initial_state_without_candidates_enters_name_on_new(self):
        """FSM transitions to NAME after 'new' when no candidates."""
        from graphclaw.agent.identity.create_person import CreatePersonFSM, CreatePersonState

        store = self._make_store()
        fsm = CreatePersonFSM(store, "caller-1", candidates=None)
        # DISAMBIGUATE is the initial state; with no candidates, 'new' moves to NAME
        await fsm.step("new")
        assert fsm.state == CreatePersonState.NAME

    @pytest.mark.asyncio
    async def test_pick_existing_returns_picked_action(self):
        """Picking candidate '1' sets fsm.result.action=picked_existing."""
        from graphclaw.agent.identity.create_person import CreatePersonFSM

        store = self._make_store()
        store.get_node = AsyncMock(return_value={"id": "res-001", "aliases": []})
        candidates = [{"id": "res-001", "node_id": "res-001", "display_name": "Alice Smith"}]
        fsm = CreatePersonFSM(store, "caller-1", candidates=candidates)
        await fsm.step("1")
        assert fsm.result is not None
        assert fsm.result.action == "picked_existing"
        assert fsm.result.node_id == "res-001"

    @pytest.mark.asyncio
    async def test_pick_new_reaches_name_state(self):
        """Typing 'new' transitions to NAME state."""
        from graphclaw.agent.identity.create_person import CreatePersonFSM, CreatePersonState

        store = self._make_store()
        candidates = [{"id": "res-001", "name": "Alice Smith"}]
        fsm = CreatePersonFSM(store, "caller-1", candidates=candidates)
        result = await fsm.step("new")
        # Result is a prompt dict when still in progress
        assert fsm.state == CreatePersonState.NAME

    @pytest.mark.asyncio
    async def test_create_new_complete_flow(self):
        """Full new-person flow: name → role → channel → contact → aliases → done."""
        from graphclaw.agent.identity.create_person import CreatePersonFSM, CreatePersonState

        store = self._make_store("res-new-001")
        fsm = CreatePersonFSM(store, "caller-1", candidates=None)
        await fsm.step("new")  # DISAMBIGUATE → NAME
        await fsm.step("Jane Doe")  # NAME → ROLE
        await fsm.step("Designer")  # ROLE → CHANNEL
        await fsm.step("email")  # CHANNEL → CONTACT
        await fsm.step("jane@example.com")  # CONTACT → ALIASES
        await fsm.step("skip")  # ALIASES → DONE
        assert fsm.state == CreatePersonState.DONE
        assert fsm.result is not None
        assert fsm.result.action == "created_new"
        assert fsm.result.display_name == "Jane Doe"

    def test_serialise_roundtrip(self):
        """to_dict / from_dict preserves state."""
        from graphclaw.agent.identity.create_person import CreatePersonFSM

        store = self._make_store()
        fsm = CreatePersonFSM(store, "caller-1", candidates=[{"id": "r1", "name": "Bob"}])
        data = fsm.to_dict()
        fsm2 = CreatePersonFSM.from_dict(data, store)
        assert fsm2.state == fsm.state
        assert fsm2._caller_user_id == "caller-1"


# ---------------------------------------------------------------------------
# FR-ID-004: ResourceMerger
# ---------------------------------------------------------------------------


class TestResourceMerger:
    """ResourceMerger alias dedup, intelligence merge, tombstone."""

    def _make_store(self, keep: dict, merge: dict) -> MagicMock:
        store = MagicMock()

        async def get_node(node_id, include_archived=True, **_):
            if node_id == keep["id"]:
                return keep
            if node_id == merge["id"]:
                return merge
            return None

        store.get_node = get_node
        store.update_node = AsyncMock(return_value=None)
        store.create_node = AsyncMock(return_value={"id": "tomb-1"})
        store.redirect_edges = AsyncMock(return_value=3)
        return store

    @pytest.mark.asyncio
    async def test_merge_deduplicates_aliases(self):
        """Merged aliases are deduplicated."""
        from graphclaw.identity.merger import ResourceMerger

        keep = {
            "id": "res-A",
            "name": "Alice",
            "aliases": [{"value": "alice@old.com", "source": "email"}],
            "intelligence": "",
            "archived_at": None,
        }
        merge_node = {
            "id": "res-B",
            "name": "Alice Smith",
            "aliases": [
                {"value": "alice@old.com", "source": "email"},  # duplicate
                {"value": "alice@new.com", "source": "email"},
            ],
            "intelligence": "",
            "archived_at": None,
        }
        store = self._make_store(keep, merge_node)
        merger = ResourceMerger(store)
        result = await merger.merge("res-A", "res-B")
        # Should have 2 unique aliases after dedup
        update_calls = store.update_node.call_args_list
        alias_update = None
        for call in update_calls:
            args = call[0]
            if args[0] == "res-A" and "aliases" in args[1]:
                alias_update = args[1]["aliases"]
                break
        assert alias_update is not None
        alias_values = [a.get("value") if isinstance(a, dict) else a for a in alias_update]
        assert len(set(alias_values)) == len(alias_values)

    @pytest.mark.asyncio
    async def test_merge_result_has_correct_ids(self):
        """MergeResult contains keep_id, merge_id, tombstone_id."""
        from graphclaw.identity.merger import ResourceMerger

        keep = {"id": "res-A", "name": "A", "aliases": [], "intelligence": "", "archived_at": None}
        merge_node = {
            "id": "res-B",
            "name": "B",
            "aliases": [],
            "intelligence": "",
            "archived_at": None,
        }
        store = self._make_store(keep, merge_node)
        merger = ResourceMerger(store)
        result = await merger.merge("res-A", "res-B")
        assert result.keep_id == "res-A"
        assert result.merge_id == "res-B"
        assert result.edges_redirected == 3

    @pytest.mark.asyncio
    async def test_merge_creates_tombstone(self):
        """Merge calls store.create_node for tombstone."""
        from graphclaw.identity.merger import ResourceMerger

        keep = {"id": "res-A", "name": "A", "aliases": [], "intelligence": "", "archived_at": None}
        merge_node = {
            "id": "res-B",
            "name": "B",
            "aliases": [],
            "intelligence": "",
            "archived_at": None,
        }
        store = self._make_store(keep, merge_node)
        merger = ResourceMerger(store)
        await merger.merge("res-A", "res-B")
        assert store.create_node.called

    @pytest.mark.asyncio
    async def test_merge_appends_intelligence(self):
        """Intelligence from merge_node is appended to keep_node."""
        from graphclaw.identity.merger import ResourceMerger

        keep = {
            "id": "res-A",
            "name": "A",
            "aliases": [],
            "intelligence": "existing intel",
            "archived_at": None,
        }
        merge_node = {
            "id": "res-B",
            "name": "B",
            "aliases": [],
            "intelligence": "new intel from B",
            "archived_at": None,
        }
        store = self._make_store(keep, merge_node)
        merger = ResourceMerger(store)
        result = await merger.merge("res-A", "res-B")
        assert result.intelligence_lines_merged > 0


# ---------------------------------------------------------------------------
# FR-ID-005: register_alias
# ---------------------------------------------------------------------------


class TestRegisterAlias:
    """register_alias tool prevents duplicates."""

    @pytest.mark.asyncio
    async def test_register_new_alias_added(self):
        """New alias is appended to node's aliases list."""
        from graphclaw.agent.tools.identity_tools import register_alias

        store = MagicMock()
        store.get_node = AsyncMock(
            return_value={"id": "res-1", "aliases": [{"value": "old@example.com"}]}
        )
        store.update_node = AsyncMock(return_value={"id": "res-1"})

        result = await register_alias(
            node_id="res-1",
            alias="new@example.com",
            source="email",
            added_by="user-1",
            store=store,
        )
        # API returns {"added": True, "alias_count": N}
        assert result.get("added") is True
        store.update_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_duplicate_alias_skipped(self):
        """Duplicate alias returns added=False and does not update node."""
        from graphclaw.agent.tools.identity_tools import register_alias

        store = MagicMock()
        store.get_node = AsyncMock(
            return_value={"id": "res-1", "aliases": [{"value": "same@example.com"}]}
        )
        store.update_node = AsyncMock()

        result = await register_alias(
            node_id="res-1",
            alias="same@example.com",
            source="email",
            added_by="user-1",
            store=store,
        )
        # API returns {"added": False, "reason": "duplicate", "alias_count": N}
        assert result.get("added") is False
        assert result.get("reason") == "duplicate"
        store.update_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_register_alias_node_not_found(self):
        """Returns error when node does not exist."""
        from graphclaw.agent.tools.identity_tools import register_alias

        store = MagicMock()
        store.get_node = AsyncMock(return_value=None)

        result = await register_alias(
            node_id="missing-id",
            alias="x@x.com",
            source="email",
            added_by="user-1",
            store=store,
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# FR-RES-004: IdentityDriftDetector
# ---------------------------------------------------------------------------


class TestIdentityDriftDetector:
    """IdentityDriftDetector detects drift and auto-registers aliases."""

    @pytest.mark.asyncio
    async def test_no_drift_when_alias_already_known(self):
        """No drift event when sender is already in aliases."""
        from graphclaw.inbound.identity_drift import IdentityDriftDetector

        store = MagicMock()
        store.get_node = AsyncMock(
            return_value={"id": "res-1", "aliases": [{"value": "known@example.com"}]}
        )
        detector = IdentityDriftDetector(store)
        event = await detector.check_and_record("known@example.com", "res-1")
        assert event is None

    @pytest.mark.asyncio
    async def test_drift_detected_for_new_email(self):
        """DriftEvent returned when sender has new email."""
        from graphclaw.inbound.identity_drift import IdentityDriftDetector

        store = MagicMock()
        store.get_node = AsyncMock(
            return_value={"id": "res-1", "aliases": [{"value": "old@example.com"}]}
        )
        detector = IdentityDriftDetector(store, alias_register_fn=None)
        event = await detector.check_and_record("new@example.com", "res-1", auto_register=False)
        assert event is not None
        assert event.drift_type == "new_email"
        assert event.auto_registered is False

    @pytest.mark.asyncio
    async def test_drift_auto_registers_alias(self):
        """Drift auto-registers the new alias when fn provided."""
        from graphclaw.inbound.identity_drift import IdentityDriftDetector

        store = MagicMock()
        store.get_node = AsyncMock(
            return_value={"id": "res-1", "aliases": [{"value": "old@example.com"}]}
        )
        register_calls = []

        async def fake_register(**kwargs):
            register_calls.append(kwargs["alias"])

        detector = IdentityDriftDetector(store, alias_register_fn=fake_register)
        event = await detector.check_and_record("new@example.com", "res-1", auto_register=True)
        assert event.auto_registered is True
        assert "new@example.com" in register_calls

    @pytest.mark.asyncio
    async def test_node_not_found_returns_none(self):
        """Returns None when node is missing (graceful)."""
        from graphclaw.inbound.identity_drift import IdentityDriftDetector

        store = MagicMock()
        store.get_node = AsyncMock(return_value=None)
        detector = IdentityDriftDetector(store)
        event = await detector.check_and_record("x@x.com", "missing-id")
        assert event is None
