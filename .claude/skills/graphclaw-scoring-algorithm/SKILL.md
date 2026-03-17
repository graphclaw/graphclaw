---
name: graphclaw-scoring-algorithm
description: Complete 7-factor scoring algorithm, chain topology modifiers, and cache invalidation rules from PRD Section 9. Use when implementing, testing, or reviewing the priority scoring engine.
---

# GraphClaw 7-Factor Scoring Algorithm

## Master Formula

```
Priority Score =
  (Timeline Urgency    * W1=0.25) +
  (Dependency Weight   * W2=0.20) +
  (Critical Path       * W3=0.20) +
  (Blocker Score       * W4=0.15) +
  (Human Override      * W5=0.10) +
  (Resource Risk       * W6=0.05) +
  (Constraint Pressure * W7=0.05)
```

Weights stored on `UserNode.scoring_weights`. Phase 0 uses hardcoded defaults.

## Factor 1: Timeline Urgency (W1=0.25)

```python
def timeline_urgency(days_remaining: float, estimated_effort_days: float) -> float:
    # Base urgency from days remaining
    if days_remaining > 14:       base = 0.2
    elif days_remaining > 7:      base = 0.4
    elif days_remaining > 3:      base = 0.6
    elif days_remaining > 1:      base = 0.85
    elif days_remaining > 0:      base = 1.0
    else:                         base = 1.2  # overdue

    # Effort slack adjustment
    slack = days_remaining - estimated_effort_days
    if slack < 0:    base += 0.30
    elif slack < 1:  base += 0.15

    return base
```

## Factor 2: Dependency Weight (W2=0.20)

```python
def dependency_weight(direct_dependents: int, transitive_dependents: int) -> float:
    return direct_dependents + (transitive_dependents * 0.5)
```
Traverse all downstream DEPENDS_ON edges recursively. Higher breadth at fork points scores higher.

## Factor 3: Critical Path (W3=0.20)

```python
def critical_path_score(on_critical_path: bool, goal_priority: str) -> float:
    if not on_critical_path:
        return 0.0

    multiplier = {"P1": 1.5, "P2": 1.3, "P3": 1.1}.get(goal_priority, 1.0)
    return 1.0 * multiplier
```

**Critical path computation:** Modified Dijkstra on DAG.
1. From Goal Node, traverse DEPENDS_ON & PART_OF edges downstream (BFS)
2. For each leaf, walk back summing estimated_effort
3. Longest path = critical path
4. Nodes on CP: score=1.0, float=0. Off CP: float = cp_length - this_path_length

## Factor 4: Blocker Score (W4=0.15)

```python
def blocker_score(blocker_type: str) -> float:
    return {"HARD": 1.0, "SOFT": 0.6, "NONE": 0.0}.get(blocker_type, 0.0)
```

If node.state == BLOCKED:
- This node's score is **suppressed** (excluded from action queue)
- Its blocker's score is **elevated**
- Agent builds root-cause chain for briefing

## Factor 5: Human Override (W5=0.10)

```python
OVERRIDE_VALUES = {
    "PRIORITY":    +1.0,   # "Make this a priority"
    "TOP":         +1.0,   # "Most important thing right now" + re-rank flag
    "WATCH":       +0.5,   # "Keep an eye on this"
    "WAIT":        -0.3,   # "This can wait"
    "SNOOZED":     None,   # Excluded from scoring entirely
}
```

Overrides **inject** into formula (don't replace). Agent surfaces tensions between overrides and objective urgency.

## Factor 6: Resource Risk (W6=0.05)

```python
def resource_risk(reliability: float, load_factor: float, risk_signals: float) -> float:
    return (1 - reliability) * 0.5 + load_factor * 0.3 + risk_signals * 0.2
```

## Factor 7: Constraint Pressure (W7=0.05)

```python
def constraint_pressure(constraints: list[dict]) -> float:
    total = 0.0
    for c in constraints:
        pressure = (c["threshold"] - c["current_value"]) / c["threshold"]
        total += max(0.0, min(1.0, pressure))
    return total
```

## Chain Topology Modifiers

### Sequential Chain
- Only first actionable node is surfaced
- Downstream urgency rolls UP: `chain_urgency_rollup = max(downstream urgency scores)`
- First node gets this multiplier applied

### Parallel Chain
- All chains simultaneously actionable
- No suppression, each scores independently
- Confluence (Milestone) activates when all complete (AND gate)

### Critical Path Multiplier
- Applied AFTER all 7 factors computed
- If on CP for P1 goal: multiply final score by 1.5x

## Score Caching

Cache invalidation triggers:
- Node state changes
- Deadline crosses urgency threshold bracket
- Dependent node state changes (invalidates upstream)
- Human override applied/removed
- Resource risk signal changes on assigned resource
- Constraint pressure score changes

Forced full rescore:
- Once per day pre-briefing
- On explicit user request
- When new Goal Node added

## ActionQueueEntry

```python
class ActionQueueEntry(BaseModel):
    node_id: str
    final_score: float
    rank: int
    recommended_action: str      # e.g., "SEND_FOLLOWUP", "ESCALATE", "BRIEF_HUMAN"
    autonomy_level: str          # "AUTONOMOUS" | "SUGGEST" | "REQUIRE_APPROVAL"
    explanation: ScoreExplanation
    batched_with: list[str] = [] # Resource batching
```
