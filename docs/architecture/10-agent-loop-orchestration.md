# 10 — Agent Loop Orchestration

## Purpose

This document describes the `MainOrchestrator` — the central agent loop that handles all user chat interactions, graph reasoning, tool execution, and sub-agent delegation. It is the entry point for every message sent to an agent.

---

## Overview

`MainOrchestrator` (`src/graphclaw/agent/main_orchestrator.py`) is a stateless LLM invocation harness. On every message it:

1. Builds a system prompt from profile + graph summary + working memory
2. Calls the LLM with the conversation history and tool definitions
3. Executes any tool calls returned by the LLM
4. Loops until the LLM returns a final text response (no more tool calls)
5. Persists the updated conversation history

The orchestrator does not maintain in-memory state between invocations. All durable state lives in object storage (MinIO) and the graph database (Memgraph).

---

## Entry Points

### `process_chat_message(user_id, session_id, message)` → `str`

Blocking async call. Returns the final text response. Used by REST chat endpoints and the test client.

### `process_chat_message_stream(user_id, session_id, message)` → `AsyncGenerator[AgentRunEvent]`

Streaming variant. Yields `AgentRunEvent` objects as the loop progresses. Used by the WebSocket chat handler at `/app/v1/chat/ws`.

---

## Agent Loop — Core Flow

```
process_chat_message / process_chat_message_stream
  │
  ├── _build_system_prompt(user_id)
  │     ├── _load_system_header()          ← system/prompts/system_header.md
  │     ├── _load_agent_profile(user_id)   ← {user_id}/agents/{user_id}/profile.md
  │     └── _build_graph_summary(user_id)  ← Memgraph: tasks, goals, scores
  │
  ├── LLM call (with tool definitions)
  │
  └── loop: while LLM returns tool_use blocks:
        │
        ├── _execute_tool(user_id, tool_name, args)
        │     ├── _tool_list_tasks / _tool_get_task_details
        │     ├── _tool_create_goal / _tool_create_task
        │     ├── _tool_update_task / _tool_update_task_state / _tool_update_goal
        │     ├── _tool_delegate_to_agent  → publishes AgentJobEvent to AGENT_JOBS queue
        │     ├── _tool_create_agent       → provisions MinIO files + Redis cache
        │     ├── _tool_send_email / _tool_post_slack_message (via Comms Agent)
        │     └── _tool_invoke_skill / _tool_call_mcp_tool
        │
        └── LLM call with tool results appended to conversation
```

---

## System Prompt Construction

`_build_system_prompt()` assembles the LLM's system context in this order:

| Section | Source | Always included? |
|---|---|---|
| System header | `system/prompts/system_header.md` (MinIO) | ✅ |
| Agent profile | `{user_id}/agents/{user_id}/profile.md` | ✅ |
| Available tools | In-memory tool manifest | ✅ |
| Graph summary | Live Memgraph query (tasks, goals, scores) | ✅ |
| Working context | `{user_id}/agents/{user_id}/memory/working/context.md` | If present |

**Note:** The main orchestrator does NOT load episodic or semantic memory directly (those are used by `SubAgentRunner`). The orchestrator reads `context.md` as the agent's scratchpad.

---

## Tool Execution

`_execute_tool()` dispatches to a named handler by `tool_name`. All handlers are async methods on `MainOrchestrator`. Tool calls are serialised — the loop waits for each result before calling the LLM again.

**Graph tools** (read/write Memgraph via `GraphQueryEngine`):
- `list_tasks`, `get_task_details`, `get_goal_details`
- `create_goal`, `create_task`
- `update_task`, `update_task_state`, `update_goal`

**Agent tools** (manage sub-agents):
- `list_available_agents` — scans AgentCatalog
- `create_agent` — provisions `profile.md`, `manifest.json`, `config.json`, `knowledge.md` in MinIO
- `delegate_to_agent` — publishes `AgentJobEvent` to `AGENT_JOBS` broker queue; returns immediately

**Communication tools** (via Comms Agent or direct):
- `send_email`, `post_slack_message`

**Skill / MCP tools**:
- `invoke_skill` — loads SKILL.md from `system/skills/` or `{user_id}/skills/`, executes via LLM
- `call_mcp_tool` — calls an MCP server bound to this agent's config

---

## Sub-Agent Delegation

When the orchestrator calls `_tool_delegate_to_agent()`:

1. Writes delegation context to `{user_id}/agents/{agent_id}/memory/working/context.md`
2. Publishes `AgentJobEvent` (agent_id, task_id, session_id, batch_id, instructions) to `AGENT_JOBS` queue
3. Returns immediately to the LLM loop (non-blocking)

The orchestrator is re-engaged when `BatchCoordinator` publishes `DELEGATION_COMPLETE` to `TRIGGER_EVENTS` (see §11).

**Sub-agent confirmation rule (encoded in `system_header.md`):** Before calling `create_agent`, the orchestrator must present a proposal to the user and wait for explicit confirmation. `agent_id` is always a deterministic slug (never auto-generated UUID).

---

## Agent ID Determinism

`_tool_create_agent()` always uses a deterministic slug derived from the agent name:

```python
agent_id = _slugify(requested_agent_id or name)[:40]
```

The idempotency check (`storage.exists()`) prevents re-creation when the same name is used twice. UUID suffix generation was removed to prevent duplicate agents.

---

## Compact-at-60% Rule

When the orchestrator estimates that any agent's combined context (working + active episodic + semantic) has reached 60% of the 80,000-character budget, it should trigger a compact operation for that agent. The rule is encoded in `system_header.md`:

- Call `GET /intelligence/agents/{id}/memory/estimate` to check `utilization_pct`
- If ≥ 60%, call `POST /intelligence/agents/{id}/memory/compact` with a summary
- Report: which agent, why (% utilization), what was archived, how much was freed

---

## Key Files

| File | Role |
|---|---|
| `src/graphclaw/agent/main_orchestrator.py` | Main class and all tool handlers |
| `src/graphclaw/agent/graph_query_engine.py` | Memgraph query/write helpers |
| `src/graphclaw/gateway/prompts/system_header.md` | Rules + philosophy loaded per invocation |
| `src/graphclaw/infra/storage.py` | `StoragePaths` — all MinIO path construction |
| `src/graphclaw/api/intelligence.py` | REST endpoints wrapping MinIO memory files |
