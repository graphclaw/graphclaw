# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.agent.tool_registry — ToolSetRegistry."""

from __future__ import annotations

from graphclaw.agent.onboarding import ONBOARDING_TOOL_ALLOWLIST, OnboardingState
from graphclaw.agent.tool_registry import ToolSetRegistry
from graphclaw.llm.base import ToolDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_names(tools: list[ToolDefinition]) -> set[str]:
    return {t.name for t in tools}


# ---------------------------------------------------------------------------
# Core tools (always present)
# ---------------------------------------------------------------------------


class TestCoreTools:
    def test_core_tools_always_present(self):
        registry = ToolSetRegistry()
        tools = registry.get_active_tools()
        names = _tool_names(tools)

        # The expected core tools
        assert "list_tasks" in names
        assert "get_task_details" in names
        assert "update_task_state" in names
        assert "list_available_agents" in names
        assert "load_tool_set" in names
        assert "read_knowledge" in names
        assert "update_profile_from_conversation" in names
        # Wave Tiered-Memory — memory tools are always-on core tools
        assert "read_memory" in names
        assert "recall_episodic" in names
        assert "compact_memory" in names
        assert "estimate_memory" in names

    def test_core_tools_present_without_optional_deps(self):
        registry = ToolSetRegistry(has_skill_registry=False, has_mcp_registry=False)
        names = _tool_names(registry.get_active_tools())
        assert "list_tasks" in names
        assert "list_available_skills" not in names
        assert "list_mcp_tools" not in names

    def test_list_tasks_has_new_parameters(self):
        registry = ToolSetRegistry()
        tools = {t.name: t for t in registry.get_active_tools()}
        lt = tools["list_tasks"]
        props = lt.parameters.get("properties", {})

        assert "goal_id" in props
        assert "task_type" in props
        assert "limit" in props
        assert "include_completed" in props
        assert "assigned_to" in props

    def test_get_task_details_in_core(self):
        registry = ToolSetRegistry()
        tools = {t.name: t for t in registry.get_active_tools()}
        assert "get_task_details" in tools


# ---------------------------------------------------------------------------
# Dependency filtering
# ---------------------------------------------------------------------------


class TestDependencyFiltering:
    def test_skills_excluded_without_skill_registry(self):
        registry = ToolSetRegistry(has_skill_registry=False)
        # skills set not available — activate should return []
        result = registry.activate("skills")
        assert result == []

    def test_skills_included_with_skill_registry(self):
        registry = ToolSetRegistry(has_skill_registry=True)
        result = registry.activate("skills")
        names = _tool_names(result)
        assert "list_available_skills" in names
        assert "invoke_skill" in names

    def test_mcp_excluded_without_mcp_registry(self):
        registry = ToolSetRegistry(has_mcp_registry=False)
        result = registry.activate("mcp")
        assert result == []

    def test_mcp_included_with_mcp_registry(self):
        registry = ToolSetRegistry(has_mcp_registry=True)
        result = registry.activate("mcp")
        names = _tool_names(result)
        assert "list_mcp_tools" in names
        assert "call_mcp_tool" in names


# ---------------------------------------------------------------------------
# activate / get_active_tools
# ---------------------------------------------------------------------------


class TestActivate:
    def test_activate_task_management_returns_tools(self):
        registry = ToolSetRegistry()
        tools = registry.activate("task_management")
        names = _tool_names(tools)

        assert "create_task" in names
        assert "update_task" in names
        assert "create_goal" in names
        assert "update_goal" in names

    def test_activate_planning_returns_tools(self):
        registry = ToolSetRegistry()
        tools = registry.activate("planning")
        names = _tool_names(tools)

        assert "propose_plan" in names
        assert "edit_plan" in names
        assert "approve_plan" in names
        assert "execute_plan" in names
        assert "propose_goal_inference" in names
        assert "approve_goal_inference" in names

    def test_activate_delegation_returns_tools(self):
        registry = ToolSetRegistry()
        tools = registry.activate("delegation")
        names = _tool_names(tools)

        assert "delegate_to_agent" in names
        assert "create_agent" in names

    def test_activate_onboarding_includes_agent_name_tool(self):
        registry = ToolSetRegistry()
        tools = registry.activate("onboarding")
        names = _tool_names(tools)

        assert "set_user_name" in names
        assert "set_agent_name" in names

    def test_onboarding_registry_covers_fsm_allowlist(self):
        registry = ToolSetRegistry()
        names = _tool_names(registry.activate("onboarding"))

        allowed_names: set[str] = set()
        for state, tools in ONBOARDING_TOOL_ALLOWLIST.items():
            if state == OnboardingState.DONE:
                continue
            allowed_names.update(tools)

        missing = sorted(allowed_names - names)
        assert missing == [], (
            "Onboarding FSM allowlist references tools missing in tool registry: "
            + ", ".join(missing)
        )

    def test_activate_unknown_set_returns_empty(self):
        registry = ToolSetRegistry()
        result = registry.activate("nonexistent_set")
        assert result == []

    def test_activate_accumulates_active_sets(self):
        registry = ToolSetRegistry()
        registry.activate("task_management")
        registry.activate("planning")

        names = _tool_names(registry.get_active_tools())
        assert "create_task" in names
        assert "propose_plan" in names

    def test_activate_twice_doesnt_duplicate_tools(self):
        registry = ToolSetRegistry()
        registry.activate("task_management")
        registry.activate("task_management")

        # Only one set of task_management tools, core + 4
        names = [t.name for t in registry.get_active_tools()]
        assert names.count("create_task") == 1


# ---------------------------------------------------------------------------
# reset_session
# ---------------------------------------------------------------------------


class TestResetSession:
    def test_reset_clears_activated_sets(self):
        registry = ToolSetRegistry()
        registry.activate("task_management")
        registry.activate("planning")

        registry.reset_session()

        names = _tool_names(registry.get_active_tools())
        # Core tools still present
        assert "list_tasks" in names
        # Non-core tools gone
        assert "create_task" not in names
        assert "propose_plan" not in names

    def test_reset_restores_to_core_only(self):
        registry = ToolSetRegistry(has_skill_registry=True, has_mcp_registry=True)
        registry.activate("skills")
        registry.activate("mcp")
        registry.activate("delegation")
        registry.reset_session()

        names = _tool_names(registry.get_active_tools())
        # core: 8 base + read_memory/recall_episodic/compact_memory/estimate_memory
        # (Wave Tiered-Memory) + unload_tool_set (Wave Model-Routing, reversible
        # tool sets).
        assert len(names) == 12


# ---------------------------------------------------------------------------
# get_manifest
# ---------------------------------------------------------------------------


class TestGetManifest:
    def test_manifest_lists_available_sets(self):
        registry = ToolSetRegistry()
        manifest = registry.get_manifest()

        assert "task_management" in manifest
        assert "planning" in manifest
        assert "delegation" in manifest

    def test_manifest_excludes_core(self):
        registry = ToolSetRegistry()
        manifest = registry.get_manifest()
        # core is always present, shouldn't appear in the "call to activate" manifest
        lines = manifest.lower()
        # The word "core" shouldn't appear as a callable set name
        assert "- core" not in lines

    def test_manifest_omits_unavailable_optional_sets(self):
        registry = ToolSetRegistry(has_skill_registry=False, has_mcp_registry=False)
        manifest = registry.get_manifest()
        assert "skills" not in manifest
        assert "mcp" not in manifest

    def test_manifest_includes_optional_when_available(self):
        registry = ToolSetRegistry(has_skill_registry=True, has_mcp_registry=True)
        manifest = registry.get_manifest()
        assert "skills" in manifest
        assert "mcp" in manifest


# ---------------------------------------------------------------------------
# deactivate — reversible tool sets
# ---------------------------------------------------------------------------


class TestDeactivate:
    def test_deactivate_removes_an_active_set(self):
        registry = ToolSetRegistry()
        registry.activate("task_management")
        assert "task_management" in registry.active_set_names

        removed = registry.deactivate("task_management")

        assert removed is True
        assert "task_management" not in registry.active_set_names

    def test_deactivate_refuses_to_drop_core(self):
        registry = ToolSetRegistry()

        removed = registry.deactivate("core")

        assert removed is False
        assert "core" in registry.active_set_names

    def test_deactivate_inactive_set_returns_false(self):
        registry = ToolSetRegistry()

        removed = registry.deactivate("task_management")

        assert removed is False

    def test_deactivate_then_reactivate_works(self):
        registry = ToolSetRegistry()
        registry.activate("planning")
        registry.deactivate("planning")

        tools = registry.activate("planning")

        assert len(tools) > 0
        assert "planning" in registry.active_set_names


# ---------------------------------------------------------------------------
# set_active — bulk replace
# ---------------------------------------------------------------------------


class TestSetActive:
    def test_set_active_replaces_current_sets(self):
        registry = ToolSetRegistry()
        registry.activate("task_management")

        registry.set_active(["planning"])

        assert registry.active_set_names == {"core", "planning"}

    def test_set_active_always_keeps_core(self):
        registry = ToolSetRegistry()

        registry.set_active([])

        assert registry.active_set_names == {"core"}

    def test_set_active_ignores_unknown_names(self):
        registry = ToolSetRegistry()

        registry.set_active(["not-a-real-set", "planning"])

        assert registry.active_set_names == {"core", "planning"}

    def test_set_active_ignores_explicit_core_duplicate(self):
        registry = ToolSetRegistry()

        registry.set_active(["core", "planning"])

        assert registry.active_set_names == {"core", "planning"}


# ---------------------------------------------------------------------------
# preload — bulk activate
# ---------------------------------------------------------------------------


class TestPreload:
    def test_preload_activates_all_named_sets(self):
        registry = ToolSetRegistry()

        tools = registry.preload(["task_management", "planning"])

        names = _tool_names(tools)
        assert "create_task" in names  # task_management
        assert "propose_plan" in names  # planning
        assert registry.active_set_names == {"core", "task_management", "planning"}

    def test_preload_returns_combined_active_tools(self):
        registry = ToolSetRegistry()
        registry.activate("delegation")

        tools = registry.preload(["planning"])

        names = _tool_names(tools)
        assert "delegate_to_agent" in names  # already-active delegation
        assert "propose_plan" in names  # newly preloaded planning


# ---------------------------------------------------------------------------
# Eviction — max_active_sets cap
# ---------------------------------------------------------------------------


class TestEviction:
    def test_activating_beyond_cap_evicts_oldest(self):
        registry = ToolSetRegistry(max_active_sets=2)
        registry.activate("task_management")
        registry.activate("planning")

        registry.activate("delegation")

        # task_management (activated first) is evicted; planning + delegation remain.
        assert registry.active_set_names == {"core", "planning", "delegation"}

    def test_reactivating_an_already_active_set_does_not_evict(self):
        registry = ToolSetRegistry(max_active_sets=2)
        registry.activate("task_management")
        registry.activate("planning")

        registry.activate("planning")  # already active — no-op re-activation

        assert registry.active_set_names == {"core", "task_management", "planning"}

    def test_recently_used_set_is_protected_from_eviction(self):
        """A set whose tool was called within the last 2 iterations must
        survive eviction even if it's the oldest-activated."""
        registry = ToolSetRegistry(max_active_sets=2)
        registry.activate("task_management")
        registry.note_tool_used("create_task")  # protects task_management
        registry.activate("planning")

        registry.activate("delegation")  # would normally evict task_management

        assert "task_management" in registry.active_set_names

    def test_protection_expires_after_two_ticks(self):
        registry = ToolSetRegistry(max_active_sets=2)
        registry.activate("task_management")
        registry.note_tool_used("create_task")
        registry.activate("planning")

        registry.tick()
        registry.tick()
        registry.tick()  # protection window (2 iterations) has passed

        registry.activate("delegation")

        assert "task_management" not in registry.active_set_names

    def test_eviction_allows_temporary_overflow_when_all_protected(self):
        registry = ToolSetRegistry(max_active_sets=1)
        registry.activate("task_management")
        registry.note_tool_used("create_task")

        registry.activate("planning")  # task_management is protected — nothing evictable

        assert registry.active_set_names == {"core", "task_management", "planning"}

    def test_reset_session_clears_eviction_state(self):
        """reset_session() must clear the iteration counter and recent-use
        tracking, not just the active-set membership — otherwise a set
        activated in a *later* turn could inherit protection earned by a
        same-named set in an earlier, unrelated turn."""
        registry = ToolSetRegistry(max_active_sets=2)
        registry.activate("task_management")
        registry.note_tool_used("create_task")
        registry.tick()
        registry.tick()

        registry.reset_session()
        # Re-activate the same set fresh after reset — if protection state
        # had survived, this would be considered "used within 2 iterations"
        # relative to the old (now-reset) counter and wrongly protected.
        registry.activate("task_management")
        registry.activate("planning")
        registry.activate("delegation")  # forces an eviction decision

        # task_management was NOT re-marked as used after reset, so it is the
        # oldest-and-unprotected set and must be the one evicted.
        assert "task_management" not in registry.active_set_names
        assert registry.active_set_names == {"core", "planning", "delegation"}

    def test_default_max_active_sets_reads_from_context_config(self, monkeypatch):
        monkeypatch.setenv("GRAPHCLAW_CONTEXT_MAX_ACTIVE_TOOL_SETS", "1")
        registry = ToolSetRegistry()
        registry.activate("task_management")

        registry.activate("planning")

        assert registry.active_set_names == {"core", "planning"}
