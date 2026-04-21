# GraphClaw — Implementation Observations

> Living document. Add observations as we review each component. Use this to build the list of changes required to make GraphClaw work to design spec.
>
> **Reference:** Design spec is in `docs/graphclaw-requirements.md.md` — Sections 3–7 (data model, edges, state machine) and Sections 10–14 (follow-up timing, goal/constraint creation, briefing, explainability, autonomy).

---

## 1. Graph Database Layer

### What's Working
- PostgreSQL 18 + Apache AGE is running and healthy
- All 16 vertex labels and 15 edge labels are initialized via `scripts/init-db.sql`
- pgvector extension is loaded (1536-dim, IVFFlat cosine index on `node_embeddings`)
- Basic task creation and `DEPENDS_ON` edge creation write correctly to the DB

### Observations / Gaps

**O-DB-01: Tasks stored in generic `TaskNode`, not type-specific labels** ✅ FIXED
- `db/age/repository.py` `_resolve_label()`: now detects `task_type` attribute and routes to the correct type-specific AGE vertex label (`TaskAtomic`, `TaskComposite`, `TaskDelegated`, `TaskFollowUp`, etc.) via explicit mapping table
- `TaskFollowUp` uses the exact casing from `init-db.sql` (not `TaskFollowup`)
- All other nodes unchanged — fallback to `node_type` attribute or class name
- 12 integration tests: 11 parametrized (one per TaskType) + 1 confirming generic `TaskNode` label not used

**O-DB-02: Relationship edges never created** ✅ FIXED
- `api/graph.py` `create_task()`: now creates `OWNED_BY` (task → user) and `ASSIGNED_TO` (task → assignee) edges after node creation
- `agent/loop.py` `_tool_create_task()`: now creates `ASSIGNED_TO` edge when `assigned_to` arg provided; also sets `assigned_to` field on the `TaskNode`
- Both edge creations are guarded with `try/except` to prevent task creation failure if the target node doesn't exist
- 6 integration tests: 3 via REST API, 3 via AgentLoop direct call, all against real AGE

**O-DB-03: No UserNode records despite provisioning code existing**
- `UserNode` table has 0 rows
- `auth/provisioning.py` has complete logic to create UserNode + WorkspaceNode on OAuth login
- `WorkspaceNode` table also has 0 rows
- Suggests: auth/OAuth flow has never been exercised, or provisioning is not called from the login route
- All test data is hardcoded to owner `USER-dev-001` (a string, not a real graph node)

**O-DB-04: ScoreExplanation records never persisted**
- Every task has `last_scored_at: null` and `computed_priority: 0.0`
- `scoring/engine.py` computes `ScoreExplanation` in-memory but never writes results back to the graph
- Spec (§4.7, §13) requires ScoreExplanation records to be stored and queryable (audit mode)
- The scoring engine is never triggered — `AgentLoop.run_cycle()` must not be called on startup

**O-DB-05: `HandoffNode` missing from schema**
- Spec (§3.2) defines a Handoff Node as a coordination node type
- Not present in `scripts/init-db.sql` and not in Pydantic models

**O-DB-06: Dependency Gate not modeled as a node**
- Spec (§3.2) defines a Dependency Gate node (AND/OR)
- Current implementation puts gate logic only on the `DEPENDS_ON` edge (`gate_type` property)
- Whether this is intentional simplification or a gap needs clarification

---

## 2. Orchestrating Agent (`agent/loop.py`)

### What's Working
- `AgentLoop` class exists with `run_cycle()` and `process_chat_message()`
- ~15 LLM tools defined: `create_task`, `create_goal`, `update_task_state`, `propose_plan`, `execute_plan`, `delegate_to_agent`, `invoke_skill`, `call_mcp_tool`, etc.
- `AgentDispatchPlanner` implements topological sort (Kahn's algorithm) for parallel task dispatch
- Sub-agent runner (`sub_agent_runner.py`) runs delegated tasks in async executor with event emission

### Observations / Gaps

**O-AGT-01: No structured system prompt with graph design rules**
- The LLM decides which nodes/edges to create based solely on tool descriptions
- There is no explicit prompt that encodes the graph construction rules from §3–6 (e.g. "always create a FollowUp sibling when creating a Delegated task", "always create OWNED_BY and ASSIGNED_TO edges after creating a task node")
- The agent is likely making structurally incorrect graphs silently

**O-AGT-02: Auto-spawn FollowUp on Delegated task creation not wired** ✅ FIXED
- `api/graph.py` `create_task()`: when `task_type == DELEGATED`, now auto-creates a FollowUp sibling with `state=INACTIVE_PENDING`, `type_metadata=FollowUpMetadata(target_task_id=..., scheduled_fire_at=now+48h)`, and wires `FOLLOW_UP_FOR` edge; also updates the DELEGATED task's `type_metadata.follow_up_task_id`
- `agent/loop.py` `_tool_create_task()`: same logic for the agent tool path
- FollowUp default `scheduled_fire_at = now + 48h` (placeholder; O-AGT-03 will refine with the formula)
- 5 integration tests: spawns FollowUp, FOLLOW_UP_FOR edge exists, ATOMIC does not spawn, metadata has follow_up_task_id, AgentLoop path

**O-AGT-03: Follow-up timing formula not implemented**
- Spec (§10): `follow_up_timing = base_cadence × complexity_factor × resource_reliability × recency_adjustment`
- No file computes this formula
- Follow-up scheduling is not dynamic — there is no code that reads ResourceNode reliability or task complexity to set `scheduled_fire_at`

**O-AGT-04: Scoring engine never called on real data**
- `run_cycle()` exists but there is no evidence it is triggered on a schedule or at startup
- TriggerEngine (`triggers/engine.py`) is designed to call it but may not be started
- All tasks show `computed_priority: 0.0` confirming the engine has never run on stored data

**O-AGT-05: `propose_plan` → human review → commit workflow incomplete**
- Spec (§11.2): agent proposes a subgraph for human review before committing to the live graph
- `propose_plan` tool exists but there is no "pending proposal" state in the DB, no UI to review/edit proposals, and no `execute_plan` path that commits a reviewed proposal atomically

**O-AGT-06: Bottom-up goal inference not implemented**
- Spec (§11.1): agent should detect clusters of related tasks and propose Goal/Constraint nodes
- No clustering or pattern detection code exists in the codebase

---

## 3. State Machine (`state/machine.py`, `state/cascade.py`)

### What's Working
- `VALID_TRANSITIONS` dict is correct and matches spec (§7.1)
- Guards for terminal states, approval tasks, and INACTIVE_PENDING activation are complete
- Cascade completion logic (`cascade.py`) implements §7.2 including AND/OR gate, confidence halting, NEEDS_REVIEW routing, and recursive upward propagation

### Observations / Gaps

**O-SM-01: Cascade never triggered from API layer** ✅ FIXED
- `check_composite_completion()` now called in `transition_task()` after a COMPLETE transition
- Composite/milestone parent is auto-completed when all children finish (AND gate) or any child finishes (OR gate)
- Parent state persisted to AGE; 7 integration tests covering both direct and HTTP-endpoint paths

**O-SM-02: `INACTIVE_PENDING` activation never fires** ✅ FIXED
- `activate_next_in_chain()` now called in `transition_task()` after a COMPLETE transition
- Sequential-chain dependents (INACTIVE_PENDING tasks with DEPENDS_ON edge) are activated and persisted
- Also fixed `_deserialize_node_props` helper added to `cascade.py` so raw AGE data parses correctly

**O-SM-03: `state_history` never written** ✅ FIXED
- `transition_task()` already persisted state via `model_dump(mode="json")` — history WAS being written to AGE as a list of JSON strings
- Root cause: `get_state_history` endpoint returned the raw list of JSON strings without deserializing them
- Fix: added `task = _deserialize_task_fields(task)` in `get_state_history` before reading `state_history`
- cascade paths (`activate_next_in_chain`, `check_composite_completion`) already used `_sm.transition()` so they append history entries correctly
- 4 integration tests: single transition, multiple transitions, GET endpoint returns dicts, cascade-activated task history

**O-SM-04: Autonomy rules not checked before state updates** ✅ FIXED
- `state/machine.py` `StateMachine.transition()`: added `_guard_autonomy()` guard — blocks `ChangedBy.AGENT` when `task.autonomy.auto_update_allowed=False` (the default)
- HUMAN, CASCADE, and SYSTEM transitions always bypass the guard
- Default `AutonomyBlock` has `auto_update_allowed=False` — so all AGENT transitions require explicit opt-in per task
- Existing tests that used `ChangedBy.AGENT` for generic transition tests updated to use `ChangedBy.HUMAN`
- 9 new unit tests: blocked/allowed AGENT cases, no history on block, CASCADE/SYSTEM bypass, default blocks AGENT

---

## 4. Scoring Engine (`scoring/engine.py`)

### What's Working
- All 7 scoring factors implemented as pure functions (timeline, dependencies, critical_path, blocker, override, resource_risk, constraint)
- Default weights W1-W7 match spec
- Chain topology analysis (sequential suppression, urgency rollup) implemented in `topology.py`
- `ScoreExplanation` dataclass with per-factor breakdown and plain-English summaries

### Observations / Gaps

**O-SCR-01: Weights always use hardcoded defaults, never read from UserNode** ✅ FIXED
- `scoring/engine.py`: added `ScoringEngine.from_user(user: UserNode)` factory classmethod that reads `user.scoring_weights` (W1-W7) and constructs the engine with those values
- Zero-valued weights fall back to PRD defaults (handles new users with no learned weights)
- Original constructor unchanged — existing call sites continue to work
- 7 unit tests: custom weights, all-zero fallback, default model, partial custom, missing attribute, instance check, direct constructor

**O-SCR-02: ScoreExplanation not persisted** ✅ FIXED
- `score_task()`: after computing the `ScoreExplanation`, now updates `task.scoring.*` in-memory (all 7 factor values, `computed_priority`, `last_scored_at`, `score_reasoning`)
- `score_all()`: after assigning ranks, iterates scored tasks and calls `context.graph_repo.update_node()` to persist the scoring block and `last_scored_at` to AGE; guarded with `try/except` per task
- COMPLETE/CANCELLED tasks are correctly excluded from scoring (and their DB records are untouched)
- 7 integration tests: 3 in-memory mutations, 4 AGE persistence tests including completed-task exclusion

**O-SCR-03: Scoring context (`ScoringContext`) requires graph queries that may not be wired**
- `build_scoring_context()` in `AgentLoop` queries graph for relationships (goal priority, dependents, blocker type, resource reliability, constraints)
- If relationship edges don't exist (see O-DB-02), scoring context is empty → all factor scores = 0

---

## 5. Inbound Protocol (`inbound/`)

### What's Working
- `InboundProcessor` pipeline: resolve → extract signal → determine action → log
- `TaskResolver` stage 1: regex-based task ID extraction (`TSK-{INITIALS}-{DIGITS}-{TYPE_CODE}`)
- `StatusExtractor`: keyword pattern matching → StatusSignal → suggested TaskState
- `InboundResult` typed value object

### Observations / Gaps

**O-INB-01: Vector search (stage 2 fallback) is a stub**
- When no task ID found in message, falls back to pgvector cosine-similarity search
- `_vector_search()` has the SQL template but no embedding model is connected
- Returns `unmatched` for all messages without an explicit task ID

**O-INB-02: Task embeddings never generated**
- `node_embeddings` table exists in DB but has no rows
- No embedding generation is triggered on task creation or update
- Vector search will always return empty until embeddings are backfilled

**O-INB-03: Inbound pipeline not connected to a real message channel**
- No email IMAP, Slack webhook, or API endpoint that feeds real inbound messages into `InboundProcessor`
- The gateway channel connector (`connectors/`) exists but its connection to `InboundProcessor` is unclear

---

## 6. Daily Briefing (`agent/briefing.py`)

### What's Working
- `format_briefing()` formats the ranked action queue into a text summary with scores, recommended actions, and top scoring factor

### Observations / Gaps

**O-BRF-01: Only section 1 of 5 briefing sections implemented** ✅ FIXED
- `agent/briefing.py` `format_briefing()` now generates all 5 sections from PRD §12.1 via optional `BriefingContext` dataclass:
  - §1 Critical (ranked action queue, capped at 3)
  - §2 Inferences to Confirm
  - §3 Completed Since Last Briefing
  - §4 Ahead of the Curve (proactive items)
  - §5 Deferred Items Check (snoozed tasks)

**O-BRF-02: Cognitive load limit not enforced** ✅ FIXED
- `format_briefing()` now caps critical section at `MAX_CRITICAL_ITEMS = 3`; extra items include an autonomous note: "Agent handling N additional lower-priority items autonomously."
- Default `top_n` parameter changed from 5 → 3 (PRD §12.1)

**O-BRF-03: Interrupt threshold not checked** ✅ FIXED
- `format_briefing()` now accepts `interrupt_threshold: float | None` parameter; items with `final_score > interrupt_threshold` are tagged `[INTERRUPT]` in section 1
- Added `has_interrupt_items(queue, interrupt_threshold) -> bool` helper for callers (e.g. `TriggerEngine`) to decide whether to fire a mid-day notification
- Default threshold is `None` (opt-in, no change to existing call sites)

---

## 7. Auth & User Provisioning

### What's Working
- `auth/provisioning.py`: `UserProvisioningService.provision_new_user()` — creates UserNode, WorkspaceNode, S3 prefix, issues RS256 JWT
- Rollback mechanism on partial failure
- Idempotent: returns existing user if email already exists
- RS256 JWT issue and verification implemented in `auth/jwt.py`

### Observations / Gaps

**O-AUTH-01: Provisioning apparently never called in practice** ✅ FIXED
- `UserProvisioningService.provision_new_user()` is now called in the OAuth callback
- Added `get_provisioning_service()` dependency in `auth/routes.py` (reads `app.state.graph_store` + `app.state.storage_client`)
- Callback creates/looks up UserNode + WorkspaceNode + OWNS edge on every login; idempotent on repeat logins
- Falls back to token-only mode if provisioning service unavailable (no DB, or transient error)
- 5 integration tests covering: new user creation, workspace + OWNS edge, idempotency, deprovision, HTTP endpoint end-to-end

**O-AUTH-02: All test data uses hardcoded `USER-dev-001` string**
- Not a real UserNode — just a string property on tasks
- Means scoring weights, behavioral model, and autonomy defaults from UserNode are never applied to any real operation

---

## 8. Cockpit Frontend (graphclaw-cockpit)

### Observations / Gaps

**O-UI-01: Task type polymorphism not reflected in UI**
- Cockpit renders all tasks from the generic `TaskNode` response
- No UI differentiation for Delegated vs Composite vs Research task types
- `type_metadata` blocks (§4.3.1) are not displayed

**O-UI-02: Graph canvas shows tasks but no entity relationships**
- Because `OWNED_BY`, `ASSIGNED_TO`, `PART_OF` etc. edges don't exist, the graph canvas only shows task-to-task dependency edges
- The rich graph structure from §6 (goals → milestones → tasks → resources) is not visible

**O-UI-03: Scoring panel shows all zeros**
- All tasks have `computed_priority: 0.0` — the score breakdown panel in the cockpit displays nothing meaningful

---

## Summary: Root Cause Chain

The system's core problems trace back to a small number of foundational gaps that cascade into everything else:

```
1. Auth/login never creates UserNode
   → No real users in graph
   → Scoring weights, autonomy defaults, behavioral model never applied

2. Task creation writes to generic TaskNode (not type-specific labels)
   + No OWNED_BY / ASSIGNED_TO edges created
   → Graph is a flat list, not a property graph
   → Scoring context is empty (no relationships to traverse)
   → All factor scores = 0

3. Scoring engine never triggered (run_cycle() not called on schedule)
   → computed_priority always 0
   → Action queue empty
   → Briefing has nothing to rank

4. Cascade and chain activation not called from API state transition handler
   → Sequential chains never advance
   → Composite parents never auto-complete

5. No embeddings generated on task creation
   → Vector search fallback always fails
   → Inbound messages without explicit task IDs never resolved
```

---

---

## 9. Dead Code / Shim Layer (`db/`)

### What Was Found

The `db/` directory contains a shim layer sitting on top of the real implementation in `db/age/`. Several files are either dead (nothing imports them) or near-duplicate re-exports.

### Observations / Gaps

**O-DEAD-01: `db/graph_repository.py` and `db/_compat.py` are completely dead** ✅ FIXED
- Deleted `db/graph_repository.py` and `db/_compat.py`
- Removed `GraphRepository` export from `db/__init__.py`
- `db/__init__.py` now exports only: `GraphStore`, `GraphQueryEngine`, `create_graph_store`, `create_query_engine`, `create_pool`, `get_connection`

**O-DEAD-02: `db/queries/` contains three near-duplicate dead files** ✅ FIXED
- Deleted `db/queries/critical_path.py`, `db/queries/dependencies.py`, `db/queries/scoring_queries.py`, and the entire `db/queries/` directory
- Deleted `db/utils.py` (was only imported by those three files)
- Canonical implementations remain in `db/age/queries/` (critical_path.py, dependencies.py, scoring_queries.py, engine.py)

**O-DEAD-03: `db/connection.py` shim removed after import migration** ✅ FIXED
- Deleted `db/connection.py` after migrating all live imports to `db/age/connection.py`
- Updated all remaining references across CLI modules, integration tests, and utility scripts
- Canonical connection import path is now `graphclaw.db.age.connection` (with top-level facade exports still available from `graphclaw.db`)

**Files deleted (safe):**
- `db/graph_repository.py`
- `db/_compat.py`
- `db/queries/critical_path.py`
- `db/queries/dependencies.py`
- `db/queries/scoring_queries.py`
- `db/utils.py`
- `db/connection.py`

**Files updated:**
- `db/__init__.py` — `GraphRepository` export removed
- CLI, tests, and scripts now import connection helpers from `db/age/connection.py`

---

## 10. MCPServerNode Design Split

Section status: COMPLETE (3/3 subsections done: O-MCP-01, O-MCP-02, O-MCP-03)

### Observations / Gaps

**O-MCP-01: MCPServerNode Pydantic model must be kept — it is the MinIO JSON schema** ✅ FIXED
- `MCPRegistry` (`mcp/registry.py`) stores server configs as JSON files in MinIO at `{user_id}/mcp/servers/{server_id}.json`
- `MCPServerNode` Pydantic model in `models/nodes.py` is the schema for that JSON — required and correct
- The docstring in `api/mcp_registry.py` incorrectly says "persisted as MCPServerNode vertices in the graph database" — MinIO is the actual store
- Updated `api/mcp_registry.py` module docs to describe object-storage JSON persistence and ownership checks against per-user stored documents

**O-MCP-02: MCPServerNode AGE vertex label and `GRANTS_ACCESS_TO_MCP` edge label are dead schema** ✅ FIXED
- The AGE vertex label `MCPServerNode` and edge label `GRANTS_ACCESS_TO_MCP` are initialized in `scripts/init-db.sql` and created by a migration in `migrations/catalogue.py`
- No code writes to either — the graph schema is orphaned
- Should be removed from `scripts/init-db.sql` and reversed/removed from `migrations/catalogue.py`
- Removed MCP label creation from `scripts/init-db.sql` (new environments no longer create dead MCP labels)
- Replaced migration `0003` with a no-op historical placeholder so sequential migration numbering remains intact
- Kept `0006` as cleanup migration and corrected it to use AGE-supported APIs (`ag_catalog.cypher`, `ag_catalog.drop_label(..., false)`) for legacy databases

**O-MCP-03: `GraphStoreDep` in `api/mcp_registry.py` is only used for approval service, not MCP nodes** ✅ FIXED
- `GraphStoreDep` is imported and used in `list_mcp_approvals` for `GatedApprovalService` (trust tier escalation)
- All MCP CRUD routes use `MCPRegistryDep` (MinIO) — the graph store is not involved in MCP persistence
- Docstring correction needed in `api/mcp_registry.py`
- Updated route-module documentation to explicitly state: MCP CRUD is storage-backed via `MCPRegistryDep`, while `GraphStoreDep` is scoped to `/mcp-approvals` only

Validation completed against real local services:
- Added integration test `tests/test_mcp/test_mcp_design_split_integration.py`
- Verified live MinIO JSON persistence through `MCPRegistry.register/get`
- Verified live graph-backed approvals through `GatedApprovalService.get_pending_approvals`
- Verified legacy MCP labels absent in local AGE catalog query

---

## 11. Main Orchestrator — Agent Intelligence Redesign

> Status update: this section is now **implemented**. Rename migration in §11.7 is complete with backward-compatible aliases.

### 11.1 System Prompt Header — Externalize to MinIO ✅ FIXED

**Problem:** `_SYSTEM_PROMPT_HEADER` at `loop.py:74` is a hardcoded Python string. Cannot be updated without a code deployment.

**Decision:** Move to `system/prompts/system_header.md` in MinIO. Seeded idempotently at gateway startup. Editable by operators without redeployment. `_build_system_prompt()` loads it via storage client with fallback to the current hardcoded default.

**New `StoragePaths` methods needed:**
- `system_prompt_header()` → `"system/prompts/system_header.md"`
- `system_prompts_prefix()` → `"system/prompts/"`

Implemented:
- Added `StoragePaths.system_prompt_header()` and `StoragePaths.system_prompts_prefix()` in `src/graphclaw/infra/storage.py`
- Added gateway startup seeding (`seed_system_content`) in `src/graphclaw/gateway/app.py` lifespan
- `AgentLoop._build_system_prompt()` now loads header from storage via `_load_system_header()` with fallback to hardcoded default

### 11.2 Tool Set Registry — Two-Tier Lazy Loading ✅ FIXED

**Problem:** 16 tool schemas sent on every LLM call (~4,800 tokens). 4 tools return "not configured" silently when optional dependencies are `None`. The LLM may attempt to call them and waste a round-trip.

**Decision:** Replace flat 16-tool list with a `ToolSetRegistry` class in `agent/tool_registry.py`.

**Tier 1 — Core set (always present, ~600 tokens):**
`list_tasks`, `get_task_details`, `update_task_state`, `load_tool_set`, `read_knowledge`, `list_available_agents`

**Tier 2 — Named sets (activated on demand via `load_tool_set(name)`):**

| Set | Tools |
|---|---|
| `task_management` | create_task, update_task, create_goal, update_goal |
| `planning` | propose_plan, execute_plan |
| `skills` | list_available_skills, invoke_skill |
| `mcp` | list_mcp_tools, call_mcp_tool |
| `delegation` | delegate_to_agent, create_agent |

- The `inbox` tool set is removed — comms agent replaces `check_inbox` (see §12)
- Tools whose backing dependencies (`skill_registry`, `mcp_registry`, etc.) are `None` at construction time are excluded from their set — prevents "not configured" waste
- `ToolSetRegistry` tracks `_active_sets: set[str]` per session. Activating a set persists for the whole conversation turn
- Compact manifest (~150 tokens) injected into system prompt tells the LLM what sets are available

Implemented:
- Added `ToolSetRegistry` in `src/graphclaw/agent/tool_registry.py`
- Integrated registry into `AgentLoop` (`load_tool_set`, active-tool fetch per turn, per-message reset)
- Added/kept core tools: `list_tasks`, `get_task_details`, `update_task_state`, `load_tool_set`, `read_knowledge`, `list_available_agents`
- Removed `check_inbox` tool path from orchestrator tool execution

### 11.3 System Knowledge Base — Domain Rules as Documents ✅ FIXED

**Problem:** The LLM has no access to graph construction rules, state machine rules, or node-type reasoning from the spec. It guesses node types and edges from conversation context alone.

**Decision:** Create 6 Markdown files seeded at `system/knowledge/` on gateway startup. Agent calls `read_knowledge(topic)` to load them on demand.

**New `StoragePaths` methods needed:**
- `system_knowledge(topic)` → `f"system/knowledge/{topic}.md"`
- `system_knowledge_prefix()` → `"system/knowledge/"`

**Knowledge files to seed:**

| File | Content source |
|---|---|
| `node_creation_rules.md` | §3.1 — when to create each of the 11 task types |
| `edge_creation_rules.md` | §6 — DEPENDS_ON vs BLOCKS vs SPAWNED_FROM vs PART_OF |
| `state_machine_rules.md` | §7.1–7.2 — valid transitions, guards, cascade rules |
| `goal_inference_rules.md` | §11.1 — bottom-up inference, top-down decomposition |
| `scoring_context.md` | §4.1, §13 — W1-W7 meanings, scoring impact |
| `follow_up_timing.md` | §10–11 — urgency escalation, follow-up cadence by domain |

**`KnowledgeBase` class** in `agent/knowledge.py`: `read(topic)`, `get_index()`, `list_topics()`. In-session cache to avoid re-fetching the same document in one conversation.

**`read_knowledge` tool** added to core set:
```
name: read_knowledge
description: Load domain rules before creating nodes/edges. Topics:
             node_creation_rules | edge_creation_rules | state_machine_rules |
             goal_inference_rules | scoring_context | follow_up_timing
```

Implemented:
- Added `KnowledgeBase` in `src/graphclaw/agent/knowledge.py` with in-session cache
- Added `StoragePaths.system_knowledge()` and `system_knowledge_prefix()`
- Added `read_knowledge` tool handler in `AgentLoop`
- Added startup seeding for all 6 knowledge docs in `src/graphclaw/gateway/seeding.py`

### 11.4 Context Compression — `ContextManager` ✅ FIXED

**Problem:** `chat.py:send_chat_message` loads history from MinIO but does not pass it to `process_chat_message`. The agent is completely memoryless — each message is processed as if it's the first. Even when history is fixed (§11.6), long conversations will hit context limits.

**Decision:** Create `ContextManager` in `agent/context.py` with a 5-stage compression pipeline:

1. **Session entity extraction** — scan history for entity IDs (TSK-*, GOAL-*, MCP-*) and state change events → build compact `## Session State` block always included
2. **Sliding window** — keep last 20 turns verbatim; older turns enter compression
3. **Tool-call collapse** — compress tool call + result triples into 1-line summaries: `[tool: create_task("Deploy API") → TSK-AG-001-AT created]`
4. **Rolling LLM summary** — when older turns exceed 30, make a cheap LLM call (haiku, no tools, 512 tokens) to generate `## Previous Conversation Summary` block
5. **Token budget check** — use `LLMClient.count_tokens()` pre-call; if over 80k tokens, reduce window and re-apply

`ContextManager` returns a `CompressedContext` dataclass with entity block, summary block, recent messages, compressed tool call summaries.

Implemented:
- Added `ContextManager` + `CompressedContext` in `src/graphclaw/agent/context.py`
- Integrated compression in both `process_chat_message()` and `process_chat_message_stream()`
- Includes entity extraction, sliding window, tool-call collapse, rolling summary, and token-budget pass

### 11.5 max_tokens Fix ✅ FIXED

**Problem:** `loop.py:493` hardcodes `max_tokens=1024`. Complex plan responses are being truncated.

**Decision:** Increase to `max_tokens=4096` for all main agent calls. The `propose_plan` inner call at `loop.py:1514` uses `max_tokens=2048` — increase that to `4096` too.

Implemented:
- Main chat call uses `max_tokens=4096`
- Streaming chat call uses `max_tokens=4096`
- `propose_plan` inner LLM call updated to `max_tokens=4096`

### 11.6 Chat Memoryless Bug Fix ✅ FIXED

**Problem (critical):** In `api/chat.py:225`:
```python
return await agent_loop.process_chat_message(user_id, user_text)
```
`conversation_history` defaults to `None`. History is loaded from MinIO (line 174) but never passed to the agent.

**Fix:** Pass history and a session_id:
```python
session_id = f"ses-{msg_index:06d}"
agent_text = await agent_loop.process_chat_message(
    user_id,
    body.content,
    conversation_history=history,
    session_id=session_id,
)
```
Schema note: history entries use `role="agent"`, `process_chat_message` already remaps this to `"assistant"` at line 477 — no further changes needed there.

Implemented:
- `send_chat_message()` now passes `conversation_history` + `session_id` to `process_chat_message()`
- Added explicit `session_id = f"ses-{msg_index:06d}"`
- Current message is excluded from forwarded history (`history[:-1]`) to avoid duplicate prompt entries

### 11.7 Rename `loop.py` → `main_orchestrator.py` ✅ FIXED

**Decision:** Rename file and class (`AgentLoop` → `MainOrchestrator`) for clarity. It is the primary LLM-facing orchestrator, not an event loop. Import sites to update: `api/deps.py`, gateway lifespan, all tests.

Implemented:
- Added canonical module `src/graphclaw/agent/main_orchestrator.py` and renamed class to `MainOrchestrator`
- Removed `src/graphclaw/agent/loop.py` compatibility shim after import-site migration
- Updated runtime import sites (gateway/app, chat_streaming, event_consumer, CLI) to use `MainOrchestrator`
- Updated package export in `src/graphclaw/agent/__init__.py` to expose `MainOrchestrator` with backward-compatible `AgentLoop` alias
- Swept Python import sites and migrated legacy `from graphclaw.agent.loop import AgentLoop` imports

### 11.8 Files to Create / Modify (for §11)

| # | File | Action |
|---|---|---|
| 1 | `src/graphclaw/infra/storage.py` | Add `system_knowledge()`, `system_knowledge_prefix()`, `system_prompt_header()`, `system_prompts_prefix()` |
| 2 | `src/graphclaw/agent/knowledge.py` | NEW: `KnowledgeBase` class |
| 3 | `src/graphclaw/agent/tool_registry.py` | NEW: `ToolSetRegistry` class |
| 4 | `src/graphclaw/agent/context.py` | NEW: `ContextManager` + `CompressedContext` |
| 5 | `src/graphclaw/agent/loop.py` → `main_orchestrator.py` | MODIFY: integrate all, fix max_tokens, remove check_inbox, add list_available_agents |
| 6 | `src/graphclaw/api/chat.py` | MODIFY: pass history + session_id to agent |
| 7 | Gateway lifespan | MODIFY: call `seed_system_content()` on startup |

Progress snapshot:
- #1 ✅ done
- #2 ✅ done
- #3 ✅ done
- #4 ✅ done
- #5 ✅ done
- #6 ✅ done
- #7 ✅ done

---

## 12. Sub-agent System — Comms Agent + Agent Discovery

Section status: COMPLETE (5/5 subsections done: 12.1, O-SAGENT-01, O-SAGENT-02, 12.3, 12.4)

### 12.1 Agent Discovery Gap

**Problem:** The main agent has no way to discover what agents are available. `delegate_to_agent` requires passing `agent_id` explicitly, but there is no `list_available_agents` tool and no agent catalog. The LLM cannot know what agents exist unless told in the prompt.

**Decision:**
- Add `list_available_agents` tool to the core tool set
- Inject a compact agent catalog into the system prompt (built by `AgentCatalog` class in `agent/catalog.py`)
- Every agent (system or user-created) has a `manifest.json` with `agent_id`, `type`, `description`, `capabilities`, `tool_hint`
- The compact catalog (~100 tokens) in the system prompt: agent ID + `tool_hint` line per agent

✅ FIXED
- Added `AgentCatalog` (`src/graphclaw/agent/catalog.py`) and integrated it into `MainOrchestrator` system prompt construction via `_agent_catalog.get_compact_catalog(user_id)`
- Added core tool handler `_tool_list_available_agents()` in `src/graphclaw/agent/main_orchestrator.py`
- `ToolSetRegistry` core set includes `list_available_agents` (`src/graphclaw/agent/tool_registry.py`)

### 12.2 AgentJobEvent Bugs

**O-SAGENT-01: `user_id` derived from `session_id` — broken** ✅ FIXED
- `sub_agent_runner.py:382`: `user_id = job.session_id.split("-")[1] if "-" in job.session_id else ""`
- Session IDs have format `"ses-000000"` — the split yields the index, not a user ID
- Profile lookup fails silently; agent runs without context

**O-SAGENT-02: No `agent_source` field — system agents not distinguishable** ✅ FIXED
- `AgentJobEvent` has no field to indicate whether the target agent is a system agent or user agent
- `SubAgentRunner` always looks up profile at `{user_id}/agents/{agent_id}/profile.md`
- System agents at `system/agents/{agent_id}/profile.md` are never found

Implemented:
- `AgentJobEvent` now includes explicit `user_id` and `agent_source` in `src/graphclaw/agent/sub_agent_runner.py`
- `_tool_delegate_to_agent()` in `src/graphclaw/agent/main_orchestrator.py` resolves and publishes both fields into `AGENT_JOBS`
- `SubAgentRunner._build_system_prompt()` branches on `job.agent_source` and loads system-agent profile from `system/agents/{agent_id}/profile.md`

### 12.3 System Agent Directory Layout

New layout under `system/`:
```
system/
├── prompts/system_header.md              ← main agent system prompt
├── knowledge/{topic}.md                  ← domain rules
├── skills/definitions/{skill}/SKILL.md   ← execution skills (existing)
└── agents/
    └── comms/
        ├── profile.md                    ← agent persona + channel instructions
        ├── manifest.json                 ← capabilities + tool_hint
        └── config.json                   ← channel config
```

User-created agents remain at `{user_id}/agents/{agent_id}/` and also gain a `manifest.json`.

**New `StoragePaths` methods needed:**
- `system_agent_root(agent_id)` → `f"system/agents/{agent_id}/"`
- `system_agent_profile(agent_id)` → `f"system/agents/{agent_id}/profile.md"`
- `system_agent_manifest(agent_id)` → `f"system/agents/{agent_id}/manifest.json"`
- `system_agent_config(agent_id)` → `f"system/agents/{agent_id}/config.json"`
- `system_agents_prefix()` → `"system/agents/"`
- `agent_manifest(user_id, agent_id)` → `f"{user_id}/agents/{agent_id}/manifest.json"`
- `agents_prefix(user_id)` → `f"{user_id}/agents/"`

✅ FIXED
- Added all `StoragePaths` helpers above in `src/graphclaw/infra/storage.py`
- Updated `_tool_create_agent()` (`src/graphclaw/agent/main_orchestrator.py`) to write user-agent `manifest.json` alongside `profile.md`, `config.json`, and working context
- Added legacy arg compatibility in `_tool_create_agent()` (`profile`/`capabilities` aliases) while standardizing on `name`/`purpose`/`skills`

### 12.4 Comms Agent

**Decision:** Replace the `check_inbox` tool (which reads from MinIO preprocessed inbox) with a `comms` system agent that connects to live communication channels via MCP tools.

**Agent ID:** `comms`  
**Storage location:** `system/agents/comms/`  
**Type:** system (shared across all users)

**How it works:**
1. Main agent calls `delegate_to_agent(task_id=..., agent_id="comms", instructions="Check for replies from john@example.com about API proposal")`
2. `AgentJobEvent` published to `AGENT_JOBS` with `agent_source="system"`
3. `SubAgentRunner` loads `system/agents/comms/profile.md` as system prompt
4. Runner's LLM loop uses existing `call_mcp_tool` to call user's configured email/Telegram MCP servers
5. Results emitted as `AGENT_UPDATES` PROGRESS events → task `intelligence` field updated → SSE to frontend

**Extensibility:** New channels (WhatsApp, Slack, SMS) are added by configuring new MCP servers — no code changes to the comms agent.

**`check_inbox` tool removed** from `loop.py` tool sets.

✅ FIXED
- Seeded `system/agents/comms/{profile.md,manifest.json,config.json}` via `seed_system_content()` (`src/graphclaw/gateway/seeding.py`)
- Gateway lifespan invokes seeding on startup (`src/graphclaw/gateway/app.py`)
- `check_inbox` removed from orchestrator tool paths; delegation flow uses `delegate_to_agent` and `AGENT_JOBS`
- Delegation now validates manifest existence before publish (system first, then user), preventing silent dispatch to unknown agents

### 12.5 Files to Create / Modify (for §12)

| # | File | Action |
|---|---|---|
| 1 | `src/graphclaw/infra/storage.py` | Add system_agent_* and agent_manifest paths |
| 2 | `src/graphclaw/agent/catalog.py` | NEW: `AgentCatalog` class (discovery + compact list) |
| 3 | `src/graphclaw/agent/sub_agent_runner.py` | Add `user_id`/`agent_source` to `AgentJobEvent`; fix `_build_system_prompt()` |
| 4 | `src/graphclaw/agent/main_orchestrator.py` | Add `list_available_agents` tool; resolve/validate agent source in `_tool_delegate_to_agent()`; pass `user_id`/`agent_source` in `AgentJobEvent`; remove `check_inbox` |
| 5 | Gateway lifespan / seeding | Seed `system/agents/comms/` (profile.md, manifest.json, config.json) |

Progress snapshot:
- #1 ✅ done
- #2 ✅ done
- #3 ✅ done
- #4 ✅ done
- #5 ✅ done

Validation completed against real local services (MinIO + AGE):
- Extended integration suite `tests/test_agent/test_loop_new_tools_integration.py`
- Verified user-agent creation writes `manifest.json` and appears in `list_available_agents`
- Verified `delegate_to_agent` publishes `AgentJobEvent` with `agent_source="system"` for `comms` and `agent_source="user"` for user-created agents
- Verified `AgentJobEvent.user_id` is propagated from caller (not derived from `session_id`)

---

---

## 13. Multi-Tenancy and Query Isolation

Section status: COMPLETE (Phase 1 scope) (4/4 subsections addressed)

### What was implemented

**O-TENANT-01: `_fetch_active_tasks()` returns all users' tasks** ✅ FIXED
- `src/graphclaw/agent/main_orchestrator.py` `_fetch_active_tasks(user_id: str | None)` now uses user-scoped retrieval when `user_id` is provided (`list_nodes_by_user("TaskNode", user_id)`), with terminal-state filtering preserved.
- `run_cycle()` now accepts optional `user_id` and fetches tasks in that scope for chat-driven cycles.

**O-TENANT-02: `OWNED_BY` edge is written but never read** ✅ FIXED
- `src/graphclaw/db/age/repository.py` `list_nodes_by_user("TaskNode", user_id)` now reads ownership from graph edges:
  - primary: `MATCH (u:UserNode {id: ...})<-[:OWNED_BY]-(n)`
  - fallback: `owned_by` property match for legacy rows
- Results are de-duplicated by task id when both edge and property are present.
- Added live integration coverage in `tests/test_db/test_user_scope_integration.py`:
  - task visible via `OWNED_BY` edge only
  - no duplicate result when both edge + property exist

**O-TENANT-03: Organization/Workspace scoping is Phase 2 — nothing is wired** ✅ TRACKED (Phase 2 boundary)
- Confirmed as intentionally out of Phase 1 scope.
- No partial org/workspace implementation introduced in Phase 1 to avoid unsafe half-isolation semantics.
- User-level isolation is now enforced for current cycle/listing paths; org/workspace scoping remains an explicit Phase 2 item.

**O-TENANT-04: Full table scan on every LLM system prompt build** ✅ FIXED
- `src/graphclaw/agent/main_orchestrator.py` `_build_graph_summary(user_id)` now triggers user-scoped scoring (`run_cycle(user_id=user_id)`) instead of global scans.
- Added scoped queue cache (`_last_queue_by_scope`) so summaries for a user reuse that user's queue instead of cross-user/global queue data.
- Added unit regression in `tests/test_agent/test_loop.py` to assert `_build_graph_summary()` invokes user-scoped cycle execution.

Validation completed against real local services:
- `pytest tests/test_db/test_user_scope_integration.py -v` (10 passed; AGE integration)
- `pytest tests/test_agent/test_loop_new_tools_integration.py -k "ListTasksWithParams" -v` (4 passed; scoped task listing)
- `pytest tests/test_agent/test_loop.py -k "TestRunCycle or TestGraphSummaryScoping" -v` (6 passed; orchestrator scoping)

---

### 14. Smart Node Retrieval — Context Optimization Design
goal's task subgraph. Without `goal_id`, return only the top-N scored tasks for the user.

Section status: COMPLETE (Phase 1 scope) (5/5 subsections addressed)

**O-SMART-01: Goal-first summary + user-scoped queue + cache invalidation** ✅ FIXED
- `src/graphclaw/agent/main_orchestrator.py` `_build_graph_summary(user_id)` now:
  - loads user goals via `list_nodes_by_user("GoalNode", user_id)`
  - skips closed goals (`COMPLETE`, `ABANDONED`)
  - includes goal title/state/priority and `node_intelligence.summary` (or `intelligence` fallback)
  - renders top-5 tasks from the scoped scoring queue
- Added `_invalidate_cached_queue(user_id)` and wired it to mutation paths so retrieval does not use stale queue snapshots after writes.

**O-SMART-02: `list_tasks` progressive filters + default top-N scored behavior** ✅ FIXED
- `src/graphclaw/agent/main_orchestrator.py` `_tool_list_tasks()` supports:
  - `goal_id`, `state_filter`, `task_type`, `limit` (max 50), `include_completed`, `assigned_to`
- For non-goal queries, results are now ordered by the latest user-scoped scored queue (priority-first).
- If scored data is absent, tool falls back to a scoped `run_cycle(user_id=...)`; unmatched leftovers are appended deterministically.

**O-SMART-03: `get_task_details` graph-aware expansion** ✅ FIXED
- `src/graphclaw/agent/main_orchestrator.py` `_tool_get_task_details()` now enriches node details with:
  - `depends_on` (outbound `DEPENDS_ON`)
  - `blocks` (outbound `BLOCKS`)
  - `blocked_by` (inbound `BLOCKS`)
  - `part_of_goal` (outbound `PART_OF`)
  - `assigned_to_user` (outbound `ASSIGNED_TO`)
  - `state_history` trimmed to last 3 entries

**O-SMART-04: Retrieval guidance available in system knowledge** ✅ FIXED
- `src/graphclaw/gateway/seeding.py` `goal_inference_rules` now encodes the retrieval strategy:
  - start from goals
  - expand into tasks on demand
  - skip completed goals unless explicitly requested
  - use `list_tasks(goal_id=...)` for scoped drill-down

**O-SMART-05: Pre-computed `node_intelligence.summary` population** ✅ TRACKED (Phase 2 boundary)
- Retrieval now consumes `node_intelligence.summary` when present.
- Background production of summaries on task-change events remains a Phase 2 intelligence-pipeline item.

Validation completed against real local services (AGE + MinIO):
- `pytest tests/test_agent/test_loop_new_tools_integration.py -k "ListTasksWithParams or GetTaskDetailsWithEdges" -v`
- `pytest tests/test_agent/test_loop.py -k "SmartRetrievalBehaviors or GraphSummaryScoping" -v`

---

## Open Questions (to resolve through further review)

- [ ] Is `auth/routes.py` wired to call `UserProvisioningService.provision_new_user()`?
- [ ] Is `AgentLoop.run_cycle()` started as a background task on gateway startup?
- [ ] Is `TriggerEngine.start()` called from the FastAPI lifespan handler?
- [ ] Does `create_task` in `api/graph.py` create `OWNED_BY` / `ASSIGNED_TO` edges after the node?
- [ ] Is the `BATCHED_IN` edge created when a CheckinNode is created?
- [ ] Is the inbound connector (email/Slack) hooked into `InboundProcessor`?
- [ ] Are task embeddings generated anywhere on create/update?

---

---
> **Observations appended: 2026-04-19T00:00:00Z**
> Topics: Context intelligence append/overwrite, system-level deployment artifacts, log format & CloudWatch, state machine cascade wiring, scoring recomputation, storage directory structure.

---

## 15. Context Intelligence — Observations Blob Must Be Append-Only with Timestamps

Section status: COMPLETE (Phase 1 scope) (5/5 subsections addressed)

**O-CTX-01: Working context writes must be append-only** ✅ FIXED
- `src/graphclaw/inbound/intelligence_agent.py` no longer rewrites heading blocks in-place.
- Memory notes now append to existing working context stream instead of structural insertion under a sentinel heading.

**O-CTX-02: Every working-context entry needs an explicit timestamp** ✅ FIXED
- Added `_utc_now_iso_z()` and `_append_working_note()` helpers in `src/graphclaw/inbound/intelligence_agent.py`.
- Working context now stores one JSON line per note with fields:
  - `timestamp`
  - `source` (`inbound_intelligence`)
  - `note`

**O-CTX-03: Intelligence trim spillover must be persisted (not dropped)** ✅ FIXED
- `src/graphclaw/inbound/intelligence_agent.py` now archives spillover text when `MAX_INTELLIGENCE_WORDS` is exceeded.
- Added `StoragePaths.agent_intelligence_archive(user_id, agent_id, task_id, date)` in `src/graphclaw/infra/storage.py` and used it for archived overflow blocks.
- Removed opaque inline marker (`... N older entries archived`) from the node field; overflow is written to durable object storage.

**O-CTX-04: Remove fragile `## Recent Context` heading dependency** ✅ FIXED
- Memory appends are now format-driven (JSON lines), not heading-driven.
- Existing arbitrary content is preserved and new structured entries are appended safely.

**O-CTX-05: Episodic flush lifecycle (auto-archive + clear trigger policy)** ✅ TRACKED (Phase 2 boundary)
- Current API already supports explicit archival flow via `/intelligence/agents/{agent_id}/memory/compact`.
- Automated flush policy (when to archive/clear working context without manual compact call) remains a Phase 2 memory-lifecycle item.

Validation completed against real local services (AGE + MinIO):
- `pytest tests/test_inbound/test_intelligence_agent_integration.py -v` (real AGE + MinIO path validation)
- `pytest tests/test_inbound/test_intelligence_agent.py tests/test_infra/test_storage_paths.py -v` (unit and path contract coverage)

---

## 16. System-Level Prompts / Skills — Deployment Artifact Gap

### Current State
- `.claude/skills/` — 21 `SKILL.md` files for **Claude Code dev use only** (not seeded to object storage)
- `gateway/seeding.py` seeds: `system/prompts/system_header.md`, `system/knowledge/*.md` (6 files), `system/agents/comms/`
- `StoragePaths.system_skill_definition(skill_name)` → `system/skills/definitions/{skill_name}/SKILL.md` defined in code but **never populated** by `seeding.py`
- All seeded content is **inline Python strings** inside `seeding.py` — not versioned as separate files

### Gaps
1. `system/skills/definitions/` is never seeded. Any agent call to `StoragePaths.system_skill_definition()` at runtime returns 404.
2. The `.claude/skills/` SKILL.md files are Claude Code dev aids — distinct from runtime skills the agent loop can invoke. There are currently no runtime skill definitions in the repo.
3. Inline Python strings in `seeding.py` are hard to review, diff, and edit. There is no canonical "initial object storage snapshot" in the repo.

### Recommendation
- Create `src/graphclaw/gateway/system_seed/` matching the `system/` object storage hierarchy:
  ```
  src/graphclaw/gateway/system_seed/
  ├── prompts/system_header.md
  ├── knowledge/{6 topic files}.md
  ├── skills/definitions/{skill_name}/SKILL.md   ← runtime agent skills
  └── agents/comms/{profile.md,manifest.json,config.json}
  ```
- Refactor `seeding.py` to walk this directory tree via `Path(__file__).parent / "system_seed"` and upload each file idempotently. No inline strings.
- User-created objects (config.json, agents, memory) remain created at login time — no change needed there.

---

## 17. Log Files — Template, Format & Sink Architecture

Section status: COMPLETE (Phase 1 scope) (5/5 subsections addressed)

**O-LOG-01: Pipe-delimited line format support** ✅ FIXED
- Added shared formatting helpers in `src/graphclaw/infra/sinks/formatting.py` with fixed-column pipe output:
  - `timestamp|level|service|event_type|session_id|user_id|task_id|extra_json`
  - `-` placeholders for absent optional fields
  - pipe escaping (`|` → `\|`) and compact JSON extras
- `AsyncLogger` now emits second-precision UTC timestamps (`...Z`) in `src/graphclaw/infra/logger.py`.

**O-LOG-02: Sink abstraction layer for logger fan-out** ✅ FIXED
- Added sink interface and implementations:
  - `src/graphclaw/infra/sinks/base.py` (`LogSink` ABC)
  - `src/graphclaw/infra/sinks/stdout.py` (`StdoutSink`)
  - `src/graphclaw/infra/sinks/object_storage.py` (`ObjectStorageSink`)
  - `src/graphclaw/infra/sinks/cloudwatch.py` (`CloudWatchSink`)
  - `src/graphclaw/infra/sinks/__init__.py` exports
- Refactored `src/graphclaw/infra/logger.py` to accept sink lists and fan out with `asyncio.gather(..., return_exceptions=True)`; sink failures are swallowed by design.

**O-LOG-03: System log path consistency (`_system` → `system`)** ✅ FIXED
- Added canonical storage path helpers in `src/graphclaw/infra/storage.py`:
  - `StoragePaths.user_log_path(...)`
  - `StoragePaths.system_log_path(...)`
- `ObjectStorageSink` now uses these helpers; no inline `_system/logs/...` string construction remains in logger flow.

**O-LOG-04: CloudWatch sink integration and service-group mapping** ✅ FIXED
- Added `CloudWatchSink` with watchtower-based best-effort dispatch and service-group routing in `src/graphclaw/infra/sinks/cloudwatch.py`.
- Supports primary service groups plus secondary fan-out to platform groups for `ERROR/CRITICAL` and `audit.*` events.
- Added optional dependency group in `pyproject.toml`:
  - `[project.optional-dependencies].monitoring = ["watchtower>=3.0.0"]`

**O-LOG-05: Runtime sink/format selection via env vars** ✅ FIXED
- `src/graphclaw/gateway/deps.py` `init_services()` now builds sink lists from env:
  - `LOG_FORMAT` (`jsonl` or `pipe`)
  - `LOG_SINKS` (`stdout`, `object_storage`, `cloudwatch`)
  - `LOG_LEVEL` (applied to durable storage sink)
  - CloudWatch config (`CLOUDWATCH_REGION`, `CLOUDWATCH_LOG_GROUP_PREFIX`)
- Default behavior remains safe (`stdout` fallback if config is empty/invalid).

Validation completed against real local services:
- `pytest tests/test_infra/test_logger.py tests/test_infra/test_log_sinks.py tests/test_infra/test_storage_paths.py -v`
- `pytest tests/test_inbound/test_intelligence_agent_integration.py -v`
- `ruff check --fix src/ tests/ && ruff format src/ tests/`

---

## 18. State Machine Cascade — Invocation Wiring Gap

Section status: COMPLETE (Phase 1 scope) (3/3 subsections addressed)

**18.1 Shared post-transition cascade pipeline** ✅ FIXED
- Added centralized helpers in `src/graphclaw/state/cascade.py`:
  - `persist_transition(...)`
  - `run_post_transition_cascade(...)`
  - `persist_transition_and_cascade(...)`
- `run_post_transition_cascade(...)` now owns the COMPLETE cascade sequence end-to-end:
  1. activate `INACTIVE_PENDING` dependents via `activate_next_in_chain(...)`
  2. evaluate PART_OF parents via `check_composite_completion(...)`
  3. persist cascade mutations and recurse upward if parent also reaches COMPLETE

**18.2 API / agent / CLI invocation wiring aligned** ✅ FIXED
- Manual transition endpoint now uses shared pipeline:
  - `src/graphclaw/api/state.py` `transition_task(...)` calls `persist_transition_and_cascade(...)`
- Agent tool transition now validates with the real state machine instead of raw dict writes:
  - `src/graphclaw/agent/main_orchestrator.py` `_tool_update_task_state(...)`
- CLI transitions now use the same shared path:
  - `src/graphclaw/cli/task_commands.py` `_transition_task_async(...)`
- Approvals path aligned for persisted transition + cascade side effects:
  - `src/graphclaw/api/approvals.py`

**18.3 Regression coverage for call-site drift** ✅ FIXED
- Added/updated tests in `tests/test_agent/test_loop.py`:
  - validates scoped queue invalidation still works with state-machine-backed transition
  - validates COMPLETE transition in agent tool path activates dependents and auto-completes composite parent
- Existing integration coverage in `tests/test_state/test_cascade_integration.py` remains green.

Validation executed:
- `pytest tests/test_agent/test_loop.py tests/test_state/test_cascade_integration.py -q` → **30 passed**
- `ruff check src/graphclaw/state/cascade.py src/graphclaw/api/state.py src/graphclaw/api/approvals.py src/graphclaw/cli/task_commands.py src/graphclaw/agent/main_orchestrator.py tests/test_agent/test_loop.py` → **passed**
- Attempted MinIO+DB integration probe:
  - `pytest tests/test_agent/test_loop_new_tools_integration.py::TestToolReadKnowledge::test_custom_knowledge_topic -q`
  - current local object storage credentials are not accepted (`InvalidAccessKeyId`), so this backend validation is presently blocked by environment configuration rather than code.

---

## 19. Scoring Engine — Recomputation Logic Gaps & Redesign

### Current State
- Invocation: `TriggerEngine` → `AgentEventConsumer._consume_loop()` → `AgentLoop.run_cycle()` → `ScoringEngine.score_all(tasks, context)` (`agent/loop.py` lines 199–259)
- All active tasks scored every cycle — a full O(n) scan, potentially O(n²) with topology modifiers
- Cache-aside: `ScoreCache.get(task_id)` in `scoring/cache.py`
- Results persisted to `TaskNode.scoring` field
- `AgentScoringCycleEvent` logs `tasks_scored`, `top_task_id`, `queue_depth`

### Gaps
1. **Full rescore every cycle is a blunt instrument.** Rescoring all active tasks on a heartbeat is unnecessary and wasteful. A task whose inputs have not changed has the same score as the last cycle — recomputing it is pure waste.
2. **Cache invalidation not wired.** If a task's deadline changes, a blocking edge is added, or an assignment changes, the cached score is stale until the next full cycle fires. No explicit `ScoreCache.invalidate(task_id)` call exists on property-change events.
3. **INACTIVE_PENDING tasks are not scored.** They have no pre-computed score at activation and must wait for the next cycle before the agent can act on them.
4. **Trigger source not logged.** `AgentScoringCycleEvent` does not record whether the cycle was heartbeat vs on-demand, making root-cause analysis of score changes harder.

---

### What actually makes a score stale?

The 7 factors and their staleness triggers:

| Factor | Weight | What makes it stale |
|---|---|---|
| F1 Timeline Urgency | 25% | Due date / effort estimate changes, or time passing (days_remaining ticks) |
| F2 Dependency Weight | 20% | A dependent task completes or an edge is added/removed |
| F3 Critical Path | 20% | Graph topology changes, goal priority changes |
| F4 Blocker Score | 15% | A blocking edge is added/removed, or a blocker resolves (→ COMPLETE) |
| F5 Human Override | 10% | Human explicitly re-prioritises — rare, must be immediate |
| F6 Resource Risk | 5% | Assignment changes, inbound channel updates resource reliability |
| F7 Constraint Pressure | 5% | Constraint added/changed — rare |

**Key observations:**
- F5 + F6 + F7 = 20% weight and change **rarely**
- F2 + F3 = 40% and only change on **graph mutations** (edge create/delete, state transitions)
- F1 = 25% changes continuously with time but **predictably** — it is a pure math function of `(due_date - now) / effort_days`
- Only F4 (15%) changes frequently, on state transitions
- **Rescoring should only happen when a specific input event touches a task — not on every heartbeat**

---

### Proposed Approaches (to be evaluated for implementation)

#### A — Event-Driven Dirty Flag (recommended core mechanism)
Never rescore on a timer. Mark a task `score_stale=True` only when a specific input event touches it:

| Event | Tasks to mark stale |
|---|---|
| Task state → COMPLETE | All tasks with `DEPENDS_ON` this task (affects F2, F3, F4) |
| Blocking edge added/removed | The blocked task (F4) |
| Due date / effort estimate changed | That task only (F1) |
| Assignment changed | That task only (F6) |
| Goal priority changed | All tasks under that goal (F3) |
| Inbound message processed for a task | That task only (F6, possible F1 urgency signal) |
| Human override set | That task only (F5) — rescore synchronously, not queued |

The heartbeat cycle checks only whether the dirty set is non-empty, then rescores only those tasks. On a busy day this is 5–20 tasks, not 200. Implemented as a Redis set `stale_task_ids`.

#### B — Threshold-Scheduled F1 Refresh
F1 is the one factor that goes stale without any user action — purely from time passing. But it does not need continuous recomputation. The urgency curve is monotone; you can calculate *when* it will cross the next meaningful threshold and schedule a single rescore at that future point.

Urgency bands: comfortable (>14 days buffer) → tight (3–14 days) → urgent (<3 days) → overdue.

```
next_rescore_at = compute_f1_threshold_crossing(due_date, effort_days, current_band)
```

The TriggerEngine already handles time-based scheduling. Each task registers its own `next_f1_rescore_at` when scored. This replaces a blanket heartbeat rescore with sparse, per-task scheduled events.

#### C — Two-Tier Factor Split
Separate factors by volatility and computation cost:

- **Fast tier** (µs, no DB, runs on every agent query): F1 only — pure math `days_remaining / effort_days`. Always live.
- **Slow tier** (event-driven, requires graph context): F2, F3, F4, F5, F6, F7 — cached until a dirty event fires.

The agent sees `displayed_score = cached_slow_score + live_f1`. Full recomputation only runs on dirty events. This gives the agent an always-fresh urgency signal with minimal overhead.

#### D — Cascaded Dirty Propagation via Reverse Dependency Index
F3 (critical path) is a graph property — a single task completion can shift critical path membership across many tasks. Rather than walking the full graph on every completion, maintain a **reverse dependency index** in Redis:

```
dependents[task_id] = {task_id_1, task_id_2, ...}   # updated on edge create/delete
```

On state change: `dirty_set.update(dependents[task_id])` — O(direct dependents), not a full graph walk.

#### E — Score Fingerprint / Content-Addressable Cache
Hash the specific inputs that feed each task's score:

```
fingerprint = hash(due_date, effort_days, direct_dep_count, blocker_type, resource_id, goal_priority, constraint_ids)
```

On rescore request, compare current fingerprint against stored one. If unchanged, return cached score without running the engine. Eliminates spurious recomputations even when a dirty flag was set by a change that turned out not to affect the hash (e.g. a dependent task changed state but is not on the critical path for this task).

---

### Recommended Design (combination of A + B + C + D)

The efficient approach is not any single option — it is a layered combination:

```
Inbound event / user action
        │
        ▼
Identify which factors are affected
        │
        ├─ F5 (human override) ──────────────► rescore immediately, synchronously
        │
        ├─ F4 (blocker) / F6 (resource) ────► add to dirty set, rescore in next batch
        │
        ├─ F2/F3 (topology) ────────────────► update reverse dependency index (D),
        │                                      add transitive dependents to dirty set (A)
        │
        └─ F1 (time only) ──────────────────► schedule threshold-crossing rescore
                                               via TriggerEngine (B)
                │
                ▼
        Batch rescore dirty set only (not all active tasks)
                │
                ▼
        Fingerprint check (E) → skip if inputs unchanged
                │
                ▼
        Run ScoringEngine on confirmed-stale tasks
                │
                ▼
        Persist to TaskNode.scoring, emit AgentScoringCycleEvent(trigger_source=...)
```

**Correct triggers for a rescore:**
1. User action: state transition, due date change, assignment, explicit override
2. Inbound channel update: new message processed for a task (reliability or urgency signal)
3. Cascade event: a blocking task or dependency resolves
4. Scheduled F1 threshold: TriggerEngine fires at the predicted urgency-band crossing

Between these events, the cached score is valid. The heartbeat cycle should only check whether the dirty set is non-empty — not rescore everything unconditionally.

### Additional Gap
- Add `trigger_source: str` field to `AgentScoringCycleEvent` with values `"heartbeat"`, `"on_demand"`, `"property_change"`, `"f1_threshold"`, `"cascade"`.
- Score INACTIVE_PENDING tasks in a low-priority pass so they have a valid score at the moment of activation.

---

## 20. Storage Folder Directory Structure — Missing Paths & Factory Gaps

### Current State (confirmed from `infra/storage.py`)
```
{bucket}/
├── system/
│   ├── prompts/system_header.md
│   ├── knowledge/{6 topic files}.md
│   ├── agents/comms/{profile,manifest,config}
│   └── skills/definitions/{skill_name}/SKILL.md   ← defined, never seeded
└── {user_id}/
    ├── config.json
    ├── scoring_weights.json
    ├── agents/{agent_id}/
    │   ├── profile.md, manifest.json, config.json
    │   └── memory/
    │       ├── working/context.md                  ← overwritten each cycle (§15)
    │       ├── episodic/{date}-{session_id}.md
    │       └── semantic/{topic}.md
    ├── skills/registry/, cache/, authored/, executions/
    ├── mcp/servers/{server_id}.json
    ├── attachments/{channel}/{date}/{msg_id}/{file}
    ├── logs/{service}/{YYYY-MM-DD/HH00Z}.jsonl
    └── inbox/recent/ + archive/
```

### Gaps
Section status: PARTIALLY COMPLETE (Phase 1 scope) (3/5 subsections addressed)

**O-STORAGE-01: `_system/logs/` vs `system/logs/` inconsistency** ✅ FIXED
- Added canonical helpers in `src/graphclaw/infra/storage.py`:
  - `StoragePaths.system_log_path(service, hour_key, extension="jsonl")`
  - `StoragePaths.user_log_path(user_id, service, hour_key, extension="jsonl")`
- Logger sink path construction now uses these helpers; no inline `_system/logs/...` assembly in sink code.

**O-STORAGE-02: Working-context archive path after episodic flush** ✅ TRACKED (Phase 2 boundary)
- Current memory model supports working context stream (`memory/working/context.md`) and explicit compact/archive flows.
- A dedicated `working/archive/...` path factory for automatic flush lifecycle is not yet implemented.

**O-STORAGE-03: Intelligence archive path missing** ✅ FIXED
- Added `StoragePaths.agent_intelligence_archive(user_id, agent_id, task_id, date)` in `src/graphclaw/infra/storage.py`.
- Inbound intelligence overflow now writes to this durable archive path.

**O-STORAGE-04: No per-session path family** ✅ TRACKED
- Session-scoped storage helpers (for `{user_id}/sessions/{session_id}/...`) are not yet present in `StoragePaths`.
- This remains pending for the session-artifact lifecycle design.

**O-STORAGE-05: `StoragePaths` factory completeness for known inline paths** ✅ PARTIALLY FIXED
- Implemented for log and intelligence archive paths.
- Remaining gap is session path helpers, still tracked.

Validation completed:
- `pytest tests/test_infra/test_storage_paths.py -v`
- `pytest tests/test_inbound/test_intelligence_agent.py -v`
