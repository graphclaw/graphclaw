# Agent Monitor v2 — Backend Requirements

**Status:** Approved · **Date:** 2026-05-03 · **Companion to:** [`graphclaw-cockpit/docs/agent/`](../../../graphclaw-cockpit/docs/agent/)

This document is the backend (gateway) side of the Agent Monitor v2 build. It captures the new endpoints, log wiring, schema additions, and architectural fixes the cockpit depends on. The cockpit-side IA, UX, and component spec live in the cockpit repo at [`docs/agent/`](../../../graphclaw-cockpit/docs/agent/README.md).

**Architecture details:** [20-agent-activity-logging.md](../architecture/20-agent-activity-logging.md).
**Cockpit ↔ gateway contract:** [`graphclaw-cockpit/docs/agent/04-api-contract.md`](../../../graphclaw-cockpit/docs/agent/04-api-contract.md).

---

## 1. Goals

The cockpit's Agent Monitor v2 needs three things from the gateway that don't exist today:

1. **Historical activity** — read NDJSON logs out of MinIO, translate to plain English, paginate.
2. **Bilateral comms audit** — flatten `TaskNode.update_log[]` (inbound) and `CheckinNode` (outbound) into queryable per-user feeds.
3. **Run history** — a new `agent_session_log` table that records one row per agent run with aggregates (tool calls, tokens, messages).

Plus four supporting changes:

4. **Tool call logging** — `AgentToolCallEvent` is currently defined but never emitted. Wire it across all 5 agent files (main_orchestrator, sub_agent_runner, comms_agent, inbound_agent, outbound_agent).
5. **MinIO write race fix** — the `_append_to_s3` GET-then-PUT pattern can lose batches under concurrent writers.
6. **Plain-language formatter** — single source of truth for translating raw events into human strings, shared with cockpit via fixture.
7. **Verify two endpoints** — `POST /scoring/simulate` and `GET /agents/delegations` may not exist; add minimal implementations if absent.

---

## 2. Functional requirements

### FR-1 — Historical activity feed
**Endpoint:** `GET /app/v1/agent/activity`
**Operation ID:** `getAgentActivity`
**Auth:** required, user-scoped via `Depends(current_user)`.

Reads MinIO NDJSON for the requesting user across the time window, applies plain-language formatter to each event, returns paginated list. See [04-api-contract.md §3.1](../../../graphclaw-cockpit/docs/agent/04-api-contract.md) for full request/response shape.

**Behavioural requirements:**
- FR-1.1 — `from`/`to` are required ISO8601; reject ranges > 7 days with `400 range_too_large`.
- FR-1.2 — `type` filter applied server-side (decisions / comms / skills / errors / all).
- FR-1.3 — Hard cap: scan ≤ 50 NDJSON files per request; return cursor for continuation.
- FR-1.4 — Cursor is opaque base64 of `{file_key, line_offset}`.
- FR-1.5 — Each row's `message` is produced by `activity_formatter.format_event(record)`.
- FR-1.6 — Returns events in reverse chronological order (newest first).
- FR-1.7 — Records missing required fields are skipped silently with WARN log.

### FR-2 — Run history
**Endpoint:** `GET /app/v1/agent/sessions`
**Operation ID:** `getAgentSessions`
**Auth:** required, user-scoped.

Queries new `agent_session_log` table; returns per-run aggregates.

**Behavioural requirements:**
- FR-2.1 — Default range: last 7 days. Configurable up to 30 days.
- FR-2.2 — `limit` ≤ 50; cursor is offset-based.
- FR-2.3 — Returns sessions in reverse chronological order.

### FR-3 — Comms summary
**Endpoint:** `GET /app/v1/comms/summary`
**Operation ID:** `getCommsSummary`
**Auth:** required, user-scoped.

Returns `{ received, sent, matched, unmatched }` for a given date.

**Behavioural requirements:**
- FR-3.1 — `received` = count `update_log[]` entries on user's `TaskNode`s where date matches.
- FR-3.2 — `sent` = count `CheckinNode` records linked to user's tasks where created date matches.
- FR-3.3 — `matched` = subset of received where the entry has a `task_id` match (i.e., agent successfully linked it).
- FR-3.4 — `unmatched` = `received - matched`.
- FR-3.5 — Cacheable for 60s.

### FR-4 — Inbound log
**Endpoint:** `GET /app/v1/tasks/inbound-log`
**Operation ID:** `getInboundLog`
**Auth:** required, user-scoped.

Flattens `TaskNode.update_log[]` across all user's tasks; paginates.

**Behavioural requirements:**
- FR-4.1 — Cypher unwinds `update_log[]`, filters by date range, sorts desc by entry timestamp.
- FR-4.2 — Returns one row per `update_log[]` entry (not per task).
- FR-4.3 — Cursor is `{ts, id}` for stable pagination.
- FR-4.4 — Returns `messagePreview` truncated at 60 chars; full message available via task detail endpoint.

### FR-5 — Outbound log
**Endpoint:** `GET /app/v1/tasks/outbound-log`
**Operation ID:** `getOutboundLog`
**Auth:** required, user-scoped.

Returns `CheckinNode` records linked to user's tasks; paginates.

**Behavioural requirements:**
- FR-5.1 — Filter by user (via task ownership traversal).
- FR-5.2 — `toDisplay` resolved via priority chain:
  1. `agent_channel_identities[recipient_hashed].display_name`
  2. `TaskNode.counterparty.display_name`
  3. `User-{lastFour}` fallback
- FR-5.3 — Cursor is `{created_at, id}`.

### FR-6 — Tool call logging
Wire `logger.info("agent.tool_call", ...)` at every TOOL_COMPLETED (success or fail) in:
- `src/graphclaw/agent/main_orchestrator.py`
- `src/graphclaw/agent/sub_agent_runner.py`
- `src/graphclaw/agent/comms_agent.py`
- `src/graphclaw/agent/inbound_agent.py`
- `src/graphclaw/agent/outbound_agent.py`

**Event payload (extended `AgentToolCallEvent`):**
```python
class AgentToolCallEvent(BaseModel):
    tool_name: str
    user_id: str
    latency_ms: int
    session_id: str          # NEW
    task_id: str | None = None  # NEW
    success: bool            # NEW
    attempt: int = 1         # NEW
```

**Behavioural requirements:**
- FR-6.1 — Logged at INFO level; rolls to MinIO via existing handler.
- FR-6.2 — Unit test per agent file asserts log emission shape and field presence.
- FR-6.3 — `session_id` always set (use `get_session_id()` ContextVar).
- FR-6.4 — `task_id` set when tool was task-scoped; null for run-level tools.

### FR-7 — Session log table
New table `agent_session_log` with one row per agent run.

**Schema (see B-2 in §4):**
```
session_id, user_id, started_at, completed_at,
trigger_type, tool_call_count, skill_count,
messages_sent, messages_received, input_tokens, output_tokens, status
```

**Behavioural requirements:**
- FR-7.1 — Insert row at run start in `agent/loop.py` (status='running').
- FR-7.2 — Update aggregates as run proceeds (counters incremented, tokens accumulated).
- FR-7.3 — Final update on run completion (status='completed' / 'failed' / 'cancelled', completed_at set).
- FR-7.4 — Unique on `session_id`.
- FR-7.5 — Indexed on `(user_id, started_at DESC)` for efficient retrieval.

### FR-8 — Plain-language formatter
**File:** `src/graphclaw/agent/activity_formatter.py`

Single function `format_event(record: dict) -> str` that produces the human-readable string for any event type the cockpit displays.

**Behavioural requirements:**
- FR-8.1 — Pure function; no I/O.
- FR-8.2 — Snapshot tested against `tests/fixtures/event_formatter_cases.json`.
- FR-8.3 — Cockpit copies the same fixture and snapshot tests its `formatEvent.ts` against it. CI fails on divergence.
- FR-8.4 — Returns sentence-case strings (e.g., "Scored 14 tasks — top priority: …").

### FR-9 — MinIO write race fix
Modify `ObjectStorageHandler._compute_path` to include process pid + 6-char uuid suffix per process lifetime:
```
{user_id}/logs/{service}/{date}/{HH}00Z-{pid}-{suffix}.jsonl
```

**Behavioural requirements:**
- FR-9.1 — Each process gets a stable `(pid, suffix)` pair at startup.
- FR-9.2 — Reader (`/agent/activity`) globs `{HH}00Z*.jsonl` and merges by timestamp.
- FR-9.3 — Concurrency test: 4 processes × 100 records each → all 400 readable.

### FR-10 — Verification: existing endpoints
Confirm or add:
- `POST /app/v1/scoring/simulate` — accepts 7 factor overrides + `taskId`, returns hypothetical score with delta. No persistence.
- `GET /app/v1/agents/delegations` — returns currently-delegated agents with status, heartbeat age, duration.

---

## 3. Non-functional requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | All endpoints return JSON, camelCase keys. |
| NFR-2 | All endpoints under `/app/v1/`. |
| NFR-3 | All endpoints require user-scoped JWT auth (`Depends(current_user)`); responses scoped to that user. |
| NFR-4 | Rate limits: `/agent/activity` 60/min/user, `/scoring/simulate` 30/min/user, others use existing limits. |
| NFR-5 | All new code passes `ruff check --fix` + `ruff format` before commit. |
| NFR-6 | All new endpoints have happy-path + auth-failure + edge-case tests under `tests/api/`. |
| NFR-7 | Existing 1451 tests must continue passing. |
| NFR-8 | All endpoint paths appear in `/openapi.json` with stable `operationId` (so cockpit's openapi-fetch typegen stays consistent). |
| NFR-9 | Plain-language formatter is byte-identical between gateway and cockpit (CI snapshot diff). |
| NFR-10 | MinIO file naming convention follows FR-9.1 going forward. Reader handles legacy files (without pid suffix) for backward compatibility. |

---

## 4. Backend tasks (B-1..B-9)

### B-0 — Migration numbering check
Run `ls migrations/` to confirm next available number (placeholder: 0023). Replace placeholder before B-2.

### B-1 — Extend AgentToolCallEvent + wire across 5 agent files
**Files:**
- `src/graphclaw/infra/logging/events.py` — extend model
- `src/graphclaw/agent/main_orchestrator.py` — wire at TOOL_COMPLETED
- `src/graphclaw/agent/sub_agent_runner.py` — wire at TOOL_COMPLETED
- `src/graphclaw/agent/comms_agent.py` — wire at TOOL_COMPLETED
- `src/graphclaw/agent/inbound_agent.py` — wire at TOOL_COMPLETED
- `src/graphclaw/agent/outbound_agent.py` — wire at TOOL_COMPLETED
- `tests/agent/test_tool_call_logging.py` — new test, per-agent emission shape assertions

### B-2 — agent_session_log table + writer
**Files:**
- `migrations/<next>_agent_session_log.sql`
- `src/graphclaw/db/models/agent_session.py` (new SQLAlchemy model)
- `src/graphclaw/agent/loop.py` (insert/update calls)
- `tests/db/test_agent_session_log.py`

**Migration SQL:**
```sql
CREATE TABLE agent_session_log (
    session_id        TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL,
    completed_at      TIMESTAMPTZ,
    trigger_type      TEXT,
    tool_call_count   INT DEFAULT 0,
    skill_count       INT DEFAULT 0,
    messages_sent     INT DEFAULT 0,
    messages_received INT DEFAULT 0,
    input_tokens      INT DEFAULT 0,
    output_tokens     INT DEFAULT 0,
    status            TEXT
);
CREATE INDEX agent_session_log_user_started_idx
  ON agent_session_log (user_id, started_at DESC);
```

### B-3 — GET /agent/activity
**Files:**
- `src/graphclaw/api/agent_activity.py` (new router)
- `src/graphclaw/agent/activity_formatter.py` (new)
- `src/graphclaw/storage/minio_log_reader.py` (new helper for file listing + line iteration)
- `tests/api/test_agent_activity_api.py`
- `tests/agent/test_activity_formatter.py` — snapshot tests
- `tests/fixtures/event_formatter_cases.json` (new — shared with cockpit)

### B-4 — GET /agent/sessions
**Files:**
- `src/graphclaw/api/agent_activity.py` (extend with sessions route)
- `tests/api/test_agent_sessions_api.py`

### B-5 — GET /comms/summary
**Files:**
- `src/graphclaw/api/comms.py` (new router)
- `src/graphclaw/db/queries/comms_summary.py` (new — Cypher composition)
- `tests/api/test_comms_summary_api.py`

### B-6 — GET /tasks/inbound-log + /tasks/outbound-log
**Files:**
- `src/graphclaw/api/tasks.py` (extend)
- `src/graphclaw/db/queries/inbound_outbound_log.py` (new — Cypher composition)
- `src/graphclaw/services/display_name_resolver.py` (new — implements priority chain from FR-5.2)
- `tests/api/test_inbound_outbound_log.py`

### B-7 — Fix MinIO write race
**Files:**
- `src/graphclaw/infra/logging/handlers/object_storage.py` (modify `_compute_path`)
- `tests/infra/test_object_storage_race.py` (new — concurrency test)

**Behavioural change:**
- Each process generates `(pid, uuid4()[:6])` at handler init; embedded in every file path.
- `_append_to_s3` no longer needs read-modify-write since each process writes to its own file.
- Reader globs `{HH}00Z*.jsonl` and merges.

### B-8 — POST /scoring/simulate (verify or add)
**Files (if missing):**
- `src/graphclaw/api/scoring.py` (extend)
- `src/graphclaw/scoring/simulator.py` (new — pure function, no DB writes)
- `tests/api/test_scoring_simulate.py`

### B-9 — GET /agents/delegations (verify or add)
**Files (if missing):**
- `src/graphclaw/api/agents.py` (extend)
- `tests/api/test_agents_delegations.py`

---

## 5. Test strategy

### Unit tests
- Each new endpoint: happy path + auth required + edge cases (empty range, invalid params, range-too-large).
- Each `AgentToolCallEvent` wiring site: log emission shape assertion.
- `activity_formatter.format_event`: snapshot against fixture.
- `_compute_path`: returns expected pattern.

### Integration tests
- Concurrency: 4 processes × 100 records → all readable (B-7).
- End-to-end: emit tool call → log lands in MinIO → `/agent/activity` returns it formatted.
- Session log: run loop → row created/updated correctly.

### Snapshot tests
- Plain-language formatter: every event_type case in `tests/fixtures/event_formatter_cases.json`.
- Same fixture copied to cockpit; cockpit CI fails on divergence.

---

## 6. Sequencing & dependencies

```
B-0 (migration #) ─┐
                   ├──> B-2 (session table)
                   │
B-1 (tool calls)   │
                   │
B-7 (race fix) ────┴──> B-3 (/agent/activity)
                                │
                   ┌────────────┘
                   │
B-4 (/agent/sessions) ─────────────────────┐
                                            │
B-5 (/comms/summary)                        │
                                            ├─> Cockpit Phase B unblocked
B-6 (/tasks/{inbound,outbound}-log)         │
                                            │
B-8, B-9 (verify) ──────────────────────────┘
```

Recommended order: B-0 → B-1 → B-7 → B-2 → B-3 → B-4 → B-5 → B-6 → B-8 → B-9.

---

## 7. Acceptance criteria (per task)

| Task | Acceptance |
|------|-----------|
| B-1 | All 5 agent files emit `agent.tool_call` log; tests pass; record visible in MinIO file |
| B-2 | Migration applies cleanly; loop writes row; row visible via direct DB query |
| B-3 | Endpoint returns formatted events; range > 7 days rejected; cursor round-trips |
| B-4 | Endpoint returns sessions in reverse-chrono; filtered by user_id |
| B-5 | Endpoint returns correct counts for fixture data |
| B-6 | Endpoints return paginated rows; display name resolution works through fallback chain |
| B-7 | Race test passes (400/400 records readable); legacy files still readable |
| B-8 | Endpoint accepts overrides, returns delta without persisting |
| B-9 | Endpoint returns currently-delegated agents with stale-heartbeat detection |

---

## 8. Out of scope for this build

(Explicit deferrals, see [`graphclaw-cockpit/docs/agent/05-open-risks.md`](../../../graphclaw-cockpit/docs/agent/05-open-risks.md).)

- LLM cost tracking (`cost_usd` always 0 in providers — separate gap).
- MinIO retention worker (gzip > 7 days, delete > 30 days).
- Per-agent invocation profile aggregation.
- Distributed trace waterfall (single-session deep view).
- Structured log NDJSON browser for admins.
