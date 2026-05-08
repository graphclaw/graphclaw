# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.agent.tool_registry — ToolSetRegistry."""

from __future__ import annotations

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

        # The 6 expected core tools
        assert "list_tasks" in names
        assert "get_task_details" in names
        assert "update_task_state" in names
        assert "list_available_agents" in names
        assert "load_tool_set" in names
        assert "read_knowledge" in names

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
        assert len(names) == 6  # exactly the 6 core tools


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
