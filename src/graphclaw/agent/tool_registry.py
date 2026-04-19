"""graphclaw.agent.tool_registry — ToolSetRegistry: lazy two-tier tool loading.

Description
-----------
Provides ``ToolSetRegistry``, which organises the agent's 16+ tool definitions
into named sets and activates them on demand.  Only the 6-tool core set is sent
to the LLM on every call (~600 tokens vs the previous flat ~4 800 tokens).  Named
sets are loaded via the ``load_tool_set`` meta-tool, reducing token cost and
preventing "not configured" round-trips for optional integrations.

Tool Set Layout
---------------
Tier 1 — Core (always present):
  list_tasks, get_task_details, update_task_state,
  list_available_agents, load_tool_set, read_knowledge

Tier 2 — Named sets (activated on demand):
  task_management  →  create_task, update_task, create_goal, update_goal
  planning         →  propose_plan, execute_plan
  skills           →  list_available_skills, invoke_skill
  mcp              →  list_mcp_tools, call_mcp_tool
  delegation       →  delegate_to_agent, create_agent

Public API
----------
- ToolSetRegistry: Manages tool set state for one agent session.
- ToolSetRegistry.activate: Load a named set; return its tool definitions.
- ToolSetRegistry.get_active_tools: Current active tool list.
- ToolSetRegistry.reset_session: Clear activated sets back to core only.
- ToolSetRegistry.get_manifest: Compact manifest string for the system prompt.

Dependencies
------------
- graphclaw.llm.base: ToolDefinition.
"""

from __future__ import annotations

import logging

from graphclaw.llm.base import ToolDefinition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definition helpers
# ---------------------------------------------------------------------------


def _td(
    name: str, description: str, properties: dict, required: list[str] | None = None
) -> ToolDefinition:
    """Build a ToolDefinition with a standard JSON Schema parameters block."""
    return ToolDefinition(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    )


# ---------------------------------------------------------------------------
# Individual tool definitions
# ---------------------------------------------------------------------------


def _make_core_tools() -> list[ToolDefinition]:
    return [
        _td(
            "list_tasks",
            (
                "List tasks from the graph. By default returns the top active tasks for the current user. "
                "Use goal_id to scope to one goal's subgraph. Exclude completed tasks unless include_completed=true."
            ),
            {
                "state_filter": {
                    "type": "string",
                    "description": "Filter by task state (e.g. ACTIVE, BLOCKED, IN_PROGRESS).",
                },
                "goal_id": {
                    "type": "string",
                    "description": "GOAL-{id}: scope results to tasks belonging to this goal.",
                },
                "task_type": {
                    "type": "string",
                    "description": "Filter by task type: ATOMIC, COMPOSITE, FOLLOW_UP, RECURRING, DELEGATED, APPROVAL, MILESTONE, REVIEW, DECISION, CHECKIN, RESEARCH.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of tasks to return (default 10, max 50).",
                    "default": 10,
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "Include COMPLETE and CANCELLED tasks (default false).",
                    "default": False,
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Filter by assignee user_id.",
                },
            },
        ),
        _td(
            "get_task_details",
            (
                "Retrieve full details for one task or goal including its relationships: "
                "dependencies (DEPENDS_ON), blockers (BLOCKS), parent goal (PART_OF), "
                "assignee (ASSIGNED_TO), and recent state history."
            ),
            {
                "node_id": {
                    "type": "string",
                    "description": "TSK-* or GOAL-* node ID to retrieve.",
                },
            },
            required=["node_id"],
        ),
        _td(
            "update_task_state",
            "Transition a task to a new state. Validates the transition against the state machine rules.",
            {
                "task_id": {"type": "string", "description": "TSK-* task ID."},
                "new_state": {
                    "type": "string",
                    "description": "Target state: PENDING, ACTIVE, IN_PROGRESS, BLOCKED, DELAYED, NEEDS_REVIEW, COMPLETE, CANCELLED, SNOOZED.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason for the state change (recorded in state_history).",
                },
            },
            required=["task_id", "new_state"],
        ),
        _td(
            "list_available_agents",
            (
                "List all agents available to delegate to — both system agents and user-created agents. "
                "Returns agent_id, name, description, capabilities, and invocation type. "
                "Call this before delegate_to_agent to discover available agents."
            ),
            {
                "capability_filter": {
                    "type": "string",
                    "description": "Optional: filter agents by capability (e.g. 'email_read', 'telegram_read').",
                },
            },
        ),
        _td(
            "load_tool_set",
            (
                "Activate a named tool set to access additional tools for this session. "
                "Available sets: task_management, planning, skills, mcp, delegation."
            ),
            {
                "name": {
                    "type": "string",
                    "description": "Tool set name: task_management | planning | skills | mcp | delegation.",
                    "enum": ["task_management", "planning", "skills", "mcp", "delegation"],
                },
            },
            required=["name"],
        ),
        _td(
            "read_knowledge",
            (
                "Load a domain knowledge document with rules and guidelines for a specific topic. "
                "Call this before creating nodes or edges to apply the correct construction rules."
            ),
            {
                "topic": {
                    "type": "string",
                    "description": "Knowledge topic: node_creation_rules | edge_creation_rules | state_machine_rules | goal_inference_rules | scoring_context | follow_up_timing.",
                    "enum": [
                        "node_creation_rules",
                        "edge_creation_rules",
                        "state_machine_rules",
                        "goal_inference_rules",
                        "scoring_context",
                        "follow_up_timing",
                    ],
                },
            },
            required=["topic"],
        ),
    ]


def _make_task_management_tools() -> list[ToolDefinition]:
    return [
        _td(
            "create_goal",
            "Create a new goal node. Goals are high-level outcomes that contain multiple tasks.",
            {
                "title": {"type": "string", "description": "Short goal title."},
                "description": {"type": "string", "description": "Detailed goal description."},
                "priority": {
                    "type": "string",
                    "description": "Priority: P1 (critical), P2 (high), P3 (normal), P4 (low).",
                    "enum": ["P1", "P2", "P3", "P4"],
                },
                "deadline": {
                    "type": "string",
                    "description": "ISO 8601 deadline date/datetime (optional).",
                },
            },
            required=["title"],
        ),
        _td(
            "create_task",
            (
                "Create a new task node. Call read_knowledge('node_creation_rules') first to select "
                "the correct task_type. Set goal_id to wire the task to its parent goal via PART_OF."
            ),
            {
                "title": {"type": "string", "description": "Short task title."},
                "description": {"type": "string", "description": "Detailed task description."},
                "task_type": {
                    "type": "string",
                    "description": "Task type: ATOMIC, COMPOSITE, FOLLOW_UP, RECURRING, DELEGATED, APPROVAL, MILESTONE, REVIEW, DECISION, CHECKIN, RESEARCH.",
                    "enum": [
                        "ATOMIC",
                        "COMPOSITE",
                        "FOLLOW_UP",
                        "RECURRING",
                        "DELEGATED",
                        "APPROVAL",
                        "MILESTONE",
                        "REVIEW",
                        "DECISION",
                        "CHECKIN",
                        "RESEARCH",
                    ],
                },
                "goal_id": {
                    "type": "string",
                    "description": "GOAL-* ID: parent goal (creates PART_OF edge).",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of TSK-* IDs this task depends on (creates DEPENDS_ON edges).",
                },
                "assigned_to": {
                    "type": "string",
                    "description": "user_id or resource_id of the assignee.",
                },
                "deadline": {
                    "type": "string",
                    "description": "ISO 8601 deadline date/datetime.",
                },
                "priority": {
                    "type": "string",
                    "description": "Priority: P1, P2, P3, P4.",
                    "enum": ["P1", "P2", "P3", "P4"],
                },
                "follow_up_contact": {
                    "type": "string",
                    "description": "For FOLLOW_UP tasks: name or identifier of the contact being followed up with.",
                },
                "recurrence_pattern": {
                    "type": "string",
                    "description": "For RECURRING tasks: recurrence pattern (e.g. 'weekly', 'daily').",
                },
            },
            required=["title", "task_type"],
        ),
        _td(
            "update_task",
            "Update task properties (title, description, deadline, priority, assigned_to).",
            {
                "task_id": {"type": "string", "description": "TSK-* task ID."},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "deadline": {"type": "string", "description": "ISO 8601 deadline."},
                "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
                "assigned_to": {"type": "string"},
            },
            required=["task_id"],
        ),
        _td(
            "update_goal",
            "Update goal properties (title, description, priority, deadline, state).",
            {
                "goal_id": {"type": "string", "description": "GOAL-* goal ID."},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
                "deadline": {"type": "string"},
                "state": {
                    "type": "string",
                    "description": "Goal state: ACTIVE, ON_HOLD, COMPLETE, ABANDONED.",
                    "enum": ["ACTIVE", "ON_HOLD", "COMPLETE", "ABANDONED"],
                },
            },
            required=["goal_id"],
        ),
    ]


def _make_planning_tools() -> list[ToolDefinition]:
    return [
        _td(
            "propose_plan",
            (
                "Generate a structured decomposition plan for a goal or complex task. "
                "Returns a plan_id that can be passed to execute_plan after user review."
            ),
            {
                "goal_or_task_id": {
                    "type": "string",
                    "description": "GOAL-* or TSK-* ID to plan against.",
                },
                "context": {
                    "type": "string",
                    "description": "Additional context or constraints for the plan.",
                },
                "max_tasks": {
                    "type": "integer",
                    "description": "Maximum number of tasks to propose (default 10).",
                    "default": 10,
                },
            },
            required=["goal_or_task_id"],
        ),
        _td(
            "execute_plan",
            "Execute a previously proposed plan, creating all tasks and edges in the graph.",
            {
                "plan_id": {
                    "type": "string",
                    "description": "Plan ID returned by propose_plan.",
                },
                "approved_task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: subset of task IDs to create (all by default).",
                },
            },
            required=["plan_id"],
        ),
    ]


def _make_skills_tools() -> list[ToolDefinition]:
    return [
        _td(
            "list_available_skills",
            "List AI automation skills available to the current user.",
            {
                "query": {
                    "type": "string",
                    "description": "Optional search query to filter skills by name or capability.",
                },
            },
        ),
        _td(
            "invoke_skill",
            "Execute an installed skill by name, passing arguments as a free-form dict.",
            {
                "skill_name": {"type": "string", "description": "Name of the skill to invoke."},
                "arguments": {
                    "type": "object",
                    "description": "Arguments to pass to the skill.",
                },
            },
            required=["skill_name"],
        ),
    ]


def _make_mcp_tools() -> list[ToolDefinition]:
    return [
        _td(
            "list_mcp_tools",
            "List all tools available from the user's registered MCP servers.",
            {
                "server_id": {
                    "type": "string",
                    "description": "Optional: filter to one MCP server by ID.",
                },
            },
        ),
        _td(
            "call_mcp_tool",
            "Call a tool on a registered MCP server.",
            {
                "server_id": {"type": "string", "description": "MCP-* server ID."},
                "tool_name": {"type": "string", "description": "Name of the tool to call."},
                "arguments": {
                    "type": "object",
                    "description": "Arguments for the MCP tool.",
                },
            },
            required=["server_id", "tool_name"],
        ),
    ]


def _make_delegation_tools() -> list[ToolDefinition]:
    return [
        _td(
            "delegate_to_agent",
            (
                "Delegate a task to a sub-agent asynchronously. Call list_available_agents first "
                "to discover agent IDs. The agent will run in the background and update the task."
            ),
            {
                "task_id": {"type": "string", "description": "TSK-* task to delegate."},
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID from list_available_agents.",
                },
                "instructions": {
                    "type": "string",
                    "description": "Specific instructions for the sub-agent.",
                },
            },
            required=["task_id", "agent_id"],
        ),
        _td(
            "create_agent",
            "Create a new user-scoped sub-agent with a custom persona and capabilities.",
            {
                "agent_id": {
                    "type": "string",
                    "description": "Optional short identifier for the agent (lowercase, hyphens). If omitted, generated from name.",
                },
                "name": {"type": "string", "description": "Human-readable agent name."},
                "purpose": {
                    "type": "string",
                    "description": "What this agent is for and how it should behave.",
                },
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capability tags (e.g. ['email_read', 'task_create']).",
                },
                "mcp_servers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional MCP server IDs this agent should use.",
                },
                "tool_hint": {
                    "type": "string",
                    "description": "Short hint shown in list_available_agents.",
                },
                "profile": {
                    "type": "string",
                    "description": "Legacy alias for purpose (still supported).",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Legacy alias for skills (still supported).",
                },
            },
            required=["name"],
        ),
    ]


# ---------------------------------------------------------------------------
# ToolSetRegistry
# ---------------------------------------------------------------------------

_MANIFEST = """\
## Available Tool Sets
Call load_tool_set(name) to activate additional tools for this session.
- task_management : create and edit tasks and goals
- planning        : decompose goals into structured execution plans
- skills          : run AI automation skills
- mcp             : call external integrations (GitHub, Calendar, Slack, etc.)
- delegation      : delegate tasks to sub-agents asynchronously"""


class ToolSetRegistry:
    """Manages which tool sets are active for one agent session.

    Parameters
    ----------
    has_skill_registry:
        Whether a skill registry is wired at construction time. If False,
        the ``skills`` set is excluded from available sets.
    has_mcp_registry:
        Whether an MCP registry is wired. If False, ``mcp`` set is excluded.
    """

    def __init__(
        self,
        has_skill_registry: bool = False,
        has_mcp_registry: bool = False,
    ) -> None:
        self._available: dict[str, list[ToolDefinition]] = {
            "core": _make_core_tools(),
            "task_management": _make_task_management_tools(),
            "planning": _make_planning_tools(),
            "delegation": _make_delegation_tools(),
        }
        if has_skill_registry:
            self._available["skills"] = _make_skills_tools()
        if has_mcp_registry:
            self._available["mcp"] = _make_mcp_tools()

        self._active_sets: set[str] = {"core"}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def activate(self, set_name: str) -> list[ToolDefinition]:
        """Add *set_name* to the active sets and return its tool definitions.

        Returns an empty list if the set name is unknown or not available
        (e.g. ``mcp`` when no MCP registry is configured).
        """
        if set_name not in self._available:
            logger.warning(
                "tool_registry.activate.unknown_set",
                extra={"set_name": set_name, "available": list(self._available)},
            )
            return []
        self._active_sets.add(set_name)
        tools = self._available[set_name]
        logger.debug(
            "tool_registry.activate",
            extra={"set_name": set_name, "tools_added": [t.name for t in tools]},
        )
        return tools

    def reset_session(self) -> None:
        """Reset active sets to core only (call at the start of each message)."""
        self._active_sets = {"core"}

    def get_active_tools(self) -> list[ToolDefinition]:
        """Return the current active tool list (core + all activated sets)."""
        tools: list[ToolDefinition] = []
        for set_name in self._active_sets:
            tools.extend(self._available.get(set_name, []))
        return tools

    def get_manifest(self) -> str:
        """Return the compact manifest string for injection into the system prompt."""
        available_set_names = [k for k in self._available if k != "core"]
        lines = ["## Available Tool Sets", "Call load_tool_set(name) to activate additional tools."]
        labels = {
            "task_management": "create and edit tasks and goals",
            "planning": "decompose goals into structured execution plans",
            "skills": "run AI automation skills",
            "mcp": "call external integrations (GitHub, Calendar, Slack, etc.)",
            "delegation": "delegate tasks to sub-agents asynchronously",
        }
        for name in available_set_names:
            desc = labels.get(name, name)
            lines.append(f"- {name:<18}: {desc}")
        return "\n".join(lines)

    @property
    def active_set_names(self) -> set[str]:
        """The currently active set names (read-only view)."""
        return frozenset(self._active_sets)  # type: ignore[return-value]


__all__ = ["ToolSetRegistry"]
