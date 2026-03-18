---
name: inbound-protocol-patterns
description: >
  Inbound Update Protocol patterns for GraphClaw — task resolution via ID lookup
  and vector embedding search, status signal taxonomy, confidence assessment, follow-up
  child decision tree, and graph cascade propagation. Use when implementing message
  parsing, task matching, embedding search, or update processing. Triggers on:
  "inbound protocol", "task resolution", "embedding match", "status signal",
  "follow-up decision", "update processing".
---

# Inbound Update Protocol (PRD Section 8)

## Node Resolution Pipeline

```
Inbound Message
  │
  ├─ 1. Extract Task ID (regex: TSK-[A-Z]{2}-\d{4,}-[A-Z]{3,})
  │     └─ Found? → Direct lookup in graph → proceed to Step 3
  │
  ├─ 2. Vector Embedding Search (fallback)
  │     └─ Build query: task_description + assigned_to + goal_context
  │     └─ Cosine similarity against node_embeddings table
  │     └─ Threshold: > 0.85 = match, 0.70-0.85 = low confidence, < 0.70 = no match
  │     └─ Low confidence → flag for human confirmation in next briefing
  │
  ├─ 3. Status Signal Extraction
  │     └─ Map to: COMPLETE | IN_PROGRESS | BLOCKED | DELAYED | NEEDS_INPUT
  │     └─ Extract progress percentage if present
  │     └─ Assess confidence: HIGH / MEDIUM / LOW
  │
  ├─ 4. Follow-up Child Evaluation (decision tree below)
  │
  ├─ 5. Graph Cascade Propagation
  │     └─ Composite parent AND/OR gate check
  │     └─ Dependency chain unblocking
  │     └─ Scoring cache invalidation
  │
  └─ 6. Action Decision
        └─ Score > interrupt_threshold → immediate alert
        └─ Otherwise → queue for daily briefing
```

## Status Signal Taxonomy

```python
class StatusSignal(str, Enum):
    COMPLETE = "COMPLETE"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DELAYED = "DELAYED"
    NEEDS_INPUT = "NEEDS_INPUT"
    PROACTIVE_UPDATE = "PROACTIVE_UPDATE"
```

## Confidence Assessment

| Signal | Examples | Confidence |
|--------|----------|------------|
| "Done", "Completed", "Finished" | Explicit completion | HIGH |
| "Will deliver by Friday" | Future commitment | MEDIUM |
| "I think it might be ready" | Uncertain language | LOW |
| "Having issues with..." | Blocker signal | HIGH (for BLOCKED) |

## Follow-up Child Decision Tree

```python
match status_signal:
    case StatusSignal.COMPLETE:
        # Close follow-up, mark resolved_by_proactive = true
        close_followup(followup_id, resolved_by="proactive")

    case StatusSignal.IN_PROGRESS:
        # Evaluate: is progress on track relative to deadline?
        if progress_on_track(task, reported_progress):
            reschedule_followup(task, next_check=adaptive_interval(task))
        else:
            escalate_to_briefing("behind schedule", task)

    case StatusSignal.BLOCKED:
        # Create new follow-up targeting the blocker entity
        create_blocker_followup(task, blocker_info)
        cascade_block_upstream(task)

    case StatusSignal.DELAYED:
        # Reschedule: new_timeline - buffer
        new_deadline = extract_new_timeline(message)
        reschedule_followup(task, next_check=new_deadline - buffer)
        cascade_delay_upstream(task)

    case StatusSignal.NEEDS_INPUT:
        # Surface immediately in briefing as CRITICAL
        queue_critical_briefing_item(task, "needs your input")
```

## Vector Embedding Search

```sql
SELECT ne.node_id, 1 - (ne.embedding <=> query_embedding) as similarity
FROM node_embeddings ne
WHERE 1 - (ne.embedding <=> query_embedding) > 0.70
ORDER BY similarity DESC
LIMIT 5;
```

### Embedding Construction
```python
def build_embedding_text(task: TaskNode) -> str:
    parts = [task.title, task.description]
    if task.assigned_to:
        parts.append(f"assigned to {task.assigned_to}")
    if task.goal_context:
        parts.append(f"goal: {task.goal_context}")
    return " ".join(parts)
```
