# Intelligence Layer — Architecture Design

**Version:** 1.0  
**Status:** Approved for build — Phase 4.5  
**Date:** 2026-04-12 (extended 2026-05-02 design pass)  
**Relates to:** PRD Sections 36 (Node Intelligence), 37 (Embedding Pipeline), 32 (Observability)

> **2026-05-02 extensions.** The Intelligence Layer is the substrate that the new comms/inbound/outbound triad shares. See:
>
> - [14-agent-triad.md](14-agent-triad.md) — distillation now applies to **all message sources including web chat** (FR-CA-002), not only inbound channels
> - [16-cross-user-conversations.md](16-cross-user-conversations.md) — counterparty-scoped storage replaces flat `chat/history.json`; sender classification matrix; reply-key dual-write (Redis + Postgres `reply_lineage`)
> - [15-user-identity-and-onboarding.md](15-user-identity-and-onboarding.md) — alias resolution + org-directory lookup augment the resolver tiers
> - [17-cross-tenant-task-projection.md](17-cross-tenant-task-projection.md) — node intelligence remains in owner's substrate; cross-tenant visibility is metadata-only via org task index
> - [19-data-lifecycle-and-deletion-policy.md](19-data-lifecycle-and-deletion-policy.md) — intelligence is append-only by design; trim via existing 500-word limiter; never deleted
>
> Tracked requirements: [/docs/requirements/agent-triad-and-comms-substrate.md](../requirements/agent-triad-and-comms-substrate.md).

---

## 1. Design Overview

The Intelligence Layer adds three interconnected capabilities to GraphClaw:

1. **Node Intelligence** — a per-task/goal `intelligence` field in the graph that accumulates the communication history, decisions, and outbound log for that node across all channels
2. **InboundIntelligenceAgent** — a lightweight LLM processor that runs on every inbound message, classifies it, summarizes it, and routes task-specific content to the graph node and general context to Betty's working memory
3. **Structured S3 Log Sink** — all agent actions and inbound/outbound events are written as JSONL to MinIO (local) or S3 (production) with PII-safe allowlist-only event models, feedable to CloudWatch

These three capabilities are intentionally separable but designed to work together. The intelligence field is the authoritative record of what happened on a task. The InboundIntelligenceAgent is the writer. The log sink is the audit trail.

---

## 2. Context Model (Two-Tier)

The system maintains two distinct tiers of context, each serving a different purpose:

| Tier | Storage | Purpose |
|---|---|---|
| **Agent memory** | MinIO `{user_id}/agents/{agent_id}/memory/` | Betty's cross-task planning, user behavioral patterns, persona notes, general discussion not tied to any specific node |
| **Node intelligence** | Graph `TaskNode.intelligence`, `GoalNode.intelligence` | Per-task/goal: email/Telegram thread summaries, outbound log, decisions, briefing context |

**Why this split matters:** Betty loads all active TaskNodes in `_build_graph_summary()` on every reasoning turn. Adding `intelligence` to those nodes means per-task context arrives automatically in proportion to what's relevant, without loading separate per-task memory files. As tasks scale to hundreds, context load scales proportionally with what's actually being acted on.

Agent memory (working/context.md) carries general intelligence: user communication style, behavioral patterns, cross-task observations, preferences. This never lives on a node because it applies globally.

### 2.1 Agent Memory — Three Sub-Tiers

Within `{user_id}/agents/{agent_id}/memory/`, there are three sub-tiers with distinct read/write behaviour:

| Sub-tier | Path | Loaded by SubAgentRunner | Writable by |
|---|---|---|---|
| **Working** | `working/context.md` | ✅ Always loaded | Agent loop, InboundIntelligenceAgent, cockpit UI |
| **Working archive** | `working/archive/{date}-compact-{label}.md` | ❌ Never loaded | `/compact` endpoint (read-only after creation) |
| **Episodic — active** | `episodic/{date}-compact-{label}.md` | ✅ All active entries, newest first | `/compact` endpoint (read-only after creation) |
| **Episodic — archived** | `episodic/archive/{date}-compact-{label}.md` | ❌ Never loaded | Archive action via cockpit (irreversible) |
| **Semantic** | `semantic/{topic}.md` | ✅ All files loaded | User via cockpit UI |

**Episodic active vs. archived:** Active episodic entries represent recent session summaries that inform the agent's reasoning. When a user archives an entry, it moves to `episodic/archive/` and is permanently excluded from context — the agent will never see it again. Archive is irreversible. This allows users to prune low-value or sensitive history without deleting it entirely.

**Semantic multi-file:** `knowledge.md` is provisioned as the default file when an agent is created. Users can add additional topic files (e.g. `users.md`, `patterns.md`). All files are loaded on every SubAgentRunner invocation.

---

## 3. Node Intelligence Field

### 3.1 Schema Addition

Two fields added to existing Pydantic models:

```python
# src/graphclaw/models/nodes.py
class TaskNode(BaseNode):
    ...
    intelligence: str | None = None   # NEW — see format below

class GoalNode(BaseNode):
    ...
    intelligence: str | None = None   # NEW
```

**Storage:** Serialized as a JSON string in Apache AGE (same pattern as `update_log` and `state_history`).

### 3.2 Intelligence Entry Format

Each entry is a single line in markdown:

```
[{ISO-date}] {channel} | {direction} | {summary}
```

Examples:
```
[2026-03-07] email | outbound | Sent deadline reminder to Soni re: deliverable submission
[2026-04-12] telegram | inbound | Soni confirmed upload by EOD today, attaching slides
[2026-04-13] email | inbound | Soni sent follow-up asking about review timeline
[2026-04-13] email | outbound | Sent review timeline to Soni, expect feedback by Apr 16
```

**Maximum size:** ~500 words (≈ 10–15 entries). When exceeded, oldest entries are trimmed and replaced with an anchor line: `... {N} older entries archived`. LLM-assisted re-consolidation (like the existing `/compact` pattern) is a future enhancement.

### 3.3 Graph Update Path

```
update_node_intelligence(node_id, text)
  → MATCH (n {id: $id}) SET n.intelligence = $text, n.updated_at = $now

get_node_intelligence(node_id) -> str | None
  → MATCH (n {id: $id}) RETURN n.intelligence
```

Both added to `src/graphclaw/db/age/repository.py`.

### 3.4 Betty's Graph Summary Integration

`_build_graph_summary()` in `loop.py` includes a truncated intelligence snippet per task:

```
[1] TSK-AG-13860-DEL | IN_PROGRESS | score=0.85 (due Apr 16)
    [ctx: 2026-04-12 telegram | inbound | Soni confirmed upload by EOD…]
```

Total summary kept under 2000 chars. Betty gets per-task context proportional to what she's reasoning about — no extra loading needed.

---

## 4. InboundIntelligenceAgent

### 4.1 Identity and MinIO Structure

`InboundIntelligenceAgent` is a **data processor, not a conversational agent**. It is never user-facing. It does not have a persona, episodic memory, or working context of its own.

Its MinIO footprint is minimal:

```
{user_id}/agents/intelligence-processor/
├── config.json          ← model selection, prompt version, confidence thresholds
└── execution_log/
    └── {YYYY-MM-DD}.jsonl   ← message_id, task_matched, action_taken, latency_ms
```

No `profile.md`, no `memory/` tier. Its output IS its memory — written into TaskNodes and Betty's `working/context.md`.

### 4.2 Resolution Waterfall (Three Tiers)

Every inbound message goes through this waterfall in order. First match wins.

```
Inbound message arrives (any channel)
         │
         ▼
Tier 1: in_reply_to / tg_reply_to_message_id
         └─ Redis lookup: checkin:{msg_id} → {checkin_id, task_id}
         │  → HIGH confidence, deterministic (Betty's own reply chain)
         │  no match
         ▼
Tier 2: TaskID regex in body
         └─ pattern: TSK-[A-Z]+-[0-9]+-[A-Z]+
         │  → confidence = 1.0 (authoritative reference)
         │  no match
         ▼
Tier 3: Vector embedding search
         └─ embed(subject + body[:300]) → cosine search on node_embeddings
         │
         ├── similarity ≥ 0.70 (HIGH)     → update node intelligence
         ├── similarity 0.40–0.70 (MEDIUM) → update node + tag [unverified-match]
         │                                   + append note to Betty's working context
         ├── similarity < 0.40 (LOW)       → UNMATCHED
         └── two results within 0.05       → AMBIGUOUS → UNMATCHED
         │  no match
         ▼
Unmatched handler
         └─ Lookup sender in graph (ResourceNode/UserNode by email or telegram user_id)
         │
         ├── Known sender → Betty ACTIVELY ASKS user:
         │   "I got a message from {sender} that I couldn't match to any task.
         │    It says: '{50-word-summary}'. What should I do with it?"
         │   (via user's preferred channel, same turn if in chat or next briefing)
         │
         └── Unknown sender → inbox/recent only, no notification
                              (possible spam / cold contact)
```

### 4.3 InboundIntelligenceAgent Class

**File:** `src/graphclaw/inbound/intelligence_agent.py`

```python
class InboundIntelligenceAgent:
    def __init__(
        self,
        llm: LLMClient,              # create_llm_client("litellm") — lightweight model
        graph_repo,                  # for update_node_intelligence()
        storage: StorageClient,      # for working memory update
        memory_lock: asyncio.Lock,   # owned by AgentEventConsumer, shared
        logger: AsyncLogger | None = None,
    ) -> None: ...

    async def process(
        self,
        inbound: InboundMessage,
        resolution: InboundResult,   # from InboundProcessor
        agent_id: str,
        user_id: str,
        existing_intelligence: str | None,
    ) -> IntelligenceUpdate: ...
```

```python
class IntelligenceUpdate(BaseModel):
    task_intelligence: str | None   # append to node.intelligence (None = not task-specific)
    memory_update: str | None       # append to working/context.md (None = no general learning)
    log_event_type: str             # for AsyncLogger
    action_taken: str               # "node_updated" | "memory_updated" | "both" | "unmatched"
```

### 4.4 LLM Prompt Design

**Single LLM call, structured JSON response.** Model: configurable via `INTELLIGENCE_AGENT_MODEL` env var (default: `claude-haiku-4` or `gpt-4o-mini` for cost efficiency).

**System prompt:**
```
You are a task intelligence processor for a task management system.
Given an inbound message, produce two outputs as valid JSON:
1. "task_entry": A single-line intelligence log entry (max 60 words) in format:
   "[{channel} | inbound | {concise factual summary}]"
   Use null if the message has no clear task-specific content.
2. "memory_note": A one-line general observation about user preferences,
   communication patterns, or project-level context. Use null if nothing to learn.
Never include PII such as SSNs, financial account numbers, or medical information.
```

**User message:**
```
Channel: {inbound.channel}
From: {inbound.sender}
Subject: {inbound.subject}
Body: {inbound.body[:600]}
Matched task: {resolution.task_id or "none"}
Existing task intelligence (last 200 chars): {existing_intelligence[-200:] or "none"}
```

**Post-processing after LLM response:**
1. `task_entry` set → read `node.intelligence` → prepend new line → trim if > 500 words → `update_node_intelligence()`
2. `memory_note` set → acquire `memory_lock` → read `working/context.md` → append under `## Recent Context` → release lock
3. Log `agent.intelligence_update` via AsyncLogger

### 4.5 Concurrency Safety

Multiple channels (email poller + Telegram poller) produce inbound messages concurrently. Two race conditions to guard:

| Race | Guard |
|---|---|
| Two messages updating same node's `intelligence` simultaneously | Node-level optimistic update: read → modify → SET with `WHERE intelligence = $old_value`; retry once on mismatch |
| Two messages updating `working/context.md` simultaneously | Single `asyncio.Lock` owned by `AgentEventConsumer`, passed to `InboundIntelligenceAgent` |

---

## 5. Outbound Intelligence Logging

### 5.1 Log Entry on Every Outbound

When Betty sends an outbound message (email or Telegram) in the context of a task, a log entry is appended to the task's `intelligence` field:

```
[{ISO-date}] {channel} | outbound | Sent "{subject[:60]}" to {recipient}
```

Example:
```
[2026-03-07] email | outbound | Sent "Re: Deliverable — deadline reminder" to soni@acme.com
```

No LLM needed — pure string construction in `_append_outbound_intelligence()`.

### 5.2 CheckinNode Creation

Every outbound message creates a `CheckinNode` in the graph:

```cypher
CREATE (:CheckinNode {
    id: 'CHK-...',
    outbound_message: $body,
    channel: $channel,
    sent_at: $now,
    sent_by: $agent_id,
    recipient: $recipient,
    inbound_response: null
})
CREATE (checkin)-[:REFERS_TO]->(task)
```

The `checkin_id` is stored in Redis (`checkin:{original_msg_id}` TTL 7 days) to enable tier-1 resolution when the reply arrives.

### 5.3 Reply Linking

When inbound arrives with `in_reply_to` matching a known checkin:
1. Tier-1 resolution returns the `task_id`
2. `update_checkin_response(checkin_id, inbound.body)` sets `inbound_response` on the CheckinNode
3. Graph now has a complete back-and-forth thread: `CheckinNode.outbound_message` → `CheckinNode.inbound_response`, linked to `TaskNode` via `REFERS_TO`

---

## 6. Embedding Pipeline (Phase 0 Prerequisite)

### 6.1 Current State

Infrastructure exists but is entirely disconnected:

| Component | Status |
|---|---|
| pgvector extension + `node_embeddings` table | ✅ In `init-db.sql` |
| IVFFlat cosine index | ✅ In `init-db.sql` |
| `EmbeddingInputs` field on `TaskNode` | ✅ Defined in `models/nodes.py` |
| `EmbeddingClient` / embedding generation code | ❌ Does not exist |
| Trigger on task create/update | ❌ Does not exist |
| `TaskResolver._vector_search()` | ⚠️ Stub — passes `None` as vector, always returns unmatched |
| Table name in resolver | ⚠️ Bug — queries `task_embeddings`, actual table is `node_embeddings` |

### 6.2 EmbeddingClient

**File:** `src/graphclaw/infra/embeddings.py` (new)

```python
class EmbeddingClient:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None: ...
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
    async def close(self) -> None: ...
```

Env vars: `EMBEDDING_MODEL` (default `text-embedding-3-small`), `OPENAI_API_KEY`.

### 6.3 Embedding Trigger

On `create_node()` and `update_node()` for `TaskNode`: fire-and-forget embedding via `asyncio.create_task()` so task creation is not blocked.

Embedding text = `f"{task.title} {task.description} {task.embedding_inputs.goal_context}"`.

### 6.4 Resolver Fix

Two changes to `src/graphclaw/inbound/resolver.py`:
1. Fix table name: `task_embeddings` → `node_embeddings`
2. Generate embedding vector from `subject + " " + body[:300]` using `EmbeddingClient.embed()`, pass as `$1`

Existing confidence thresholds (`HIGH_THRESHOLD = 0.7`, `MEDIUM_THRESHOLD = 0.4`) are correct — no change needed.

---

## 7. Structured S3 Log Sink

### 7.1 MinIO Folder Structure

```
graphclaw/                              ← MinIO bucket
│
├── _system/                            ← infra events, no user context
│   └── logs/
│       ├── gateway/
│       │   └── {YYYY-MM-DD}/
│       │       └── {HH00Z}.jsonl       ← startup, channel errors, routing
│       ├── scorer/
│       │   └── {YYYY-MM-DD}/
│       │       └── {HH00Z}.jsonl
│       └── trigger-engine/
│           └── {YYYY-MM-DD}/
│               └── {HH00Z}.jsonl
│
└── {user_id}/
    ├── agents/...                      ← (existing)
    ├── inbox/...                       ← (existing, see §8)
    └── logs/
        ├── agent/
        │   └── {YYYY-MM-DD}/
        │       └── {HH00Z}.jsonl       ← tool_call, message, scoring_cycle, outbound_sent
        └── inbound/
            └── {YYYY-MM-DD}/
                └── {HH00Z}.jsonl       ← inbound_processed, intelligence_update
```

**Rationale for hybrid root:**
- `_system/`: infra events have no user context; forces no fake user association; separate lifecycle policy (90 days)
- `{user_id}/logs/`: user activity scoped under user root, enabling single-prefix GDPR erasure (`DELETE {user_id}/`), per-user IAM prefix conditions, per-user retention policy, multi-tenant cost attribution
- Both use identical JSONL format → same CloudWatch ingest pipeline (Fluent Bit or CW agent reads both prefixes)

### 7.2 Log Level Routing

| Level | Destination |
|---|---|
| DEBUG | stdout only (never persisted) |
| INFO, WARNING, ERROR | stdout + MinIO JSONL sink |

### 7.3 AsyncLogger Extension

Changes to `src/graphclaw/infra/logger.py`:

- Add `_storage: StorageClient | None` and `_log_prefix: str` to `__init__`
- Add `min_level: str = "INFO"` threshold
- `_write_batch()`: if `_storage` set and batch has entries at or above `min_level`, append to hourly JSONL file
- Add `AsyncLogger.create(service, storage=None, log_prefix="logs/", user_id=None)` factory

### 7.4 PII/PHI Safety — Allowlist-Only Log Events

**Core principle:** No message content, body, subject, or raw text ever reaches a durable log sink.

Each `event_type` has an explicit allowlist of safe fields:

| event_type | Safe fields only |
|---|---|
| `agent.tool_call` | `tool_name`, `user_id`, `latency_ms` — NO args |
| `agent.message` | `user_id`, `input_tokens`, `output_tokens`, `latency_ms` — NO content |
| `agent.scoring_cycle` | `user_id`, `tasks_scored`, `top_task_id`, `queue_depth` |
| `agent.inbound_processed` | `message_id`, `channel`, `task_id`, `signal`, `matched_by` — NO body |
| `agent.intelligence_update` | `task_id`, `channel`, `direction`, `action_taken` — NO summary text |
| `agent.outbound_sent` | `task_id`, `channel`, `recipient_hashed`, `subject_length` — NO body |
| `gateway.startup` | `service`, `version`, `channels_registered` |
| `gateway.error` | `error_type`, `channel`, `session_id` — NO message content |

**`args_summary` redaction in tool calls:** Known sensitive keys (`body`, `content`, `subject`, `to`, `text`, `message`, `email`) → `"[{key}: {N} chars]"`.

**Intelligence field PII scrubbing** (in `InboundIntelligenceAgent._scrub_pii()`): regex patterns for SSN (`\b\d{3}-\d{2}-\d{4}\b`), credit card, phone numbers → replaced with `[REDACTED-PII]`. The LLM summarization should abstract away raw PII, but the regex is a safety net before graph write.

---

## 8. Inbox Summarize-and-Archive

### 8.1 Folder Structure

```
{user_id}/
└── inbox/
    ├── recent/
    │   └── {ISO}-{msg_id}.json    ← compact summary (always small)
    └── archive/
        └── {ISO}-{msg_id}.json    ← full original email
```

### 8.2 Entry Contents

**Recent (compact) entry:**
```json
{
  "message_id": "...",
  "sender": "soni@acme.com",
  "subject": "Re: Deliverable",
  "body_summary": "First 150 chars of body...",
  "channel": "email",
  "received_at": "2026-04-12T14:23:00Z",
  "task_id_matched": "TSK-AG-13860-DEL",
  "signal": "RESOLVED",
  "archive_ref": "{user_id}/inbox/archive/2026-04-12T142300Z-msg-001.json"
}
```

**Archive (full) entry:**
```json
{
  "message_id": "...",
  "sender": "soni@acme.com",
  "subject": "Re: Deliverable",
  "body": "Full email body...",
  "raw_headers": {...},
  "attachments": [...],
  "received_at": "2026-04-12T14:23:00Z",
  "task_id_matched": "TSK-AG-13860-DEL",
  "signal": "RESOLVED",
  "intelligence_result": {...}
}
```

### 8.3 Betty's check_inbox Tool

Added to `AgentLoop._build_tool_definitions()`:

```python
ToolDefinition(
    name="check_inbox",
    description="Check recent inbound messages received from external contacts.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 5},
            "from_sender": {"type": "string", "default": ""},
            "channel": {"type": "string", "default": ""}
        },
        "required": []
    }
)
```

`_tool_check_inbox()` reads from `inbox/recent/` only — compact, always fast. Betty references `archive_ref` when the user asks for the full message.

---

## 9. InboundIntelligenceAgent MinIO Identity (vs. Betty)

| | Betty (orchestrating agent) | InboundIntelligenceAgent |
|---|---|---|
| User-facing | Yes | No |
| Persona / profile.md | Yes | No |
| Working memory | Yes — cross-task planning | No — writes to Betty's context |
| Episodic memory | Yes — session summaries | No |
| Semantic memory | Yes — long-term user/project facts | No |
| MinIO footprint | `agents/main/` full tree | `agents/intelligence-processor/config.json` + `execution_log/` |
| LLM usage | Orchestrating (Anthropic/Claude) | Lightweight (LiteLLM haiku/mini) |

---

## 10. SubAgentRunner Context Loading

`SubAgentRunner._build_system_prompt()` assembles the sub-agent's execution context in this order:

```
1. profile.md                    ← agent persona and working style
2. ## Working Context            ← memory/working/context.md
3. ## Episodic Memory            ← ALL files in memory/episodic/ (active only — NOT episodic/archive/)
                                    newest first; truncate oldest if over budget
4. ## Semantic Knowledge         ← ALL files in memory/semantic/
                                    knowledge.md first; rest alphabetical
```

**Token budget guard:** 80,000 tokens default. Truncation order when over budget:
1. Oldest episodic entries removed first
2. Alphabetically-last semantic files removed next
3. Working context is never truncated

**Current implementation state:**
- Steps 1–2 are implemented in SubAgentRunner.
- Steps 3–4 (episodic + semantic loading) are **not yet implemented** — required as backend change B5.

**Why this order:** Profile establishes the agent's identity and constraints. Working context is the most recent live scratchpad — highest priority after profile. Episodic entries are time-ordered session history; older ones are lower value and dropped first under pressure. Semantic knowledge is durable reference material; loss of any single file is more impactful than losing an old episodic entry, so it is dropped second.

### 10.1 Episodic Memory Loading Detail

```python
# Pseudocode for _build_system_prompt() episodic loading
active_entries = storage.list(StoragePaths.agent_memory_episodic_prefix(user_id, agent_id))
# NOTE: episodic/archive/ prefix is excluded — list only the direct episodic/ prefix
sorted_entries = sorted(active_entries, key=lambda e: e.created_at, reverse=True)  # newest first
for entry in sorted_entries:
    content = storage.get(StoragePaths.agent_memory_episodic_entry(user_id, agent_id, entry.name))
    if budget_remaining > len(content):
        prompt += f"\n\n### {entry.name}\n{content}"
        budget_remaining -= len(content)
    else:
        break  # oldest entries dropped silently
```

### 10.2 Semantic Memory Loading Detail

```python
# Pseudocode for _build_system_prompt() semantic loading
all_topics = storage.list(StoragePaths.agent_memory_semantic_prefix(user_id, agent_id))
# knowledge.md first; then alphabetical
ordered = sorted(all_topics, key=lambda t: (t.name != "knowledge.md", t.name))
for topic in ordered:
    content = storage.get(StoragePaths.agent_memory_semantic_topic(user_id, agent_id, topic.stem))
    if budget_remaining > len(content):
        prompt += f"\n\n### {topic.stem}\n{content}"
        budget_remaining -= len(content)
    else:
        break
```

---

## 11. Phase Plan

### Phase 0.5 — Embedding Pipeline (prerequisite, ~4 days)

| Step | File | Change |
|---|---|---|
| 0.1 | `src/graphclaw/infra/embeddings.py` | NEW: `EmbeddingClient` wrap OpenAI/LiteLLM embedding API |
| 0.2 | `src/graphclaw/db/age/repository.py` | Add embedding write on `create_node()` / `update_node()` for TaskNode (fire-and-forget) |
| 0.3 | `src/graphclaw/inbound/resolver.py` | Fix table name bug + wire `EmbeddingClient.embed()` for vector search |

### Phase 1 — Structured Log Sink (~2 days)

| Step | File | Change |
|---|---|---|
| 1.1 | `src/graphclaw/infra/logger.py` | Add `StorageClient` sink, `min_level` filter, `AsyncLogger.create()` factory |
| 1.2 | `src/graphclaw/gateway/deps.py` | Pass storage to AsyncLogger on init |
| 1.3 | `src/graphclaw/agent/loop.py` | Add `_logger` param; log `agent.tool_call`, `agent.message`, `agent.scoring_cycle` |

### Phase 2 — Node Intelligence Field (~2 days)

| Step | File | Change |
|---|---|---|
| 2.1 | `src/graphclaw/models/nodes.py` | Add `intelligence: str | None = None` to `TaskNode`, `GoalNode` |
| 2.2 | `src/graphclaw/db/age/repository.py` | Add `update_node_intelligence()`, `get_node_intelligence()`, `create_checkin_node()`, `update_checkin_response()` |
| 2.3 | `src/graphclaw/agent/loop.py` | `_build_graph_summary()` includes intelligence snippet |

### Phase 3 — InboundIntelligenceAgent (~3 days)

| Step | File | Change |
|---|---|---|
| 3.1 | `src/graphclaw/inbound/intelligence_agent.py` | NEW: full `InboundIntelligenceAgent` class |
| 3.2 | `src/graphclaw/agent/event_consumer.py` | Add `_intelligence_agent`, `_memory_lock`, `_inbound_consume_loop()` (direct INBOUND_MESSAGES consumer bypassing missing TriggerEngine), fix broken `InboundProcessor` instantiation, wire `_process_raw_inbound()` |

### Phase 4 — Outbound Intelligence Logging (~2 days)

| Step | File | Change |
|---|---|---|
| 4.1 | `src/graphclaw/agent/event_consumer.py` | Thread `task_id` to dispatcher calls; add `_append_outbound_intelligence()` |
| 4.2 | `src/graphclaw/db/age/repository.py` | `create_checkin_node()`, `update_checkin_response()` (wired here) |
| 4.3 | `src/graphclaw/agent/event_consumer.py` | Redis checkin key storage + reply linking |

### Phase 5 — Inbox + check_inbox Tool (~2 days)

| Step | File | Change |
|---|---|---|
| 5.1 | `src/graphclaw/infra/storage.py` | Add `agent_inbox_recent_prefix()`, `agent_inbox_archive()` |
| 5.2 | `src/graphclaw/agent/event_consumer.py` | Two-track inbox write in `_process_raw_inbound()` |
| 5.3 | `src/graphclaw/agent/loop.py` | Add `check_inbox` ToolDefinition + `_tool_check_inbox()` |

### Phase 6 — Direct INBOUND_MESSAGES Consumer (~1 day, bundled with 3.2)

| Step | File | Change |
|---|---|---|
| 6.1 | `src/graphclaw/agent/event_consumer.py` | Add second asyncio.Task consuming `INBOUND_MESSAGES` directly (bypasses missing TriggerEngine service in local dev); `stop()` cancels both tasks |
| 6.2 | `src/graphclaw/gateway/app.py` | Pass `default_user_id` from `GRAPHCLAW_USER_ID` env to `AgentEventConsumer` |

### Phase 7 — Intelligence Hub: Episodic + Semantic Context Loading (~1 day)

| Step | File | Change |
|---|---|---|
| 7.1 | `src/graphclaw/agent/sub_agent_runner.py` | `_build_system_prompt()`: load active episodic entries (from `episodic/` prefix, NOT `archive/`) + all semantic files; apply 80k token budget guard |
| 7.2 | `src/graphclaw/infra/storage.py` | Add `agent_memory_episodic_archive_prefix()`, `agent_memory_episodic_archive_entry()`, `agent_memory_working_archive_prefix()`, `agent_memory_working_archive_entry()` to `StoragePaths` |
| 7.3 | `src/graphclaw/api/intelligence.py` | Episodic archive endpoint; working/archive list endpoint; updated compact response with context metrics |

**Total estimated effort:** ~17 days

---

## 12. Open Questions / Future Enhancements

| Item | Decision | Notes |
|---|---|---|
| Intelligence trim strategy | Simple word-count truncation for Phase 1 | LLM re-consolidation (like `/compact`) is a future enhancement |
| `link_message_to_task` tool | Deferred | Betty can ask user to associate unmatched messages; tool needed for Betty to action it |
| GoalNode intelligence update path | Deferred | Goals not resolved by InboundProcessor; update via Betty's tools only |
| Intelligence field on other node types | Deferred | CheckinNode, ResourceNode could benefit; design when use case emerges |
| Embedding model selection | `text-embedding-3-small` (1536d) | Configurable via `EMBEDDING_MODEL` env var |
| MinIO SSE encryption for archive/ | Required for production | Add to docker-compose config + Phase 5 infra |
