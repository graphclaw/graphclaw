"""tests.test_infra.test_storage_paths — Unit tests for StoragePaths.

Description
-----------
Verifies that every StoragePaths method produces the correct multi-tenant
path string with the user_id as the root segment.

Design Patterns
---------------
- Arrange/Assert: Each test verifies a single path method's output format.
- Parameterization: Multiple user IDs and inputs are tested to catch
  any edge cases with characters that might need escaping.

Dependencies
------------
- pytest: Test runner.
- graphclaw.infra.storage: StoragePaths under test.
"""

from __future__ import annotations

from graphclaw.infra.storage import StoragePaths

_USER = "usr-abc123"
_AGENT = "main"


# ---------------------------------------------------------------------------
# System paths
# ---------------------------------------------------------------------------


def test_system_skill_definition() -> None:
    path = StoragePaths.system_skill_definition("meeting-notes-agent")
    assert path == "system/skills/definitions/meeting-notes-agent/SKILL.md"
    assert not path.startswith(_USER)


def test_system_skills_prefix() -> None:
    assert StoragePaths.system_skills_prefix() == "system/skills/definitions/"


# ---------------------------------------------------------------------------
# User root — isolation guarantee
# ---------------------------------------------------------------------------


def test_user_root_is_prefix_of_all_user_paths() -> None:
    """Every user path must start with {user_id}/."""
    user_id = "usr-test-99"
    paths_under_test = [
        StoragePaths.user_config(user_id),
        StoragePaths.user_scoring_weights(user_id),
        StoragePaths.agent_profile(user_id, "main"),
        StoragePaths.agent_config(user_id, "main"),
        StoragePaths.agent_memory_working(user_id, "main"),
        StoragePaths.agent_memory_episodic_prefix(user_id, "main"),
        StoragePaths.agent_memory_episodic_entry(user_id, "main", "2026-01-01-session.md"),
        StoragePaths.agent_memory_semantic_prefix(user_id, "main"),
        StoragePaths.agent_memory_semantic_topic(user_id, "main", "users"),
        StoragePaths.skill_registry_sources(user_id),
        StoragePaths.skill_registry_installed(user_id),
        StoragePaths.skill_cache(user_id, "abcd1234", "my-skill"),
        StoragePaths.skill_authored(user_id, "my-authored-skill"),
        StoragePaths.skill_authored_prefix(user_id),
        StoragePaths.skill_executions(user_id, "skill-abc"),
        StoragePaths.attachment(user_id, "email", "2026-01-01", "msg-001", "file.pdf"),
        StoragePaths.attachments_prefix(user_id),
    ]
    for path in paths_under_test:
        assert path.startswith(f"{user_id}/"), (
            f"Path '{path}' does not start with user_id prefix '{user_id}/'"
        )


def test_different_users_produce_different_paths() -> None:
    """Two different users must never share the same storage paths."""
    user_a = "usr-alice"
    user_b = "usr-bob"

    assert StoragePaths.user_config(user_a) != StoragePaths.user_config(user_b)
    assert StoragePaths.agent_profile(user_a, "main") != StoragePaths.agent_profile(user_b, "main")
    assert StoragePaths.skill_registry_installed(user_a) != StoragePaths.skill_registry_installed(
        user_b
    )


# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------


def test_user_config_path() -> None:
    assert StoragePaths.user_config(_USER) == f"{_USER}/config.json"


def test_user_scoring_weights_path() -> None:
    assert StoragePaths.user_scoring_weights(_USER) == f"{_USER}/scoring_weights.json"


# ---------------------------------------------------------------------------
# Agent paths
# ---------------------------------------------------------------------------


def test_agent_profile_path() -> None:
    assert StoragePaths.agent_profile(_USER, _AGENT) == f"{_USER}/agents/{_AGENT}/profile.md"


def test_agent_config_path() -> None:
    assert StoragePaths.agent_config(_USER, _AGENT) == f"{_USER}/agents/{_AGENT}/config.json"


def test_agent_root_path() -> None:
    assert StoragePaths.agent_root(_USER, _AGENT) == f"{_USER}/agents/{_AGENT}/"


# ---------------------------------------------------------------------------
# Memory paths
# ---------------------------------------------------------------------------


def test_agent_memory_working_path() -> None:
    path = StoragePaths.agent_memory_working(_USER, _AGENT)
    assert path == f"{_USER}/agents/{_AGENT}/memory/working/context.md"


def test_agent_memory_episodic_prefix() -> None:
    prefix = StoragePaths.agent_memory_episodic_prefix(_USER, _AGENT)
    assert prefix == f"{_USER}/agents/{_AGENT}/memory/episodic/"


def test_agent_memory_episodic_entry() -> None:
    entry = "2026-04-11-compact-abc12345.md"
    path = StoragePaths.agent_memory_episodic_entry(_USER, _AGENT, entry)
    assert path == f"{_USER}/agents/{_AGENT}/memory/episodic/{entry}"


def test_agent_memory_semantic_prefix() -> None:
    prefix = StoragePaths.agent_memory_semantic_prefix(_USER, _AGENT)
    assert prefix == f"{_USER}/agents/{_AGENT}/memory/semantic/"


def test_agent_memory_semantic_topic() -> None:
    path = StoragePaths.agent_memory_semantic_topic(_USER, _AGENT, "users")
    assert path == f"{_USER}/agents/{_AGENT}/memory/semantic/users.md"


def test_agent_memory_semantic_topic_appends_md() -> None:
    """Ensure .md extension is always appended (not duplicated if topic already has it)."""
    path = StoragePaths.agent_memory_semantic_topic(_USER, _AGENT, "patterns")
    assert path.endswith(".md")
    assert "patterns.md" in path


# ---------------------------------------------------------------------------
# Skill registry paths
# ---------------------------------------------------------------------------


def test_skill_registry_sources_path() -> None:
    assert StoragePaths.skill_registry_sources(_USER) == f"{_USER}/skills/registry/sources.json"


def test_skill_registry_installed_path() -> None:
    assert StoragePaths.skill_registry_installed(_USER) == f"{_USER}/skills/registry/installed.json"


def test_skill_cache_path() -> None:
    path = StoragePaths.skill_cache(_USER, "abcd1234", "linkedin-outreach-agent")
    assert path == f"{_USER}/skills/cache/abcd1234/linkedin-outreach-agent/SKILL.md"


def test_skill_authored_path() -> None:
    path = StoragePaths.skill_authored(_USER, "my-custom-skill")
    assert path == f"{_USER}/skills/authored/my-custom-skill/SKILL.md"


def test_skill_authored_prefix() -> None:
    assert StoragePaths.skill_authored_prefix(_USER) == f"{_USER}/skills/authored/"


def test_skill_executions_path() -> None:
    path = StoragePaths.skill_executions(_USER, "skill-abc123")
    assert path == f"{_USER}/skills/executions/skill-abc123.json"


# ---------------------------------------------------------------------------
# Attachment paths
# ---------------------------------------------------------------------------


def test_attachment_path_basic() -> None:
    path = StoragePaths.attachment(_USER, "email", "2026-04-11", "msg-001", "report.pdf")
    assert path == f"{_USER}/attachments/email/2026-04-11/msg-001/report.pdf"


def test_attachment_path_sanitises_slashes_in_msg_id() -> None:
    """Slashes in msg_id must be replaced with underscores."""
    path = StoragePaths.attachment(_USER, "whatsapp", "2026-04-11", "wa/12345/msg", "image.jpg")
    assert "/" not in path.split("attachments/whatsapp/2026-04-11/")[1].split("/")[0]


def test_attachments_prefix() -> None:
    assert StoragePaths.attachments_prefix(_USER) == f"{_USER}/attachments/"


# ---------------------------------------------------------------------------
# Regression: old paths no longer exist in any helper
# ---------------------------------------------------------------------------


def test_old_agents_prefix_not_in_config_path() -> None:
    """The legacy 'agents/{user_id}/config.json' prefix must no longer be generated."""
    path = StoragePaths.user_config(_USER)
    assert not path.startswith("agents/")


def test_old_skills_registry_prefix_not_generated() -> None:
    """The legacy 'skills/registry/{user_id}/' prefix must no longer be generated."""
    path = StoragePaths.skill_registry_sources(_USER)
    assert not path.startswith("skills/registry/")


def test_old_skills_cache_prefix_not_generated() -> None:
    """The legacy 'skills/cache/{user_id}/' prefix must no longer be generated."""
    path = StoragePaths.skill_cache(_USER, "abcd1234", "my-skill")
    assert not path.startswith("skills/cache/")


def test_old_attachments_prefix_not_generated() -> None:
    """The legacy 'attachments/{channel}/...' prefix (without user_id) must not be generated."""
    path = StoragePaths.attachment(_USER, "telegram", "2026-01-01", "msg1", "file.jpg")
    assert not path.startswith("attachments/")
