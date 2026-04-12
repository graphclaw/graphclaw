# GraphClaw — Future Phases Implementation Plan

**Version:** 1.0 | **Date:** 2026-04-11 | **Status:** Planning
**Scope:** Features documented in the PRD or review notes that are deferred beyond the current implementation baseline.

> This document is the backlog for deferred features. Each item includes the original design intent, the current state, the implementation specification, and the dependencies required before work can begin. When a phase is picked up, move its section into the active build plan and delete it from here.

---

## Index

| Phase | Feature | Trigger Condition | Complexity |
|-------|---------|-------------------|------------|
| [F-1](#f-1-message-broker-hardening-redis) | Message Broker Hardening (Redis) | Before AWS production go-live | Medium |
| [F-2](#f-2-sqsbroker-for-production-scale) | SQSBroker for Production Scale | Queue depth consistently non-zero or horizontal consumer scaling needed | High |
| [F-3](#f-3-status_updates-consumer--state-machine-wiring) | `STATUS_UPDATES` Consumer | Required for inbound messages to drive task state transitions | Medium |
| [F-4](#f-4-skill_jobs-async-consumer) | `SKILL_JOBS` Async Consumer | Required for async (non-blocking) skill execution | Medium |
| [F-5](#f-5-approval-escalation--timeout) | Approval Escalation & Timeout | Required for production workflows with SLA expectations | Medium |
| [F-6](#f-6-recurring-task-spawn-trigger) | Recurring Task Spawn Trigger | Required for automated task repetition (RECURRING type) | Low |
| [F-7](#f-7-embedding-staleness-recompute) | Embedding Staleness Recompute | Required for accurate inbound message resolution over time | Medium |
| [F-8](#f-8-multi-user-graph-conflict-resolution) | Multi-User Graph Conflict Resolution | Required when multiple agents or users write to the same graph simultaneously | High |
| [F-9](#f-9-skill-quality-feedback-loop) | Skill Quality Feedback Loop | Required for marketplace quality ranking | Low |

---

## F-1: Message Broker Hardening (Redis)

**Original design intent (PRD §29.5):** Both `INBOUND_MESSAGES` and `OUTBOUND_MESSAGES` queues must have DLQ semantics with 3-retry limits. The PRD assumed SQS would provide this natively. Since Redis is the confirmed broker for both local and initial AWS deployment, these guarantees must be implemented within `RedisMessageBroker`.

**Current state:**
- `RedisMessageBroker` uses `LPUSH` / `BRPOP` with implicit at-most-once delivery
- No retry counter, no dead-letter list, no reaper task
- If a consumer crashes mid-processing, the message is silently dropped
- ElastiCache (production) has no AOF persistence by default — messages lost on Redis restart

**Implementation specification:**

### 1. Two-List Reliable Queue Pattern

Replace the current single-list BRPOP with a two-list pattern:

```
inbound_messages          ← producer list (LPUSH here)
inbound_messages:processing  ← in-flight list (consumer moves here)
inbound_messages:dead        ← dead-letter list (reaper moves poison messages here)
```

Consumer flow:
```python
# Atomic move: BRPOP source + LPUSH processing
msg = await r.brpoplpush("inbound_messages", "inbound_messages:processing", timeout=5)
try:
    await process(msg)
    await r.lrem("inbound_messages:processing", 1, msg)   # ACK
except Exception:
    await increment_retry_counter(msg)  # stored in Redis hash
    if retry_count >= MAX_RETRIES:
        await r.lmove("inbound_messages:processing", "inbound_messages:dead", ...)
    else:
        await r.lmove("inbound_messages:processing", "inbound_messages", ...)  # re-enqueue
```

The `MessageBroker.acknowledge()` method is already defined on the ABC as a no-op — replace with real LREM logic.

### 2. Reaper Task

A background task that wakes every 60 seconds and scans all `:processing` lists. Any message that has been in `:processing` longer than `PROCESSING_TIMEOUT_SECONDS` (default: 300) is considered failed and is re-enqueued (or moved to `:dead` if `retry_count >= MAX_RETRIES`).

```python
async def _reaper_loop(broker: RedisMessageBroker) -> None:
    while True:
        await asyncio.sleep(60)
        for queue in QueueNames.all():
            await broker.reap_stale(queue, timeout_seconds=300)
```

Start alongside the existing consumer loops in `gateway/app.py`.

### 3. Dead-Letter CLI Commands

Expose dead-letter inspection and replay via CLI:

```bash
graphclaw broker dead-letters list [--queue inbound_messages]
graphclaw broker dead-letters show <message_id>
graphclaw broker dead-letters replay <message_id>    # re-enqueues from :dead → source
graphclaw broker dead-letters purge [--queue inbound_messages]  # destructive — requires --confirm
```

### 4. AOF Persistence on ElastiCache

In the ElastiCache Redis parameter group (`infra/` Terraform):
```hcl
parameter {
  name  = "appendonly"
  value = "yes"
}
parameter {
  name  = "appendfsync"
  value = "everysec"    # balance between durability and performance
}
```

**Files to change:**
- `src/graphclaw/infra/broker.py` — `RedisMessageBroker.consume()`, `acknowledge()`, new `reap_stale()`
- `src/graphclaw/infra/config.py` — `BrokerConfig` add `processing_timeout_seconds`, `max_retries`
- `src/graphclaw/cli/main.py` — add `broker` sub-command group
- `src/graphclaw/cli/broker_commands.py` — new CLI module
- `infra/terraform/elasticache.tf` — AOF parameter group

**Dependencies:** None. Can be implemented independently of all other features.

**Test coverage required:**
- Consumer crashes mid-process → message re-enqueues
- Message exceeds `max_retries` → moves to dead-letter list
- Reaper re-enqueues stale processing entries
- Dead-letter CLI replay re-enqueues correctly

---

## F-2: SQSBroker for Production Scale

**Original design intent (`architecture.md`):** `SQSBroker` was planned as the production message broker, providing native at-least-once delivery, DLQ, and CloudWatch queue-depth metrics for auto-scaling.

**Current state:** Only `RedisMessageBroker` is implemented. `BrokerConfig.backend` accepts `"sqs"` but no factory reads it.

**Trigger condition:** Implement when any of the following are true:
- Daily inbound message volume exceeds ~500 and queue depth is regularly non-zero (consumer backpressure)
- Multiple stateless ECS task replicas are needed to consume the same queue (horizontal scaling)
- Compliance or audit requirements demand durable message log independent of the application
- Multi-region deployment where SQS FIFO or SNS fan-out provides clear simplification

**Implementation specification:**

### 1. SQSBroker Class

```python
class SQSBroker(MessageBroker):
    """AWS SQS implementation of MessageBroker.
    
    Queue URL mapping: one SQS queue per QueueNames constant.
    Visibility timeout replaces the Redis two-list pattern.
    DLQ is configured at the SQS queue level (not in application code).
    """
    
    def __init__(self, queue_urls: dict[str, str], region: str) -> None:
        self._queue_urls = queue_urls   # {"inbound_messages": "https://sqs..."}
        self._client = boto3.client("sqs", region_name=region)
    
    async def publish(self, queue: str, message: str) -> None:
        await self._client.send_message(
            QueueUrl=self._queue_urls[queue],
            MessageBody=message,
            MessageAttributes={"queue": {"StringValue": queue, "DataType": "String"}},
        )
    
    async def consume(self, queue: str) -> AsyncIterator[str]:
        while True:
            resp = await self._client.receive_message(
                QueueUrl=self._queue_urls[queue],
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,    # long polling — no busy wait
                VisibilityTimeout=300,
            )
            for msg in resp.get("Messages", []):
                yield msg["Body"]
                # Caller must call acknowledge() with receipt_handle to delete
    
    async def acknowledge(self, queue: str, message_id: str) -> None:
        await self._client.delete_message(
            QueueUrl=self._queue_urls[queue],
            ReceiptHandle=message_id,   # SQS uses receipt handle, not message_id
        )
```

### 2. BrokerConfig Factory

Wire `BrokerConfig.backend` to a factory function:

```python
def create_broker(config: BrokerConfig) -> MessageBroker:
    if config.backend == "redis":
        return RedisMessageBroker(url=config.redis_url)
    if config.backend == "sqs":
        return SQSBroker(
            queue_urls=config.sqs_queue_urls,  # new field: dict[str, str]
            region=config.sqs_region,           # new field
        )
    raise ValueError(f"Unknown broker backend: {config.backend!r}")
```

### 3. SQS Infrastructure (Terraform)

Five SQS queues + DLQs for INBOUND and OUTBOUND:

```hcl
resource "aws_sqs_queue" "inbound_messages_dlq" { ... }
resource "aws_sqs_queue" "inbound_messages" {
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.inbound_messages_dlq.arn
    maxReceiveCount     = 3
  })
  message_retention_seconds = 345600   # 4 days (PRD §29.5)
}
# Repeat for outbound_messages (24h retention), trigger_events, skill_jobs, status_updates
```

**SNS alerting (separate from application queues):**

Per original PRD design, operational alerts use SNS topics — not SQS:
```hcl
resource "aws_sns_topic" "workgraph_p1_alerts" { name = "workgraph-p1-alerts" }
resource "aws_sns_topic" "workgraph_p2_alerts" { name = "workgraph-p2-alerts" }
# Subscriptions: PagerDuty endpoint (P1), Slack webhook (P2)
```

**Files to change:**
- `src/graphclaw/infra/broker.py` — `SQSBroker` class
- `src/graphclaw/infra/config.py` — `BrokerConfig` add `sqs_queue_urls`, `sqs_region`; add `create_broker()` factory
- `src/graphclaw/gateway/deps.py` — use factory instead of direct `RedisMessageBroker()`
- `infra/terraform/sqs.tf` — new Terraform resource file
- `infra/terraform/sns.tf` — alerting topics

**Dependencies:** F-1 should be implemented before F-2. If going straight to SQS, F-1 is not needed (SQS provides equivalent guarantees natively).

---

## F-3: `STATUS_UPDATES` Consumer — State Machine Wiring

**Original design intent:** The inbound processor publishes state signals to the `STATUS_UPDATES` queue. A separate consumer loop picks these up and drives `StateMachine.transition()` to update task state in the graph.

**Current state:**
- `InboundProcessor` publishes to `STATUS_UPDATES` (implemented)
- No consumer loop reads from `STATUS_UPDATES`
- Task state transitions from inbound channel messages are silently dropped

**Implementation specification:**

### Consumer Loop

```python
# src/graphclaw/inbound/status_consumer.py

class StatusUpdateConsumer:
    """Consumes STATUS_UPDATES queue and drives StateMachine transitions."""

    def __init__(
        self,
        broker: MessageBroker,
        graph_store: GraphStore,
        state_machine: StateMachine,
        logger: AsyncLogger,
    ) -> None: ...

    async def run(self) -> None:
        async for raw in self._broker.consume(QueueNames.STATUS_UPDATES):
            update = StatusUpdate.model_validate_json(raw)
            await self._process(update)

    async def _process(self, update: StatusUpdate) -> None:
        task = await self._graph_store.get_node(update.task_id)
        valid = await self._state_machine.valid_transitions(task.state)
        if update.new_state not in valid:
            await self._logger.warning("invalid_transition", ...)
            return
        await self._state_machine.transition(
            task_id=update.task_id,
            new_state=update.new_state,
            session_id=update.session_id,
        )
```

### Integration

Start `StatusUpdateConsumer` in `gateway/app.py` alongside the existing email poller and outbound sender tasks.

**StatusUpdate payload schema** (already published by `InboundProcessor`):
```json
{
  "task_id": "TSK-USER-001-ATM",
  "new_state": "IN_PROGRESS",
  "signal": "PROGRESS_UPDATE",
  "confidence": "HIGH",
  "session_id": "SES-uuid4"
}
```

**Files to change:**
- `src/graphclaw/inbound/status_consumer.py` — new consumer class
- `src/graphclaw/gateway/app.py` — start `StatusUpdateConsumer` as background task
- `tests/test_inbound/test_status_consumer.py` — new test file

**Dependencies:** None beyond what is currently implemented. GraphStore and StateMachine are both ready.

---

## F-4: `SKILL_JOBS` Async Consumer

**Original design intent:** Long-running skill executions are enqueued to `SKILL_JOBS` so the HTTP endpoint returns immediately (202) and the skill worker processes asynchronously. The current implementation runs skills synchronously in the request handler or via the worker pool directly.

**Current state:**
- `QueueNames.SKILL_JOBS` is defined in `broker.py`
- No producer publishes to `SKILL_JOBS`
- No consumer reads from `SKILL_JOBS`
- Skill execution is synchronous or bound to the worker pool lifecycle

**Implementation specification:**

### Producer

When `POST /app/v1/skills/{id}/test` is called (or when the agent loop triggers a skill):

```python
job = SkillJob(
    job_id=f"JOB-{uuid4().hex[:8]}",
    skill_id=skill_id,
    task_id=task_id,
    session_id=session_id,
    input_data=input_data,
    submitted_at=utcnow(),
)
await broker.publish(QueueNames.SKILL_JOBS, job.model_dump_json())
```

### Consumer

```python
# src/graphclaw/skills/job_consumer.py

class SkillJobConsumer:
    async def run(self) -> None:
        async for raw in self._broker.consume(QueueNames.SKILL_JOBS):
            job = SkillJob.model_validate_json(raw)
            await self._worker_pool.submit(job)
```

### Status Callback

On job completion, the worker writes the result to `{user_id}/skills/executions/{skill_id}.json` (via `StoragePaths.skill_executions`) and publishes a status update to Redis pub/sub for SSE delivery to the cockpit.

**Files to change:**
- `src/graphclaw/skills/job_consumer.py` — new consumer
- `src/graphclaw/skills/models.py` — `SkillJob` Pydantic model
- `src/graphclaw/api/skill_registry.py` — `POST /skills/{id}/test` publishes to queue instead of direct worker call
- `src/graphclaw/gateway/app.py` — start `SkillJobConsumer` as background task

**Dependencies:** F-3 recommended first. Worker pool must be running.

---

## F-5: Approval Escalation & Timeout

**Original design intent (review notes §5):** APPROVAL tasks that are not actioned within a configurable timeout window should escalate — either to a different approver, or to an automated default decision with a notification.

**Current state:**
- `APPROVAL` task type and state machine transitions are implemented
- No timeout tracking on APPROVAL tasks
- No escalation path, no escalation fields on the node
- The state machine has no "stale approval" trigger

**Implementation specification:**

### Schema Changes

Add escalation fields to `TaskNode` metadata for APPROVAL tasks:

```python
class ApprovalMetadata(BaseModel):
    approver_user_id: str
    escalation_user_id: str | None = None
    timeout_hours: int = 48
    escalation_timeout_hours: int = 24
    auto_decision: str | None = None  # "APPROVE" | "DENY" | None (block indefinitely)
    requested_at: datetime
    escalated_at: datetime | None = None
    decision_at: datetime | None = None
```

### Escalation Engine

A scheduled task (runs every 15 minutes via `TriggerEngine`) scans all APPROVAL nodes:

```python
async def check_approval_timeouts(graph_store: GraphStore) -> None:
    pending = await graph_store.list_nodes(type="TASK", state="WAITING_APPROVAL")
    for node in pending:
        meta = ApprovalMetadata.model_validate(node.metadata)
        age = utcnow() - meta.requested_at
        
        if meta.escalated_at is None and age > timedelta(hours=meta.timeout_hours):
            await _escalate(node, meta, graph_store)
        
        elif meta.escalated_at and age > timedelta(
            hours=meta.timeout_hours + meta.escalation_timeout_hours
        ):
            await _auto_decide(node, meta, graph_store)
```

**Files to change:**
- `src/graphclaw/models/task.py` — `ApprovalMetadata` model
- `src/graphclaw/triggers/engine.py` — register `check_approval_timeouts` as a scheduled trigger
- `src/graphclaw/api/approvals.py` — include escalation metadata in POST body
- `tests/test_triggers/test_approval_escalation.py` — new test file

**Dependencies:** TriggerEngine is implemented and ready. StateMachine transitions are ready.

---

## F-6: Recurring Task Spawn Trigger

**Original design intent (PRD §4.3.1):** Tasks with type `RECURRING` should automatically spawn a new instance of themselves when they reach a terminal state (`COMPLETED` or `CANCELLED`). The spawn schedule is defined in the task's trigger configuration.

**Current state:**
- `RECURRING` task type exists in the type enum
- No spawn logic exists
- No trigger registration for RECURRING tasks
- TriggerEngine does not watch for terminal-state RECURRING tasks

**Implementation specification:**

### State Machine Hook

When `StateMachine.transition()` moves a RECURRING task to `COMPLETED`:

```python
if task.type == "RECURRING" and new_state in {"COMPLETED", "CANCELLED"}:
    await _spawn_next_occurrence(task, graph_store, broker)
```

### Spawn Logic

```python
async def _spawn_next_occurrence(
    parent: TaskNode,
    graph_store: GraphStore,
    broker: MessageBroker,
) -> None:
    recurrence = RecurrenceConfig.model_validate(parent.metadata.get("recurrence"))
    next_run = recurrence.next_after(parent.completed_at)
    
    child = TaskNode(
        task_id=generate_task_id(parent.user_id),
        parent_task_id=parent.task_id,
        type="RECURRING",
        title=parent.title,
        metadata={**parent.metadata, "scheduled_for": next_run.isoformat()},
    )
    await graph_store.upsert_node(child)
    
    await broker.publish(QueueNames.TRIGGER_EVENTS, TriggerEvent(
        trigger_type="SCHEDULED",
        task_id=child.task_id,
        fire_at=next_run,
    ).model_dump_json())
```

**Files to change:**
- `src/graphclaw/models/task.py` — `RecurrenceConfig` model (`cron_expression`, `interval_hours`, `max_occurrences`)
- `src/graphclaw/engine/state_machine.py` — hook in `_spawn_next_occurrence`
- `src/graphclaw/triggers/recurrence.py` — new `RecurrenceConfig.next_after()` implementation

**Dependencies:** TriggerEngine scheduler is ready. StateMachine is ready.

---

## F-7: Embedding Staleness Recompute

**Original design intent (review notes §7):** Task and resource node embeddings are computed at creation time. Over time, as tasks are updated and descriptions change, the embeddings become stale. Stale embeddings cause inaccurate inbound message resolution (the vector search in `InboundProcessor` may match the wrong task).

**Current state:**
- Embeddings are computed once at node creation
- No `embedding_computed_at` field on `TaskNode` or `ResourceNode`
- No staleness threshold check
- No recompute trigger

**Implementation specification:**

### Schema Changes

```python
class TaskNode(BaseModel):
    # ... existing fields ...
    embedding_computed_at: datetime | None = None
    embedding_version: str | None = None    # LLM model ID used to compute
```

### Staleness Check

```python
EMBEDDING_MAX_AGE_DAYS = 30
EMBEDDING_MODEL_CURRENT = "text-embedding-3-small"   # or configured

def is_stale(node: TaskNode) -> bool:
    if node.embedding_computed_at is None:
        return True
    if node.embedding_version != EMBEDDING_MODEL_CURRENT:
        return True
    age = utcnow() - node.embedding_computed_at
    return age.days > EMBEDDING_MAX_AGE_DAYS
```

### Recompute Job

A nightly scheduled trigger (via `TriggerEngine`) scans all nodes:

```python
async def recompute_stale_embeddings(graph_store, llm_client) -> None:
    stale = [n for n in await graph_store.list_all_nodes() if is_stale(n)]
    for node in stale:
        embedding = await llm_client.embed(node.title + " " + node.description)
        await graph_store.update_embedding(node.task_id, embedding)
        await graph_store.update_field(node.task_id, {
            "embedding_computed_at": utcnow().isoformat(),
            "embedding_version": EMBEDDING_MODEL_CURRENT,
        })
```

**Files to change:**
- `src/graphclaw/models/task.py` — add `embedding_computed_at`, `embedding_version`
- `src/graphclaw/db/age/store.py` — `update_embedding()` method
- `src/graphclaw/triggers/engine.py` — register nightly staleness recompute
- `src/graphclaw/inbound/resolver.py` — log staleness warning when resolving against old embeddings

**Dependencies:** LLM client embedding support. TriggerEngine scheduler.

---

## F-8: Multi-User Graph Conflict Resolution

**Original design intent (PRD §19):** When multiple agents or users write to the same graph node concurrently, conflicts must be detected and resolved consistently. The current implementation has no locking or version tracking.

**Current state:**
- `GraphStore.upsert_node()` is last-write-wins with no version check
- No `version` field on `TaskNode` or `EdgeNode`
- No conflict detection at the API level
- No resolution strategy

**Implementation specification:**

### Optimistic Locking

Add a `version` integer to every node:

```python
class TaskNode(BaseModel):
    version: int = 1
    # ...
```

`PATCH /app/v1/graph/tasks/{id}` requires `If-Match: {version}` header:

```python
@router.patch("/graph/tasks/{task_id}")
async def update_task(task_id, body, if_match: str = Header(...)):
    expected_version = int(if_match)
    current = await graph_store.get_node(task_id)
    if current.version != expected_version:
        raise HTTPException(409, detail="Conflict: node was modified")
    await graph_store.upsert_node({**body, "version": expected_version + 1})
```

### AGE Store Implementation

In `db/age/store.py`, make `upsert_node()` version-aware:

```cypher
MATCH (t:Task {task_id: $task_id})
WHERE t.version = $expected_version
SET t += $props, t.version = $expected_version + 1
RETURN t
```

Return an error if no rows matched (version mismatch).

### Resolution Strategy

For agent-to-agent conflicts (both agents update the same node without coordination):
- Last write wins for metadata fields (title, description)
- State transitions always go through `StateMachine` (which enforces valid transitions, preventing invalid concurrent state changes)
- Embeddings are merged by picking the most recent

**Files to change:**
- `src/graphclaw/models/task.py` — `version: int = 1`
- `src/graphclaw/db/age/store.py` — version-aware upsert
- `src/graphclaw/api/graph.py` — `If-Match` header handling (this module is not yet built — see cockpit-backend-api-prd.md P1)

**Dependencies:** `api/graph.py` (Priority 1, not yet built) must exist before this can be wired at the API level. DB-level versioning can be implemented independently.

---

## F-9: Skill Quality Feedback Loop

**Original design intent (PRD §18.13):** Each time a skill is used, its quality score is updated via an exponential moving average (EMA). The cockpit displays `avg_quality_score` in the skill list. Users can submit explicit feedback ratings.

**Current state:**
- `SkillEntry` model has a `usage_count` field but no `avg_quality_score`
- `POST /app/v1/skills/{id}/feedback` endpoint exists and calls `record_usage()` on `FakeInstalledSkill` in tests
- The real `SkillRegistryService` does not have `record_usage()` or `avg_quality_score`
- The EMA formula is documented but not implemented in the real registry

**Implementation specification:**

### Schema

Add to `InstalledSkill` (registry models):

```python
class InstalledSkill(BaseModel):
    # ... existing fields ...
    usage_count: int = 0
    avg_quality_score: float = 0.0
    last_used_at: datetime | None = None

    def record_usage(self, quality_score: float | None = None) -> None:
        self.usage_count += 1
        self.last_used_at = utcnow()
        if quality_score is not None:
            # EMA with alpha=0.2 (weight recent scores more)
            self.avg_quality_score = (
                0.2 * quality_score + 0.8 * self.avg_quality_score
            )
```

### SkillRegistryService

Implement `record_usage()` on the real service:

```python
async def record_usage(
    self,
    user_id: str,
    skill_id: str,
    quality_score: float | None = None,
) -> None:
    installed = await self._load_installed(user_id)
    skill = next((s for s in installed if s.skill_id == skill_id), None)
    if skill is None:
        raise KeyError(skill_id)
    skill.record_usage(quality_score)
    await self._save_installed(user_id, installed)
```

**Files to change:**
- `src/graphclaw/skills/registry_models.py` — add `avg_quality_score`, `last_used_at`, `record_usage()`
- `src/graphclaw/skills/registry.py` — implement `record_usage()` on `SkillRegistryService`
- `tests/test_skills/test_registry.py` — add quality score EMA tests

**Dependencies:** None. Can be implemented immediately. Lowest complexity item in this backlog.

---

## Prioritisation Guide

When picking up items from this backlog, use the following sequence:

1. **F-1 first** — broker hardening is a prerequisite for production go-live regardless of scale
2. **F-3 and F-9** — both are low-complexity completions of already-published queue/model patterns
3. **F-5 and F-6** — trigger engine work; pick up together as a single sprint
4. **F-7** — nightly embedding recompute; pick up when inbound resolution accuracy becomes a reported issue
5. **F-4** — async skill jobs; pick up when skill execution latency is reported as blocking the UI
6. **F-8** — conflict resolution; pick up when multi-agent or multi-user concurrent editing becomes a use case
7. **F-2** — SQS; pick up only when the trigger conditions in that section are met
