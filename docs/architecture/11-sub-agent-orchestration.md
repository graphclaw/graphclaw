# 11 — Sub-Agent Orchestration

## Purpose

This document describes the `SubAgentRunner` — the executor responsible for carrying out tasks delegated by the `MainOrchestrator`. It covers the runner lifecycle, context loading strategy, tool dispatch, retry/timeout handling, and the event protocol used to report progress.

---

## Overview

`SubAgentRunner` (`src/graphclaw/agent/sub_agent_runner.py`) executes exactly one delegated task per invocation. It:

1. Reads agent profile + working context + episodic + semantic memory from MinIO
2. Runs a multi-turn LLM tool-use loop (max 15 iterations)
3. Emits structured `AgentUpdateEvent` objects to the `AGENT_UPDATES` broker queue
4. Returns a final status string: `COMPLETED`, `FAILED`, `TIMED_OUT`, or `CANCELLED`

Sub-agents **cannot delegate further** — the `delegate_to_agent` tool is excluded from their tool manifest. This prevents unbounded recursive delegation.

---

## Lifecycle — State Machine

```
IDLE
 │
 ▼ execute(job)
RUNNING
 │
 ├── COMPLETED   (LLM loop finished, no exceptions)
 ├── FAILED      (unhandled exception in LLM loop)
 ├── TIMED_OUT   (execution_timeout_seconds exceeded)
 └── CANCELLED   (asyncio.CancelledError received)
```

State is exposed via `runner.state` (enum) and `runner.status` (full `RunnerStatus` snapshot including elapsed_ms and last_heartbeat).

`is_idle` returns `True` for any terminal state (COMPLETED, FAILED, TIMED_OUT, CANCELLED) — a runner pool can reuse runners in these states.

---

## Entry Point

### `execute(job: AgentJobEvent) → str`

Called by the broker consumer when a message arrives on the `AGENT_JOBS` queue.

```
execute(job)
  │
  ├── _build_system_prompt(job)      ← MinIO reads (profile, working, episodic, semantic)
  │
  ├── asyncio.wait_for(
  │     _run_llm_loop(job),
  │     timeout=execution_timeout_seconds        ← default 600s
  │   )
  │
  ├── _heartbeat_loop(job)           ← background task, emits HEARTBEAT every 60s
  │
  └── emit COMPLETED / BLOCKED / TIMED_OUT to AGENT_UPDATES
```

---

## System Prompt Construction

`_build_system_prompt()` loads agent context from MinIO in this order:

| Section | Source | Condition |
|---|---|---|
| Base identity | Inline (agent_id, task_id, instructions) | Always |
| Agent profile | `{user_id}/agents/{agent_id}/profile.md` (user agents) | If storage present |
| Agent profile | `system/agents/{agent_id}/profile.md` (system agents) | If agent_source == "system" |
| Working context | `{user_id}/agents/{agent_id}/memory/working/context.md` | User agents only |
| Episodic memory | `{user_id}/agents/{agent_id}/memory/episodic/*.md` | Active entries only (archive/ excluded) |
| Semantic knowledge | `{user_id}/agents/{agent_id}/memory/semantic/*.md` | All .md files |

**Context budget:** 80,000 characters. When adding episodic or semantic sections would exceed this budget, loading stops. Episodic entries are loaded newest-first (sorted descending by filename); `knowledge.md` is always loaded first in the semantic tier.

**System agents** (`agent_source == "system"`, e.g. `comms`) have no per-user working memory. Only their system profile is loaded.

---

## LLM Tool-Use Loop

`_run_llm_loop()` runs up to `_MAX_ITERATIONS = 15` turns:

```
for iteration in range(15):
  │
  ├── llm.complete(messages, system_prompt, tools)
  │
  ├── if response has no tool_use blocks → break (done)
  │
  ├── for each tool_call:
  │     └── _dispatch_tool(tool_name, tool_input, job)
  │           ├── invoke_skill   → _tool_invoke_skill()
  │           └── call_mcp_tool  → _tool_call_mcp()
  │
  └── append assistant turn + tool results → next iteration
```

Each iteration emits a `PROGRESS` event with the first 200 characters of the LLM's text content.

---

## Available Tools (Sub-Agent Manifest)

Sub-agents have a restricted tool set — no delegation, no graph writes:

| Tool | Description |
|---|---|
| `invoke_skill` | Execute a named skill via WorkerPool |
| `call_mcp_tool` | Call an external MCP server tool (GitHub, Calendar, Slack, etc.) |

---

## Retry and Timeout Handling

Each tool call is wrapped by `_execute_tool_with_retries()`:

| Parameter | Default | Effect |
|---|---|---|
| `tool_timeout_seconds` | 120s | Per-call hard timeout via `asyncio.wait_for` |
| `tool_max_retries` | 0 | Extra attempts for retry-eligible tools |
| `retry_backoff_base_ms` | 200ms | Exponential backoff base |
| `retry_backoff_max_ms` | 1000ms | Backoff ceiling |
| `retryable_skills` | `set()` | Skill names eligible for retry |
| `retryable_mcp_tools` | `set()` | MCP tool names eligible for retry |

**Retryable signals for skills:** `"no idle skill workers"` or `"timeout"` in the error string.

**Retryable signals for MCP:** timeout, temporarily unavailable, connection reset/refused/unreachable.

All other errors are propagated immediately (no retry).

---

## Event Protocol — AGENT_UPDATES Queue

`SubAgentRunner` publishes `AgentUpdateEvent` objects to the `AGENT_UPDATES` broker queue:

| Event Type | When emitted |
|---|---|
| `STARTED` | Immediately on `execute()` entry |
| `PROGRESS` | Each LLM iteration that produces text content |
| `HEARTBEAT` | Every `heartbeat_interval` seconds (default 60s) |
| `BLOCKED` | On TIMED_OUT, FAILED, or CANCELLED |
| `COMPLETED` | Always on exit (carries `status` + `duration_ms`) |

All events carry `agent_id`, `task_id`, `session_id`, `batch_id`, and `parent_task_id` for audit correlation.

The heartbeat loop runs as a background `asyncio.Task` and is cancelled in the `finally` block regardless of outcome.

---

## AgentJobEvent — Input Payload

Published to `AGENT_JOBS` by `MainOrchestrator._tool_delegate_to_agent()`:

| Field | Type | Description |
|---|---|---|
| `agent_id` | str | Target sub-agent identifier |
| `task_id` | str | Graph task being delegated |
| `session_id` | str | Parent chat session |
| `user_id` | str | Explicit user ID (never derived from session) |
| `agent_source` | str | `"user"` or `"system"` — determines profile path |
| `parent_task_id` | str? | For nested delegation tracking |
| `batch_id` | str | Auto-generated `batch-{hex8}` for grouping |
| `instructions` | str | Full task instructions written by orchestrator |
| `dispatched_at` | datetime | UTC timestamp |

---

## Key Files

| File | Role |
|---|---|
| `src/graphclaw/agent/sub_agent_runner.py` | Runner, state machine, LLM loop, tool dispatch |
| `src/graphclaw/infra/broker.py` | `AGENT_JOBS` / `AGENT_UPDATES` queue constants |
| `src/graphclaw/infra/storage.py` | `StoragePaths` — profile, working, episodic, semantic paths |
| `src/graphclaw/skills/worker.py` | `WorkerPool` for skill execution |
| `src/graphclaw/mcp/registry.py` | `MCPRegistry` for MCP server resolution |
