# Agent & Sub-Agent Design Requirements

**Version:** 1.1  
**Status:** COMPLETE — B1–B12 and F1–F8 all done (2026-04-28). Architecture docs 10–12 written.  
**Purpose:** Canonical reference for agent/sub-agent architecture, memory design, and Intelligence Hub implementation. Intended to be self-contained for a new session read.

---

## Table of Contents

1. [Design Principles](#1-design-principles)
2. [Agent Types and Storage Layout](#2-agent-types-and-storage-layout)
3. [Sub-Agent Creation — Two Paths](#3-sub-agent-creation--two-paths)
4. [Agent Discovery (AgentCatalog)](#4-agent-discovery-agentcatalog)
5. [Memory Architecture](#5-memory-architecture)
6. [Context Loading Specification](#6-context-loading-specification)
7. [Compact Operation](#7-compact-operation)
8. [System Header Rules (system_header.md)](#8-system-header-rules-system_headermd)
9. [Intelligence Hub API Endpoints](#9-intelligence-hub-api-endpoints)
10. [Canvas vs. Runtime Agent Separation](#10-canvas-vs-runtime-agent-separation)
11. [Duplicate Agent Cleanup](#11-duplicate-agent-cleanup)
12. [Backend Changes Required](#12-backend-changes-required)

---

## 1. Design Principles

### 1.1 Stateless Reasoning with Durable File System

The orchestrating agent is not a long-running process. It is a **stateless LLM call paired with a durable file system** (MinIO/S3). All persistent state — profiles, memory, skills — lives in object storage. The agent reconstitutes itself from these files on every invocation.

### 1.2 Agent Isolation

Every agent (system or user-created) has its own dedicated folder in object storage. No agent ever reads another agent's memory files. Memory isolation is enforced by path construction in `StoragePaths`.

### 1.3 Intelligence Hub as Transparency Layer

The Intelligence Hub in the Cockpit UI is the user-facing interface for reading, editing, and understanding everything an agent "knows" — its persona, its memory, and its skills. It does not introduce new backend storage models; it exposes the same MinIO paths via REST API.

### 1.4 Two-Tier Storage Convention

```
system/agents/{agent_id}/          ← admin-seeded, stateless, read-only
{user_id}/agents/{agent_id}/       ← all user agents (personal + sub-agents)
agents/{user_id}/definitions/      ← Canvas UI workflow artefacts (NOT runtime agents)
```

These three prefixes serve distinct purposes and must never be conflated.

---

## 2. Agent Types and Storage Layout

### 2.1 System Agents

- Seeded at gateway startup from `src/graphclaw/gateway/prompts/agents/` into `system/agents/`
- Currently: `comms` (Communications Agent)
- **Stateless** — no memory subfolder; no per-user state
- Available to all users for delegation
- Read-only; no user-writable fields

```
system/agents/{agent_id}/
├── profile.md         ← agent persona and working style
├── manifest.json      ← capabilities, discovery metadata, tool hint
└── config.json        ← LLM selection, heartbeat config
```

### 2.2 User Agents (including orchestrator-created sub-agents)

All agents created by the main orchestrator or by the user via the Canvas Editor are stored under the user's prefix:

```
{user_id}/agents/{agent_id}/
├── profile.md                                ← persona, role, working style
├── manifest.json                             ← name, capabilities, tool hint
├── config.json                               ← LLM, heartbeat, assigned skills/MCP
└── memory/
    ├── working/
    │   ├── context.md                        ← active scratchpad (single file)
    │   └── archive/
    │       └── {date}-compact-{label}.md     ← compacted snapshots (read-only)
    ├── episodic/
    │   ├── {date}-compact-{label}.md         ← ACTIVE session archives (loaded into context)
    │   └── archive/
    │       └── {date}-compact-{label}.md     ← ARCHIVED sessions (never loaded, read-only)
    └── semantic/
        ├── knowledge.md                      ← DEFAULT: provisioned empty on agent creation
        └── {topic}.md                        ← additional user-authored knowledge topics
```

### 2.3 Main Orchestrator Agent

The main orchestrator runs as the **user's primary agent** with `agent_id = user_id` (e.g., `USER-dev-001`). Its profile, working memory, episodic archives, and semantic knowledge all live at `{user_id}/agents/{user_id}/memory/`.

---

## 3. Sub-Agent Creation — Two Paths

### 3.1 Path A: Orchestrator-Proposed (User Confirmation Required)

When the main orchestrator determines that a sub-agent would be useful for a task, it **must not create the agent autonomously**. The required flow is:

**Step 1 — Orchestrator proposes:**
The orchestrator presents a proposal to the user containing:
- **agent_id**: meaningful slug derived from the role (e.g., `research-agent`, `data-processor`)
- **Name**: human-readable display name
- **Purpose/Role**: one paragraph describing what this agent will do
- **Profile**: draft persona text for the agent's `profile.md`
- **Skills**: list of skill IDs to assign
- **MCP Servers**: list of MCP server IDs to connect
- **Tool hint**: text that appears in `list_available_agents` catalog — how the main agent should decide to delegate to this sub-agent

**Step 2 — User approves:** The user explicitly confirms the proposal.

**Step 3 — Orchestrator creates:** Only after approval does the orchestrator call `_tool_create_agent` with all parameters, including the **explicitly specified `agent_id`**.

**Critical rule:** `agent_id` must always be passed explicitly. The orchestrator must never rely on auto-generation which appends a UUID suffix (see §11 for why this causes duplicates).

### 3.2 Path B: Canvas-Created by User

When the user opens the Canvas Editor and adds a new agent node via the (+) action:

1. User fills in: agent name, purpose, skills, MCP servers
2. On save, Canvas calls the Intelligence API to create runtime agent files at `{user_id}/agents/{agent_name}/`
3. The Canvas definition JSON (visual node positions, edge connections) continues to be stored at `agents/{user_id}/definitions/{agent_id}.json` as usual
4. The two artefacts are linked by `agent_id`

This bridge between the canvas design artefact and runtime agent files **must be implemented** in `src/graphclaw/api/agents.py` — currently the canvas POST only creates the definition JSON, it does not provision runtime agent files.

### 3.3 Storage on Creation

Regardless of path, `_tool_create_agent` writes four files to `{user_id}/agents/{agent_id}/`:

```
profile.md      ← agent persona, goals, assigned skills listed
manifest.json   ← { agent_id, name, type: "user", description, capabilities, tool_hint }
config.json     ← { llm_model, heartbeat_interval, skills: [...], mcp_servers: [...] }
memory/semantic/knowledge.md   ← empty file (default knowledge base, always provisioned)
```

---

## 4. Agent Discovery (AgentCatalog)

### 4.1 How Discovery Works

`AgentCatalog` (`src/graphclaw/agent/catalog.py`) is the authoritative source for which agents are available to the main orchestrator:

- Scans `system/agents/` prefix → system agents (cached in-process, 30-minute TTL)
- Scans `{user_id}/agents/` prefix → user agents (cached in Redis per user, 10-minute TTL)
- Reads each agent's `manifest.json` to get name, capabilities, and tool hint
- Invalidates Redis cache when user creates or deletes an agent

### 4.2 Intelligence Hub Agent Selector

The Intelligence Hub Cockpit UI must use a **new dedicated API endpoint** to list agents — NOT the Canvas API (`GET /app/v1/agents`). The canvas API returns visual workflow definitions, not the runtime agents that have memory.

**New endpoint:** `GET /app/v1/intelligence/agents`
- Scans `{user_id}/agents/` + reads each manifest.json
- Includes system agents accessible to the user
- Returns: `[{ agent_id: string, name: string, source: "user" | "system" }]`

### 4.3 Why the Canvas API is Wrong for This

`GET /app/v1/agents` returns canvas definitions from `agents/{user_id}/definitions/*.json`. These are UI artefacts. A sub-agent created by the orchestrator has runtime files in `{user_id}/agents/` but no canvas definition — it would be invisible to the current agent selector.

---

## 5. Memory Architecture

### 5.1 Working Memory

**Purpose:** The agent's live scratchpad for the current session. Captures in-progress reasoning, task state, skill results, and delegation context.

**Storage:** Single file — `{user_id}/agents/{agent_id}/memory/working/context.md`

**Writers:**
| Writer | Format | When |
|---|---|---|
| `InboundIntelligenceAgent` | JSON lines: `{"timestamp":..., "source":"inbound_intelligence", "note":"..."}` | On each classified inbound message |
| `ResultCollector` | Markdown sections: `## Skill Result: {name}\n- Task: {id}\n- Status: COMPLETED\n...` | After skill execution or sub-agent completion |
| `MainOrchestrator` | Markdown: `# Delegation: {task_id}\n- Delegated by: orchestrator\n...` | Before dispatching a delegation job |

**Readers:** `SubAgentRunner._build_system_prompt()` — injected under `## Working Context` section

**Archive:** When `/compact` is called, `context.md` is archived to `memory/working/archive/{date}-compact-{label}.md` and replaced with the user-supplied summary.

**UI:** Left panel lists `context.md` (editable) and all `archive/` entries (read-only) with character counts.

---

### 5.2 Episodic Memory

**Purpose:** Append-only archive of past session summaries. Created by the `/compact` operation. Provides an audit trail of what the agent did across sessions.

**Active path:** `{user_id}/agents/{agent_id}/memory/episodic/{date}-compact-{label}.md`  
**Archive path:** `{user_id}/agents/{agent_id}/memory/episodic/archive/{date}-compact-{label}.md`

**Active vs. Archived:**

| State | Path | Loaded into context? | Editable? |
|---|---|---|---|
| **Active** | `episodic/` | ✅ Yes — in every agent invocation | No (read-only) |
| **Archived** | `episodic/archive/` | ❌ No — permanently excluded | No (read-only) |

**Archive action is irreversible.** Moving a file from `episodic/` to `episodic/archive/` permanently removes it from the agent's context. Users must be warned before archiving.

**Concrete example entry:**
```markdown
# Session: sprint-12-planning
*Archived: 2026-04-20 | Original: 2026-04-20-compact-sprint12.md*

## Summary
Completed sprint 12 planning. Assigned 8 tasks across 3 team members. 
Budget approved at $45k. Key blocker: UX mockups not ready.

## Original Working Context (archived)
[full working context content at time of compact...]
```

**UI:** Left panel with two sections — ACTIVE and ARCHIVED. Archive button per active entry. Monaco read-only viewer on right.

---

### 5.3 Semantic Memory

**Purpose:** Long-term knowledge base authored by the user (or the agent in future). Topic files encode facts, preferences, constraints, and domain knowledge that the agent should reference continuously.

**Storage:** `{user_id}/agents/{agent_id}/memory/semantic/{topic}.md`

**Default file:** `knowledge.md` — provisioned empty when any agent is created. Users populate it with general knowledge about their domain, preferences, and rules.

**Example topics and content:**

`knowledge.md`:
```markdown
# General Knowledge
- Budget year runs April–March
- All external communications must be approved before sending
- Preferred communication channel: email for formal, Slack for informal
```

`users.md`:
```markdown
# Key Contacts
- Soni Gupta (partner): responds quickly, prefers bullet points
- Alex Chen (CTO): detailed documentation required, weekly cadence preferred
```

`patterns.md`:
```markdown
# Recurring Patterns
- Sprint planning happens every other Monday
- Financial approvals require 48-hour lead time
- End of quarter: all goals must be reviewed against KPIs
```

**Readers:** All semantic files are loaded into `SubAgentRunner._build_system_prompt()` under `## Semantic Knowledge` section. This applies to ALL active files — not selectively.

**UI:** Left panel lists all topic files. `knowledge.md` is shown first. User can add/rename/delete. Monaco editor on right with Save / Discard / Delete File buttons.

**Multi-file support:** Unlike working memory (single file), semantic memory supports any number of topic files. Create via PUT (same endpoint as update — `PUT .../memory/semantic/{topic}` creates the file if it doesn't exist).

---

### 5.4 Memory Isolation

All memory files are per-agent. No agent reads another agent's memory. The main orchestrator and each sub-agent maintain completely separate memory stores under their own `agent_id`.

```
USER-dev-001/agents/USER-dev-001/memory/   ← main orchestrator's memory
USER-dev-001/agents/research-agent/memory/ ← sub-agent's memory
USER-dev-001/agents/comms/memory/          ← comms sub-agent's memory (if user-scoped)
```

Note: system agents (`system/agents/comms/`) are stateless and have **no memory subfolder**.

---

## 6. Context Loading Specification

### 6.1 SubAgentRunner System Prompt Assembly Order

When `SubAgentRunner._build_system_prompt()` assembles the system prompt for a delegated task:

```
[1] Agent Profile (profile.md)
    — persona, role, assigned skills

[2] ## Working Context
    — full content of memory/working/context.md
    — (includes delegation context written by orchestrator before dispatching)

[3] ## Episodic Memory
    — all ACTIVE files from memory/episodic/ prefix (newest first)
    — NOT from episodic/archive/
    — truncate oldest entries first if combined size would exceed token budget

[4] ## Semantic Knowledge
    — ALL files from memory/semantic/ prefix
    — no ordering guarantee; load all
    — truncate if combined size approaches budget (semantic is lower priority than episodic)
```

**Current state (Phase 5):** SubAgentRunner loads [1] + [2] only. Loading [3] and [4] is a **required backend change** (see §12.B5).

### 6.2 MainOrchestrator System Prompt Assembly Order

```
[1] system_header.md         — philosophy, tool-use rules, sub-agent rules
[2] ## Your Persona          — agent profile.md
[3] ## Available Tool Sets   — compact tool manifest (~150 tokens)
[4] ## Knowledge Base        — topic names only (agent loads on demand)
[5] ## Available Agents      — compact AgentCatalog (~100 tokens)
[6] ## Execution Context     — registered skills + MCP servers
[7] ## Current Task Graph    — live graph data fetched per-request
```

The main orchestrator **does not** load working/episodic/semantic memory into its system prompt directly. It reasons about the task graph and delegates to sub-agents that have their own context-loading.

### 6.3 Token Budget Guard

Default context budget: **80,000 tokens** (`GRAPHCLAW_AGENT_CONTEXT_BUDGET_TOKENS`).

When loading episodic + semantic files, truncation order (oldest/lowest priority first):
1. Oldest episodic entries
2. Semantic files (alphabetical, last alphabetically first)
3. Working context (never truncated — always included in full)

Compact should be triggered when budget utilization approaches 60%.

---

## 7. Compact Operation

### 7.1 User-Triggered Compact (via Intelligence Hub UI)

**Endpoint:** `POST /app/v1/intelligence/agents/{agent_id}/memory/compact`

**Request body:**
```json
{
  "summary": "Sprint 12 planning complete. 8 tasks assigned...",
  "session_label": "sprint-12-planning"
}
```

**What happens:**
1. Read current `memory/working/context.md` → capture `context_before_chars`
2. Archive original content to BOTH:
   - `memory/episodic/{date}-compact-{label}.md` (becomes an active episodic entry)
   - `memory/working/archive/{date}-compact-{label}.md` (archived snapshot)
3. Replace `context.md` with the caller-supplied `summary`

**Response:**
```json
{
  "agent_id": "research-agent",
  "archived_as": "2026-04-20-compact-sprint-12-planning.md",
  "working_context_replaced": true,
  "context_before_chars": 12450,
  "context_after_chars": 387,
  "reduction_pct": 96.9
}
```

**UI behavior:** After compact, working memory editor clears to the summary content. Toast: "Compacted. Archived as `{archived_as}`. Context reduced from 12,450 → 387 chars (96.9% freed)."

### 7.2 Agent-Triggered Compact (via orchestrator tool)

The main orchestrator should trigger compact when its working context (or a sub-agent's) exceeds 60% of the context budget. Rule is encoded in `system_header.md` (see §8.2).

**Tool call form (orchestrator invokes on behalf of sub-agent):**
```python
await self._compact_agent_memory(
    user_id=user_id,
    agent_id=agent_id,   # can be main agent or sub-agent
    summary="...",
    session_label="auto-compact-{date}"
)
```

**Utility functions required** (new — see §12.B6):
- `estimate_context_chars(user_id, agent_id)` — sum of working + active episodic + semantic file sizes
- Returns `{ working_chars, episodic_chars, semantic_chars, total_chars, budget_chars, utilization_pct }`

---

## 8. System Header Rules (system_header.md)

The file at `system/prompts/system_header.md` is the main orchestrator's philosophy and rules document. Two new rules must be added:

### 8.1 Sub-Agent Creation Confirmation Rule

```markdown
## Sub-Agent Creation Rule

Before creating a new sub-agent, you MUST present a proposal to the user and wait for
explicit approval. The proposal must include:

1. **agent_id**: A meaningful, lowercase-hyphenated slug derived from the agent's role
   (e.g., `research-agent`, `email-processor`, `data-analyst`). NEVER omit agent_id —
   always provide it explicitly to prevent duplicate creation.
2. **Name**: Human-readable display name
3. **Purpose**: One paragraph describing what this agent will do and why it is needed
4. **Profile**: Draft persona text for the agent
5. **Skills**: Specific skill IDs to assign
6. **MCP Servers**: Specific MCP server IDs to connect
7. **Tool hint**: One sentence describing when the main agent should delegate to this sub-agent

Only call `create_agent` after the user has explicitly confirmed the proposal.
```

### 8.2 Compact at 60% Rule

```markdown
## Memory Compact Rule

When you estimate that any agent's active context (working memory + active episodic entries
+ semantic files) has reached 60% or more of the context budget, initiate a compact operation
for that agent. 

Before compacting, call `estimate_context_usage(agent_id)` to get the current breakdown.
After compacting, report to the user:
- Which agent's memory was compacted
- Why (context utilization %)
- What was archived (entry name)
- How much context was freed (characters and percentage)
```

---

## 9. Intelligence Hub API Endpoints

All endpoints are under `/app/v1/intelligence/` and require Bearer JWT authentication.

### 9.1 Agent List (NEW)

| Method | Path | Description |
|---|---|---|
| `GET` | `/intelligence/agents` | List all agents with memory (scans MinIO, not canvas API) |

Response: `[{ agent_id, name, source: "user" | "system" }]`

### 9.2 Agent Profile

| Method | Path | Description |
|---|---|---|
| `GET` | `/intelligence/agents/{id}/profile` | Load agent profile.md |
| `PUT` | `/intelligence/agents/{id}/profile` | Save agent profile.md |

### 9.3 Working Memory

| Method | Path | Description |
|---|---|---|
| `GET` | `/intelligence/agents/{id}/memory/working` | Load context.md |
| `PUT` | `/intelligence/agents/{id}/memory/working` | Save context.md |
| `GET` | `/intelligence/agents/{id}/memory/working/archive` | **NEW** — list archive entries with sizes |
| `POST` | `/intelligence/agents/{id}/memory/compact` | Archive + replace working context |

Archive list response: `[{ name, size_chars, created_at }]`

### 9.4 Episodic Memory

| Method | Path | Description |
|---|---|---|
| `GET` | `/intelligence/agents/{id}/memory/episodic` | List all entries (active + archived, with `status` field) |
| `GET` | `/intelligence/agents/{id}/memory/episodic/{name}` | Read one entry |
| `POST` | `/intelligence/agents/{id}/memory/episodic/{name}/archive` | **NEW** — move to archive (irreversible) |

List response now includes `status: "active" | "archived"` per entry.

> **Note:** Delete endpoint removed in favour of archive. Entries are never deleted — only archived to `episodic/archive/`.

### 9.5 Semantic Memory

| Method | Path | Description |
|---|---|---|
| `GET` | `/intelligence/agents/{id}/memory/semantic` | List all topic files |
| `GET` | `/intelligence/agents/{id}/memory/semantic/{topic}` | Read one topic |
| `PUT` | `/intelligence/agents/{id}/memory/semantic/{topic}` | Create or update topic |
| `DELETE` | `/intelligence/agents/{id}/memory/semantic/{topic}` | Delete topic |

### 9.6 Skills (Authored)

| Method | Path | Description |
|---|---|---|
| `GET` | `/intelligence/skills/authored` | List authored skills |
| `POST` | `/intelligence/skills/authored` | Create skill |
| `GET` | `/intelligence/skills/authored/{id}` | Get skill content |
| `PUT` | `/intelligence/skills/authored/{id}` | Update skill |
| `DELETE` | `/intelligence/skills/authored/{id}` | Delete skill |
| `POST` | `/intelligence/skills/authored/{id}/fork` | Fork skill |
| `POST` | `/intelligence/skills/validate` | Validate SKILL.md content |
| `POST` | `/intelligence/skills/import` | Import SKILL.md file (multipart) |

---

## 10. Canvas vs. Runtime Agent Separation

### 10.1 Two Distinct Storage Systems

| | Canvas Definitions | Runtime Agents |
|---|---|---|
| **Path** | `agents/{user_id}/definitions/{id}.json` | `{user_id}/agents/{agent_id}/` |
| **API** | `GET/POST/PATCH /app/v1/agents` | `GET /app/v1/intelligence/agents` |
| **Purpose** | Visual workflow design (React Flow export) | Executable agent with profile + memory |
| **Connection** | Linked by `agent_id` only | None back to canvas |
| **Managed by** | Canvas Editor UI | Orchestrator tool or Canvas save bridge |

### 10.2 Bridge Required

Currently, saving a canvas definition does **not** create runtime agent files. This means:
- User-designed sub-agents in Canvas are invisible to the AgentCatalog
- The main orchestrator cannot delegate to them via `delegate_to_agent`

**Required change:** When `POST /app/v1/agents` creates a new canvas definition, the API should also provision the runtime agent at `{user_id}/agents/{agent_id}/` with profile.md, manifest.json, config.json, and the default `memory/semantic/knowledge.md`.

### 10.3 What Should NOT Change

- Canvas definitions continue to be stored at `agents/{user_id}/definitions/` — this is correct for the React Flow visual state
- Version history at `agents/{user_id}/definitions/{id}/versions/` remains unchanged
- The `PATCH /app/v1/agents/{id}` endpoint creates version snapshots — this should also update the runtime agent's `profile.md` and `config.json`

---

## 11. Duplicate Agent Cleanup

### 11.1 Root Cause

`_tool_create_agent` in `main_orchestrator.py` generates a UUID suffix when `agent_id` is not explicitly provided:

```python
if requested_agent_id:
    agent_id = agent_id_base              # idempotent
else:
    agent_id = f"{agent_id_base}-{uuid.uuid4().hex[:6]}"  # creates new agent every call
```

If the LLM calls `create_agent(name="Research Agent")` three times without an explicit agent_id, it creates `research-agent-a1b2c3`, `research-agent-d4e5f6`, `research-agent-g7h8i9` — three separate agents for the same function.

### 11.2 Fix

Remove UUID auto-generation. Always use `agent_id_base` (slugified from name). The existing idempotency check (`storage.exists()`) prevents re-creation when the same slug is used.

```python
agent_id = _slugify(requested_agent_id or name)[:40]
# No UUID suffix — rely on idempotency check + user confirmation rule
```

### 11.3 Cleanup CLI Commands

Add to `src/graphclaw/cli/intelligence_commands.py`:

```bash
# List all agents for the current user (shows user-created agents in MinIO)
graphclaw intelligence agents list

# Delete a specific agent and all its memory files
graphclaw intelligence agents delete <agent_id> [--force]

# Show all agents and identify potential duplicates (same base name, different UUID suffix)
graphclaw intelligence agents audit
```

---

## 12. Backend Changes Required

These changes are required to align the implementation with the design principles in this document.

| ID | Change | File | Status |
|---|---|---|---|
| **B1** | Fix UUID auto-generation in `_tool_create_agent` — use deterministic slug | `agent/main_orchestrator.py` | ✅ **DONE** (2026-04-27) |
| **B2** | New `GET /app/v1/intelligence/agents` endpoint | `api/intelligence.py` | ✅ **DONE** (2026-04-27) |
| **B3** | Episodic archive endpoint + `StoragePaths.agent_memory_episodic_archive_*` | `api/intelligence.py`, `infra/storage.py` | ✅ **DONE** (2026-04-27) |
| **B4** | New `GET /app/v1/intelligence/agents/{id}/memory/working/archive` | `api/intelligence.py` | ✅ **DONE** (2026-04-27) |
| **B5** | SubAgentRunner: load active episodic + ALL semantic files into system prompt | `agent/sub_agent_runner.py` | ✅ **DONE** (2026-04-27) |
| **B6** | Compact response: add `context_before_chars`, `context_after_chars`, `reduction_pct` | `api/intelligence.py` | ✅ **DONE** (2026-04-27) |
| **B7** | Add sub-agent creation confirmation rule to `system_header.md` | `system/prompts/system_header.md` | ✅ **DONE** (2026-04-27) |
| **B8** | Add compact-at-60% rule to `system_header.md` | `system/prompts/system_header.md` | ✅ **DONE** (2026-04-27) |
| **B9** | Provision `memory/semantic/knowledge.md` when `_tool_create_agent` runs | `agent/main_orchestrator.py` | ✅ **DONE** (2026-04-27) |
| **B10** | Canvas → runtime agent bridge: create runtime files on canvas POST/PATCH | `api/agents.py` | ✅ **DONE** (2026-04-28) |
| **B11** | Add `GET .../memory/estimate` endpoint → `ContextUsageResponse` with utilization_pct | `api/intelligence.py` | ✅ **DONE** (2026-04-28) |
| **B12** | Add CLI commands: `agents list`, `agents delete`, `agents audit` | `cli/intelligence_commands.py` | ✅ **DONE** (2026-04-28) |

### Frontend Changes (Cockpit)

| ID | Change | File | Status |
|---|---|---|---|
| **F1** | Fix/add hooks: `useIntelligenceAgents`, `useWorkingMemoryArchive`, `useUpdateWorkingMemory`, `useArchiveEpisodicEntry`, `useSemanticTopic`, `useUpdateSemanticMemory`, `useDeleteSemanticMemory`; fix `useCompactWorkingMemory` + `CompactResponse` | `src/lib/api-hooks.ts` | ✅ **DONE** (2026-04-27) |
| **F2** | New `MemoryEditor.tsx` Monaco wrapper (markdown, word-wrap, read-only support) | `src/features/intelligence/MemoryEditor.tsx` | ✅ **DONE** (2026-04-27) |
| **F3** | Switch `IntelligenceLayout.tsx` to `useIntelligenceAgents()` | `src/features/intelligence/IntelligenceLayout.tsx` | ✅ **DONE** (2026-04-27) |
| **F4** | `AgentProfilePage.tsx`: swap textarea → MemoryEditor; sonner toasts | `src/features/intelligence/AgentProfilePage.tsx` | ✅ **DONE** (2026-04-27) |
| **F5** | `WorkingMemoryPage.tsx`: left panel (archive list), save/discard/compact, size warning | `src/features/intelligence/WorkingMemoryPage.tsx` | ✅ **DONE** (2026-04-27) |
| **F6** | `EpisodicMemoryPage.tsx`: active/archived sections, irreversible archive action | `src/features/intelligence/EpisodicMemoryPage.tsx` | ✅ **DONE** (2026-04-27) |
| **F7** | `SemanticMemoryPage.tsx`: multi-file, local-only new topics, save/discard/delete | `src/features/intelligence/SemanticMemoryPage.tsx` | ✅ **DONE** (2026-04-27) |
| **F8** | `SkillAuthoringPage.tsx`: delete with confirmation; sonner toasts for all actions | `src/features/intelligence/SkillAuthoringPage.tsx` | ✅ **DONE** (2026-04-27) |
