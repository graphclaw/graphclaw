# 04 — Graph Database Schema

GraphClaw stores its entire domain in a **property graph** backed by PostgreSQL 18 +
Apache AGE.  Every entity is a labelled vertex; relationships are directed, labelled edges.
All properties are stored as `agtype` (AGE's JSON superset).

---

## Node Types (Vertex Labels)

| Label | Description | Key Properties |
|-------|-------------|----------------|
| `TaskAtomic` | Single indivisible unit of work | id, title, state, scoring, timeline, autonomy |
| `TaskComposite` | Parent task with sub-tasks | id, title, state, breakdown_strategy, gate_type |
| `TaskDelegated` | Task owned by an external resource | id, title, state, assigned_to |
| `TaskMilestone` | Key deliverable checkpoint | id, title, state, timeline.deadline |
| `TaskApproval` | Requires explicit human sign-off | id, title, state, requires_approval_from |
| `TaskRecurring` | Repeating task with schedule | id, title, state, recurrence_rule |
| `GoalNode` | High-level objective | id, title, priority (P1/P2/P3), state, success_criteria |
| `UserNode` | Platform user | id, email, name, role, working_hours |
| `ResourceNode` | Human or AI agent resource | id, name, resource_type, capacity, availability_status |
| `ConstraintNode` | Budget / deadline / compliance constraint | id, constraint_type, threshold, current_value, risk_level |
| `CheckinNode` | Asynchronous status check-in message | id, task_id, resource_id, state, scheduled_at |
| `OrganizationNode` | Tenant / company | id, name, domain, owner_id, members |
| `WorkspaceNode` | Project workspace within an org | id, name, org_id, visibility |
| `VisibilityGrantNode` | Fine-grained access grant record | id, grantor_id, grantee_id, scope |
| `MCPServerNode` | Registered MCP tool server | id, name, command, transport, trust_tier, enabled |

---

## Edge Types (Edge Labels)

```mermaid
erDiagram
    TaskNode ||--o{ TaskNode : "DEPENDS_ON"
    TaskNode ||--o{ TaskNode : "BLOCKS"
    TaskNode ||--o{ TaskNode : "SPAWNED_FROM"
    TaskNode ||--o{ TaskNode : "FOLLOW_UP_FOR"
    TaskNode ||--o{ TaskNode : "PART_OF"
    TaskNode ||--o{ TaskNode : "BRANCHED_FROM"
    TaskNode ||--o{ TaskNode : "BATCHED_IN"
    TaskNode }|--|| GoalNode : "PART_OF"
    TaskNode }|--|| ResourceNode : "ASSIGNED_TO"
    TaskNode }|--|| UserNode : "OWNED_BY"
    TaskNode }|--|| WorkspaceNode : "SCOPED_TO_WS"
    GoalNode }|--|| WorkspaceNode : "SCOPED_TO_WS"
    ConstraintNode }|--o{ TaskNode : "APPLIES_TO"
    ConstraintNode }|--o{ GoalNode : "APPLIES_TO"
    CheckinNode }|--|| TaskNode : "INFORMS"
    UserNode }|--|| OrganizationNode : "MEMBER_OF"
    UserNode }|--|| OrganizationNode : "ADMIN_OF"
    WorkspaceNode }|--|| OrganizationNode : "BELONGS_TO_ORG"
    VisibilityGrantNode }|--o{ TaskNode : "GRANTS_ACCESS_TO"
    UserNode }|--o{ MCPServerNode : "GRANTS_ACCESS_TO_MCP"
```

---

## Full Entity Relationship (Mermaid Graph)

```mermaid
graph LR
    subgraph Org["Organisation Layer"]
        ORG["OrganizationNode\n(id, name, domain, owner_id)"]
        WS["WorkspaceNode\n(id, name, visibility)"]
        USER["UserNode\n(id, email, role)"]
    end

    subgraph Work["Work Layer"]
        TASK["TaskNode\n(id, title, state, scoring\ntimeline, autonomy)"]
        GOAL["GoalNode\n(id, title, priority, state)"]
        CHECKIN["CheckinNode\n(task_id, state, scheduled_at)"]
    end

    subgraph Resources["Resource Layer"]
        RES["ResourceNode\n(name, type, capacity)"]
        CONSTRAINT["ConstraintNode\n(type, threshold, risk_level)"]
    end

    subgraph Access["Access Control"]
        VG["VisibilityGrantNode\n(grantor, grantee, scope)"]
        MCP["MCPServerNode\n(name, command, trust_tier)"]
    end

    USER -->|MEMBER_OF| ORG
    USER -->|ADMIN_OF| ORG
    WS -->|BELONGS_TO_ORG| ORG
    TASK -->|SCOPED_TO_WS| WS
    GOAL -->|SCOPED_TO_WS| WS

    TASK -->|PART_OF| GOAL
    TASK -->|DEPENDS_ON| TASK
    TASK -->|BLOCKS| TASK
    TASK -->|SPAWNED_FROM| TASK
    TASK -->|ASSIGNED_TO| RES
    TASK -->|OWNED_BY| USER

    CONSTRAINT -->|APPLIES_TO| TASK
    CONSTRAINT -->|APPLIES_TO| GOAL
    CHECKIN -->|INFORMS| TASK

    VG -->|GRANTS_ACCESS_TO| TASK
    VG -->|GRANTS_ACCESS_TO| GOAL
    USER -->|GRANTS_ACCESS_TO_MCP| MCP
```

---

## Task State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> ACTIVE : activate
    PENDING --> CANCELLED : cancel
    PENDING --> INACTIVE_PENDING : defer

    ACTIVE --> IN_PROGRESS : start_work
    ACTIVE --> BLOCKED : block
    ACTIVE --> CANCELLED : cancel
    ACTIVE --> SNOOZED : snooze

    IN_PROGRESS --> NEEDS_REVIEW : submit_for_review
    IN_PROGRESS --> BLOCKED : block
    IN_PROGRESS --> DELAYED : flag_delayed
    IN_PROGRESS --> COMPLETE : complete
    IN_PROGRESS --> CANCELLED : cancel

    NEEDS_REVIEW --> IN_PROGRESS : request_changes
    NEEDS_REVIEW --> COMPLETE : approve
    NEEDS_REVIEW --> CANCELLED : reject

    BLOCKED --> ACTIVE : unblock
    BLOCKED --> CANCELLED : cancel

    DELAYED --> IN_PROGRESS : resume
    DELAYED --> CANCELLED : cancel

    SNOOZED --> ACTIVE : wake
    SNOOZED --> CANCELLED : cancel

    INACTIVE_PENDING --> PENDING : reactivate

    COMPLETE --> [*]
    CANCELLED --> [*]
```

---

## Scoring Model (W1–W7)

```mermaid
graph TD
    subgraph Inputs["Scoring Inputs"]
        T["Timeline\n(deadline proximity)"]
        D["Dependency graph\n(blocked count)"]
        CP["Critical path\n(on/off)"]
        BL["Blocker status\n(HARD/SOFT/none)"]
        HO["Human override\n(PRIORITIZE/SNOOZE)"]
        RR["Resource risk\n(capacity/availability)"]
        CS["Constraint pressure\n(threshold proximity)"]
    end

    W1["W1 × timeline_urgency\n0.0 – 1.2"]
    W2["W2 × dependency_weight\n0.0 – 1.0"]
    W3["W3 × critical_path\n0.0 or 1.0"]
    W4["W4 × blocker\n0.0 / 0.6 / 1.0"]
    W5["W5 × human_override\n−0.3 to +1.0"]
    W6["W6 × resource_risk\n0.0 – 1.0"]
    W7["W7 × constraint_pressure\n0.0 – 1.0"]

    SCORE["computed_priority\n= Σ(Wi × fi)"]

    T --> W1 --> SCORE
    D --> W2 --> SCORE
    CP --> W3 --> SCORE
    BL --> W4 --> SCORE
    HO --> W5 --> SCORE
    RR --> W6 --> SCORE
    CS --> W7 --> SCORE
```

---

## Key Cypher Query Patterns

```sql
-- All tasks assigned to a resource
MATCH (t:TaskNode)-[:ASSIGNED_TO]->(r:ResourceNode {id: $resource_id})
RETURN t;

-- Critical path for a goal
MATCH path = (g:GoalNode {id: $goal_id})<-[:PART_OF*1..10]-(t:TaskNode)
WHERE t.on_critical_path = true
RETURN nodes(path), relationships(path);

-- Blocking chain (what does this task block transitively)
MATCH path = (t:TaskNode {id: $task_id})-[:BLOCKS*1..5]->(blocked)
RETURN path;

-- User's MCP servers
MATCH (u:UserNode {id: $user_id})-[:GRANTS_ACCESS_TO_MCP]->(m:MCPServerNode)
WHERE m.enabled = true
RETURN m;

-- Tasks in workspace with state filter
MATCH (t:TaskNode)-[:SCOPED_TO_WS]->(w:WorkspaceNode {id: $ws_id})
WHERE t.state IN ['PENDING', 'IN_PROGRESS', 'BLOCKED']
RETURN t ORDER BY t.scoring.computed_priority DESC;
```
