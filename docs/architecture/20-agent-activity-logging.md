# 20 — Agent Activity Logging

**Status:** Draft v1.0 | **Date:** 2026-05-03

This document describes how GraphClaw captures, stores, and surfaces agent activity for the cockpit's Agent Monitor v2. It defines the logging pipeline, the new `agent_session_log` table, the plain-language formatter, and the MinIO write race fix introduced in this build.

**Companion docs:**
- [`docs/requirements/agent-monitor-v2-backend.md`](../requirements/agent-monitor-v2-backend.md) — functional requirements
- [`graphclaw-cockpit/docs/agent/`](../../../graphclaw-cockpit/docs/agent/README.md) — cockpit-side IA + components
- [`graphclaw-cockpit/docs/agent/04-api-contract.md`](../../../graphclaw-cockpit/docs/agent/04-api-contract.md) — endpoint contracts
- [14-agent-triad.md](14-agent-triad.md) — agent triad context (now logs tool calls)
- [10-agent-loop-orchestration.md](10-agent-loop-orchestration.md) — main loop (now writes session log)
- [11-sub-agent-orchestration.md](11-sub-agent-orchestration.md) — sub-agent runner (now logs tool calls)

---

## 1. Pipeline overview

```
                        (per request / per cycle)
   Agent code (5 files)
   ─────────────────────
   main_orchestrator         │
   sub_agent_runner          │  logger.info("agent.tool_call", extra={...})
   comms_agent               │  logger.info("agent.message", extra={...})
   inbound_agent             │  logger.info("agent.scoring_cycle", extra={...})
   outbound_agent            │  logger.info("outbound.sent", extra={...})
                             ▼
              ┌──────────────────────────────┐
              │  stdlib QueueHandler         │
              │  (in-process, non-blocking)  │
              └──────────┬───────────────────┘
                         │ enqueue → bounded queue
                         ▼
              ┌──────────────────────────────┐
              │  QueueListener (OS thread)   │
              │  fan-out to handlers         │
              └─┬────────────┬───────────────┘
                │            │
        ┌───────▼─────┐  ┌───▼───────────────┐
        │ stdout      │  │ ObjectStorageHandler │
        │ JSON        │  │ batched → MinIO    │
        └─────────────┘  └────────┬───────────┘
                                  │
                                  ▼
              {user_id}/logs/{service}/{date}/{HH}00Z-{pid}-{suffix}.jsonl
                                  │
                                  │ on demand (cockpit reads)
                                  ▼
              ┌──────────────────────────────┐
              │  GET /app/v1/agent/activity  │
              │  - lists files for window    │
              │  - parses NDJSON lines       │
              │  - applies activity_formatter│
              │  - paginates + cursors       │
              └──────────────────────────────┘

Parallel:
   on agent run start/complete
   ───────────────────────────
   agent/loop.py   ─INSERT/UPDATE→  Postgres `agent_session_log`
                                          │
                                          ▼
                               GET /app/v1/agent/sessions
```

---

## 2. Storage layout (MinIO)

### 2.1 Path convention

```
{user_id}/logs/{service}/{YYYY-MM-DD}/{HH}00Z-{pid}-{suffix}.jsonl
```

| Segment | Meaning |
|---------|---------|
| `{user_id}` | Owning user. System-level logs use `system/`. |
| `{service}` | Source service (`gateway`, `agent-runtime`, `inbound-processor`, etc.). |
| `{YYYY-MM-DD}` | UTC date. |
| `{HH}00Z` | UTC hour (00–23). |
| `{pid}` | OS process id of the writer. |
| `{suffix}` | 6-char random uuid set once per process lifetime. |

The `{pid}-{suffix}` segment is **new in this build** and resolves the write race in §4.

### 2.2 File contents

Each line is a JSON object with at minimum:
```json
{
  "timestamp": "2026-05-03T14:32:07.123Z",
  "level": "INFO",
  "event_type": "agent.tool_call",
  "service": "agent-runtime",
  "user_id": "abc-123",
  "session_id": "SES-3f8a2c1d",
  "message": "Tool call completed"
}
```

Plus event-specific fields per the model in `infra/logging/events.py`.

### 2.3 System logs

Files at `system/logs/...` are **never** read by the cockpit. Admin / engineering tooling only. Phase C may build a structured log viewer for these.

---

## 3. Event types written

| Event type | Emitted from | When |
|------------|--------------|------|
| `agent.tool_call` *(NEW Phase B)* | All 5 agent files | TOOL_COMPLETED (success or fail) |
| `agent.message` | LLM call sites | LLM message completes |
| `agent.scoring_cycle` | scoring engine | each cycle finishes |
| `outbound.sent` | outbound agent | message dispatched successfully |
| `agent.task_started` | sub_agent_runner | sub-agent picks up delegated task |
| `agent.task_progress` | sub_agent_runner | intermediate progress |
| `agent.task_completed` | sub_agent_runner | task finishes |
| `agent.task_blocked` | sub_agent_runner | heartbeat timeout / explicit blocker |
| `agent.heartbeat` | sub_agent_runner | liveness pulse |
| `inbound.processed` | inbound agent | message classified + matched |
| `intelligence.update` | distillation pipeline | memory note written |
| `mcp.action` | MCP gateway | MCP tool invoked |

All these models live in `src/graphclaw/infra/logging/events.py` with explicit field allowlists for PII safety.

### 3.1 New: `AgentToolCallEvent` extension

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

Wire site pattern (per agent file):
```python
event = AgentToolCallEvent(
    tool_name=name,
    user_id=current_user_id,
    latency_ms=int((time.monotonic() - t0) * 1000),
    session_id=get_session_id(),
    task_id=current_task_id,
    success=not raised,
    attempt=attempt,
)
logger.info(
    "agent.tool_call",
    extra={"event_type": "agent.tool_call", **event.model_dump()},
)
```

Logged at INFO level — flows to MinIO via existing handler.

---

## 4. The MinIO write race fix

### 4.1 Problem

`ObjectStorageHandler._append_to_s3` does:
```python
existing = client.get_object(Bucket, Key).read()
client.put_object(Bucket, Key, existing + new_lines)
```

Two processes writing to the **same hourly file** (same user, same service, same hour) can interleave: P1 reads, P2 reads, P1 writes (with its records), P2 writes (with its records but missing P1's). P1's batch is silently lost.

### 4.2 Fix

Each process writes to a **distinct file** by including its `pid + 6-char uuid` in the path:

```python
class ObjectStorageHandler:
    def __init__(self, ...):
        ...
        import os, uuid
        self._process_id = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"

    def _compute_path(self, record):
        ...
        return (
            f"{user_id}/logs/{service}/{date}/"
            f"{hour}00Z-{self._process_id}.jsonl"
        )
```

`_append_to_s3` simplifies — no read-modify-write needed since each process owns its file:
```python
client.put_object(Bucket, Key, existing_buffer + new_lines)
# 'existing_buffer' is now this process's own previous contents (in-memory)
```

The reader (next section) merges all per-process files for an hour by timestamp.

### 4.3 Backward compatibility

Files written before the fix (without pid suffix) are still valid NDJSON. Reader's glob `{HH}00Z*.jsonl` matches both old (`14:00Z.jsonl`) and new (`14:00Z-1234-abc123.jsonl`) names.

### 4.4 Test
`tests/infra/test_object_storage_race.py`: spawn 4 processes × 100 records each. Read all matching files. Assert all 400 records present.

---

## 5. The activity reader

### 5.1 Strategy

For `GET /app/v1/agent/activity?from=&to=`:

1. **Determine file set:** for each (user_id, service, hour) intersecting `[from, to]`, list files matching `{HH}00Z*.jsonl`. Cap at 50 total files; reject larger ranges.
2. **Read in reverse-chronological order** (newest hour first).
3. **Parse each line** as JSON; skip malformed lines with WARN log.
4. **Filter** by `event_type` per `type` param.
5. **Format** each record via `activity_formatter.format_event(record) → str`.
6. **Paginate**: cursor encodes `{file_key, line_offset}` so the next call resumes mid-file.

### 5.2 Performance characteristics

- 1 user × 1 service × 1 hour × 1 process = 1 file.
- Worst case (Phase C concern): 1 user × 4 services × 168 hours × 4 processes = 2,688 files. Capped at 50.
- Per file: typically < 1 MB. Parse cost ~50ms.

If/when this becomes a bottleneck (Phase C), options:
- Pre-aggregate to Postgres summary table.
- Use an OLAP backend (DuckDB on parquet, ClickHouse).

---

## 6. The plain-language formatter

### 6.1 Source of truth

```
src/graphclaw/agent/activity_formatter.py
```

Single function:
```python
def format_event(record: dict) -> str:
    """Translate a structured log record to a one-line human string."""
    event_type = record["event_type"]
    if event_type == "task.scored":
        return f"Scored {record['count']} tasks — top priority: {record['top_task_title']}"
    if event_type == "skill.completed":
        if record.get("status") == "failed":
            err = record.get("error", {})
            if err.get("type") == "TimeoutError":
                return f"{record['skill_name']} skill failed — timed out after {err['after_seconds']}s on attempt {err['attempt']}"
            return f"{record['skill_name']} skill failed — {err.get('message', 'unknown error')}"
        return f"{record['skill_name']} skill completed for {record.get('task_title', 'task')} ({record['duration_ms']/1000:.0f}s, {record.get('tokens', 0)} tokens)"
    # … other event types
```

Pure function — no I/O, no side effects. Snapshot tested.

### 6.2 Cockpit parity

The cockpit has a parallel implementation in TypeScript at `src/features/agent-monitor/lib/formatEvent.ts`. Both are tested against:

```
tests/fixtures/event_formatter_cases.json
```

Each case is `{ input, expected }`. The cockpit copies this fixture verbatim and asserts its formatter produces the same string. CI in cockpit fails if the fixture has drifted (file hash mismatch).

This gives us **byte-identical strings** between live SSE ticker (cockpit-formatted) and historical activity feed (gateway-formatted).

### 6.3 Adding new event types

When adding a new event type:
1. Add a case to `format_event` in gateway.
2. Add a case to `formatEvent` in cockpit.
3. Add a case to the JSON fixture.
4. Run gateway tests + cockpit tests. Both must pass.

---

## 7. The session log table

### 7.1 Schema

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

### 7.2 Write pattern

In `agent/loop.py`:

```python
# At run start
session_id = generate_session_id()
set_session_id(session_id)
db.execute(
    "INSERT INTO agent_session_log (session_id, user_id, started_at, trigger_type, status) "
    "VALUES (:sid, :uid, :now, :trig, 'running')",
    sid=session_id, uid=user_id, now=now_utc(), trig=trigger_type,
)

# As run proceeds — increment counters / accumulate tokens
# (use UPDATE … SET tool_call_count = tool_call_count + 1, …)

# On completion
db.execute(
    "UPDATE agent_session_log SET status=:s, completed_at=:c, "
    "tool_call_count=:tc, skill_count=:sk, messages_sent=:ms, "
    "messages_received=:mr, input_tokens=:it, output_tokens=:ot "
    "WHERE session_id=:sid",
    s='completed', c=now_utc(), tc=…, sk=…, ms=…, mr=…, it=…, ot=…, sid=session_id,
)
```

### 7.3 Why a new table (vs reading MinIO)?

- Aggregates over a session require scanning many log lines — too slow for the Run History list.
- The cockpit's Activity panel needs grouping by `session_id`; the table provides cheap per-session summaries.
- Future analytics (per-user weekly summaries, anomaly detection) all benefit.

---

## 8. Endpoint surface

Endpoints introduced or modified by this build, all `/app/v1/`:

| Endpoint | Method | Purpose | New? |
|----------|--------|---------|------|
| `/agent/activity` | GET | Historical activity feed | NEW |
| `/agent/sessions` | GET | Run history | NEW |
| `/comms/summary` | GET | Today's received/sent counts | NEW |
| `/tasks/inbound-log` | GET | Flattened `update_log[]` per user | NEW |
| `/tasks/outbound-log` | GET | `CheckinNode` records per user | NEW |
| `/scoring/simulate` | POST | What-if score recompute | VERIFY |
| `/agents/delegations` | GET | Active sub-agent delegations | VERIFY |

Full request/response shapes: [04-api-contract.md](../../../graphclaw-cockpit/docs/agent/04-api-contract.md).

All require `Depends(current_user)`; all responses are user-scoped.

---

## 9. Authorisation

- Every endpoint resolves the user from the JWT and scopes data to that user.
- MinIO reader uses `user_id` from JWT to compute the file prefix; system files are never accessible via these endpoints.
- Graph queries filter by ownership traversal (only the requesting user's tasks).
- Postgres queries include `WHERE user_id = :current_user`.
- Cross-user access is impossible by construction — there is no admin override in these endpoints.

---

## 10. Cost / token tracking — explicitly out of scope

`cost_usd` is `0.0` across all current LLM providers. Until a provider populates it correctly, the cockpit deliberately hides cost columns and shows tokens only. This is **Phase C** work in this build.

When fixed:
- Add `total_cost_usd` to `agent_session_log`.
- Add cost columns to `/agent/activity`, `/agent/sessions` responses.
- Cockpit re-introduces cost columns + LLM Cost Monitor surface.

---

## 11. Retention — explicitly out of scope

There is currently **no retention policy** on MinIO logs. Files accumulate indefinitely. Phase C adds a nightly worker:

1. Files > 7 days old: gzip in place (~10× compression).
2. Files > 30 days old: delete (or move to cold storage tier).
3. Reader handles both `.jsonl` and `.jsonl.gz`.

Until then: monitor MinIO disk usage in admin dashboards.

---

## 12. Open issues / future work

| Topic | Status |
|-------|--------|
| Cost tracking | Phase C (provider gap) |
| MinIO retention | Phase C |
| Per-agent invocation profile aggregation | Phase C |
| Distributed trace waterfall (single session deep view) | Phase C |
| Structured log NDJSON browser | Phase C (admin) |
| `update_log[]` unbounded growth | Watch — may need archival in Phase C |
| Activity range query performance | Mitigated (50-file cap); revisit if data grows |

---

## 13. Summary diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                       Agent Activity Logging Pipeline                     │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   5 agent files ─emit→ logger.info("agent.tool_call", ...) etc.          │
│                                                                          │
│   QueueHandler ─→ QueueListener (OS thread) ─→ ObjectStorageHandler      │
│                                                       │                  │
│                                                       ▼                  │
│                              {user}/logs/{svc}/{date}/{hh}00Z-{pid}-…    │
│                                                       │                  │
│                       ┌───────────────────────────────┘                  │
│                       │                                                  │
│                       ▼                                                  │
│              activity_formatter.format_event() ── shared fixture ──→ cockpit  │
│                       │                                                  │
│                       ▼                                                  │
│              GET /agent/activity ←──── cockpit ActivityFeed              │
│                                                                          │
│   loop.py ─→ Postgres agent_session_log ─→ GET /agent/sessions          │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```
