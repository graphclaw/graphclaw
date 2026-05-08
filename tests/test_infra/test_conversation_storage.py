# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_infra.test_conversation_storage — FR-STORE-001 acceptance tests.

Verifies StoragePaths conversation methods:
  AC1: conversation_thread() returns correct scoped path.
  AC2: conversation_index() returns correct path.
  AC3: conversation_counterparty_dir() returns correct prefix.
  AC4: conversation_legacy_archive() returns correct archive path.
  AC5: Path validation rejects traversal and empty segments.
"""

from __future__ import annotations

import pytest

from graphclaw.infra.storage import StoragePaths


class TestConversationThreadPath:
    def test_basic_path(self) -> None:
        path = StoragePaths.conversation_thread(
            "USER-alice", "RES-bob", "telegram", "tg-thread-001"
        )
        assert path == "USER-alice/conversations/RES-bob/telegram/tg-thread-001.jsonl"

    def test_self_chat_cockpit(self) -> None:
        """Owner cockpit chat: counterparty_id == user_id."""
        path = StoragePaths.conversation_thread("USER-alice", "USER-alice", "cockpit", "main")
        assert path == "USER-alice/conversations/USER-alice/cockpit/main.jsonl"

    def test_whatsapp_path(self) -> None:
        path = StoragePaths.conversation_thread("USER-alice", "RES-bob", "whatsapp", "wa-thread-1")
        assert path == "USER-alice/conversations/RES-bob/whatsapp/wa-thread-1.jsonl"

    def test_rejects_traversal_in_user_id(self) -> None:
        with pytest.raises(ValueError):
            StoragePaths.conversation_thread("../evil", "RES-bob", "email", "t1")

    def test_rejects_empty_thread_id(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            StoragePaths.conversation_thread("USER-x", "RES-y", "telegram", "")


class TestConversationIndexPath:
    def test_basic_path(self) -> None:
        path = StoragePaths.conversation_index("USER-alice")
        assert path == "USER-alice/conversations/index.json"


class TestConversationCounterpartyDir:
    def test_basic_path(self) -> None:
        path = StoragePaths.conversation_counterparty_dir("USER-alice", "RES-bob")
        assert path == "USER-alice/conversations/RES-bob/"


class TestConversationLegacyArchive:
    def test_basic_path(self) -> None:
        path = StoragePaths.conversation_legacy_archive("USER-alice")
        assert path == "USER-alice/conversations/.legacy/chat-history.json.archived"


class TestAgentPolicyPath:
    def test_basic_policy_path(self) -> None:
        path = StoragePaths.agent_policy("USER-alice", "main", "delegation")
        assert path == "USER-alice/agents/main/policies/delegation.md"

    def test_policies_prefix(self) -> None:
        prefix = StoragePaths.agent_policies_prefix("USER-alice", "main")
        assert prefix == "USER-alice/agents/main/policies/"

    def test_outbound_profile(self) -> None:
        path = StoragePaths.outbound_profile("USER-alice", "main")
        assert path == "USER-alice/agents/main/outbound_profile.md"

    def test_rejects_path_separator_in_policy_name(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            StoragePaths.agent_policy("USER-alice", "main", "del/egation")
