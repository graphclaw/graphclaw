# Task Graph Management System — Product Requirements Document

**Version:** 1.3 — Implementation in Progress  
**Status:** Active build — Phases 0–4 complete, Phase 5 delivered  
**Purpose:** Comprehensive reference document for UX, architecture, and implementation discussions

**Implementation status (2026-04-13):**
- Phases 0–3 delivered: graph schema, scoring engine, state machine, CLI, gateway, channels, auth, BYOK, compliance, MCP, connectors, skill registry, A2A
- Phase 4 complete: cockpit backend API all 6 waves delivered (graph, scoring, state, events, sessions, skills, MCP, intelligence)
- **Phase 5 delivered (2026-04-13):** Sub-agent parallel orchestration — `SubAgentRunner`, `SubAgentPool`, `AgentDispatchPlanner`, `AgentHealthMonitor`, broker integration
- Cockpit frontend: separate project at `graphclaw-cockpit/` (HTML wireframes + React target)
- API backlog: `docs/cockpit-backend-api-prd.md` (104 new endpoints, 6-wave build plan)
- Test baseline: 1575 passing unit tests, 15 DB integration tests (require live Postgres+AGE)

---

## Table of Contents

1. [Product Vision](#1-product-vision)
2. [Core Concepts](#2-core-concepts)
3. [Node Taxonomy](#3-node-taxonomy)
4. [Full Node Schemas](#4-full-node-schemas)
5. [Edge Taxonomy & Schema](#5-edge-taxonomy--schema)
6. [Graph Structure & Tree Examples](#6-graph-structure--tree-examples)
7. [Task Lifecycle & State Machine](#7-task-lifecycle--state-machine)
8. [Inbound Update Protocol](#8-inbound-update-protocol)
9. [Agent Reasoning Algorithm](#9-agent-reasoning-algorithm)
10. [Follow-up Timing Model](#10-follow-up-timing-model)
11. [Goal & Constraint Node Creation](#11-goal--constraint-node-creation)
12. [Daily Briefing](#12-daily-briefing)
13. [Explainability](#13-explainability)
14. [Autonomy Permission Model](#14-autonomy-permission-model)
15. [Communication Channels](#15-communication-channels)
16. [Organization Workspaces](#16-organization-workspaces)
17. [Alias System](#17-alias-system)
18. [Skill Agent System](#18-skill-agent-system)
19. [Multi-User Graph](#19-multi-user-graph)
20. [Agent-to-Agent Protocol](#20-agent-to-agent-protocol)
21. [Graph Storage & Query Patterns](#21-graph-storage--query-patterns)
22. [Onboarding & Network Growth](#22-onboarding--network-growth)
23. [UX & Interface Design](#23-ux--interface-design)
24. [Real-World Scenario Validation](#24-real-world-scenario-validation)
25. [Design Gap Resolutions](#25-design-gap-resolutions)
26. [Architecture: Orchestrating Agent](#26-architecture-orchestrating-agent)
27. [Architecture: Graph Database](#27-architecture-graph-database)
28. [Architecture: Multi-Tenant Runtime](#28-architecture-multi-tenant-runtime)
29. [Architecture: Channel Integration Layer](#29-architecture-channel-integration-layer)
30. [Architecture: Skill Agent Runtime](#30-architecture-skill-agent-runtime)
31. [Architecture: Security, Identity & Secrets](#31-architecture-security-identity--secrets)
32. [Architecture: Observability & Operations](#32-architecture-observability--operations)
33. [Design Principles](#33-design-principles)
34. [Architecture: MCP Server Integration](#34-architecture-mcp-server-integration)
35. [Architecture: Application API Layer (Cockpit Backend)](#35-architecture-application-api-layer-cockpit-backend)
36. [Architecture: Node Intelligence Layer](#36-architecture-node-intelligence-layer)
37. [Architecture: Embedding Pipeline](#37-architecture-embedding-pipeline)
38. [Architecture: Sub-Agent Parallel Orchestration](#38-architecture-sub-agent-parallel-orchestration)

---

## 1. Product Vision

A task management system built on a **property graph** data model, orchestrated by an AI agent that actively manages work on behalf of a human user. The system is not a passive tracker — the orchestrating agent reasons about the graph, identifies dependencies and bottlenecks, communicates with humans and AI agents, and continuously learns from the user's working style.

The core insight driving the design: work in reality is a graph, not a list. Tasks have dependencies, owners, delegation chains, follow-up requirements, and goal context. Modeling this as a graph allows the agent to reason about the whole, not just individual items.

---

## 2. Core Concepts

### 2.1 The Task Graph

Every unit of work is a **node**. Relationships between units of work are **edges**. The orchestrating agent traverses, scores, and acts on this graph continuously. The graph database is a **property graph model** — both nodes and edges carry rich properties, not just connectivity. Suitable implementations include Neo4j, Amazon Neptune, or Apache AGE over Postgres.

### 2.2 The Orchestrating Agent

A single AI agent that:
- Owns the graph on behalf of the human user
- Breaks down tasks into structured subgraphs
- Delegates work to human users and AI agents
- Monitors progress through follow-ups and inbound updates
- Batches outreach to avoid pinging the same person multiple times
- Surfaces prioritized, explainable briefings to the human
- Learns from the human's decisions over time

### 2.3 The Human User

The human is the ultimate authority. The agent works *for* the human, not instead of them. The human sets autonomy permissions, reviews inferences, and can always override the agent's decisions. The agent always explains its reasoning when asked.

### 2.4 Four Trigger Mechanisms

The agent reasoning loop is activated by four trigger types:

| Trigger | Description |
|---------|-------------|
| **Time-based** | Scheduled triggers — daily briefing window, follow-up cadence timers |
| **Event-based** | A node changes state, triggering re-evaluation of its neighbors |
| **Inbound update** | An external update arrives from a human or AI agent and is processed |
| **On-demand** | Human asks the agent directly at any time |

---

## 3. Node Taxonomy

### 3.1 Task Nodes

| Node Type | Description | Key Behavior |
|-----------|-------------|--------------|
| **Atomic Task** | Smallest unit of work, no further breakdown | Leaf node, manually completed |
| **Composite Task** | Container for subtasks | Completion derived from children, not manually set |
| **Delegated Task** | Assigned to a human or AI agent | Always spawns a Follow-up child on creation |
| **Follow-up Task** | Monitors a Delegated Task | Auto-resolves on proactive update; rescheduled or closed based on inbound signals |
| **Milestone Task** | Significant checkpoint in a project | Triggers stakeholder notifications on completion |
| **Approval Task** | Hard-blocking human review | Cannot be auto-resolved by agent |
| **Review Task** | Soft review, feedback required | May or may not block downstream work |
| **Decision Task** | Output is a choice, not a deliverable | Activates/prunes downstream branches on resolution |
| **Research Task** | Open-ended information gathering | Has "sufficient to proceed" threshold, not binary done/not-done |
| **Recurring Task** | Regenerates on schedule or trigger | Parent node persists; instances spawned from it |
| **Blocked Task** | State any task node can enter | Agent deprioritizes and elevates the blocker instead |

### 3.2 Coordination Nodes

| Node Type | Description | Key Behavior |
|-----------|-------------|--------------|
| **Check-in Node** | Batched communication artifact | Agent composes one message covering multiple tasks to the same recipient |
| **Handoff Node** | Marks ownership transition | Captures context that travels with the work |
| **Dependency Gate** | Enforces conditions before downstream task activates | AND (all predecessors) or OR (any predecessor) logic |

### 3.3 Context Nodes

| Node Type | Description | Key Behavior |
|-----------|-------------|--------------|
| **Goal Node** | The "why" behind a cluster of tasks | Not directly completable; provides intent for agent reasoning |
| **Constraint Node** | A rule or limit applying to tasks below it | Deadline, budget, compliance, external dependency |
| **Resource Node** | A person, AI agent, or asset tasks are assigned to | Agent uses to reason about capacity and reliability |
| **Context/Note Node** | Freeform information attached to a task cluster | Agent uses when composing outreach or briefing handoffs |

### 3.4 User Node

A behavioral model the agent continuously refines. Not just a profile — a living model of how this human works, including static preferences, learned scoring weights, inferred behavioral patterns, and per-resource relationship models.

---

## 4. Full Node Schemas

### 4.1 User Node

```
UserNode {
  // Identity
  id:                     USER-[uuid]
  name:                   string
  email:                  string
  role:                   string
  timezone:               string
  working_hours:          { start: time, end: time }

  // Static Preferences (explicitly set by user)
  preferences: {
    briefing_time:              time
    briefing_style:             "concise" | "detailed"
    default_follow_up_days:     number
    interrupt_threshold:        float     // urgency score that justifies mid-day alert
    autonomy_defaults: {
      auto_update_ai_agents:    boolean
      auto_send_followups:      boolean
      auto_close_resolved:      boolean
    }
  }

  // Scoring Weights (learned over time, initialized to defaults)
  scoring_weights: {
    W1_timeline:      float   // default 0.25
    W2_dependencies:  float   // default 0.20
    W3_critical_path: float   // default 0.20
    W4_blocker:       float   // default 0.15
    W5_override:      float   // default 0.10
    W6_resource_risk: float   // default 0.05
    W7_constraint:    float   // default 0.05
    last_updated:     timestamp
    update_count:     integer
  }

  // Inferred Behavioral Patterns (agent builds over time)
  behavioral_model: {
    avg_estimate_accuracy:      float     // how often their effort estimates are right
    preferred_task_batch_size:  integer   // how many tasks they handle comfortably at once
    responsive_hours:           [time_range]
    decision_speed:             "fast" | "deliberate" | "variable"
    override_frequency:         float     // how often they override agent recommendations
  }

  created_at:   timestamp
  updated_at:   timestamp
}
```

---

### 4.2 Resource Node

Represents any entity that can own or work on a task — human or AI agent.

```
ResourceNode {
  // Identity
  id:             RES-[uuid]
  type:           "HUMAN" | "AI_AGENT"
  name:           string
  contact:        string | endpoint_url
  timezone:       string    // for humans

  // Capacity Model
  capacity: {
    max_concurrent_tasks:   integer
    current_active_tasks:   integer
    load_factor:            float     // current_active / max_concurrent
    availability_status:    "AVAILABLE" | "BUSY" | "AT_CAPACITY" | "UNAVAILABLE"
    last_signaled_at:       timestamp
  }

  // Reliability Model (learned from history)
  reliability: {
    overall_score:            float     // 0.0 to 1.0
    on_time_delivery_rate:    float
    proactive_update_rate:    float     // updates without being asked
    response_rate:            float     // responses to follow-ups
    avg_response_time_hrs:    float
    total_tasks_completed:    integer
    total_tasks_delayed:      integer
  }

  // Current Risk Signals (inferred from inbound text, decay over time)
  current_risk: {
    capacity_risk:        "LOW" | "MEDIUM" | "HIGH"
    delivery_risk:        "LOW" | "MEDIUM" | "HIGH"
    responsiveness_risk:  "LOW" | "MEDIUM" | "HIGH"
    risk_signals: [{
      signal:       string      // e.g. "mentioned low bandwidth"
      inferred_at:  timestamp
      source_node:  node_id     // which inbound update triggered this
      expires_at:   timestamp   // risk signals decay over time
    }]
  }

  // Communication Preferences
  communication: {
    preferred_channel:    "email" | "slack" | "api" | "chat"
    batch_messages:       boolean
    batch_window_hours:   integer
  }

  // Vector embedding for inbound update matching
  embedding:      vector[1536]

  created_at:     timestamp
  updated_at:     timestamp
}
```

---

### 4.3 Task Node

The core node type. All task variants share this base schema with a `task_type` discriminator and a `type_metadata` block for type-specific fields.

```
TaskNode {
  // Identity
  id:             TSK-[USER_ID]-[SEQUENCE]-[TYPE_CODE]
  task_type:      "ATOMIC" | "COMPOSITE" | "DELEGATED" | "FOLLOWUP" |
                  "MILESTONE" | "APPROVAL" | "REVIEW" | "DECISION" |
                  "RESEARCH" | "RECURRING" | "CHECKIN"
  title:          string
  description:    string

  // Ownership
  created_by:     user_id
  owned_by:       user_id | resource_id
  assigned_to:    resource_id     // null if not yet assigned

  // State Machine
  state:          "PENDING" | "ACTIVE" | "IN_PROGRESS" | "BLOCKED" |
                  "DELAYED" | "NEEDS_REVIEW" | "COMPLETE" |
                  "CANCELLED" | "SNOOZED" | "INACTIVE_PENDING"

  state_history: [{
    from_state:   string
    to_state:     string
    changed_at:   timestamp
    changed_by:   "AGENT" | "HUMAN" | "INBOUND_UPDATE"
    reason:       string
  }]

  // Timeline
  timeline: {
    created_at:         timestamp
    started_at:         timestamp
    deadline:           timestamp
    estimated_effort:   float     // in days
    actual_effort:      float     // populated on completion
    completed_at:       timestamp
  }

  // Priority & Scoring (all 7 factors stored explicitly)
  scoring: {
    computed_priority:    float
    urgency_score:        float
    dependency_score:     float
    critical_path_score:  float
    blocker_score:        float
    override_score:       float
    resource_risk_score:  float
    constraint_score:     float
    on_critical_path:     boolean
    chain_urgency_rollup: float
    last_scored_at:       timestamp
    score_reasoning:      string    // natural language — see ScoreExplanation
  }

  // Progress
  progress: {
    percentage:           float
    confidence:           "HIGH" | "MEDIUM" | "LOW"
    last_update:          timestamp
    completion_signal:    "EXPLICIT" | "INFERRED" | "CASCADED"
  }

  // Human Override
  override: {
    is_overridden:        boolean
    override_type:        "PRIORITIZE" | "DEPRIORITIZE" | "SNOOZE" | null
    override_note:        string
    set_by:               user_id
    set_at:               timestamp
    expires_at:           timestamp
  }

  // Inbound Update Log
  update_log: [{
    received_at:        timestamp
    source:             resource_id
    raw_text:           string
    parsed_status:      string
    parsed_progress:    float
    matched_by:         "TASK_ID" | "VECTOR_SEARCH"
    match_confidence:   float
    action_taken:       string
  }]

  // Type-specific metadata block (see Section 4.3.1)
  type_metadata:    object

  // Vector Embedding (for inbound update matching)
  embedding:        vector[1536]
  embedding_inputs: {
    title:          string
    description:    string
    goal_context:   string
    key_entities:   [string]
  }

  // Autonomy permissions (per-node overrides of global defaults)
  autonomy: {
    auto_update_allowed:      boolean
    auto_close_allowed:       boolean
    requires_approval_from:   user_id   // null if fully autonomous
  }

  tags:       [string]
  created_at: timestamp
  updated_at: timestamp
}
```

#### 4.3.1 Type-Specific Metadata Blocks

```
// DELEGATED task
type_metadata: {
  expected_deliverable:     string
  outbound_message_sent:    string
  task_id_in_message:       boolean
}

// FOLLOWUP task
type_metadata: {
  parent_delegated_id:      task_id
  scheduled_fire_at:        timestamp
  fire_reason:              string
  resolved_by_proactive:    boolean
  resolution_source:        update_log_id
}

// DECISION task
type_metadata: {
  options:                  [string]
  decision_made:            string
  branches_activated:       [task_id]
  branches_pruned:          [task_id]
}

// RESEARCH task
type_metadata: {
  completion_threshold:     string    // definition of "sufficient to proceed"
  outputs:                  [string]
  confidence_to_proceed:    float
}

// RECURRING task
type_metadata: {
  recurrence_rule:          string    // cron-like expression
  last_spawned_at:          timestamp
  next_spawn_at:            timestamp
  spawn_history:            [task_id]
}

// APPROVAL task
type_metadata: {
  approver:                 user_id
  approval_criteria:        string
  approved_at:              timestamp
  rejection_reason:         string
}

// MILESTONE task
type_metadata: {
  notifies:                 [resource_id]   // stakeholders to notify on completion
  child_task_count:         integer
  child_tasks_complete:     integer
}

// COMPOSITE task
type_metadata: {
  breakdown_strategy:        "SEQUENTIAL" | "PARALLEL" | "HYBRID"
  auto_complete_on_children: boolean
}
```

---

### 4.4 Goal Node

```
GoalNode {
  id:             GOAL-[uuid]
  title:          string
  description:    string
  owner:          user_id

  state:          "ACTIVE" | "COMPLETE" | "OBSOLETE" | "ON_HOLD"

  timeline: {
    target_date:    timestamp
    completed_at:   timestamp
  }

  progress: {
    milestone_count:      integer
    milestones_done:      integer
    derived_percentage:   float     // computed from milestone completions
  }

  priority:             "P1" | "P2" | "P3"    // set by human

  // Origin tracking
  origin:               "USER_DEFINED" | "AGENT_INFERRED"
  inferred_from:        [task_id]              // if agent inferred this goal
  inference_note:       string
  confirmed_by_user:    boolean

  embedding:            vector[1536]
  created_at:           timestamp
  updated_at:           timestamp
}
```

---

### 4.5 Constraint Node

```
ConstraintNode {
  id:             CON-[uuid]
  type:           "DEADLINE" | "BUDGET" | "COMPLIANCE" |
                  "DEPENDENCY" | "CAPACITY" | "CUSTOM"
  title:          string
  description:    string

  // The rule this constraint enforces
  rule: {
    hard_limit:       boolean     // true = cannot be overridden by human
    threshold:        string      // e.g. "$50,000" or "2024-12-31"
    current_value:    string      // current state vs. threshold
    pressure_score:   float       // computed proximity to limit (0.0-1.0)
    breached:         boolean
  }

  scope:              "TASK" | "MILESTONE" | "GOAL" | "GLOBAL"
  applies_to:         [node_id]

  origin:             "USER_DEFINED" | "AGENT_INFERRED"
  confirmed_by_user:  boolean

  created_at:         timestamp
  updated_at:         timestamp
}
```

---

### 4.6 Check-in Node

The batched communication artifact. Not a task itself — a scheduled interaction covering multiple tasks to the same recipient in one outbound message.

```
CheckinNode {
  id:               CHK-[uuid]
  target_resource:  resource_id
  created_by:       "AGENT"

  // All tasks being batched into this check-in
  task_refs:        [task_id]

  state:            "SCHEDULED" | "SENT" | "RESPONDED" | "EXPIRED"

  scheduled_for:          timestamp
  sent_at:                timestamp
  response_received_at:   timestamp

  outbound_message:       string    // agent-composed consolidated message
  inbound_response:       string    // raw response received

  // What the agent did with the response, per task
  resolution: [{
    task_id:        task_id
    action_taken:   string
    new_state:      string
  }]

  created_at:       timestamp
}
```

---

### 4.7 ScoreExplanation Record

Written to the database at every scoring pass. Powers the explainability interface without requiring the agent to reason from scratch at query time.

```
ScoreExplanation {
  node_id:        task_id
  scored_at:      timestamp
  final_score:    float
  rank:           integer     // position in current action queue

  factors: [{
    factor_name:      string
    raw_score:        float
    weight:           float
    weighted_score:   float
    plain_english:    string
    // e.g. "Deadline is in 3 days and estimated effort is 2 days -
    //       very little slack remaining"
  }]

  modifiers: [{
    modifier_type:    string
    multiplier:       float
    plain_english:    string
    // e.g. "This task is on the critical path for the Q3 Launch goal (P1)"
  }]

  summary:        string
  // e.g. "Ranked #1 because it is on the critical path for your highest
  //       priority goal, the deadline is in 3 days, and the assigned
  //       resource has signaled low bandwidth this week."

  topology_note:  string
  // e.g. "This is the first actionable node in a sequential chain of
  //       4 tasks. Moving this forward unblocks the entire chain."
}
```

---

## 5. Edge Taxonomy & Schema

Edges are first-class citizens — they carry semantics, not just connectivity.

### 5.1 Edge Type Reference

| Edge Type | From -> To | Meaning | Key Properties |
|-----------|-----------|---------|----------------|
| `DEPENDS_ON` | Task -> Task | Hard dependency; B cannot start without A | `gate_type`: AND / OR |
| `SPAWNED_FROM` | Task -> Task/Goal | B was created as a breakdown of A | — |
| `FOLLOW_UP_FOR` | FollowUp -> Delegated | B exists to monitor A's status | `scheduled_fire_at` |
| `BLOCKS` | Task -> Task | A is actively blocking B | `strength`: HARD / SOFT |
| `ASSIGNED_TO` | Task -> Resource | Task assigned to a resource | — |
| `OWNED_BY` | Task/Goal -> User | Node owned by a user | — |
| `APPLIES_TO` | Constraint -> Task/Goal | Constraint governs this node | — |
| `PART_OF` | Task -> Goal/Milestone | Task is part of a larger goal | `sequence_order` |
| `INFORMS` | Context -> Task | Context node enriches a task node | — |
| `BRANCHED_FROM` | Task -> Decision | Decision node created this branch | — |
| `BATCHED_IN` | Task -> Checkin | Task included in a check-in | — |

### 5.2 Edge Schema

```
Edge {
  id:           EDGE-[uuid]
  from_node:    node_id
  to_node:      node_id
  edge_type:    string    // one of the types above

  properties: {
    gate_type:        "AND" | "OR"    // for DEPENDS_ON edges at a fork
    sequence_order:   integer         // for sequential chains
    strength:         "HARD" | "SOFT"
    created_at:       timestamp
    created_by:       "HUMAN" | "AGENT"
    note:             string
  }
}
```

---

## 6. Graph Structure & Tree Examples

### 6.1 Canonical Graph Structure

```
UserNode
  |
  |-- OWNS --> GoalNode (P1: "Launch feature X by Q3")
  |              |
  |              |-- PART_OF <-- ConstraintNode ("Must comply with data privacy policy")
  |              |
  |              |-- PART_OF <-- TaskNode [Milestone: "API complete and reviewed"]
  |              |                 |
  |              |                 |-- SPAWNED_FROM --> TaskNode [Composite: "Build backend API"]
  |              |                 |                     |
  |              |                 |                     |-- SPAWNED_FROM --> TaskNode [Delegated -> AI Agent A]
  |              |                 |                     |                     |
  |              |                 |                     |                     `-- FOLLOW_UP_FOR --> TaskNode [FollowUp]
  |              |                 |                     |
  |              |                 |                     |-- SPAWNED_FROM --> TaskNode [Approval -> Tech Lead]
  |              |                 |                     |                     |
  |              |                 |                     |                     `-- BLOCKS --> [Frontend work]
  |              |                 |                     |
  |              |                 |                     `-- SPAWNED_FROM --> TaskNode [Atomic: "Write unit tests"]
  |              |                 |
  |              |                 `-- SPAWNED_FROM --> TaskNode [Delegated -> Human User B]
  |              |                                       |
  |              |                                       `-- FOLLOW_UP_FOR --> TaskNode [FollowUp]
  |              |                                             |
  |              |                                             `-- BATCHED_IN --> CheckinNode --> ResourceNode [Human User B]
  |              |
  |              `-- PART_OF <-- TaskNode [Milestone: "Frontend complete"]
  |                                `-- ... (similar structure)
  |
  `-- IS_A --> ResourceNode (the user themselves as a resource)
                 |
                 `-- ASSIGNED_TO <-- [all tasks currently assigned to this user]
```

---

### 6.2 Delegated Task with Follow-up Chain

```
TaskNode [Delegated: "Build auth module"]
  id: TSK-JD-4821-DEL
  assigned_to: RES-agent-a
  state: IN_PROGRESS
  |
  `-- FOLLOW_UP_FOR --> TaskNode [FollowUp: "Check on auth module"]
                          id: TSK-JD-4822-FLW
                          state: SCHEDULED
                          type_metadata.scheduled_fire_at: [48hrs from now]
```

When AI Agent A sends a proactive update before the follow-up fires:

```
Inbound: "TSK-JD-4821-DEL - Auth module is 80% done, on track for Friday"
  |
  |-- Matched by: TASK_ID (direct lookup)
  |-- Parsed status: IN_PROGRESS
  |-- Parsed progress: 80%
  |-- Confidence signal: "on track" -> delivery_risk stays LOW
  |
  `-- Follow-up evaluation:
        progress 80%, deadline Friday, today Wednesday -> on track
        Action: push follow-up back by 1 day
        Resource: reliability_score += small positive adjustment
```

---

### 6.3 Sequential Chain

Only the first node is actionable. Downstream urgency rolls up to elevate it.

```
TaskNode [Composite: "Deploy to production"]
  breakdown_strategy: SEQUENTIAL
  |
  |-- [1] TaskNode [Atomic: "Code review"]
  |         sequence_order: 1
  |         state: IN_PROGRESS        <-- ONLY THIS IS ACTIONABLE
  |         chain_urgency_rollup: 0.91  (elevated by critical urgency downstream)
  |
  |-- [2] TaskNode [Approval: "Staging sign-off"]
  |         sequence_order: 2
  |         state: INACTIVE_PENDING
  |         DEPENDS_ON -> [1]
  |
  |-- [3] TaskNode [Atomic: "Deploy to staging"]
  |         sequence_order: 3
  |         state: INACTIVE_PENDING
  |         DEPENDS_ON -> [2]
  |
  `-- [4] TaskNode [Atomic: "Deploy to production"]
            sequence_order: 4
            state: INACTIVE_PENDING
            DEPENDS_ON -> [3]
            own priority_score: 0.91  (critical path, 1 day to deadline)
```

Agent briefing note: *"Only 'Code review' is actionable now. But 'Deploy to production' at the end of this chain is due tomorrow with high urgency — that urgency rolls back to make 'Code review' the top priority in your queue."*

---

### 6.4 Parallel Chain

All chains are simultaneously actionable. Milestone activates when all complete (AND gate).

```
TaskNode [Milestone: "v2 Launch Ready"]
  |
  |-- SPAWNED_FROM --> TaskNode [Composite: "Backend"]      state: IN_PROGRESS  <- Actionable
  |                     assigned_to: RES-agent-backend
  |
  |-- SPAWNED_FROM --> TaskNode [Composite: "Frontend"]     state: IN_PROGRESS  <- Actionable
  |                     assigned_to: RES-agent-frontend
  |
  `-- SPAWNED_FROM --> TaskNode [Composite: "QA suite"]     state: ACTIVE       <- Actionable
                        assigned_to: RES-agent-qa

DependencyGate [AND]: Milestone activates only when all three complete.
```

---

### 6.5 Hybrid Chain (Sequential with Parallel Branches)

```
GoalNode: "Ship onboarding flow"
  |
  |-- [Phase 1 - Sequential]
  |     TaskNode [Research: "Audit current onboarding"]     state: COMPLETE
  |       |
  |       `-- DEPENDS_ON --> TaskNode [Decision: "Choose approach"]
  |                             state: IN_PROGRESS   <-- ONLY ACTIONABLE NODE
  |                             options: ["Redesign", "Iterate", "Third-party"]
  |                             |
  |                             `-- On resolution, Phase 2 activates:
  |
  `-- [Phase 2 - Parallel, activates after Decision resolves]
        |-- TaskNode [Delegated: "UX design"]               state: INACTIVE_PENDING
        |     DEPENDS_ON -> Decision (BRANCHED_FROM)
        |
        |-- TaskNode [Delegated: "Copy & content"]          state: INACTIVE_PENDING
        |     DEPENDS_ON -> Decision (BRANCHED_FROM)
        |
        `-- TaskNode [Delegated: "Tech spec"]               state: INACTIVE_PENDING
              DEPENDS_ON -> Decision (BRANCHED_FROM)
```

---

### 6.6 Blocked Task — Agent Inference Chain

```
TaskNode [Delegated: "Integrate payment API"]
  state: BLOCKED
  computed_priority: 0.12   <-- SUPPRESSED (blocked node deprioritized)
  |
  `-- BLOCKS --> TaskNode [Milestone: "Checkout complete"]
                   computed_priority: 0.88

Root blocker:
  TaskNode [Approval: "Legal review of payment provider"]
    state: PENDING
    assigned_to: RES-legal-team
    computed_priority: 0.88   <-- ELEVATED (root of blockage chain)
```

Agent inference: *"'Integrate payment API' is blocked, holding up 'Checkout complete' milestone. Root blocker is 'Legal review of payment provider' assigned to the legal team — that's what needs to move. Want me to send a follow-up?"*

---

### 6.7 Resource Risk Triggering Reallocation

```
ResourceNode [Human: "James"]
  capacity.load_factor: 0.95
  current_risk.capacity_risk: HIGH
  risk_signals: ["mentioned 'slammed this week' in update on TSK-JD-4830"]

  ASSIGNED_TO <-- TaskNode [TSK-JD-4835-DEL: "Review security spec"]
                    on_critical_path: true
                    computed_priority: 0.88   <-- KEEP

  ASSIGNED_TO <-- TaskNode [TSK-JD-4830-DEL: "Write API docs"]
                    on_critical_path: false
                    computed_priority: 0.45   <-- REASSIGN

  ASSIGNED_TO <-- TaskNode [TSK-JD-4840-DEL: "Update changelog"]
                    on_critical_path: false
                    computed_priority: 0.22   <-- DEFER
```

Agent recommendation: *"James has signaled low bandwidth with 3 active tasks. Recommend: keep TSK-4835 (critical path), reassign TSK-4830 (API docs), defer TSK-4840 (changelog). Should I proceed?"*

---

### 6.8 Batched Check-in Node

Three tasks to the same resource consolidated into one outbound message.

```
CheckinNode [CHK-001]
  target_resource: RES-sarah
  state: SCHEDULED
  scheduled_for: Thursday 9am

  task_refs: [
    TSK-JD-4821-DEL  ("Review the API spec")
    TSK-JD-4850-DEL  ("Sign off on database schema")
    TSK-JD-4863-DEL  ("Confirm deployment window")
  ]

  outbound_message (agent-composed):
    "Hi Sarah - a few things to check in on when you have a moment:
     1. API spec review (TSK-4821) - any updates on your end?
     2. Database schema sign-off (TSK-4850) - still on track for Friday?
     3. Deployment window (TSK-4863) - can you confirm the Thursday slot?
     Happy to answer any questions on these."
```

---

## 7. Task Lifecycle & State Machine

### 7.1 State Transitions

```
                    .----------------------------------------.
                    |                                        |
PENDING --> ACTIVE --> IN_PROGRESS --> COMPLETE              |
                |                                            |
                |--> BLOCKED --> (unblocked) --> ACTIVE      |
                |                                            |
                |--> DELAYED --> IN_PROGRESS                 |
                |                                            |
                |--> NEEDS_REVIEW --> IN_PROGRESS            |
                |                `-> COMPLETE                |
                |                                            |
                |--> CANCELLED                               |
                |                                            |
                |--> SNOOZED --> (timer expires) --> --------'
                |
                `--> INACTIVE_PENDING  (sequential chain predecessor
                                        not yet complete)
```

Every state transition is recorded in `state_history`:

```
state_history entry: {
  from_state:   string
  to_state:     string
  changed_at:   timestamp
  changed_by:   "AGENT" | "HUMAN" | "INBOUND_UPDATE"
  reason:       string
}
```

### 7.2 Composite Task Completion Cascade

```
Child task completes
  |
  v
Are there remaining incomplete children?
  |
  |-- YES --> Update parent progress percentage. No state change yet.
  |
  `-- NO  --> Does parent have an open Review or Approval child?
                |
                |-- YES --> Parent enters NEEDS_REVIEW. Waits for resolution.
                |
                `-- NO  --> Parent auto-completes (completion_signal: CASCADED)
                               |
                               `--> Repeat check on parent's parent
                                      |
                                      `--> If Milestone completes:
                                             Notify stakeholders
                                             Surface in next briefing
                                             Update GoalNode progress %
                                               |
                                               `--> If Goal's last Milestone completes:
                                                      Flag prominently in briefing
                                                      Do NOT auto-close Goal
                                                      Require explicit human acknowledgment
```

**Completion confidence exception:** Research and Review task completions carry a `confidence` score. If `confidence = LOW`, cascade is halted and the agent flags it in the briefing rather than silently propagating.

---

## 8. Inbound Update Protocol

### 8.1 Task ID Structure

```
TSK-[USER_ID]-[SEQUENCE]-[TYPE_CODE]

Type codes:
  DEL = Delegated        ATM = Atomic
  FLW = Follow-up        CMP = Composite
  APR = Approval         MIL = Milestone
  RVW = Review           REC = Recurring
  DEC = Decision         CHK = Check-in
  RES = Research

Examples:
  TSK-JD-4821-DEL   (delegated task)
  TSK-JD-4822-FLW   (follow-up child of above)
  TSK-JD-4823-APR   (approval task)
```

### 8.2 Node Resolution — Dual Mechanism

**Primary: Task ID Lookup**
Every delegated task includes its Task ID in the outbound delegation message to maximize match rate on inbound responses.

**Fallback: Vector Embedding Search**
When no Task ID is present, the agent extracts signals and runs similarity search against stored node embeddings, built from:

```
node_embedding = embed(
  task_description +
  assigned_to +
  created_by +
  goal_context (from parent Goal Node) +
  key_entities (people, systems, deadlines mentioned)
)
```

If confidence is below threshold, the agent flags for human confirmation in the next briefing.

### 8.3 Full Protocol Pipeline

```
INBOUND TEXT
     |
     v
.--------------------------------------------.
|  1. EXTRACTION LAYER                       |
|                                            |
|  - Task ID present?                        |
|  - Sender identity                         |
|  - Status signal:                          |
|      COMPLETE | IN_PROGRESS | BLOCKED |    |
|      DELAYED | NEEDS_INPUT |               |
|      PROACTIVE_UPDATE                      |
|  - Progress quantification (% if present) |
|  - Blocker entity (who/what is blocking)  |
|  - Confidence/sentiment reading            |
`---------------------.----------------------'
                      |
             .--------v---------.
             |  Task ID found?  |
             `-----.--------.---'
                  YES        NO
                   |          |
                   v          v
              Direct     Vector search
              lookup     + confidence
                   |          |
                   `-----.----'
                         |
     .-------------------v-------------------------.
     |  2. NODE RESOLUTION                         |
     |  High confidence -> proceed                 |
     |  Low confidence  -> queue for briefing      |
     `--------------------.------------------------'
                          |
     .--------------------v------------------------.
     |  3. STATE EVALUATION                        |
     |  Map signal -> state transition             |
     |  Record reason in state_history             |
     `--------------------.------------------------'
                          |
     .--------------------v------------------------.
     |  4. FOLLOW-UP CHILD EVALUATION              |
     |  See decision tree in Section 8.4           |
     `--------------------.------------------------'
                          |
     .--------------------v------------------------.
     |  5. GRAPH PROPAGATION                       |
     |  Cascade state changes upward               |
     |  Check composite completion logic           |
     |  Update ResourceNode capacity +             |
     |  reliability scores                         |
     `--------------------.------------------------'
                          |
     .--------------------v------------------------.
     |  6. ACTION DECISION                         |
     |  Immediate alert / briefing / silent?       |
     |  Autonomous action or human approval?       |
     `---------------------------------------------'
```

### 8.4 Follow-up Child Evaluation Decision Tree

```
Inbound update received for Delegated Task
  |
  |-- Status = COMPLETE, follow-up not yet fired
  |     -> Close follow-up (resolved_by_proactive = true)
  |     -> Log: "Closed by proactive update [timestamp]"
  |     -> ResourceNode reliability_score += positive adjustment
  |
  |-- Status = IN_PROGRESS, follow-up not yet fired
  |     |-- Progress on track for deadline
  |     |     -> Keep follow-up as scheduled
  |     |-- Progress ahead of schedule
  |     |     -> Push follow-up back slightly
  |     `-- Progress behind schedule
  |           -> Bring follow-up forward, flag in briefing
  |
  |-- Status = BLOCKED, follow-up already fired
  |     -> Do not reschedule follow-up for same question
  |     -> Create new follow-up child targeting the blocker entity
  |     -> Escalate to briefing if blocker requires human action
  |
  `-- Status = DELAYED with new timeline given
        -> Reschedule follow-up to (new_timeline - buffer)
        -> Update Constraint Node if delay threatens a deadline
        -> Cascade delay signal upward to Milestone / Goal nodes
```

### 8.5 Status Signal Taxonomy

| Signal | Example Phrases | Agent Action |
|--------|----------------|--------------|
| `COMPLETE` | "Done", "Finished", "Delivered" | Close follow-up, cascade completion |
| `IN_PROGRESS` | "Working on it", "About 60% there" | Update progress %, evaluate follow-up timing |
| `BLOCKED` | "Stuck on X", "Waiting for Y" | Create blocker-targeting follow-up, elevate blocker |
| `DELAYED` | "Running behind", "Won't make Friday" | Reschedule, cascade delay, check constraints |
| `NEEDS_INPUT` | "Need clarification on X" | Surface to human immediately |
| `PROACTIVE_UPDATE` | No explicit status, context only | Extract any signals, update progress if inferable |

### 8.6 Unresolved Update Handling

| Situation | Action |
|-----------|--------|
| Completely unrecognizable, unknown sender | Hold queue -> surface in briefing |
| Recognizable topic, ambiguous node match | Create ghost node -> flag for human classification |
| Clearly a task update, missing ID, known agent | Reply to sender requesting Task ID |

### 8.7 Confidence and Sentiment Reading

```
"Will deliver Friday"               -> confidence = HIGH
"Should be done by Friday"          -> confidence = MEDIUM
"I think this approach works"       -> confidence = LOW
"Almost there, just testing"        -> progress ~85%, confidence = MEDIUM-HIGH
"Making good progress"              -> risk_adjustment = -0.1 on ResourceNode
```

---

## 9. Agent Reasoning Algorithm

### 9.1 Node Scoring Formula

```
Priority Score =
  (Timeline Urgency Score       * W1) +
  (Dependency Weight Score      * W2) +
  (Critical Path Score          * W3) +
  (Blocker Score                * W4) +
  (Human Override Score         * W5) +
  (Resource Risk Score          * W6) +
  (Constraint Pressure Score    * W7)
```

Default weights stored on UserNode, evolved through learning:

| Weight | Factor | Default |
|--------|--------|---------|
| W1 | Timeline Urgency | 0.25 |
| W2 | Dependency Weight | 0.20 |
| W3 | Critical Path | 0.20 |
| W4 | Blocker | 0.15 |
| W5 | Human Override | 0.10 |
| W6 | Resource Risk | 0.05 |
| W7 | Constraint Pressure | 0.05 |

---

### 9.2 Factor 1 — Timeline Urgency Score

| Days Remaining | Base Urgency |
|----------------|-------------|
| > 14 days | 0.2 |
| 7-14 days | 0.4 |
| 3-7 days | 0.6 |
| 1-3 days | 0.85 |
| < 1 day | 1.0 |
| Overdue | 1.2 |

Effort slack adjustment:
```
slack = days_remaining - estimated_effort_days
slack < 0  -> urgency += 0.30  (behind even before deadline)
slack < 1  -> urgency += 0.15
```

---

### 9.3 Factor 2 — Dependency Weight Score

```
dependency_weight = direct_dependents + (transitive_dependents * 0.5)
```

Traverses all downstream edges recursively. Breadth is also scored — a blocker at a fork where 3 parallel chains are waiting scores higher than a single-chain blocker with the same raw count.

---

### 9.4 Factor 3 — Critical Path Score

```
For each Goal Node with a deadline:
  1. Map all paths from current state to Goal completion
  2. Estimate total duration per path (sum of estimated_effort)
  3. Longest path = critical path
  4. Nodes on critical path         -> CP_score = 1.0
  5. Nodes off critical path        -> CP_score = 0.0
  6. Low float nodes elevated even if not strictly on CP

Critical path multiplier on entire node score:
  final_score * (1.0 + (parent_goal_priority * 0.5))

  P1 goal -> 1.5x multiplier
  P2 goal -> 1.3x multiplier
  P3 goal -> 1.1x multiplier
```

---

### 9.5 Factor 4 — Blocker Score

```
HARD_BLOCKER (Approval, Dependency Gate):     blocker_score = 1.0
SOFT_BLOCKER (partial workaround possible):   blocker_score = 0.6
NOT_A_BLOCKER:                                blocker_score = 0.0

If node.state == BLOCKED:
  -> This node's score is suppressed
  -> Its blocker's score is elevated
  -> Agent builds root-cause inference chain for briefing
```

---

### 9.6 Factor 5 — Human Override Score

```
"Make this a priority"               -> +1.0, persists until complete
"Most important thing right now"     -> +1.0 + relative re-ranking flag
"Keep an eye on this"                -> +0.5
"This can wait"                      -> -0.3
"Ignore this for now"                -> SNOOZED state, excluded from scoring
```

Overrides inject into the scoring formula — they do not replace it. The agent surfaces tensions between overrides and objective urgency rather than silently complying.

---

### 9.7 Factor 6 — Resource Risk Score

```
resource_risk =
  (1 - reliability_score)  * 0.5 +
  (current_load_factor)    * 0.3 +
  (explicit_risk_signals)  * 0.2
```

Risk signal inference from inbound text:
```
"I don't have bandwidth right now"   -> capacity_risk = HIGH
"We're slammed this week"            -> capacity_risk = HIGH
"Should be fine but it's complex"    -> delivery_risk = MEDIUM
[No response to last 2 follow-ups]   -> responsiveness_risk = HIGH
"Making good progress"               -> risk_adjustment = -0.1
```

When capacity_risk = HIGH, agent triggers reallocation reasoning:
```
1. Pull all tasks assigned to this resource
2. Score each by their own priority
3. Surface recommendation to human with reasoning
4. Wait for human approval before any reassignment
```

---

### 9.8 Factor 7 — Constraint Pressure Score

| Constraint Type | Pressure | Override Allowed? |
|----------------|---------|-------------------|
| Hard deadline (within threshold) | 1.0 | Yes |
| Budget constraint near limit | 0.7 | Yes |
| Compliance / regulatory | 0.9 | No — agent flags any attempt |
| External dependency | 0.6 | Yes |

---

### 9.9 Sequential vs. Parallel Chain Analysis

```
SEQUENTIAL CHAIN (A -> B -> C, each depends on previous):
  -> Only A is actionable; B and C enter INACTIVE_PENDING
  -> Chain urgency rollup applied:
       chain_urgency = max(priority_scores of all nodes in chain)
  -> A's score is elevated by the urgency of everything downstream

PARALLEL CHAINS (A, B, C all independent children):
  -> All three simultaneously actionable
  -> Agent pursues all chains concurrently
  -> Dependency Gate type = AND (all must complete to trigger Milestone)

HYBRID (A -> [B, C parallel] -> D):
  -> A actionable now
  -> B, C become actionable when A completes
  -> D becomes actionable when BOTH B and C complete (AND gate)
  -> Agent tracks topology explicitly for each subgraph
```

---

### 9.10 Action Queue Entry Schema

```
ActionQueueEntry {
  node_id:              string
  priority_score:       float
  recommended_action:   "SEND_FOLLOWUP" | "ESCALATE" | "REASSIGN" |
                        "BRIEF_HUMAN" | "CREATE_CHILD" | "MONITOR"
  reasoning:            string    // natural language from ScoreExplanation
  requires_human:       boolean
  batch_group:          resource_id   // groups multiple items to same recipient
  urgency_window:       "today" | "this_week" | "monitor"
  autonomous_action:    boolean
}
```

---

### 9.11 The Full Continuous Reasoning Loop

```
TRIGGER (time-based / event-based / inbound update / on-demand)
         |
         v
  Score all active nodes using 7-factor formula
         |
         v
  Analyze chain topology
  (sequential suppression, parallel activation, chain urgency rollup)
         |
         v
  Build ranked Action Queue (scored, reasoned, batched by resource)
         |
         v
     .---+---.
     |       |
     v       v
AUTONOMOUS  HUMAN-NEEDED
 actions     items accumulate
 execute     until briefing window
 immediately (or urgency interrupt if
              score > interrupt_threshold)
     |       |
     `---+---'
         |
         v
  DELIVER BRIEFING (see Section 12)
         |
         v
  RECEIVE human input
  (overrides, approvals, corrections, snoozes)
         |
         v
  UPDATE UserNode behavioral model
  Adjust W1-W7 from decisions made
  Record override patterns and reasoning preferences
         |
         `----------------------- repeat -----------------------'
```

---

## 10. Follow-up Timing Model

Follow-up fire timing is computed dynamically from multiple factors:

```
follow_up_timing =
  base_cadence (from UserNode preferences)
  * complexity_factor (from task type and estimated_effort)
  * resource_reliability_score (from ResourceNode)
  * recency_adjustment (did this resource just deliver something recently?)
```

The agent surfaces its inferred follow-up schedule during the daily briefing before committing:

> *"I've scheduled a follow-up with Sarah on the API spec for Thursday. Based on similar tasks she usually delivers 2 days ahead of deadline, so I moved it earlier than default — want me to keep that?"*

Human can confirm, adjust per instance, or instruct the agent to always apply the pattern for that resource. Every decision updates the relationship model stored on the ResourceNode.

---

## 11. Goal & Constraint Node Creation

### 11.1 Bottom-up Inference (agent-initiated)

Agent detects patterns across existing tasks — shared stakeholders, domain proximity, deadline clustering — and proposes a Goal or Constraint Node for human confirmation before committing to the graph.

Example: *"These 6 tasks all seem related to the product launch. Want me to create a Goal Node and link them? I'm also inferring a soft end-of-month deadline — should I make that a Constraint Node?"*

### 11.2 Top-down Decomposition (human-initiated)

Human states a goal. Agent decomposes into a proposed subgraph and presents for review before committing:

```
Human: "We need to get the new onboarding flow live before the conference"

Agent proposes:
  GoalNode: "Onboarding flow live"
    ConstraintNode: "Conference date" (hard deadline)
      |
      |-- TaskNode [Milestone: "Design approved"]
      |     `-- TaskNode [Composite: "UX design"]
      |           |-- TaskNode [Atomic: "Wireframes"] -> ASSIGNED_TO RES-ux-agent
      |           `-- TaskNode [Approval: "Design review"] -> ASSIGNED_TO user-design-lead
      |
      |-- TaskNode [Milestone: "Engineering complete"]
      |     `-- TaskNode [Composite: "Frontend build"] -> ...
      |
      `-- TaskNode [Milestone: "QA passed"]
            `-- ...

Human reviews, edits, approves -> subgraph committed to live graph.
```

Human edits to the proposed structure teach the agent how this user thinks about decomposing work.

---

## 12. Daily Briefing

The briefing is the primary human-agent interaction surface, serving three functions: state reporting, inference review, and decision surfacing — all batched into one conversation.

### 12.1 Briefing Structure

```
.-------------------------------------------------------.
|  DAILY BRIEFING  [Date] [Time]                       |
|-------------------------------------------------------|
|  1. CRITICAL  (your decision needed today)           |
|     -> Max 3 items, highest priority first           |
|     -> Each with: recommended action + plain-english |
|        reasoning                                     |
|     -> "I am autonomously managing N other items"    |
|-------------------------------------------------------|
|  2. INFERENCES TO CONFIRM                            |
|     -> Follow-up timings I have set and why          |
|     -> New Goal/Constraint nodes I have inferred     |
|     -> Resource risk signals + proposed responses    |
|-------------------------------------------------------|
|  3. COMPLETED SINCE LAST BRIEFING                    |
|     -> Task completions and milestone progress       |
|     -> Proactive updates received (positive signals) |
|-------------------------------------------------------|
|  4. AHEAD OF THE CURVE  (awareness only)             |
|     -> Items approaching high urgency                |
|     -> Critical path float warnings                  |
|-------------------------------------------------------|
|  5. DEFERRED ITEMS CHECK                             |
|     -> Previously snoozed: "Still want to defer?"   |
`-------------------------------------------------------'
```

### 12.2 Cognitive Load Limit

The agent never surfaces more items than the human can reasonably act on. If many critical items exist, the agent surfaces the top 3, handles the rest autonomously where permitted, and tells the human explicitly that it is doing so.

### 12.3 Interrupt Threshold

Items accumulate until the scheduled briefing window unless a node's `computed_priority` exceeds `UserNode.preferences.interrupt_threshold`. Only genuinely urgent situations break through mid-day.

---

### 12.4 System Prompt Graph Context (Always-On)

Every agent turn — including daily briefings — receives a **system prompt** assembled from seven ordered sections. Section 7 (the graph context) is the only section that contains live graph data fetched from the database. All other sections are static configuration loaded from MinIO.

#### 12.4.1 System Prompt Assembly Order

```
[1] system_header.md         — agent persona, philosophy, tool-use instructions
    + "Today's date is {ISO-date}."

[2] ## Your Persona           — user/agent-specific profile.md (MinIO)

[3] ## Available Tool Sets    — compact manifest of tool set names only (~150 tokens)
                               (full schemas are NOT sent — progressive declaration)

[4] ## Knowledge Base         — index of topic names only
                               Agent calls read_knowledge(topic) to load rules on demand.

[5] ## Available Agents       — compact agent catalog (comms, etc.)

[6] ## Execution Context      — registered skills + MCP servers

[7] ## Current Task Graph Summary   ← live graph data fetched per-request
    ### Active Goals
    ### Top Priority Tasks
```

The progressive declaration approach (sections 3–4) means the agent receives tool and knowledge topic names only. It loads schemas and rule content via tool calls within the conversation, keeping the system prompt compact and deterministic in size.

#### 12.4.2 Active Goals Line Format

Each active GoalNode in the system prompt summary renders as a single line:

```
- {title} [{goal_id}] | {priority} | {state} | due {target_date} | {milestones_done}/{milestone_count} milestones | {derived_percentage}%
```

**Fields included:**

| Field | Source | Rationale |
|-------|--------|-----------|
| `title` | `GoalNode.title` | Primary identifier for the LLM |
| `goal_id` | `GoalNode.id` | Required for tool calls (`list_tasks(goal_id=...)`) |
| `priority` | `GoalNode.priority` (P1/P2/P3) | Changes urgency of all tasks under this goal |
| `state` | `GoalNode.state` | ACTIVE / ON_HOLD — ON_HOLD goals shown separately, not mixed |
| `target_date` | `GoalNode.timeline.target_date` | Deadline proximity drives W1 urgency for all child tasks |
| `milestones_done / milestone_count` | `GoalNode.progress` | Trajectory signal — avoids a tool call to assess completion phase |
| `derived_percentage` | `GoalNode.progress.derived_percentage` | Single number for completion state |

**Excluded from summary** (available via `get_task_details(goal_id)`):

- `intelligence` log — unbounded length, on-demand only
- `inferred_from` task IDs, `confirmed_by_user` flag
- Full `timeline` block (started_at, completed_at)
- `origin` (USER_DEFINED / AGENT_INFERRED)

**Example:**
```
### Active Goals
- Ship GraphClaw Phase 0 MVP [goal-001] | P1 | ACTIVE | due 2026-04-30 | 2/5 milestones | 40%
- Vendor contract renewal [goal-002] | P2 | ON_HOLD | due 2026-06-15 | 0/3 milestones | 0%
```

ON_HOLD goals appear in a separate `### On Hold Goals` block below active goals so they are visible but do not compete with ACTIVE items.

#### 12.4.3 Top Priority Task Line Format

Each task in the top-5 scored queue renders as a single line:

```
- [{rank}] {title} [{task_id}] | {task_type} | {state} | score={final_score} | @{assignee_name} | due {deadline}
```

When `state = BLOCKED`:
```
- [{rank}] {title} [{task_id}] | {task_type} | BLOCKED by {blocker_task_id} | score={final_score} | @{assignee_name} | due {deadline}
```

**Fields included:**

| Field | Source | Rationale |
|-------|--------|-----------|
| `rank` | `ActionQueueEntry.rank` | Action ordering |
| `title` | `TaskNode.title` | Primary identifier |
| `task_id` | `TaskNode.id` | Required for tool calls |
| `task_type` | `TaskNode.task_type` | ATOMIC / COMPOSITE / DELEGATED / FOLLOWUP / APPROVAL etc. — changes recommended action |
| `state` | `TaskNode.state` | Current lifecycle state |
| `blocker_task_id` | inbound BLOCKS edge source (when state=BLOCKED) | Identifies what to resolve; absent when not blocked |
| `final_score` | `ActionQueueEntry.final_score` | Priority signal |
| `assignee_name` | `ResourceNode.name` or `UserNode.name` (via ASSIGNED_TO edge) | Most common follow-up question; one word prevents a tool call |
| `deadline` | `TaskNode.timeline.deadline` | Scheduling anchor |

**Excluded from summary** (available via `get_task_details(task_id)`):

- `intelligence` log
- `state_history`, `update_log`
- `type_metadata` discriminated union (FollowUpMetadata, DelegatedMetadata, etc.)
- Full `scoring` block (all 7 W-factor raw values)
- All non-blocker edge relationships (DEPENDS_ON, SPAWNED_FROM, PART_OF, etc.)
- `autonomy`, `override`, `progress`, `embedding_inputs` blocks

**Example (seed data, scored 2026-04-23):**
```
### Top Priority Tasks
- [1] Implement AGE query helpers in db/queries [task-002] | ATOMIC | IN_PROGRESS | score=0.81 | @Alice | due 2026-04-30
- [2] Build CLI command suite [task-004] | COMPOSITE | PENDING | score=0.74 | @Alice | due 2026-04-30
- [3] Implement Pydantic node schemas [task-003] | ATOMIC | PENDING | score=0.71 | @Alice | due 2026-04-30
- [4] Research Apache AGE Cypher query patterns [task-001] | RESEARCH | PENDING | score=0.58 | @Alice
- [5] Code review: Phase 0 core loop [task-005] | APPROVAL | BLOCKED by task-003 | score=0.31 | @Alice | due 2026-04-30
```

#### 12.4.4 Design Principle

> **Summary answers: "what should I act on and why is it urgent?"**
> **On-demand answers: "tell me everything about this specific node."**

Anything that does not change the agent's first-turn routing decision does not belong in the system prompt. The goal is to eliminate the most common follow-up tool calls (who owns this, what is blocking this, how far along is this goal) while keeping the summary compact enough to fit within token budgets as the graph grows.

---

### 12.5 On-Demand Task/Goal Detail View

When the user asks about a specific task or goal — or when the agent needs full context before a mutation — it calls `get_task_details(node_id)`. The tool returns a **layered structured response**, not a flat property dump.

#### 12.5.1 Response Structure

The response is ordered from most-actionable to most-historical. The agent reads top-to-bottom and stops when it has enough context.

```
Task: {title} [{task_id}]
Type: {task_type} | State: {state} | Score: {final_score} | Autonomy: {autonomy_level}

Timeline: due {deadline} | started {started_at} | est. {estimated_effort_hours}h | progress: {progress.percentage}%
Assigned to: {assignee_name} [{assignee_id}] | load: {capacity.load_factor} | reliability: {reliability.overall_score}

Goal: {goal_title} [{goal_id}] — {priority}, {goal_state}

Dependencies (this task is blocked by / blocking):
  → {dep_id} [{dep_state}]  {dep_title}         (this task DEPENDS ON — waiting on this)
  ← {dep_id} [{dep_state}]  {dep_title}         (this task is DEPENDED UPON BY — blocking this)

Active blockers:
  BLOCKS → {task_id} [{task_state}]  {task_title}

Edges:
  PART_OF      → {goal_id}
  ASSIGNED_TO  → {assignee_id} ({assignee_name})
  DEPENDS_ON   → {dep_id}, {dep_id}, ...
  SPAWNED_FROM → {parent_id}  (if applicable)

Scoring factors:
  W1 timeline_urgency:    {value}  ({plain_english_reason})
  W2 dependency_weight:   {value}  ({direct_dependent_count} direct dependents)
  W3 critical_path:       {value}  ({topology_note})
  W4 blocker:             {value}  (HARD / SOFT / NONE)
  W5 human_override:      {value}
  W6 resource_risk:       {value}  (load {load_factor}, reliability {reliability_score})
  W7 constraint_pressure: {value}  ({pressure_score} on {constraint_title})

Type metadata: {task_type}-specific fields only
  (e.g. DELEGATED: assigned_resource_id, expected_deliverable, follow_up_task_id)
  (e.g. FOLLOWUP:  target_task_id, scheduled_fire_at, follow_up_count)
  (e.g. APPROVAL:  approver_id, approval_criteria, max_wait_days)

Intelligence log (last 5 entries):
  [{ISO-date}] {channel} | {direction} | {summary}
  [{ISO-date}] {channel} | {direction} | {summary}
  ...
```

**For GoalNode detail**, the same structure applies with goal-specific fields:
```
Goal: {title} [{goal_id}]
State: {state} | Priority: {priority} | Origin: {USER_DEFINED | AGENT_INFERRED}

Timeline: due {target_date} | {derived_percentage}% complete
Progress: {milestones_done}/{milestone_count} milestones complete

Tasks under this goal:
  - [{rank}] {task_title} [{task_id}] | {task_type} | {state} | score={score}
  ... (all tasks, not just top-5)

Constraints applying to this goal:
  - {constraint_title} [{con_id}] | {constraint_type} | pressure: {pressure_score}

Intelligence log (last 5 entries):
  [{ISO-date}] {entry}
```

#### 12.5.2 What is Always Excluded

These fields are never returned by `get_task_details`, even in drill-down:

| Field | Reason |
|-------|--------|
| `embedding_inputs` | Internal — never LLM-visible |
| `state_history` beyond last 3 entries | Audit record; last 3 are enough for context |
| `update_log` beyond last 3 entries | Same |
| Raw edge property blobs | Surfaced as named relationships in the Edges block |
| Full `override` block | Surfaced only as a flag in the header if `is_overridden=true` |

#### 12.5.3 Dependency Name Resolution

`get_task_details` resolves dependency IDs to titles. The current implementation returns only IDs (e.g. `depends_on: ["task-001"]`). The enhanced response fetches `get_node(dep_id)` for each dependency and includes both ID and title. This adds N graph round-trips but the count is bounded by typical dependency fan-out (2–5 nodes).

For performance, the repository layer should offer a `get_nodes_bulk(ids)` query to batch these fetches in a single Cypher call rather than N sequential calls.

---

### 12.6 Implementation Plan

The following files require changes to implement §12.4 and §12.5. No schema changes are needed — all required fields already exist on the node models.

#### Files to Change

| File | Change |
|------|--------|
| `src/graphclaw/agent/main_orchestrator.py` | `_build_graph_summary`: add `target_date`, `milestones_done/milestone_count`, `derived_percentage` to goal lines; add `task_type`, resolved `assignee_name`, `blocked_by` ID to task lines |
| `src/graphclaw/agent/main_orchestrator.py` | `_tool_get_task_details`: restructure flat dict return into layered response (timeline block, assignee block, goal block, dependency block with resolved titles, edges block, scoring factor block, type_metadata block, intelligence log) |
| `src/graphclaw/gateway/seeding.py` | `_KNOWLEDGE_TOPICS` list: add `"constraint_rules"`, `"resource_rules"`, `"goal_lifecycle_rules"` so the three new knowledge files are seeded into MinIO on startup |
| `src/graphclaw/db/age/repository.py` | Add `get_nodes_bulk(ids: list[str])` query: single Cypher `MATCH` on multiple IDs using `WHERE n.id IN [...]`, returns list of property dicts — eliminates N+1 round-trips in the detail view dependency resolution |
| `src/graphclaw/agent/tool_registry.py` | Update `get_task_details` tool description to reflect the new layered response structure so the LLM knows what to expect |

#### Files Not Changed

| File | Reason |
|------|--------|
| `src/graphclaw/agent/briefing.py` | 5-section briefing format is correct as-is; `format_briefing` is called from a tool response, not the system prompt |
| `src/graphclaw/models/nodes.py` | All required fields already exist on TaskNode and GoalNode |
| `src/graphclaw/models/scoring.py` | `ActionQueueEntry` already carries `autonomy_level`, `explanation.factors`, `topology_note` |
| Knowledge rule `.md` files | Already written (`constraint_rules.md`, `resource_rules.md`, `goal_lifecycle_rules.md`) |

---

## 13. Explainability

### 13.1 Principle

Explainability is the default interface. The agent can always answer *"why is this at the top?"* in natural language. The human never experiences the system as a black box.

- **Default mode**: Agent explains decisions in natural language on request
- **Power user mode**: Scoring weights W1-W7 are visible and adjustable  
- **Audit mode**: Full ScoreExplanation records are stored and queryable for any node

### 13.2 Example Explainability Dialogue

```
Human: "Why is TSK-4821 at the top of my list?"

Agent: "TSK-4821 is ranked #1 for three reasons:

  1. Critical path: It is on the critical path for your Q3 Launch goal
     (P1 priority), which applies a 1.5x multiplier to its base score.

  2. Tight deadline: The deadline is in 3 days and estimated effort is
     2 days — almost no slack remaining (urgency score: 0.85).

  3. Chain position: This is the first node in a sequential chain of
     4 tasks. 'Deploy to production' at the end of the chain is due
     tomorrow, so that urgency has rolled back here.

  Additionally, Alex (assigned) has not responded to the last follow-up,
  which elevates the resource risk score."
```

---

## 14. Autonomy Permission Model

### 14.1 Global Defaults (UserNode)

| Permission | Description | Default |
|-----------|-------------|---------|
| `auto_update_ai_agents` | Update node state from AI agent inbound updates without asking | ON |
| `auto_send_followups` | Send follow-up messages to AI agents autonomously | ON |
| `auto_close_resolved` | Close follow-up children when proactive updates satisfy them | ON |

### 14.2 Per-Node Permissions (TaskNode.autonomy)

```
autonomy: {
  auto_update_allowed:      boolean   // overrides global default
  auto_close_allowed:       boolean
  requires_approval_from:   user_id   // null = fully autonomous
}
```

### 14.3 Human Update Approval Flow (when auto_update_allowed = false)

```
1. Inbound update received and parsed
2. Agent prepares proposed state change
3. Queued for next briefing:
   "Received update from [resource] on [task]:
    '[excerpt from raw text]'
    Proposed change: IN_PROGRESS at 60%. Apply this?"
4. Human approves or modifies
5. Agent applies change, logs changed_by: HUMAN
```

### 14.4 Trust Gradient

| Action | Autonomy Level |
|--------|---------------|
| Update AI agent task state | Autonomous (default ON) |
| Send follow-up to AI agent | Autonomous (default ON) |
| Close resolved follow-up child | Autonomous (default ON) |
| Send follow-up to human | Requires permission (default OFF) |
| Update human-delegated task state | Requires approval (default OFF) |
| Reassign a task | Always requires human approval |
| Re-prioritize the graph | Always requires human approval |
| Close a Goal Node | Always requires explicit human acknowledgment |

---

## 15. Communication Channels

### 15.1 Core Philosophy

The orchestrating agent has **one identity, multiple contact points**. A user interacts with the same agent brain regardless of whether they message via WhatsApp, Telegram, or email. The channel is purely the transport layer — context, memory, and continuity are maintained above it.

The agent always responds on whichever registered channel the user most recently initiated from. It never forces the user back to a preferred channel.

---

### 15.2 Orchestrating Agent Channel Identity

At provisioning, each user's agent instance is assigned its own contact points per channel. Each user gets a distinct agent identity so there is no cross-user message routing.

```
OrchestratingAgentChannelIdentity {
  agent_id:         string

  channels: {

    whatsapp: {
      enabled:                boolean
      phone_number:           string    // e.g. +1-555-AGENT-01 (user-specific)
      business_account_id:    string    // WhatsApp Business API account
      display_name:           string    // e.g. "WorkGraph Agent"
      webhook_url:            string    // SaaS platform inbound webhook
    }

    telegram: {
      enabled:                boolean
      bot_handle:             string    // e.g. @jd_workgraph_bot (user-specific)
      bot_token:              string    // stored encrypted server-side
      webhook_url:            string
      display_name:           string
    }

    email: {
      enabled:                boolean
      address:                string    // e.g. jd-agent@workgraph.app
      display_name:           string    // e.g. "WorkGraph Agent (John)"
      inbound_method:         "WEBHOOK" | "POLLING"
    }

  }
}
```

---

### 15.3 One-Time Onboarding — Settings Panel

> **Note:** The settings panel UI is part of the separate Web UI project (`docs/ui-requirements.md`). The GraphClaw backend exposes `/app/v1/` REST API endpoints that the UI consumes.

Channel authentication is configured once during onboarding. The user never touches authentication internals — the platform handles all credential management.

```
ONBOARDING FLOW — Channel Setup

Step 1: Select channels to activate
  [ ] WhatsApp
  [ ] Telegram
  [x] Email  (always available, no further setup needed)

Step 2: WhatsApp activation
  -> Platform provisions a WhatsApp Business number for this user
  -> User sends "ACTIVATE [code]" to that number from their WhatsApp
  -> Platform receives message, verifies sender phone number
  -> Links phone number to UserNode
  -> User saves the agent number as a contact

Step 3: Telegram activation
  -> Platform shows user their personal bot handle: @jd_workgraph_bot
  -> User opens Telegram, finds the handle, sends /start
  -> Bot records Telegram user_id, links to UserNode
  -> User saves the bot as a contact

Step 4: Email
  -> No user-side setup required
  -> Agent email shown: jd-agent@workgraph.app
  -> Platform configures DKIM/SPF at domain level
  -> User saves agent email as a contact

Step 5: Set preferred channel and per-org channel bindings
  -> "Which channel should your agent use for outbound briefings?"
  -> Optionally bind different channels to different organizations
```

---

### 15.4 UserNode Channel Configuration Schema

```
UserNode.channel_config: {

  configured_channels:    ["whatsapp", "telegram", "email"]

  channel_identifiers: {
    whatsapp: {
      user_phone:             string    // verified sender number
      agent_phone:            string    // agent's number for this user
      verified_at:            timestamp
      verification_method:    "SEND_CODE"
    }
    telegram: {
      user_telegram_id:       string    // Telegram numeric user ID (routing key)
      user_handle:            string    // stored for display only
      agent_bot_handle:       string
      verified_at:            timestamp
      verification_method:    "START_COMMAND"
    }
    email: {
      user_email:             string
      agent_email:            string
      verified_at:            timestamp
      verification_method:    "DKIM_DOMAIN"
    }
  }

  preferred_channel:        string    // default outbound channel
  fallback_channel:         string    // used if preferred is unavailable
  active_channel:           string    // last channel used by user (updated per message)

}
```

---

### 15.5 Inbound Authentication — Platform Gateway

Authentication occurs at the platform boundary before any message reaches the orchestrating agent. Channel-native verification is used for each transport.

```
.--------------------------------------------------------------.
|  PLATFORM INBOUND GATEWAY                                   |
|                                                              |
|  WhatsApp                                                    |
|    -> WhatsApp Business API sends HMAC-signed webhook        |
|    -> Platform verifies signature using app secret           |
|    -> Extracts sender phone number from payload              |
|    -> Looks up UserNode by phone number                      |
|    -> Matched -> authenticated, route to agent               |
|    -> Not matched -> drop silently                           |
|                                                              |
|  Telegram                                                    |
|    -> Telegram sends update to bot webhook                   |
|    -> Platform verifies using bot token                      |
|    -> Extracts Telegram user_id from update                  |
|    -> Looks up UserNode by telegram user_id                  |
|    -> Matched -> authenticated, route to agent               |
|    -> Not matched -> bot replies: "Not a registered user"    |
|                                                              |
|  Email                                                       |
|    -> Platform receives email to jd-agent@workgraph.app      |
|    -> Verifies sender domain via SPF/DKIM                    |
|    -> Matches From: address to UserNode.channel_config       |
|    -> Matched -> authenticated, route to agent               |
|    -> Not matched -> silently discard                        |
`--------------------------------------------------------------'
```

All authentication is binary at the gateway. The orchestrating agent never receives unauthenticated messages.

---

### 15.6 Unified Conversation Thread

Every message — regardless of channel — writes into a single ConversationThread per user. This is what enables seamless cross-channel context continuity.

```
ConversationThread {
  thread_id:        string
  user_id:          user_id

  messages: [{
    message_id:         string
    direction:          "INBOUND" | "OUTBOUND"
    channel:            "whatsapp" | "telegram" | "email"
    content:            string
    content_type:       "TEXT" | "VOICE" | "IMAGE" | "FILE"
    sent_at:            timestamp
    task_refs:          [task_id]     // tasks mentioned or updated
    resolved_intent:    string        // what the agent understood
  }]

  active_channel:     string          // last channel used by user
  last_activity:      timestamp
}
```

---

### 15.7 Cross-Channel Response Rule

```
Inbound message arrives
  |
  v
Is the incoming channel a registered channel for this user?
  |
  |-- NO  -> Reject (not authenticated)
  |
  `-- YES -> Is it the same channel as last outbound?
               |
               |-- YES -> Reply on same channel (normal flow)
               |
               `-- NO  -> User switched channels
                            Reply on the channel the user just used
                            Update active_channel
                            Do NOT force return to preferred channel
```

**Cross-channel context resolution:** When a user responds on a different channel to a pending briefing, the agent checks the ConversationThread for the most recent outbound message containing unresolved decisions, and applies the response to those items.

```
Example:
[08:00 WhatsApp -> User]  "3 decisions needed: reply 1/2/3 + yes/no"
[08:15 Telegram -> Agent] "1 yes, 2 yes, 3 no"

Agent:
  Checks ConversationThread -> finds pending decisions from WhatsApp briefing
  Resolves: approve items 1 and 2, keep item 3 active
  Replies via Telegram: "Got it. Updated all three."
  Updates active_channel to "telegram"
  Next briefing goes to Telegram
```

**Channel switch notification (org-scoped):**
If a user responds to a work briefing via their personal channel, the agent notes the ambiguity:

```
Agent: "Got your update on TSK-4821 (work task). You replied via
        WhatsApp — your work briefings usually go through Telegram.
        Should I keep responding here or switch back to Telegram?"
```

---

### 15.8 Organization-to-Channel Binding

Each organization can bind to a specific channel for outbound briefings, creating mental separation through channel routing.

```
OrganizationNode [Personal]   -> briefing.channel: "whatsapp"
OrganizationNode [Work]       -> briefing.channel: "telegram"
OrganizationNode [Side Project] -> briefing.channel: "email"
```

Example daily schedule:
```
07:00  WhatsApp  -> Personal briefing
09:00  Telegram  -> Work briefing
19:00  Email     -> Side project check-in
```

Org-to-channel binding governs outbound only. Inbound always follows the cross-channel response rule.

---

### 15.9 Channel Router Architecture

```
.---------------------------------------------------------.
|  USER-FACING SETTINGS PANEL (one-time onboarding)      |
|  Enable channels, verify identity, set org bindings    |
`---------------------------.---------------------------'
                             |
.----------------------------v----------------------------.
|  PLATFORM INBOUND GATEWAY                              |
|  Channel-native auth (HMAC / bot token / DKIM)        |
|  Only authenticated messages proceed                   |
`----------------------------.---------------------------'
                              |
.-----------------------------v---------------------------.
|  CHANNEL ROUTER                                         |
|  Normalize to internal message format                   |
|  Attach channel metadata                                |
|  Resolve sender to UserNode                             |
|  Write to ConversationThread                            |
|  Route to Inbound Update Protocol                       |
`-----------------------------.--------------------------'
                              |
.-----------------------------v---------------------------.
|  ORCHESTRATING AGENT                                    |
|  Channel-agnostic reasoning                             |
|  Full ConversationThread context available              |
|  Selects reply channel (active_channel rule)            |
|  Formats response for target channel                    |
`-----------------------------.--------------------------'
                              |
.-----------------------------v---------------------------.
|  OUTBOUND CHANNEL DISPATCHER                            |
|  WhatsApp Business API                                  |
|  Telegram Bot API                                       |
|  Email (SMTP / SendGrid)                                |
|  Writes outbound message to ConversationThread          |
`---------------------------------------------------------'
```

---

### 15.10 Channel-Specific Message Formatting

Each channel has different constraints. The agent adapts format automatically.

```
WhatsApp / Telegram (conversational)
  -> Short paragraphs, emoji for scanning
  -> Numbered choices for decisions
  -> Bold for task references (*TSK-4821*)
  -> Max 3 action items per message

  Example:
  "Work Briefing - 3 decisions needed today:

  1 - TSK-4821: Legal review blocking checkout
     Reassign to external counsel?

  2 - TSK-4835: Budget approval - $12k cloud upgrade

  3 - TSK-4850: Security spec - Alex waiting on feedback

  Reply: 1 yes / 1 no, etc."

Email (structured, async)
  -> Subject always includes org tag + date + task IDs
  -> HTML formatted with clear sections
  -> Full context included (email is self-contained)
  -> Task IDs in subject line for threading

  Subject: [Work] Daily Briefing - 3 Decisions | TSK-4821, 4835, 4850 | Mar 6
```

---

### 15.11 Resource Channel Configuration

Human resources the user delegates to also carry channel preferences on their ResourceNode. The agent uses these when constructing Check-in Nodes.

```
ResourceNode.communication: {
  preferred_channel:              string
  fallback_channel:               string
  highest_response_rate_channel:  string    // agent learns from history
  batch_messages:                 boolean
  batch_window_hours:             integer
}
```

If a resource responds via a different channel than the Check-in was sent on, the agent processes the update normally and records the channel shift for future routing.

---

## 16. Organization Workspaces

### 16.1 Core Concept

A single user may have multiple completely isolated domains of work — personal life, professional work, a side project. Each domain is an **Organization Workspace** with its own task graph, resource list, briefing schedule, and channel binding. The user sees them as distinct tracks but the agent manages all of them.

```
UserNode
  |
  |-- HAS_ORG --> OrganizationNode [Personal]
  |-- HAS_ORG --> OrganizationNode [Work]
  `-- HAS_ORG --> OrganizationNode [Side Project]
```

---

### 16.2 Organization Node Schema

```
OrganizationNode {
  id:               ORG-[uuid]
  name:             string        // "Personal", "Work", "Side Project"
  type:             "PERSONAL" | "PROFESSIONAL" | "PASSION" | "CUSTOM"
  owner:            user_id

  // Isolation settings
  isolation: {
    data_isolated:        boolean   // tasks never cross org boundaries
    contact_isolated:     boolean   // resource lists are org-specific
    channel_isolated:     boolean   // briefing uses org-specific channel
  }

  // Per-org briefing schedule
  briefing: {
    channel:        string          // "whatsapp" | "telegram" | "email"
    time:           time
    days:           [string]        // ["MON","TUE","WED","THU","FRI"]
    style:          "concise" | "detailed"
    timezone:       string
  }

  // Visual identity
  color_tag:        string          // for UI distinction
  emoji_tag:        string          // e.g. "personal", "work", "passion"

  // Resource and channel scope
  permitted_resources:    [resource_id]
  permitted_channels:     [string]

  // Skill agents available in this org
  active_skills:          [skill_id]

  created_at:       timestamp
  updated_at:       timestamp
}
```

---

### 16.3 Graph Isolation Model

Tasks are hard-partitioned by organization. No task node can belong to more than one org. The `organization_id` is a partition key on every task, goal, and constraint node.

```
TaskNode.organization_id:       ORG-[uuid]    // hard partition key
GoalNode.organization_id:       ORG-[uuid]
ConstraintNode.organization_id: ORG-[uuid]
```

A resource (human or AI agent) can be a member of multiple organizations but sees only the tasks within each org they are assigned to. They never see tasks from orgs they are not members of.

```
ResourceNode
  |-- MEMBER_OF --> OrganizationNode [Personal]
  |-- MEMBER_OF --> OrganizationNode [Work]
  `-- MEMBER_OF --> OrganizationNode [Side Project]

// Task visibility is always org-scoped, not resource-wide
```

---

### 16.4 Per-Org Briefing Schedule Example

```
07:00  WhatsApp  Personal briefing
  "Good morning! Home life:
   - Contractor appointment confirmed Tuesday
   - School form due Friday
   - Gym: 3/5 sessions this week"

09:00  Telegram  Work briefing
  "Work digest:
   - Q3 launch: 3 decisions needed
   - TSK-4821 blocked - legal review pending
   - 2 milestones completed yesterday"

19:00  Email     Side project check-in
  "Newsletter update:
   - Writing agent completed draft 3
   - Designer delivered banner assets
   - Awaiting your review before Thursday send"
```

---

### 16.5 Unified Cross-Org View (Pull-Based)

The user can request a unified view across all organizations at any time. This is always pull-based — the agent never mixes org content into a single briefing unprompted.

```
User: "Give me the full picture across everything"

Agent: "Unified view across all 3 workspaces:

  Personal  - 3 active, 1 overdue
  Work      - 14 active, 2 decisions needed
  Side Proj - 6 active, all on track

  Top items across all:
  1. [Work]     Legal review blocking checkout launch
  2. [Personal] Contractor deposit due tomorrow
  3. [Side]     Newsletter review needed before Thursday send"
```

---

## 17. Alias System

### 17.1 Purpose

When a user says "follow up with Mike on the API stuff" the orchestrating agent must resolve "Mike" to a specific ResourceNode. The alias system is the bridge between human natural language and the graph's formal identity layer.

Aliases live in two places: on the UserNode (personal dictionary — what *this* user calls things) and on the ResourceNode (canonical registry — what names this resource is known by).

---

### 17.2 Alias Schema

**On UserNode — personal alias dictionary:**

```
UserNode.preferences.aliases: [{
  alias:            string          // "Mike", "the research bot", "dev team"
  resolves_to:      resource_id | [resource_id]   // group aliases supported
  alias_type:       "PERSONAL_NAME" | "NICKNAME" | "ROLE_BASED" | "GROUP"
  org_scope:        [org_id]        // which orgs this alias applies in
  confidence:       float           // how certain the agent is of this mapping
  confirmed_by:     "USER" | "AGENT_INFERRED"
  created_at:       timestamp
  last_used:        timestamp
}]
```

**On ResourceNode — canonical alias registry:**

```
ResourceNode.aliases: [{
  alias:            string
  known_to:         [user_id]       // which users use this alias
  is_canonical:     boolean         // the official display name
  org_scope:        [org_id]
}]
```

---

### 17.3 Resolution Algorithm

```
Input: "follow up with Mike on the API spec"

Step 1: Exact match in UserNode.aliases
  -> Found "Mike" -> RES-michael-chen (confidence: 1.0)
  -> Proceed directly

If no exact match:
  Step 2: Fuzzy match against all UserNode aliases
  Step 3: Fuzzy match against ResourceNode names in active org
  Step 4: Multiple candidates found -> agent asks:
    "Just to confirm - 'Mike': Michael Chen (backend) or
     Mike Torres (design)?"
  Step 5: User confirms -> alias stored, never asked again

If completely unrecognized:
  Step 6: Agent asks:
    "I don't have a 'Mike' in your network.
     Want me to add them? What's their contact?"
  Step 7: New ResourceNode created, alias stored
```

---

### 17.4 Cross-Org Alias Conflict Handling

The same alias may refer to different people in different organizations. The agent resolves using the active org context of the current conversation.

```
UserNode.aliases:
  { alias: "Mike", resolves_to: RES-michael-chen, org_scope: ["ORG-work"] }
  { alias: "Mike", resolves_to: RES-mike-brother, org_scope: ["ORG-personal"] }

During work briefing conversation:
  "Mike" -> RES-michael-chen (work org context)

During personal briefing conversation:
  "Mike" -> RES-mike-brother (personal org context)

Ambiguous context (unified view):
  Agent asks: "Which Mike - Michael from work or your brother?"
```

---

### 17.5 Group Aliases

A user may reference a group of resources with a single alias. The agent resolves it to all members and can fan out tasks or ask for a specific individual.

```
alias: "dev team"
alias_type: GROUP
resolves_to: [RES-agent-backend, RES-agent-frontend, RES-michael-chen]
org_scope: ["ORG-work"]
```

When a task is assigned to a group alias, the agent asks:
```
"'Dev team' refers to 3 people. Assign to all three
 (separate tasks), or a specific member?"
```

---

### 17.6 Passive Alias Learning

The agent builds alias confidence from context without being told explicitly. If the user consistently discusses a backend API task and says "check with Sarah", and only one Sarah is linked to that project, confidence increases automatically. At high confidence the agent acts on the inference. At medium confidence it confirms once and then stores the result.

---

## 18. Skill Agent System

### 18.1 Core Concept

The user can define personal AI agents using a portable, LLM-agnostic SKILL.md file format. When the orchestrating agent identifies a task assigned to the user themselves, it checks the skill registry for a matching agent that can do (or assist with) the work — then invokes it on the user's behalf.

The format is deliberately LLM-provider-agnostic. Skills are defined in markdown, resolved to any configured LLM provider at runtime, and portable across provider switches without modification.

---

### 18.2 SKILL.md File Format

```markdown
---
skill_id:           email-drafter-v1
skill_name:         Email Drafter
version:            1.0
author:             user
llm_provider:       any           # openai | anthropic | google | any
model:              any           # specific model or "any" / "fast" / "best"
trigger_types:      [EMAIL_DRAFT, OUTREACH, FOLLOW_UP_COMPOSE]
output_type:        DRAFT_FOR_REVIEW   # or AUTO_COMPLETE
requires_approval:  true
org_scope:          [ORG-work, ORG-personal]
---

# Email Drafting Agent

## Purpose
Draft professional emails on behalf of the user based on task
context, recipient information, and any relevant history.

## Context to inject
- User name and role:        {user.name}, {user.role}
- Recipient details:         {recipient.name}, {recipient.context}
- Task description:          {task.description}
- Goal context:              {task.goal_context}
- Tone preference:           {user.preferences.communication_tone}
- Previous correspondence:   {recipient.history}

## Instructions
1. Draft a professional email that accomplishes the task objective
2. Match the user's typical writing tone from previous emails
3. Keep subject lines concise and action-oriented
4. Include a clear call to action
5. Flag any assumptions made about content

## Output format
Subject: [subject line]
Body: [email body]
Notes: [assumptions or options for the user to consider]

## Review behavior
Always return draft to user for review before sending.
User can approve as-is, edit, or request a revision with feedback.
```

---

### 18.3 Skill Registry Schema (on UserNode)

```
UserNode.skill_registry: [{
  skill_id:               string
  skill_name:             string
  skill_file_path:        string        // path to SKILL.md
  trigger_types:          [string]
  org_scope:              [org_id]
  llm_config: {
    provider:             string        // resolved at runtime
    model:                string
    api_key_ref:          string        // reference to key store only
  }
  output_type:            "DRAFT_FOR_REVIEW" | "AUTO_COMPLETE"
  created_via:            "MANUAL" | "CONVERSATION" | "SYSTEM_FORK"
  creation_conversation:  thread_id     // if created conversationally
  version_history: [{
    version:    string
    created_at: timestamp
    change:     string
  }]
  usage_count:            integer
  last_used:              timestamp
  avg_quality_score:      float         // from user feedback over time
}]
```

---

### 18.4 Skill File Storage Structure

```
/skills/
  user/                         <- user-defined skills
    email-drafter/
      SKILL.md                  <- current version
      SKILL.v1.0.md             <- archived versions
      SKILL.v1.1.md
    linkedin-post/
      SKILL.md
    research-summarizer/
      SKILL.md
    weekly-review-email/
      SKILL.md
    proposal-writer/
      SKILL.md
  system/                       <- platform-provided default skills
    basic-email-drafter/
      SKILL.md
    meeting-notes/
      SKILL.md
```

User skills always override system skills when both match the same trigger type. Users can fork system skills conversationally.

---

### 18.5 LLM Provider Resolution

The SKILL.md file specifies a provider and model preference. The orchestrating agent resolves this at runtime against the user's configured providers.

```
Skill says: llm_provider: any
  -> Use UserNode.llm_preferences.preferred_provider + model

Skill says: llm_provider: anthropic, model: claude-sonnet-4-6
  -> Use Anthropic Claude Sonnet regardless of user preference

Skill says: llm_provider: any, model: "fast"
  -> Resolve "fast" to the fastest model in user's configured providers

Skill says: llm_provider: any, model: "best"
  -> Resolve "best" to the highest-capability configured model
```

Switching LLM providers at the UserNode level automatically applies to all skills with `llm_provider: any`. Skills with explicit provider declarations are unaffected.

---

### 18.6 Skill Invocation Flow

```
TaskNode [Atomic: "Draft outreach email to Acme Corp"]
  assigned_to: UserNode (self-assigned)
  state: ACTIVE
  |
  v
Orchestrating agent detects:
  Task type matches EMAIL_DRAFT
  UserNode.skill_registry has email-drafter-v1 for EMAIL_DRAFT
  |
  v
Check output_type:
  DRAFT_FOR_REVIEW -> invoke, return draft to user for approval
  AUTO_COMPLETE    -> invoke, complete task automatically
  |
  v
Skill invocation:
  1. Load SKILL.md from skill_file_path
  2. Resolve LLM provider and model
  3. Inject context variables from TaskNode, UserNode, ResourceNode
  4. Call LLM with assembled prompt
  5. Receive output
  |
  v
DRAFT_FOR_REVIEW path:
  Agent presents via preferred channel:
  "I have drafted the Acme Corp outreach email:

   Subject: Partnership opportunity - [Company]
   [body]

   Options:
   [1] Approve and send
   [2] Edit then send
   [3] Request revision - tell me what to change"
  |
  v
User approves -> Task COMPLETE
User edits    -> Agent updates, confirms, marks COMPLETE
User revises  -> Skill re-invoked with feedback, new draft produced
```

---

### 18.7 Skill Agent Chaining via the Graph

When a task requires multiple skill agents in sequence, the graph's sequential chain model handles this natively. Each agent in the chain is a separate Delegated Task node. The orchestrating agent coordinates handoffs using a structured folder system keyed by task ID.

**The Folder Structure:**

```
/workspace/
  tasks/
    TSK-JD-4900-CMP/                  <- Composite task root
      task.md                         <- Goal, context, decomposition
      |
      TSK-JD-4901-DEL/                <- Delegated to Research Agent
        task.md                       <- Assignment briefing
        output.md                     <- Research agent's output
        status.md                     <- Live status (agent writes here)
        artifacts/
          research-notes.md
          sources.md
      |
      TSK-JD-4902-DEL/                <- Delegated to Proposal Writer
        task.md
        context/
          from-TSK-JD-4901.md         <- Assembled by orchestrating agent
        output.md
        status.md
        artifacts/
          proposal-draft-v1.md
          proposal-draft-v2.md
      |
      TSK-JD-4903-DEL/                <- Delegated to Email Drafter
        task.md
        context/
          from-TSK-JD-4901.md         <- Research output
          from-TSK-JD-4902.md         <- Proposal output
        output.md
        status.md
        artifacts/
          email-draft.md
```

**The task.md Briefing File (written by orchestrating agent before each invocation):**

```markdown
---
task_id:        TSK-JD-4902-DEL
task_type:      DELEGATED
skill_agent:    proposal-writer-v1
parent_task:    TSK-JD-4900-CMP
created_by:     ORCHESTRATING_AGENT
created_at:     2025-03-06T09:00:00Z
deadline:       2025-03-07T17:00:00Z
status:         ACTIVE
---

# Task: Write Research Report Proposal

## Objective
Transform the research output into a structured proposal document
suitable for sending to the client.

## Context from upstream agents
- Research output: context/from-TSK-JD-4901.md
- Original goal: Send AI trends report to Acme Corp

## User preferences
- Tone: professional, concise
- Length: 2-3 pages
- Format: executive summary + key findings + recommendations

## Output requirements
- Write output to: output.md
- Save versioned draft to: artifacts/proposal-draft-v1.md
- Flag any research gaps that need addressing

## Completion signal
Write "STATUS: COMPLETE" as the last line of status.md
If blocked write "STATUS: BLOCKED: [reason]"
```

**The status.md File (agent writes, orchestrating agent reads):**

```markdown
---
task_id:    TSK-JD-4902-DEL
agent:      proposal-writer-v1
updated_at: 2025-03-06T09:45:00Z
---

STATUS: IN_PROGRESS
PROGRESS: 60
NOTES: Executive summary complete. Working on key findings.
       No research gaps found.
```

On completion:
```markdown
STATUS: COMPLETE
PROGRESS: 100
OUTPUT: output.md
ARTIFACTS: artifacts/proposal-draft-v2.md
NOTES: v2 used after revision. One assumption flagged in
       recommendations for user review.
```

The `STATUS:` line maps directly to the inbound update protocol status signal taxonomy. The orchestrating agent reads status.md via the same pipeline as any other inbound update — no special handling required.

---

### 18.8 Orchestrating Agent Handoff Logic

When a node in a skill chain completes, the orchestrating agent executes this sequence before activating the next node:

```
Node TSK-JD-4901-DEL completes (research agent done)
  |
  v
1. Read TSK-JD-4901-DEL/output.md
   Verify exists and non-empty
  |
  v
2. Assemble context for next node:
   Create TSK-JD-4902-DEL/context/
   Copy research output ->
     TSK-JD-4902-DEL/context/from-TSK-JD-4901.md
  |
  v
3. Write TSK-JD-4902-DEL/task.md
   Inject: task definition, objective, context paths,
   user preferences, output requirements, completion format
  |
  v
4. Activate TSK-JD-4902-DEL in the graph
   state: INACTIVE_PENDING -> ACTIVE
   DEPENDS_ON edge marked satisfied
  |
  v
5. Invoke proposal-writer-v1 skill agent
   Pass: path to TSK-JD-4902-DEL/ folder
   Agent reads task.md + context/, writes output.md + status.md
  |
  v
6. Monitor status.md via inbound update protocol
```

**Context distillation for long chains (4+ nodes):**

For chains longer than 3 nodes, the orchestrating agent generates a distilled summary to prevent context window overflow:

```
For node N in chain of 4+:
  - Generate chain-summary.md covering key decisions from nodes 1..N-2
  - Always include full output from immediate predecessor (node N-1)
  - Pass both to node N's context/ folder

context/
  chain-summary.md      <- distilled summary of all prior nodes
  from-TSK-[N-1].md     <- full output of immediate predecessor
```

---

### 18.9 Graph Representation of a Skill Chain

```
TaskNode [Composite: "Send AI trends report to Acme Corp"]
  id: TSK-JD-4900-CMP
  breakdown_strategy: SEQUENTIAL
  folder: /workspace/tasks/TSK-JD-4900-CMP/
  |
  |-- [1] TaskNode [Delegated: "Research AI trends"]
  |         id: TSK-JD-4901-DEL
  |         assigned_to: RES-research-agent
  |         state: COMPLETE
  |         output_path: TSK-JD-4901-DEL/output.md
  |
  |-- [2] TaskNode [Delegated: "Write proposal from research"]
  |         id: TSK-JD-4902-DEL
  |         assigned_to: RES-proposal-writer-agent
  |         state: IN_PROGRESS
  |         context_injected: [TSK-JD-4901-DEL/output.md]
  |         DEPENDS_ON -> [1]
  |
  `-- [3] TaskNode [Delegated: "Draft client email"]
            id: TSK-JD-4903-DEL
            assigned_to: RES-email-drafter-agent
            state: INACTIVE_PENDING
            context_injected: [TSK-JD-4901-DEL/output.md,
                               TSK-JD-4902-DEL/output.md]
            DEPENDS_ON -> [2]
```

---

### 18.10 Conversational Skill Creation

The user can define a new skill through natural conversation — no manual SKILL.md authoring required. The orchestrating agent conducts a brief interview and generates the file automatically.

**Trigger 1 — No matching skill found:**
```
Agent detects task type with no matching skill in registry:
  "I don't have a LinkedIn post drafting skill set up yet.
   Want me to create one? I can ask a few quick questions."
```

**Trigger 2 — User explicitly requests:**
```
User: "Set up an agent for writing my weekly review emails"
Agent: "I'll create that skill agent. A few quick questions..."
```

**The guided interview (minimum 5 questions):**

```
Agent: "What should this agent do in one sentence?"
User:  "Draft my weekly team review email summarizing
        what we accomplished."

Agent: "What information should it pull from the graph?
        Completed tasks, milestones, contributor highlights?"
User:  "Completed tasks, any milestones hit, max 3-4 bullets,
        end with next week's focus."

Agent: "Tone - formal, casual, somewhere in between?"
User:  "Casual but professional."

Agent: "Always show you a draft first, or auto-send when ready?"
User:  "Always show me first."

Agent: "Which workspace - work only or all?"
User:  "Work only."
```

After the interview, the agent generates and shows the SKILL.md for confirmation before saving:

```
Agent: "Here is the skill I have built from your answers.
        Say 'looks good' to save it, or tell me what to change."

[displays generated SKILL.md]

User: "Change the tone description and reduce max length to 120 words."
Agent: "Updated. Anything else?"
User: "Looks good."
Agent: "Saved as 'Weekly Review Email' v1.0. I will use it
        every Friday when preparing your work briefing.
        Want me to also set up a Recurring Task for Fridays at 4pm?"
```

---

### 18.11 Conversational Skill Refinement

After a skill has been used, the user can refine it in natural language without opening any file:

```
User: "The weekly review emails are too long lately"

Agent: "I will tighten the length limit. Currently set to 200 words.
        Target?"

User: "120 max, and drop the next-week section - I will add that manually."

Agent: "Done. Saved as v1.1. Changes apply from the next draft."
```

The agent edits the SKILL.md directly, archives the previous version, and bumps the version number with a changelog entry.

---

### 18.12 Proactive Skill Suggestion

The agent proposes creating a skill when it notices repeated manual work of the same type:

```
Agent: "I have noticed you have manually written 4 LinkedIn posts
        over the past 3 weeks for product updates. Want me to create
        a LinkedIn post drafting skill?

        From your previous posts I can already infer:
        - Professional but conversational tone
        - 3 short paragraphs
        - Ends with a question or call to action
        - 2-3 relevant topic tags

        I can build the skill from that pattern now. You would
        just review drafts before posting. Set it up?"
```

---

### 18.13 Skill Quality Feedback Loop

```
After task completion via skill:
  Agent: "The Acme Corp email draft was sent.
          How was the draft quality? (1-5 or 'good'/'needs work')"

User feedback -> updates avg_quality_score on skill registry

avg_quality_score < 3.0:
  Agent suggests reviewing the SKILL.md instructions

avg_quality_score > 4.5 after 5+ uses:
  Agent may recommend switching output_type to AUTO_COMPLETE:
  "Your email drafting skill has been rated 4.8 across 8 uses.
   Want me to auto-complete these tasks without showing you
   drafts each time?"
```

---

### 18.14 Example Skill Library

| Skill | Trigger Types | Output Type |
|-------|--------------|-------------|
| Email Drafter | EMAIL_DRAFT, OUTREACH, FOLLOW_UP_COMPOSE | DRAFT_FOR_REVIEW |
| LinkedIn Post | LINKEDIN_POST, SOCIAL_MEDIA | DRAFT_FOR_REVIEW |
| Research Summarizer | RESEARCH, SUMMARIZE | DRAFT_FOR_REVIEW |
| Meeting Prep | MEETING_PREP, BRIEFING | AUTO_COMPLETE |
| Proposal Writer | PROPOSAL, PITCH | DRAFT_FOR_REVIEW |
| Weekly Review | WEEKLY_REVIEW, EMAIL_DRAFT | DRAFT_FOR_REVIEW |
| Code Reviewer | CODE_REVIEW, TECHNICAL_REVIEW | DRAFT_FOR_REVIEW |
| Social Media | SOCIAL_MEDIA, CONTENT | DRAFT_FOR_REVIEW |


---


---

## 19. Multi-User Graph

### 19.1 Core Model

Multiple human users can operate within the same Organization Workspace. Each user runs their own orchestrating agent instance against a shared graph partition. Agents do not communicate directly — they coordinate through shared graph state.

```
Alice's Agent        Bob's Agent          Carol's Agent
      |                    |                    |
      v                    v                    v
  [Alice's view]     [Bob's view]         [Carol's view]
      |                    |                    |
      `-----------.--------.-----------'
                  |
         SHARED GRAPH DB
         (org-scoped partition)
```

### 19.2 Node Ownership & Role Model

Every node has a single owner. Additional roles grant specific levels of access.

```
TaskNode {
  owner:          user_id         // single owner, full control
  collaborators:  [user_id]       // can modify state, add children
  viewers:        [user_id]       // read-only
  assigned_to:    resource_id     // can update status only
}
```

| Role | Permissions |
|------|-------------|
| OWNER | Full control — create, edit, delete, reassign, reprioritize |
| COLLABORATOR | Update state, add child nodes, add comments |
| VIEWER | Read-only, receive check-ins about this node |
| ASSIGNEE | Update status and progress on assigned task only |

When User A delegates a task to User B, User B receives the `ASSIGNEE` role — not ownership. User A remains owner. User B can update status but cannot reassign, reprioritize, or delete the node.

### 19.3 Organization Member Roles

```
OrganizationNode.members: [
  { user_id: USER-alice,  org_role: "ADMIN"  }
  { user_id: USER-bob,    org_role: "MEMBER" }
  { user_id: USER-carol,  org_role: "MEMBER" }
  { user_id: USER-dave,   org_role: "VIEWER" }
]
```

| Org Role | Capabilities |
|----------|-------------|
| ADMIN | Create/delete goals, manage members, see all tasks, override priorities |
| MEMBER | Create tasks, delegate within org, see owned/assigned tasks |
| VIEWER | Read-only on tasks explicitly shared with them |

### 19.4 Visibility Boundaries

```
ADMIN (Alice):
  Full graph visibility — all goals, milestones, tasks, scoring data,
  team status across all members

MEMBER (Bob, assigned to TSK-4821):
  Sees TSK-4821 and its immediate context
  Does NOT see: other users' goals, other tasks, scoring internals
  Does NOT see: full goal hierarchy unless explicitly shared

VIEWER (Dave):
  Sees only nodes where Dave is listed in the viewers array
```

### 19.5 Conflict Resolution

```
Scenario 1: Two collaborators update the same node simultaneously
  -> Last-write-wins with conflict logged
  -> Both agents notified
  -> Node enters NEEDS_REVIEW state
  -> Owner is the tiebreaker

Scenario 2: Admin reprioritizes a task a Member has overridden
  -> ADMIN role overrides MEMBER
  -> Member's agent notified of the change

Scenario 3: Assignee marks task complete with open child tasks
  -> Task enters NEEDS_REVIEW
  -> Owner's agent surfaces in briefing:
     "Bob marked TSK-4821 complete but it has open child tasks.
      Confirm completion?"

Scenario 4: Two agents schedule a check-in to the same human resource
  -> Check-in batching applies across agents in the same org
  -> One consolidated message from the org's primary agent (ADMIN's)
  -> Not two separate pings to the same person
```

### 19.6 Cross-User Team Awareness in Briefing

The ADMIN user's briefing includes a team status section — read-only awareness, no autonomous action on others' tasks.

```
Alice's Work Briefing:

YOUR TASKS  (3 decisions needed today)
  ...

TEAM STATUS  (awareness only, no action needed)
  Bob:   2 active, 1 delayed — "Auth module" slipping
  Carol: On track across all assignments
  Shared blocker: Legal review (TSK-4899) blocking 3 people
```

Alice's agent does not act on Bob's tasks without Alice explicitly instructing it. Awareness is surfaced; action requires human direction.

---

## 20. Agent-to-Agent Protocol

### 20.1 Two Communication Modes

The protocol supports two modes depending on the sophistication of the reporting agent.

**Mode 1: Structured API/MCP call (preferred)**

```json
POST /api/v1/task-update

{
  "task_id":          "TSK-JD-4821-DEL",
  "agent_id":         "RES-research-agent-01",
  "timestamp":        "2025-03-06T10:30:00Z",
  "status":           "IN_PROGRESS",
  "progress":         75,
  "confidence":       "HIGH",
  "notes":            "Research complete on sections 1-3. Starting conclusions.",
  "blockers":         [],
  "artifacts": [
    {
      "type":         "FILE",
      "path":         "TSK-JD-4821-DEL/artifacts/research-draft.md",
      "label":        "Research draft - sections 1-3"
    }
  ],
  "next_update_eta":  "2025-03-06T14:00:00Z"
}
```

**Mode 2: Natural language via channel (fallback)**

```
"TSK-JD-4821-DEL — Research sections 1-3 done. About 75% through.
 Starting conclusions now. Should have full draft by 2pm."
```

Falls back to the Inbound Update Protocol (Section 8) — vector search if no task ID, standard status signal extraction.

### 20.2 Outbound Delegation Payload

When the orchestrating agent delegates to an AI agent it includes everything needed to report back correctly:

```json
{
  "task_id":            "TSK-JD-4821-DEL",
  "task_description":   "Research AI trends in enterprise software",
  "context_path":       "/workspace/tasks/TSK-JD-4821-DEL/",
  "task_file":          "/workspace/tasks/TSK-JD-4821-DEL/task.md",
  "output_path":        "/workspace/tasks/TSK-JD-4821-DEL/output.md",
  "status_path":        "/workspace/tasks/TSK-JD-4821-DEL/status.md",
  "deadline":           "2025-03-07T17:00:00Z",
  "reporting_endpoint": "https://api.workgraph.app/v1/task-update",
  "reporting_channel":  "api",
  "update_frequency":   "on_milestone",
  "completion_signal":  "STATUS: COMPLETE in status.md OR POST to endpoint"
}
```

### 20.3 Agent Capability Discovery

Before delegating to an AI agent for the first time the orchestrating agent performs a capability check:

```json
GET /api/v1/agent/capabilities

Response:
{
  "agent_id":             "RES-research-agent-01",
  "agent_name":           "Research Agent",
  "version":              "2.1.0",
  "capabilities":         ["web_research", "document_analysis",
                           "summarization", "citation_tracking"],
  "input_formats":        ["text", "url", "file_path"],
  "output_formats":       ["markdown", "json"],
  "max_context_tokens":   200000,
  "reporting_modes":      ["api", "file", "mcp"],
  "protocols":            ["REST", "MCP", "A2A"]
}
```

The capability manifest is stored on the ResourceNode. The orchestrating agent only delegates task types matching an agent's declared capabilities.

### 20.4 Proactive Update Expectations

```
Milestone events that should trigger a proactive update:
  - Significant progress checkpoint (~25% increments on long tasks)
  - Blocker encountered
  - Discovery that changes task scope or timeline
  - Completion

Minimum reporting frequency for tasks > 4 hours:
  - At least one update every 2 hours even if just "still in progress"

Agents that consistently fail to report proactively:
  -> proactive_update_rate score reduced on ResourceNode
  -> follow-up frequency automatically increases for their future tasks
```

### 20.5 Error and Failure States

```
AGENT_TIMEOUT (no update within expected window):
  -> Orchestrating agent sends follow-up via reporting channel
  -> No response in 1 additional window -> escalate to human briefing
  -> After 2 failed follow-ups -> task flagged NEEDS_REASSIGNMENT

AGENT_ERROR (explicit error reported):
  -> { "status": "ERROR", "error": "Context window exceeded" }
  -> Orchestrating agent evaluates: retry / reassign / decompose?
  -> Surfaces options to human if autonomous resolution not possible

AGENT_BLOCKED (dependency or resource issue):
  -> { "status": "BLOCKED", "reason": "Cannot access URL X" }
  -> Orchestrating agent creates blocker node
  -> Attempts autonomous resolution
  -> Escalates to human if unresolvable
```

---

## 21. Graph Storage & Query Patterns

### 21.1 Polyglot Persistence Architecture

Three different query types are required simultaneously — no single database handles all three optimally.

```
.---------------------------------------------------------.
|  PRIMARY: Property Graph DB                            |
|  (Neo4j / Amazon Neptune / Apache AGE over Postgres)   |
|                                                        |
|  Stores: All node and edge data with properties        |
|  Optimized for: Graph traversal, relationship queries, |
|  critical path analysis, dependency resolution         |
|                                                        |
|  Key indexes:                                          |
|  - node_id (primary key, all types)                    |
|  - organization_id (partition key, all task nodes)     |
|  - assigned_to (frequent filter)                       |
|  - state (frequent filter)                             |
|  - deadline (range queries)                            |
`---------------------------.----------------------------'
                             |
.----------------------------v----------------------------.
|  VECTOR INDEX                                          |
|  (pgvector / Pinecone / Weaviate)                      |
|                                                        |
|  Stores: Node embeddings only (1536-dim vectors)       |
|  Keyed by: node_id (joins back to graph DB)            |
|  Optimized for: ANN similarity search for             |
|  inbound update matching                               |
|  Updated: on node creation or significant              |
|  description change                                    |
`---------------------------.----------------------------'
                             |
.----------------------------v----------------------------.
|  TIME-SERIES / OPERATIONAL                             |
|  (Postgres / TimescaleDB)                              |
|                                                        |
|  Stores: state_history, update_log,                    |
|  ConversationThread, ScoreExplanation records,         |
|  behavioral model training data                        |
|  Optimized for: time-range queries, audit trail        |
|  Retention: configurable per org                       |
`---------------------------------------------------------'
```

### 21.2 Critical Path Query Pattern

The most expensive recurring query. Runs on every scoring cycle per active Goal Node.

```
Algorithm: Modified Dijkstra on directed acyclic subgraph

1. From Goal Node, traverse all DEPENDS_ON and PART_OF edges
   downstream to leaf nodes (BFS)

2. For each leaf node, walk back up summing estimated_effort

3. Longest path = critical path

4. Nodes on critical path:
     on_critical_path = true, float = 0

5. All other nodes:
     float = critical_path_length - this_path_length

Cache strategy:
  - Cache critical path result per Goal Node
  - Invalidate ONLY when:
      a. A node on the path changes state
      b. An estimated_effort value changes
      c. A new edge is added to the subgraph
  - Do NOT recompute on every scoring cycle
```

### 21.3 Vector Search Query Pattern

Runs on every inbound update lacking a Task ID.

```
1. Extract key signals from inbound text
   (entity extraction, topic identification)

2. Generate embedding of extracted signals:
   embed(title_signal + entity_signal + topic_signal)

3. ANN search against vector index:
   top_k = 5, similarity_threshold = 0.82

4. For each candidate:
   a. Retrieve full node from graph DB by node_id
   b. Apply filters:
      - Node state is ACTIVE or IN_PROGRESS (not COMPLETE/CANCELLED)
      - Sender resource is linked via ASSIGNED_TO
      - Deadline consistent with urgency signal in text

5. Score candidates:
   vector_similarity * 0.6 +
   sender_match      * 0.3 +
   deadline_match    * 0.1

6. Top scorer above threshold -> match
   Below threshold -> queue for human confirmation
```

### 21.4 Scoring Cache Strategy

```
Per-node cache invalidation triggers:
  - Node state changes
  - Deadline crosses a new urgency threshold bracket
  - Dependent node state changes (invalidates upstream)
  - Human override applied or removed
  - Resource risk signal changes on assigned resource
  - Constraint node pressure score changes

Between invalidations:
  - Serve cached score + ScoreExplanation
  - Only recompute affected nodes, not full graph

Forced full graph rescore:
  - Once per day pre-briefing
  - On explicit user request
  - When a new Goal Node is added to the org
```

---

## 22. Onboarding & Network Growth

### 22.1 Registration Entry Points

```
Entry Point 1: Self-Registration (proactive)
  User discovers the product and signs up directly.
  Standard SaaS flow — account creation followed by
  guided onboarding interview.

Entry Point 2: Task-Triggered Recruitment (viral)
  User A assigns a task to someone not yet in the system.
  Orchestrating agent confirms with User A, then reaches out
  to the new person with the task and a soft onboarding invite.

Entry Point 3: Inbound Reply Recruitment
  Someone outside the system replies to a check-in that
  the orchestrating agent sent. They are now interacting
  with the agent but have no account. Agent recruits them
  naturally in the reply thread.
```

### 22.2 Task-Triggered Recruitment Flow

```
User A: "Assign the API spec review to james@company.com"

Nonna: "James isn't in your network yet. Want me to reach
        out with the task and introduce myself? I can also
        invite him to get his own assistant."

User A: "Yes, go ahead."
  |
  v
Nonna sends outreach email to James (see 22.3)
  |
  v
James receives email with task + soft onboarding invite
  |
  |-- James replies to update task status only
  |     -> Treated as inbound update from PROVISIONAL_RESOURCE
  |     -> Task status updated, no account created
  |
  `-- James clicks [Get your own assistant]
        -> Onboarding flow begins (see 22.5)
        -> New UserNode created
        -> New agent instance provisioned
```

### 22.3 The Recruitment Outreach Email

The outreach email has three jobs: deliver the task clearly, establish the agent's identity, and extend a soft invitation. Task comes first, always. Invite is secondary and clearly separated.

```
From:    Nonna (WorkGraph Agent for Alice Chen) <alice-agent@workgraph.app>
To:      james@company.com
Subject: Task from Alice Chen: API spec review [TSK-AC-0042-DEL]

Hi James,

I'm Nonna, Alice's AI work assistant. Alice has assigned
you a task and asked me to reach out.

─────────────────────────────────────
TASK: API Spec Review
ID:   TSK-AC-0042-DEL
Due:  Friday, March 14
─────────────────────────────────────

Alice would like you to review the API specification and
provide feedback on the authentication flow.
Spec document: [link]

You can reply directly to this email with any updates —
just mention the task ID (TSK-AC-0042-DEL) and I'll make
sure Alice gets your updates right away.

─────────────────────────────────────

As Alice's assistant I help her stay on top of tasks,
follow-ups, and projects. If you'd like the same for
your own work, I can get you set up in about 10 minutes.

[Get your own assistant →]

Happy to answer any questions — just reply to this email.

Nonna
AI Assistant for Alice Chen | workgraph.app
```

### 22.4 User States in the System

A person progresses through states before becoming a full user:

```
EXTERNAL
  -> Known as an email address only
  -> No ResourceNode yet

PROVISIONAL_RESOURCE
  -> ResourceNode created (type: HUMAN)
  -> Has received at least one task or check-in
  -> Can reply via email to update task status
  -> No agent, no account
  -> Reliability tracking begins silently

INVITED
  -> Has clicked the onboarding link
  -> Placeholder UserNode exists (state: PENDING_ONBOARDING)
  -> Onboarding flow in progress

ACTIVE_USER
  -> Full UserNode created and confirmed
  -> Orchestrating agent instance provisioned
  -> Cold start interview completed or in progress
  -> Full graph access for their own workspaces
```

### 22.5 ResourceNode-to-UserNode Upgrade

When a PROVISIONAL_RESOURCE completes onboarding, existing tasks assigned to them seamlessly transfer into their new graph:

```
UserNode (created on signup) {
  id:                   USER-james-[uuid]
  email:                james@company.com
  state:                PENDING_ONBOARDING -> ACTIVE
  provenance: {
    source:             "TASK_RECRUITMENT"
    referred_by:        USER-alice
    referring_task:     TSK-AC-0042-DEL
    onboarded_at:       timestamp
  }
  existing_resource_id: RES-james    // links to pre-existing ResourceNode
}

On ACTIVE:
  ResourceNode.linked_user_id -> USER-james
  All tasks assigned to RES-james automatically appear
  in James's new graph without re-entry
  James's agent begins cold start with pre-seeded context
```

### 22.6 The Four Onboarding Phases

**Phase 1: Identity & Channels (Day 0, ~10 minutes)**

Mechanical setup. Configure channels, verify identities, set organization workspaces, bind channels per org. No AI reasoning required. Covered by settings panel (Section 15.3).

If onboarded via task recruitment, the referring task and the referring user's org context pre-populate some of this.

**Phase 2: Guided Intent Interview (Day 0, ~15 minutes)**

Bootstraps the behavioral model before history exists. Adapted based on whether the user was self-registered or recruited.

```
For recruited users (e.g. James from Alice's task):

Agent: "Welcome James! I'm your new work assistant.
        Alice's API spec review task is already in your list.

        A few quick questions to get properly set up:"

Q1: "What are you working on beyond Alice's task?
     Any other projects or responsibilities?"
    -> Seeds initial Goal Nodes

Q2: "Who do you work with regularly? Names and
     roughly what they do."
    -> Seeds alias dictionary and ResourceNodes

Q3: "Any AI agents or tools I should coordinate with?"
    -> Seeds agent ResourceNodes

Q4: "How do you prefer updates — WhatsApp, Telegram,
     or email for now?"
    -> Sets preferred channel

Q5: "Anything urgent or overdue I should know about?"
    -> Seeds first action queue

For self-registered users (cold start):

Q1: "What are the 2-3 biggest things you are trying to
     accomplish in the next 90 days?"
    -> Creates initial Goal Nodes

Q2: "Who do you work with most regularly?"
    -> Seeds ResourceNodes and aliases

Q3: "Things you need to stay on top of daily or weekly?
     Things that tend to slip without reminders?"
    -> Seeds follow-up cadences and Recurring Tasks

Q4: "Brief updates or full context each time?"
    -> Sets briefing_style

Q5: "Anything active right now I should know about?"
    -> Seeds first wave of task nodes
```

**Phase 3: First-Week Observed Learning (~7 days)**

Agent runs with defaults but asks one learning question per briefing:

```
After Day 1 briefing:
  "How did that feel — too much detail, about right,
   or would you like more?"

After first override:
  "You moved TSK-X above TSK-Y. Should I generally
   prioritize [task type A] over [task type B] for you?"

After first snoozed follow-up:
  "You snoozed the follow-up for [person]. Reliable
   and doesn't need chasing, or was the timing off?"
```

Each answer updates W1–W7 weights and relationship model parameters.

**Phase 4: Continuous Passive Refinement (ongoing)**

After the first two weeks the agent has enough behavioral signal to stop asking explicit learning questions and infer from actions alone.

```
Confidence milestones:
  5  briefing cycles  -> Scoring weights diverge from defaults
  10 briefing cycles  -> Follow-up timing adapts per resource
  20 briefing cycles  -> Agent proactively suggests skill creation
  30 briefing cycles  -> Alias inference reaches high confidence
  60 briefing cycles  -> Full behavioral model calibrated
```

### 22.7 Skill Agent Bootstrapping at Onboarding

At onboarding the system offers a starter set of system skills the user can activate immediately — before writing any custom SKILL.md files:

```
Agent: "A few built-in skill agents are available now:

        Email Drafter   - draft emails from task context
        Meeting Notes   - summarize and structure meetings
        Research Helper - gather and summarize research

        Activate any now, or set up later. You can always
        add custom ones at any time."
```

System skills use `llm_provider: any` and work with whatever LLM the user has configured.

### 22.8 The Network Growth Model

Every task delegation to an external person is a potential recruitment event. Over time this creates a self-reinforcing network:

```
Alice (User A)
  |-- delegates to --> James (recruited, becomes User B)
                           |
                           |-- delegates to --> Sarah (recruited, becomes User C)
                                                    |
                                                    `-- delegates to --> ...
```

Properties of this growth model:

- **Each new user's tasks are pre-seeded at onboarding** — immediate value from day one
- **Each new user has an existing relationship** (the person who delegated to them) — warm entry
- **Agents coordinate through the graph, not directly** — no uncontrolled agent mesh
- **Trust flows from the task relationship** — James's agent gives Alice's agent implicit trust because Alice initiated the connection

### 22.9 Provenance Tracking

```
UserNode.provenance: {
  source:           "SELF_REGISTERED" | "TASK_RECRUITMENT" | "DIRECT_INVITE"
  referred_by:      user_id       // who triggered the outreach
  referring_task:   task_id       // which task triggered it
  onboarded_at:     timestamp
  first_task_id:    task_id       // first task in their graph
}
```

The referral chain is tracked separately from the task graph, enabling network topology analysis and understanding of how the user base grows organically through work.

---

## 23. UX & Interface Design

### 23.1 Three Interface Surfaces

```
Surface 1: Conversational Channel (primary, daily use)
  WhatsApp / Telegram / Email
  -> Daily briefings, quick decisions, task creation, status updates
  -> No app required — agent comes to the user
  -> Design principle: the agent must be fully useful
     without the user ever opening a visual interface

Surface 2: Visual Graph Interface (power use, weekly)
  Web / Mobile app
  -> Review and edit graph structure
  -> Planning sessions and project decomposition
  -> Dependency visualization
  -> Skill agent and settings management
  -> Complement to the channel, not a requirement

Surface 3: Settings Panel (one-time + occasional)
  -> Channel configuration and verification
  -> Organization workspace setup
  -> Skill agent library management
  -> LLM provider configuration
  -> Scoring weight adjustment (power users)
```

### 23.2 Visual Graph Interface — Key Views

```
GOAL VIEW
  -> Zoom out: all goals, milestone progress bars
  -> Entry point for weekly planning session

PROJECT VIEW
  -> One goal expanded, full task tree visible
  -> Critical path highlighted
  -> Sequential chains visually distinct from parallel

MY TASKS VIEW
  -> Flat list: tasks assigned to or owned by me
  -> Sorted by computed priority score
  -> ScoreExplanation visible on hover/tap

RESOURCE VIEW
  -> All tasks grouped by assignee
  -> Capacity bar per resource (load_factor visualization)
  -> At-risk resources highlighted

TIMELINE VIEW
  -> Gantt-style: tasks plotted against deadlines
  -> Critical path in distinct color
  -> Constraint nodes shown as boundary markers
```

### 23.3 Visual Language

```
Node color     = state
  Active       -> blue
  In Progress  -> green
  Blocked      -> red
  Delayed      -> amber
  Complete     -> grey
  Snoozed      -> light grey

Node size      = priority score (larger = higher priority)
Edge thickness = dependency strength (hard vs. soft)
Critical path  = highlighted in accent color
Org color tags = distinguish workspace nodes visually
```

### 23.4 Key Interactions

```
Drag node          -> reassign to different resource
Click node         -> see ScoreExplanation ("why is this ranked here?")
Inline edit        -> update status, deadline, or description in-context
Click empty space  -> create new task node at that level of the hierarchy
Approve/reject     -> pending decisions resolved without leaving the graph
Bulk select        -> multi-task reassign, defer, or priority change
```

### 23.5 Notification Model

```
URGENT INTERRUPT (immediate, any time of day)
  Trigger:    computed_priority > UserNode.interrupt_threshold
  Channel:    preferred channel push notification
  Content:    single item, clear decision or action required
  Frequency:  max 2 per day (prevents alert fatigue)

SCHEDULED BRIEFING (daily, per org)
  Trigger:    time-based per OrganizationNode.briefing.time
  Channel:    org-bound channel
  Content:    full 5-section briefing (Section 12)
  Frequency:  per org schedule (daily or weekdays only)

MILESTONE ALERT (event-based)
  Trigger:    Milestone or Goal Node completion
  Channel:    preferred channel
  Content:    brief acknowledgment + next milestone preview
  Action:     none required

FOLLOW-UP PERMISSION REQUEST (event-based)
  Trigger:    agent about to send a follow-up to a human
              that requires explicit permission
  Channel:    preferred channel
  Content:    draft message + approve / edit / cancel
  Expiry:     if no response in 2 hours, held for next briefing
```

### 23.6 Mobile vs. Desktop Considerations

```
Mobile (primary for conversational + quick decisions)
  - Conversational channel is natively mobile (WhatsApp/Telegram)
  - Visual app optimized for MY TASKS VIEW
  - Quick approve/snooze/delegate gestures
  - Voice note support for longer updates (future)

Desktop (primary for planning and graph editing)
  - Full graph visualization with zoom and pan
  - Multi-task bulk operations
  - Skill agent management and SKILL.md editing
  - Side-by-side: graph view + conversation thread

Sync principle: all actions on any surface immediately
reflect in the graph. The conversational agent always
has current state regardless of where the last action
happened.
```

---


---

## 24. Real-World Scenario Validation

This section stress-tests the design against four real-world use cases, documenting how the system handles each and what gaps were surfaced. The gaps are formally resolved in Section 25.

---

### 24.1 Scenario 1 — Boss-Assigned Work with Team Follow-up and Review

**Context:** Boss assigns you a deliverable. You delegate pieces to team members, track their completion, review their output, then send the consolidated result back to your boss.

#### Graph Structure

```
GoalNode: "Complete deliverable for Boss"
  owner: YOU
  |
  |-- ConstraintNode: "Boss deadline: Friday"
  |
  |-- TaskNode [Delegated: "Boss assigned work to you"]
  |     id: TSK-YOU-001-DEL
  |     assigned_to: YOU (from Boss)
  |     |
  |     `-- FollowUp: "Deliver completed work to Boss"
  |           scheduled_fire_at: Thursday (day before deadline)
  |
  |-- TaskNode [Composite: "Team execution"]
  |     breakdown_strategy: PARALLEL
  |     |
  |     |-- TaskNode [Delegated: "Member A piece"]
  |     |     id: TSK-YOU-002-DEL
  |     |     assigned_to: RES-member-a
  |     |     `-- FollowUp: check on Member A
  |     |
  |     |-- TaskNode [Delegated: "Member B piece"]
  |     |     id: TSK-YOU-003-DEL
  |     |     assigned_to: RES-member-b
  |     |     `-- FollowUp: check on Member B
  |     |
  |     `-- TaskNode [Delegated: "Member C piece"]
  |           id: TSK-YOU-004-DEL
  |           assigned_to: RES-member-c
  |           `-- FollowUp: check on Member C
  |
  `-- TaskNode [Review: "Review all team outputs"]
        id: TSK-YOU-005-RVW
        assigned_to: YOU
        state: INACTIVE_PENDING
        DEPENDS_ON -> Composite [Team execution] (AND gate)
        artifacts_trigger: true   // activates on artifact receipt, not just status
        |
        `-- TaskNode [Delegated: "Send reviewed work to Boss"]
              id: TSK-YOU-006-DEL
              state: INACTIVE_PENDING
              DEPENDS_ON -> Review task
```

#### How the Agent Behaves

**On creation:** Agent decomposes the structure above, confirms with you before committing, sends outbound task assignments to Members A, B, C with task IDs embedded in each message.

**During execution:** Agent monitors the three parallel delegated tasks independently. Batched check-ins are sent to members who go quiet — not separate pings if multiple members need following up at the same time. Critical path analysis keeps the Friday constraint pressure propagating backward through all member tasks, elevating their priority scores as the week progresses.

**Completion cascade:** When all three member tasks complete and deliverables are submitted, the AND gate triggers, the Review task activates, and your briefing surfaces:

```
Nonna: "All three team members have delivered.
        TSK-YOU-005 is ready for your review.
        Member A and C delivered on time.
        Member B was 1 day late — noted.
        Once you complete the review I will prepare
        the delivery message to your boss."
```

**After your review:** You mark the review complete. The "Send to Boss" task activates. If you have an email drafter skill, it composes the delivery message using team outputs as context. You approve and send. The original delegated task from your boss is marked complete — his follow-up child on his own graph receives a proactive update and closes automatically.

#### Design Gaps Surfaced
- **Gap 1:** No explicit mechanism for artifact submission to auto-trigger Review task state transition. Currently requires a manual status update. Resolved in Section 25.1.

---

### 24.2 Scenario 2 — Program with Multiple Projects and PMs

**Context:** You own a program with two projects. Each project has a PM leading a team of 5. You need periodic status reviews across both projects at defined intervals without being in the detail.

#### Graph Structure

```
GoalNode: "Program delivery"
  owner: YOU
  priority: P1
  |
  |-- TaskNode [Milestone: "Week 4 Program Review"]
  |-- TaskNode [Milestone: "Week 8 Program Review"]
  |-- TaskNode [Milestone: "Program Complete"]
  |
  |-- GoalNode: "Project A"
  |     owner: YOU (management delegated to PM-A)
  |     program_visibility: true   // PM-A grants YOU viewer access to
  |                                // Goal + Milestone nodes only
  |     |
  |     |-- ConstraintNode: "Project A deadline"
  |     |
  |     |-- TaskNode [Delegated: "PM-A: Lead Project A"]
  |     |     assigned_to: RES-pm-a
  |     |     `-- FollowUp: weekly recurring every Monday
  |     |
  |     `-- TaskNode [Composite: "Project A workstream"]
  |           |-- TaskNode [Delegated] -> RES-member-a1
  |           |-- TaskNode [Delegated] -> RES-member-a2
  |           |-- TaskNode [Delegated] -> RES-member-a3
  |           |-- TaskNode [Delegated] -> RES-member-a4
  |           `-- TaskNode [Delegated] -> RES-member-a5
  |
  `-- GoalNode: "Project B"
        owner: YOU (management delegated to PM-B)
        program_visibility: true
        |
        |-- ConstraintNode: "Project B deadline"
        |
        |-- TaskNode [Delegated: "PM-B: Lead Project B"]
        |     assigned_to: RES-pm-b
        |     `-- FollowUp: weekly recurring every Monday
        |
        `-- TaskNode [Composite: "Project B workstream"]
              `-- (5 member tasks, same pattern)
```

#### Visibility Boundary Model

You own the GoalNodes but are not a collaborator on individual member task nodes. Your agent reads only milestone and goal-level progress. PM-A and PM-B manage their team detail — their noise never reaches you unless a Milestone is at risk or a PM explicitly escalates.

```
YOUR agent sees:
  -> Program goal progress (aggregated from milestone nodes)
  -> PM-A and PM-B delegated task status (one level down only)
  -> Milestone health directly from the graph (via program_visibility)
  -> Escalations flagged by either PM

PM-A's agent sees:
  -> Full Project A task graph
  -> Team of 5 task detail
  -> Reports to YOU via status on their delegated task node
```

#### Periodic Program Review

At each defined interval a structured check-in fires to both PMs simultaneously (batched into one scheduling event, not two):

```
CheckinNode [Week 4 Program Review]
  target: [RES-pm-a, RES-pm-b]
  outbound_message:
    "Week 4 program review. Please provide:
     1. RAG status (Red / Amber / Green)
     2. Key achievements this fortnight
     3. Risks or blockers needing program-level attention
     4. Forecast: on track for deadline?
     Ref: TSK-PROG-RVW-W4"
```

Both PMs reply. Your agent aggregates into a consolidated program briefing:

```
Nonna: "Week 4 Program Review:

  Project A (PM-A): GREEN
    Achievements: Auth module complete, API 80% done
    Risks: None
    Forecast: On track

  Project B (PM-B): AMBER
    Achievements: Design phase complete
    Risks: Resource constraint — one member out sick
    Forecast: At risk for milestone 2

  Recommended: Schedule focused sync with PM-B
  re: resource gap. Want me to arrange that?"
```

Ten team members' individual task detail never surfaces to you unless PM-B escalates it explicitly.

#### Design Gaps Surfaced
- **Gap 2:** PMs need to grant program owner VIEWER access to Goal and Milestone nodes without exposing individual task detail. Org-level roles alone do not cover cross-user node-level sharing between non-org-members. Resolved in Section 25.2.

---

### 24.3 Scenario 3 — Personal Podcast Channel Project

**Context:** A personal passion project. Proactive LinkedIn outreach to guest prospects. Each confirmed guest spawns a sequential episode mini-project: research → script → guest approval → shoot → edit → article → publish.

#### Graph Structure

```
GoalNode: "Podcast Channel"
  owner: YOU
  org: ORG-passion-project
  |
  |-- ConstraintNode: "Publish cadence: 2 episodes per month"
  |
  |-- GoalNode: "Guest Pipeline"
  |     |
  |     |-- RecurringTask: "Weekly LinkedIn outreach batch"
  |     |     recurrence: every Monday
  |     |     skill: linkedin-outreach-v1
  |     |
  |     |-- TaskNode [Research: "Identify prospects this week"]
  |     |     assigned_to: RES-research-agent
  |     |
  |     `-- [Per prospect]
  |           TaskNode [Delegated: "Outreach to [Name]"]
  |             `-- FollowUp: if no response in 5 days
  |             `-- On acceptance: spawn Episode Composite (below)
  |
  `-- GoalNode: "Episode Pipeline"
        |
        `-- TaskNode [Composite: "Episode — [Guest Name]"]
              breakdown_strategy: SEQUENTIAL
              |
              |-- [1] Research: "Pre-interview research on guest"
              |         assigned_to: RES-research-agent
              |
              |-- [2] Delegated: "Script development"
              |         assigned_to: YOU (writing skill agent drafts)
              |         DEPENDS_ON -> [1]
              |
              |-- [3] Approval: "Guest reviews script"
              |         assigned_to: RES-guest
              |         DEPENDS_ON -> [2]
              |
              |-- [4] Atomic: "Shoot episode"
              |         assigned_to: YOU
              |         DEPENDS_ON -> [3]
              |
              |-- [5] Delegated: "Edit episode"
              |         assigned_to: RES-editor-vendor
              |         DEPENDS_ON -> [4]
              |
              |-- [6] Review: "Review edit"
              |         assigned_to: YOU
              |         DEPENDS_ON -> [5]
              |
              |-- [7] Delegated: "Write companion article"
              |         assigned_to: YOU (writing skill agent)
              |         DEPENDS_ON -> [4]  // parallel with edit
              |
              `-- [8] Atomic: "Publish episode + article"
                        assigned_to: YOU
                        DEPENDS_ON -> [6] AND [7]  // AND gate
```

#### Guest Acceptance Trigger

When a prospect replies accepting the invitation, the inbound update closes the outreach task and automatically spawns the episode subgraph:

```
Inbound: "Yes I'd love to join your podcast"
  -> Matched to outreach task node for this prospect
  -> Status: COMPLETE (accepted)
  -> Agent creates Episode Composite Task
  -> Spawns full sequential chain (steps 1-8)
  -> Creates ResourceNode for guest
  -> Activates step [1] research immediately
  -> Notifies you:
     "Sarah Kim accepted! Episode workflow is live.
      Research agent is starting background work on
      Sarah now. Script will be ready for you once
      research completes."
```

#### Vendor Reliability Tracking

The editor vendor is a human ResourceNode. After multiple episodes the agent builds a reliability picture:

```
After 3 episodes, RES-editor.reliability.on_time_delivery_rate: 0.67

Nonna: "Your editor has delivered late on 2 of 3 episodes.
        Current edit is due Friday. Want me to send an
        earlier check-in given the track record?"
```

#### Design Gaps Surfaced
- **Gap 3:** Prospect outreach generates large numbers of nodes that mostly end in no-response or decline. No explicit archival mechanic exists for nodes that are definitively inactive. Without it, the active graph fills with dead-end prospect nodes that pollute scoring and briefings. Resolved in Section 25.3.

---

### 24.4 Scenario 4 — Business Development & Network Outreach

**Context:** You reach out to business contacts on your boss's behalf to find prospective customers. Meetings to schedule, notes to draft, follow-ups to plan post-meeting, weekly pipeline report back to boss.

#### Graph Structure

```
GoalNode: "BD Pipeline — Q1"
  owner: YOU
  org: ORG-work
  priority: P1
  |
  |-- ConstraintNode: "Weekly pipeline report to Boss: every Friday"
  |
  |-- RecurringTask: "Weekly pipeline report"
  |     recurrence: every Friday
  |     skill: pipeline-report-v1
  |
  `-- GoalNode: "Prospect Outreach Pipeline"
        |
        `-- TaskNode [Composite: "Prospect: [Contact Name]"]
              breakdown_strategy: SEQUENTIAL
              |
              |-- [1] Research: "Background on [Contact]"
              |         assigned_to: RES-research-agent
              |
              |-- [2] Delegated: "Initial outreach"
              |         assigned_to: YOU (skill drafts message)
              |         DEPENDS_ON -> [1]
              |         `-- FollowUp: 5 days if no reply
              |
              |-- [3] Decision: "Contact response"
              |         DEPENDS_ON -> [2]
              |         options:
              |           "Interested"      -> activates [4] Meeting
              |           "Not now"         -> activates Recurring: re-engage in 60 days
              |           "Not interested"  -> node archived
              |           "No response x3"  -> node archived (Gap 3 mechanic)
              |
              |-- [4] Composite: "Meeting with [Contact]"
              |         DEPENDS_ON -> [3] = "Interested"
              |         |
              |         |-- [4a] Atomic: "Schedule meeting"
              |         |          assigned_to: YOU
              |         |
              |         |-- [4b] Delegated: "Draft meeting prep brief"
              |         |          assigned_to: RES-research-agent
              |         |          DEPENDS_ON -> [4a]
              |         |
              |         |-- [4c] Atomic: "Conduct meeting"
              |         |          assigned_to: YOU
              |         |          DEPENDS_ON -> [4a], [4b]
              |         |
              |         `-- [4d] Delegated: "Draft meeting notes"
              |                    assigned_to: RES-notes-agent (skill)
              |                    DEPENDS_ON -> [4c]
              |
              `-- [5] Composite: "Post-meeting follow-up"
                        DEPENDS_ON -> [4d]
                        |
                        |-- [5a] Delegated: "Draft follow-up email"
                        |          assigned_to: RES-email-drafter (skill)
                        |          context: meeting notes from [4d]
                        |
                        |-- [5b] Approval: "Review + send follow-up"
                        |          assigned_to: YOU
                        |          DEPENDS_ON -> [5a]
                        |
                        `-- [5c] Decision: "Next step"
                                   options: ["Qualify further",
                                             "Introduce to Boss",
                                             "Send proposal",
                                             "Not a fit"]
                                   DEPENDS_ON -> [5b]
```

#### Weekly Pipeline Report to Boss

The recurring report task fires every Friday. The pipeline-report-v1 skill agent reads all prospect composite nodes, aggregates stage distribution and activity, flags prospects ready to introduce to the boss, and drafts a report for your review:

```
Nonna (Friday 4pm):
  "Weekly pipeline report ready:

   12 prospects total:
   - 4 in initial outreach
   - 3 awaiting meeting
   - 2 post-meeting follow-up in progress
   - 1 ready to introduce to Boss (David Park)
   - 1 cold after 3 attempts — recommend archive

   [Approve and send to Boss] [Edit] [Revise]"
```

#### The Boss Reporting Loop

Your boss is a ResourceNode in your work org who assigned this goal to you. When you send the weekly report, his agent receives the inbound update, updates his delegated task to you, and closes or reschedules his follow-up — automatically. Both graphs stay in sync through the inbound update protocol without either party manually updating the other.

```
Boss's graph:                        Your graph:
  Delegated: "BD outreach"  ------>  Task assigned to YOU
    FollowUp: weekly update               |
         ^                               v
         |                       RecurringTask: pipeline report
         `------- receives -----  sent to Boss every Friday
```

#### Meeting Notes to Follow-up Flow

After a meeting (step 4c completes), the notes skill agent activates automatically. It reads the meeting prep brief from step 4b as context, incorporates any voice notes or text you send, and produces structured meeting notes (pain points, proposed solutions, agreed next steps, action items). This output flows directly into the email drafter at step 5a as primary context — the follow-up email is grounded in what was actually discussed, not a generic template.

#### Scaling Across Many Prospects

With 20-30 prospects in various stages, the agent manages scale through batching and priority scoring:

- Outreach messages for a batch of new prospects are drafted together for bulk review, not one at a time
- Prospects who responded positively rank higher in the scoring model than cold ones
- After 3 unreplied follow-ups the node is auto-proposed for archiving (Gap 3 mechanic)

---

### 24.5 Cross-Scenario Observations

**Patterns that hold across all four scenarios:**

The graph decomposition handles all four levels of complexity — a 3-person team task, a 10-person program, a solo podcast project, and a 30-prospect BD pipeline — using the same node types and edge semantics without any special cases.

The skill agent pattern is load-bearing in every scenario. Research, note-taking, outreach drafting, report writing, and pipeline summarization all recur across scenarios. Configured once as SKILL.md files, they activate automatically on matching trigger types.

Batching is essential for Scenarios 3 and 4. Without it the user receives individual pings for every prospect or every episode stage — the system becomes noisier than the problem it solves.

The boss/program-owner reporting loop in Scenarios 1, 2, and 4 works symmetrically — delegator and delegatee graphs stay in sync through the inbound update protocol without manual cross-graph updates.

**Three genuine design gaps identified:**

All three gaps are formally resolved in Section 25.

---

## 25. Design Gap Resolutions

Three design gaps were surfaced through real-world scenario validation. Each is resolved here with schema additions and behavioral specifications.

---

### 25.1 Gap 1 — Artifact Submission Triggering State Transition

**Problem:** When a team member submits a deliverable (a document, file, or output), there is currently no mechanism for that artifact receipt to automatically trigger the downstream Review task's activation. The design requires a manual status update, creating friction and a potential dropped handoff.

**Resolution: TaskNode Artifact Schema + Submission Trigger**

Add an `artifacts` array to TaskNode and an `artifact_policy` field that defines what happens when an artifact is received.

```
TaskNode {
  ...
  artifacts: [{
    artifact_id:      string
    artifact_type:    "FILE" | "DOCUMENT" | "URL" | "TEXT_OUTPUT" | "VOICE_NOTE"
    label:            string          // "Q3 Report Draft", "Research Output"
    submitted_by:     resource_id
    submitted_at:     timestamp
    storage_path:     string          // path in task folder structure
    review_required:  boolean
    reviewed_by:      user_id
    reviewed_at:      timestamp
    review_outcome:   "APPROVED" | "REVISION_REQUESTED" | "REJECTED"
    revision_notes:   string
  }]

  artifact_policy: {
    on_artifact_received:   "NOTIFY_ONLY"
                          | "TRIGGER_REVIEW_TASK"
                          | "AUTO_ADVANCE_IF_APPROVED"
    required_artifact_types: [string]   // gate completion on specific types
    min_artifacts_required:  integer    // e.g. 1 = at least one submission needed
  }
}
```

**Behavior:**

```
Member B submits deliverable:
  Sends message: "TSK-YOU-003-DEL done — here is my report [attachment]"
  |
  v
Inbound update protocol:
  Extracts: STATUS=COMPLETE, artifact detected (attachment)
  Creates artifact record on TSK-YOU-003-DEL
  Stores file to: /workspace/tasks/TSK-YOU-003-DEL/artifacts/
  |
  v
artifact_policy.on_artifact_received = TRIGGER_REVIEW_TASK:
  Checks: all sibling tasks in Composite complete? (AND gate)
  If yes -> Review task state: INACTIVE_PENDING -> ACTIVE
  |
  v
Nonna notifies you:
  "Member B submitted their deliverable. All three team
   members have now delivered. TSK-YOU-005 (Review) is
   ready for you. I've stored all three outputs in the
   task folder."
```

**Review task sees all artifacts:**

When you are working on the Review task, the agent presents all submitted artifacts from the child tasks as context, assembled from their task folders. You do not need to hunt for them.

**Revision loop:**

```
You request revision:
  review_outcome: REVISION_REQUESTED
  revision_notes: "Section 3 needs more detail"
  |
  v
Member task state: COMPLETE -> IN_PROGRESS (reopened)
Member notified via their channel with your notes
New artifact submission resets the trigger
```

---

### 25.2 Gap 2 — Cross-User Node-Level Visibility Permission

**Problem:** In the program management scenario, the program owner needs to read Project A and Project B's Goal and Milestone nodes directly from the graph to get an accurate picture of project health — not just rely on PM self-reporting. But PM-A and PM-B are users managing their own graphs within (potentially separate) org contexts. Org-level roles alone do not support node-level sharing across users who are not both members of the same org.

**Resolution: Node-Level Visibility Grant**

Add a `visibility_grants` array to GoalNode and MilestoneNode that allows a node owner to explicitly grant read access to specific users or roles, independent of org membership.

```
GoalNode {
  ...
  visibility_grants: [{
    granted_to:       user_id | org_role
    granted_by:       user_id
    granted_at:       timestamp
    access_level:     "GOAL_AND_MILESTONES"  // read Goal + Milestone nodes only
                    | "FULL_SUBTREE"         // read entire task subgraph (for PMs reporting up)
                    | "MILESTONE_ONLY"       // read Milestone nodes only
    expires_at:       timestamp | null       // null = indefinite
    purpose:          string                 // "Program visibility for [name]"
  }]
}
```

**Behavior:**

```
PM-A (Project A owner) grants program visibility:

  PM-A: "Grant [YOU] visibility on Project A goals
         and milestones"

  PM-A's agent:
    Adds visibility_grant to GoalNode [Project A]:
      granted_to: USER-you
      access_level: GOAL_AND_MILESTONES

YOUR agent can now:
  -> Read GoalNode [Project A] progress directly
  -> Read all MilestoneNode states under Project A
  -> See on_critical_path flags and constraint pressure
  -> NOT read individual TaskNode detail (not granted)
  -> NOT see PM-A's resource assignments or scoring data
```

**Program review consolidation:**

With visibility grants in place, your agent can assemble the program review status directly from the graph rather than solely from PM check-in responses. The check-in to PMs still happens for qualitative context (risks, blockers, forecast commentary), but the objective milestone data comes from the graph directly — no reliance on self-reporting for the health indicators.

```
Your agent's program view assembly:
  1. Read GoalNode [Project A] progress (direct graph read)
  2. Read GoalNode [Project B] progress (direct graph read)
  3. Read all Milestone states under each project (direct)
  4. Send check-in to PM-A and PM-B for qualitative commentary
  5. Merge: objective graph data + PM qualitative input
  6. Produce consolidated program briefing
```

**Visibility grant propagation rule:**

A visibility grant on a GoalNode does not automatically cascade to child GoalNodes. Each level of the hierarchy requires an explicit grant. This ensures PMs can share top-level program visibility without inadvertently exposing sub-project detail they may be managing for other stakeholders.

---

### 25.3 Gap 3 — Prospect and Contact Lifecycle Archival

**Problem:** Both the podcast outreach (Scenario 3) and BD pipeline (Scenario 4) generate large numbers of prospect nodes representing people who are mostly unreachable, uninterested, or not yet ready. Without an explicit archival mechanic, these nodes accumulate in the active graph, polluting the priority scoring cycle, inflating the active task count, and degrading briefing quality.

**Resolution: Prospect Lifecycle State + Archival Policy**

Add two new task states and a configurable archival policy to TaskNode.

**New task states:**

```
TaskNode.state additions:
  "ARCHIVED"          // definitively inactive, removed from scoring
  "RE_ENGAGE_LATER"   // snoozed with a future reactivation date
```

**Archival policy on TaskNode:**

```
TaskNode {
  ...
  archival_policy: {
    enabled:                  boolean
    auto_archive_after_attempts: integer  // default: 3
    auto_archive_after_days:  integer     // default: 30
    re_engage_option:         boolean     // offer "try again in N days" vs hard archive
    re_engage_after_days:     integer     // default: 60
  }
}
```

**Archival behavior:**

```
Prospect node: 3 unreplied follow-ups, 30 days elapsed
  |
  v
Agent evaluates archival_policy:
  auto_archive_after_attempts: 3 -> threshold reached
  |
  v
Agent proposes in briefing (not autonomous — always surfaces to human):
  "5 prospects have not responded after 3 follow-up attempts:
   - Contact A (last attempt: 18 days ago)
   - Contact B (last attempt: 22 days ago)
   - Contact C (last attempt: 31 days ago)
   - Contact D (last attempt: 14 days ago)
   - Contact E (last attempt: 28 days ago)

   For each: [Archive] [Re-engage in 60 days] [Keep active]"

User responds with batch decision (one reply, not five)
  |
  v
Archived nodes:
  state: ARCHIVED
  Removed from active scoring cycle
  Removed from briefing active task count
  Retained in graph history (searchable, reportable)
  ResourceNode for contact retained (not deleted)

Re-engage nodes:
  state: RE_ENGAGE_LATER
  reactivation_date: now + re_engage_after_days
  Dormant until reactivation date
  On reactivation: state -> ACTIVE, new outreach task spawned
  Agent notifies: "[Contact] is back in the pipeline — ready
                   for fresh outreach. Draft message?"
```

**Archival vs. deletion:**

Archived nodes are never deleted. They are excluded from the active scoring cycle and daily briefing active counts, but remain fully queryable. This matters for:
- Historical reporting ("how many prospects did we contact in Q1?")
- Re-engagement decisions ("what did we say to this person last time?")
- Network map integrity (the ResourceNode and relationship edges are preserved)

**Bulk archival for pipelines:**

For BD and podcast scenarios managing 20-50 prospects simultaneously, the agent can propose a batch archival sweep:

```
Nonna: "Pipeline hygiene check:
        8 prospects meet the archival threshold.
        Want to review them now or handle in the weekly
        pipeline report on Friday?"
```

Archival decisions are batched into one interaction, not surfaced one at a time.

**Program-level archival reporting:**

For the program owner scenario, archival also applies to completed projects. Once a project GoalNode is marked complete, the agent proposes archiving the full project subgraph after a configurable retention window — keeping the active graph focused on live work.

```
GoalNode archival_policy: {
  archive_after_completion_days: 30   // move to archived after 30 days post-completion
  retain_milestones_in_history: true  // keep milestone record even when archived
}
```

---

### 25.4 Summary of Schema Changes

The three gap resolutions require the following additions to existing schemas:

| Schema | Addition | Purpose |
|--------|----------|---------|
| TaskNode | `artifacts[]` array | Store submitted deliverables with metadata |
| TaskNode | `artifact_policy` object | Define behavior on artifact receipt |
| TaskNode | `state` additions: ARCHIVED, RE_ENGAGE_LATER | Prospect and contact lifecycle |
| TaskNode | `archival_policy` object | Configurable auto-archival thresholds |
| GoalNode | `visibility_grants[]` array | Cross-user node-level read access |
| GoalNode | `archival_policy` object | Post-completion subgraph archival |
| MilestoneNode | `visibility_grants[]` array | Same cross-user visibility model |

These additions are additive — no existing schemas are modified or broken. All new fields are optional with sensible defaults.


---

## 26. Architecture: Orchestrating Agent

### 26.1 Core Architecture Principle

The orchestrating agent is not a running process. It is a **stateless reasoning engine paired with a durable file system**. The MD files ARE the agent's persistent brain. When the agent reasons, it reads relevant files into its context window progressively. When it learns, it writes back to those files. When the system restarts or a session fails, the agent reconstitutes itself entirely from the file system — no state is ever lost because no state lives only in memory.

```
What the agent IS:
  A stateless LLM call + a structured MD file system
  that together produce stateful, persistent behavior

What the agent is NOT:
  A long-running process holding state in memory
  A database-backed session object
  A single monolithic prompt
```

---

### 26.2 Agent File System — Folder Structure

Each user gets their own isolated agent instance directory. All agent state, identity, memory, and configuration lives here.

```
/agents/
  USER-[id]/
    main.md                   <- entry point, manifest, loading protocol
    |
    core/
      soul.md                 <- immutable identity, values, non-negotiables
      persona.md              <- name, tone, communication style (mutable)
      memory.md               <- episodic memory, key learned facts
      skills.md               <- skill agent registry
      assets.md               <- tools, channels, LLM configuration
      reference.md            <- org context, glossary, standing instructions
    |
    state/
      heartbeat.md            <- last active timestamp, health, recovery flags
      context.md              <- active session checkpoint (progressive)
      queue.md                <- pending actions awaiting processing
      locks.md                <- active thread locks (concurrency control)
    |
    user/
      profile.md              <- identity, preferences, working style
      behavioral.md           <- learned scoring weights, patterns, history
      aliases.md              <- alias dictionary
      relationships.md        <- resource trust and reliability models
    |
    graph/
      active_goals.md         <- current goal nodes (lightweight summary)
      critical_path.md        <- cached critical path per goal
      pending_actions.md      <- action queue from last scoring cycle
      briefing_draft.md       <- assembled briefing before sending
    |
    channels/
      config.md               <- channel identities and configuration
      thread.md               <- recent conversation thread (last N messages)
      pending_responses.md    <- messages sent awaiting reply
    |
    orgs/
      [ORG-id].md             <- per-org context, briefing schedule, members
    |
    log/
      decisions.md            <- agent decision log with reasoning
      overrides.md            <- human override history
      learning.md             <- weight adjustment history
```

---

### 26.3 File Specifications

#### main.md — Entry Point and Loading Manifest

First file loaded on every agent invocation. Defines the agent's identity, what files exist, and the progressive loading protocol. Tells the LLM exactly what to load for each trigger type.

```markdown
---
agent_id:         AGT-USER-john-doe
agent_name:       Nonna
user_id:          USER-john-doe
instance_version: 1.0.0
last_active:      2025-03-06T09:00:00Z
llm_primary:      anthropic/claude-opus-4
---

# Orchestrating Agent — Nonna

You are Nonna, the orchestrating work agent for John Doe.
Your purpose, identity, and operating instructions are defined
in the files below. Load them in the order specified.

## Mandatory load on every invocation
- core/soul.md            (who you are — always loaded first)
- core/persona.md         (how you communicate)
- state/heartbeat.md      (current health and status)
- state/context.md        (what was happening last session)
- state/queue.md          (what is pending right now)

## Load based on trigger type
- INBOUND_MESSAGE:        channels/thread.md,
                          channels/pending_responses.md
- SCHEDULED_BRIEFING:     graph/active_goals.md,
                          graph/pending_actions.md,
                          graph/briefing_draft.md
- FOLLOWUP_TRIGGER:       graph/pending_actions.md,
                          user/relationships.md
- GRAPH_STATE_CHANGE:     graph/active_goals.md,
                          graph/critical_path.md

## Load on demand (only when needed for current reasoning)
- user/profile.md         (when personalizing a response)
- user/behavioral.md      (when scoring or learning)
- user/aliases.md         (when resolving a name or entity)
- user/relationships.md   (when evaluating a resource)
- core/reference.md       (when org context is needed)
- core/assets.md          (when invoking a tool or channel)
- core/skills.md          (when matching a task to a skill agent)

## Never load unless explicitly instructed
- log/decisions.md        (large — load only for audit or explain)
- log/learning.md         (load only for behavioral review)

## Progressive loading rule
Load only what the trigger requires. If context window
pressure is detected mid-reasoning (>70% utilized),
checkpoint current state to state/context.md, compress
files no longer actively needed, then continue.
Always write final state back to relevant files before
the invocation ends.
```

---

#### soul.md — Immutable Identity

Written once at agent creation during onboarding. Never modified by learning, user preference, or persona customization. These are the agent's non-negotiable operating constraints.

```markdown
---
file: core/soul.md
mutable: false
written_at: [onboarding timestamp]
---

# Soul — Core Identity

## What I am
I am an orchestrating work agent. My purpose is to manage
work on behalf of my user, reduce cognitive load, surface
what matters, and act within the boundaries of trust I have
been given.

## What I will always do
- Tell the truth about what I know and do not know
- Explain my reasoning when asked
- Surface decisions that require human judgment
- Protect my user's data and not share it without consent
- Respect the trust gradient — autonomous where permitted,
  seeking approval where required

## What I will never do
- Take irreversible actions without explicit human approval
- Override compliance-linked constraints for any reason
- Impersonate my user in communications
- Make up information I do not have
- Suppress information my user needs to make a decision
- Delete data from the graph (archive only, never delete)

## My relationship to my user
I work for my user. They retain authority. I reduce load,
not responsibility. When in doubt, I ask.
```

---

#### persona.md — Tone and Communication Style

Mutable. User can update via conversation at any time. Drives all outbound message formatting and tone decisions.

```markdown
---
file: core/persona.md
mutable: true
last_updated: 2025-03-06T09:00:00Z
updated_by: USER-john-doe
---

# Persona — Nonna

## Name
Nonna

## Communication style
- Direct and concise — John prefers brevity over elaboration
- Professional but warm — not robotic, not overly casual
- Proactive — surface issues before being asked
- Never over-explains — one sentence of reasoning is enough

## Tone by channel
- WhatsApp:  brief, emoji acceptable, numbered choices
- Telegram:  same as WhatsApp
- Email:     structured sections, full context, formal subject lines

## User preferences
- Does not like long preambles before the point
- Does not like being asked the same clarifying question twice
- Wants decisions framed with a clear recommendation
- Wants to know what I am handling autonomously
```

---

#### memory.md — Episodic Memory

Rolling log of key facts about the user, their work, and their context. Not the full behavioral model (that is in `behavioral.md`) — this is narrative memory for intelligent reasoning. Oldest entries compressed and archived when the entry limit is reached.

```markdown
---
file: core/memory.md
mutable: true
last_updated: 2025-03-06T09:00:00Z
max_entries: 200
---

# Memory

## About John
- Works in product management at Acme Corp
- Running Q3 launch program — always highest priority
- Has a side podcast project (passion org, lower interrupt threshold)
- Prefers no contact before 8am or after 7pm
- Boss is Sarah Chen — deadline-driven, expects weekly Friday updates

## About his team
- Alex:  reliable, delivers early, low follow-up needed
- Mike:  goes quiet mid-task, needs proactive check-ins,
         do not contact before 10am
- Priya: highly responsive, sometimes over-scopes deliverables
- Editor vendor: delivered late twice — apply shorter follow-up cadence

## Active context (current as of last session)
- Q3 launch: legal review is the current blocker on critical path
- BD pipeline: 12 prospects active, David Park is the priority
- Podcast: Sarah Kim episode in production, editor has raw footage

## Standing instructions
- Sarah Chen's weekly update due every Friday — never miss
- John always reviews email drafts before they send
- Do not send follow-ups to Mike before 10am
```

---

#### heartbeat.md — Health and Recovery

Updated at the start and end of every invocation. Primary mechanism for detecting stale or incomplete sessions.

```markdown
---
file: state/heartbeat.md
mutable: true
---

# Heartbeat

## Last invocation
start:    2025-03-06T09:00:00Z
end:      2025-03-06T09:02:14Z
trigger:  SCHEDULED_BRIEFING
result:   COMPLETE
incomplete_flag: false

## Health
context_window_pressure: LOW
files_written: [state/context.md, graph/briefing_draft.md]
errors: none

## Recovery instruction
If result is INCOMPLETE or end timestamp is missing:
  Previous session was interrupted.
  Load state/context.md to find last checkpoint.
  Resume from that point before processing current trigger.
  Do not re-execute steps already marked COMPLETE in context.md.
```

---

#### context.md — Session Checkpoint

The agent writes its reasoning state here incrementally during every session. If a session is interrupted, the next invocation reads this and resumes exactly where it left off. This is the primary crash recovery mechanism.

```markdown
---
file: state/context.md
mutable: true
checkpoint_interval: after every major reasoning step
---

# Current Session Context

## Session
trigger:        INBOUND_MESSAGE
channel:        whatsapp
received_at:    2025-03-06T11:30:00Z

## Input
"Mike just told me the auth module is going to be 2 days late"

## Reasoning steps
Step 1: COMPLETE — resolved "Mike" -> RES-mike-chen (aliases.md)
Step 2: COMPLETE — matched to TSK-AC-0089-DEL (auth module)
Step 3: COMPLETE — delay impact: auth on critical path,
                   2-day slip cascades to Q3 launch milestone
Step 4: IN_PROGRESS — downstream cascade evaluation
        TSK-AC-0091 (integration testing) DEPENDS_ON auth
        TSK-AC-0094 (launch sign-off) DEPENDS_ON integration
        Cascade analysis incomplete at checkpoint

## Resume instructions
If session interrupted: begin at Step 4.
Complete cascade analysis then:
  - Update TSK-AC-0089-DEL state to DELAYED
  - Recalculate critical path for Q3 Launch goal
  - Determine: immediate interrupt to Sarah or hold for briefing

## Files loaded this session
main.md, soul.md, persona.md, heartbeat.md, context.md,
queue.md, channels/thread.md, user/aliases.md,
graph/active_goals.md, graph/critical_path.md
```

---

#### locks.md — Concurrency Control

Prevents race conditions when multiple triggers fire simultaneously for the same user agent.

```markdown
---
file: state/locks.md
mutable: true
---

# Active Locks

## Lock protocol
Before writing to any shared state:
  1. Check this file for an existing lock on the resource
  2. If locked: queue the action in queue.md, do not proceed
  3. If unlocked: write lock entry below, proceed, release on complete

## Current locks
[]

## Lock entry format
{
  lock_id:          string
  locked_resource:  "TASK:[id]" | "GOAL:[id]" |
                    "GRAPH_SCORE" | "BRIEFING"
  acquired_at:      timestamp
  acquired_by:      "TRIGGER:[type]:[session_id]"
  expected_release: timestamp
  status:           "ACTIVE" | "RELEASED" | "STALE"
}

## Stale lock recovery
If status=ACTIVE but expected_release has passed:
  1. Mark as STALE
  2. Check heartbeat.md — did the locking session complete?
  3. If COMPLETE: lock not released cleanly, safe to clear
  4. If INCOMPLETE: read context.md, reconcile partial state
     before reacquiring
```

---

### 26.4 LLM Configuration

#### Orchestrating Agent — Approved Model List

The orchestrating agent drives the entire user experience. Degraded model performance degrades everything. The approved list is restricted to frontier-class models only. Users may not configure below-tier models for the primary orchestrator.

```
Provider: Anthropic
  claude-opus-4         (default — highest capability)
  claude-sonnet-4-6     (cost-performance balance)

Provider: OpenAI
  gpt-4o
  o3

Provider: Google
  gemini-2.5-pro

Provider: xAI
  grok-3

Rules:
  - Default at onboarding: claude-opus-4
  - User may switch within the approved list
  - Non-approved models: blocked with explanation
  - System warns if user downgrades from default
```

#### Skill Agents — Open Model Configuration

Skill agents can use any model the user configures, including cheaper and faster options:

```
Orchestrating agent:  approved list only (above)
Skill agents:         any configured model
  claude-haiku-4-5    fast, cheap — good for drafting
  gpt-4o-mini         cheap alternative
  gemini-2.0-flash    fast, cheap
  local/ollama        self-hosted (advanced users only)
```

#### LLM Configuration Schema (stored in core/assets.md)

```markdown
primary_llm:
  provider:    anthropic
  model:       claude-opus-4
  api_key_id:  KEY-REF-001   // reference to secure key store only
                              // never store plaintext keys in MD files

configured_llms:
  - id:          LLM-001
    provider:    anthropic
    model:       claude-opus-4
    role:        PRIMARY_ORCHESTRATOR
    api_key_id:  KEY-REF-001

  - id:          LLM-002
    provider:    anthropic
    model:       claude-haiku-4-5
    role:        SKILL_AGENT_DEFAULT
    api_key_id:  KEY-REF-001

  - id:          LLM-003
    provider:    openai
    model:       gpt-4o-mini
    role:        SKILL_AGENT_OPTIONAL
    api_key_id:  KEY-REF-002
```

---

### 26.5 The Four Trigger Mechanisms

All four triggers funnel into the same reasoning loop. Trigger type determines which MD files are loaded and which reasoning path is taken — the loop itself is identical across all triggers.

```
.---------------------------------------------------------.
|  TRIGGER SOURCES                                       |
|                                                        |
|  [1] Inbound Message     [2] Scheduled Briefing        |
|      (webhook fired)         (cron fired)              |
|                                                        |
|  [3] Follow-up Timer     [4] Graph State Change        |
|      (queue.md entry         (graph DB event /         |
|       time reached)           state transition)        |
`---------------------------.----------------------------'
                             |
                             v
.----------------------------+----------------------------.
|  TRIGGER ROUTER                                        |
|  Identifies trigger type                               |
|  Routes to agent invocation with trigger metadata      |
`---------------------------.----------------------------'
                             |
                             v
.----------------------------+----------------------------.
|  AGENT INVOCATION                                      |
|  1. Load main.md                                       |
|  2. Load mandatory files:                              |
|     soul, persona, heartbeat, context, queue           |
|  3. Check heartbeat — incomplete prior session?        |
|     YES -> resume from context.md checkpoint           |
|     NO  -> proceed with current trigger                |
|  4. Load trigger-specific files                        |
|  5. Acquire necessary locks (locks.md)                 |
|  6. Execute reasoning loop                             |
|  7. Write state changes back to MD files               |
|  8. Release locks                                      |
|  9. Update heartbeat.md (invocation complete)          |
`---------------------------.----------------------------'
                             |
                             v
.----------------------------+----------------------------.
|  REASONING LOOP (identical for all four triggers)     |
|                                                        |
|  Parse trigger input                                   |
|   -> What happened? What does it mean?                 |
|                                                        |
|  Update graph state                                    |
|   -> State transitions, cascade analysis               |
|                                                        |
|  Score and prioritize                                  |
|   -> Re-score affected nodes                           |
|   -> Identify action queue changes                     |
|                                                        |
|  Decide: autonomous action or human needed?            |
|   -> Autonomous: execute, log to decisions.md          |
|   -> Human needed: add to briefing_draft.md            |
|      or interrupt if above threshold                   |
|                                                        |
|  Write output                                          |
|   -> Update relevant MD files                          |
|   -> Send outbound message if required                 |
|   -> Checkpoint context.md                             |
`---------------------------------------------------------'
```

#### Trigger 1: Inbound Message
```
Webhook fires (WhatsApp / Telegram / Email)
  -> Authenticate sender at platform gateway
  -> Identify user -> load agent for USER-[id]
  -> Load: channels/thread.md, channels/pending_responses.md
  -> Reason: what does this message mean for the graph?
  -> Update: task nodes, follow-up nodes, conversation thread
  -> Respond via appropriate channel
```

#### Trigger 2: Scheduled Briefing
```
Cron fires at briefing time for USER-[id], ORG-[id]
  -> Load agent for USER-[id]
  -> Load: graph/active_goals.md, graph/pending_actions.md
  -> Assemble: graph/briefing_draft.md
  -> Send via org-bound channel
  -> Update: queue.md (remove actioned items)
```

#### Trigger 3: Follow-up Timer
```
Queue processor scans queue.md
  Finds entry with scheduled_fire_at <= now
  -> Load agent for USER-[id]
  -> Load: graph/pending_actions.md, user/relationships.md
  -> Check: is follow-up still relevant?
  -> If needed: compose and send (or queue for approval)
  -> Update queue.md entry state
```

#### Trigger 4: Graph State Change
```
Graph DB emits state change event
  (task complete, milestone reached, blocker created)
  -> Load agent for USER-[id]
  -> Load: graph/active_goals.md, graph/critical_path.md
  -> Assess: critical path change? cascade required?
  -> If interrupt threshold exceeded: send immediate alert
  -> If not: update briefing_draft.md for next briefing
  -> Update: graph/critical_path.md if path changed
```

---

### 26.6 Concurrency and Race Condition Resolution

#### Scenario A: Two triggers fire simultaneously for the same user

```
08:59:58  Inbound message received (Mike: auth delay)
09:00:00  Scheduled briefing cron fires

Both invocations attempt to load and write agent state.

Resolution:
  Inbound message invocation acquires lock: GRAPH_SCORE
  Briefing invocation reads locks.md -> GRAPH_SCORE locked
  Briefing writes to queue.md:
    { deferred_trigger: SCHEDULED_BRIEFING,
      reason: LOCK_CONFLICT, retry_after: 30s }
  Inbound message completes, releases lock
  Queue processor retries briefing after 30s
  Briefing now sees updated graph state (including delay)
  -> Briefing is more accurate because it waited
```

#### Scenario B: Multi-user org — two agents write to the same task node

```
Alice's agent and Bob's agent both update TSK-4821 simultaneously.

Resolution via graph DB optimistic locking:
  Each write carries node version number
  First write succeeds: version 1 -> 2
  Second write fails: expected version 1, found version 2
  Second agent:
    -> Reads current state (version 2)
    -> Re-evaluates intended write against new state
    -> If still valid: retries (version 2 -> 3)
    -> If conflict: sets task state to NEEDS_REVIEW
    -> Notifies both users in next briefing
```

#### Scenario C: Session interrupted mid-reasoning

```
Invocation starts, begins cascade analysis, system crashes.

Next invocation recovery:
  Load heartbeat.md -> result: INCOMPLETE, end: missing
  Load context.md -> Step 4 IN_PROGRESS (cascade analysis)
  Agent resumes from Step 4
  Does not repeat Steps 1-3 (already marked COMPLETE)
  Completes reasoning, writes clean state to all files
  Updates heartbeat.md -> result: COMPLETE
```

---

### 26.7 Progressive Context Window Management

The main.md loading protocol prevents context overflow. A follow-up timer trigger for a single task loads 4-5 files. A full scheduled briefing loads more. The agent never loads the entire file system into a single context window.

```
Context pressure protocol (triggered at >70% window utilization):

  1. Write current reasoning state to context.md (checkpoint)
  2. Identify files loaded but no longer actively needed
  3. Summarize their key content into context.md notes
  4. Release those files from active context
  5. Load next required file(s)
  6. Continue reasoning from checkpoint

This is progressive loading — the agent pages through its
own knowledge the way a human pages through reference
material, keeping only what it is actively using in
working memory.
```

---

### 26.8 Restart and Recovery Guarantee

On any system restart or agent failure, full recovery is guaranteed through the file system alone. No external session store is required.

```
System restart -> agent invoked for USER-[id]
  |
  Load main.md -> load heartbeat.md
  |
  Heartbeat shows incomplete session:
    result: INCOMPLETE / end timestamp missing
  |
  Load context.md -> full reasoning checkpoint present
  |
  Agent knows:
    - What trigger fired and what the input was
    - What reasoning steps were already completed
    - What step it was mid-way through
    - What files were loaded
    - What actions were still pending
  |
  Resumes from last checkpoint
  Completes reasoning
  Writes clean final state to all relevant MD files
  Updates heartbeat.md -> result: COMPLETE

No in-memory state is lost because no state ever
lived only in memory. The file system is the single
source of truth for agent state.
```

---

### 26.9 Agent Instance Lifecycle

```
CREATION (at user onboarding):
  Platform provisions /agents/USER-[id]/ directory
  Writes initial MD files from onboarding interview responses
  Configures LLM from user's selection (default: claude-opus-4)
  Creates soul.md (immutable, written once)
  Bootstraps memory.md, behavioral.md with onboarding data
  Sets heartbeat.md: first_active timestamp

ACTIVE (normal operation):
  Agent invoked on each trigger event
  Stateless LLM call + file system reads/writes per invocation
  No persistent process — each invocation is independent
  State continuity provided entirely by MD files

UPGRADE (model or config change):
  User updates assets.md via settings panel
  Next invocation uses new LLM config
  No agent restart required — config is read fresh each invocation

DORMANT (user inactive):
  No triggers fire -> no invocations
  File system persists unchanged
  On user return: next trigger invokes agent
  context.md and heartbeat.md confirm no incomplete sessions
  Agent resumes as if no time has passed

MIGRATION (platform version upgrade):
  MD file format versioned in main.md
  Migration scripts transform old format to new
  Agent continues from existing state post-migration
```

---

## 27. Architecture: Graph Database

### 27.1 The Storage Challenge

The system requires three fundamentally different query patterns running simultaneously and consistently against the same underlying data:

```
Pattern 1 — Graph traversal
  "Find all tasks on the critical path of GOAL-001"
  "Find every task assigned to Sarah that is blocking others"
  Characteristics: relationship-heavy, variable depth,
  requires following edges recursively

Pattern 2 — Vector similarity search
  "Which task node is most similar to this inbound message?"
  Characteristics: high-dimensional ANN search,
  no joins, pure similarity ranking

Pattern 3 — Time-series and filter queries
  "All tasks due in the next 3 days across all active goals"
  "All state transitions in the last 24 hours for USER-[id]"
  Characteristics: range scans, time ordering,
  audit trail, behavioral training data
```

No single database handles all three optimally at production scale. The architecture uses **polyglot persistence** — three purpose-built stores that each own their query pattern, kept consistent through a coordination layer.

---

### 27.2 Three-Store Architecture

```
.---------------------------------------------------------.
|                                                        |
|  APPLICATION LAYER                                     |
|  (Orchestrating Agent, Channel Router, Trigger Engine) |
|                                                        |
`---------.-----------------.-------------------.--------'
          |                 |                   |
          v                 v                   v
.-----------.         .-----------.       .-----------.
|  GRAPH DB |         |  VECTOR   |       | RELATIONAL|
|           |         |  INDEX    |       | / TIME-   |
| Neo4j /   |         |           |       | SERIES DB |
| Amazon    |         | pgvector  |       |           |
| Neptune / |         | or        |       | Postgres  |
| Apache    |         | Pinecone  |       | or        |
| AGE       |         | or        |       | Timescale |
|           |         | Weaviate  |       | DB        |
|           |         |           |       |           |
| Owns:     |         | Owns:     |       | Owns:     |
| All nodes |         | Node      |       | state_    |
| All edges |         | embeddings|       | history,  |
| Node      |         | (1536-dim)|       | update_   |
| properties|         | keyed by  |       | log,      |
|           |         | node_id   |       | Conver-   |
|           |         |           |       | sation-   |
|           |         |           |       | Thread,   |
|           |         |           |       | Score-    |
|           |         |           |       | Explana-  |
|           |         |           |       | tion,     |
|           |         |           |       | behavioral|
|           |         |           |       | training  |
`-----------'         `-----------'       `-----------'
          |                 |                   |
          `---------.-------'-------------------'
                    |
          .---------v---------.
          |  CONSISTENCY      |
          |  COORDINATOR      |
          |                   |
          | Ensures writes    |
          | propagate to all  |
          | three stores      |
          | atomically where  |
          | required          |
          `-------------------'
```

---

### 27.3 Primary Store — Property Graph Database

#### Technology Choice

**Default recommendation: Apache AGE over Postgres**

AGE (A Graph Extension) runs directly on top of Postgres, giving the system full openCypher graph query support while keeping the operational footprint of a single Postgres cluster. This matters particularly in early stages — one database to operate, backup, and scale instead of two.

**Scale-out alternative: Amazon Neptune or Neo4j**

When graph traversal performance becomes a bottleneck (typically at millions of nodes and billions of edges), migrating to a purpose-built graph DB provides better traversal throughput. The openCypher query syntax is compatible across AGE, Neo4j, and Neptune, making migration straightforward.

```
Startup / Early Scale:   Apache AGE over Postgres
                         -> Single operational DB
                         -> Full graph + SQL in one cluster
                         -> pgvector extension available for
                            vector index in same cluster

Growth / Scale:          Amazon Neptune (managed, AWS-native)
                         OR Neo4j Enterprise (self-hosted)
                         -> Dedicated graph traversal engine
                         -> Better performance at >10M nodes
```

#### Node Label Schema

Every node type defined in the product design maps to a graph DB label:

```
Node Labels:
  :User               :Resource           :Organization
  :TaskAtomic         :TaskComposite      :TaskDelegated
  :TaskFollowUp       :TaskMilestone      :TaskApproval
  :TaskReview         :TaskDecision       :TaskResearch
  :TaskRecurring      :TaskBlocked
  :Goal               :Constraint
  :CheckIn            :HandOff            :DependencyGate
  :ConversationThread :ScoreExplanation
```

#### Edge Type Schema

```
Relationship Types:
  -[:DEPENDS_ON]->         -[:SPAWNED_FROM]->
  -[:FOLLOW_UP_FOR]->      -[:BLOCKS]->
  -[:ASSIGNED_TO]->        -[:OWNED_BY]->
  -[:APPLIES_TO]->         -[:PART_OF]->
  -[:INFORMS]->            -[:BRANCHED_FROM]->
  -[:BATCHED_IN]->         -[:MEMBER_OF]->
  -[:HAS_ORG]->            -[:VISIBILITY_GRANT]->
  -[:REFERRED_BY]->
```

#### Critical Indexes

```sql
-- Primary lookup (every query starts here)
CREATE INDEX node_id_idx ON nodes (node_id);

-- Org partition (all task queries are org-scoped)
CREATE INDEX org_partition_idx ON nodes (organization_id);

-- State filter (most queries exclude COMPLETE/CANCELLED)
CREATE INDEX state_idx ON nodes (state);

-- Deadline range scans (urgency scoring, briefing assembly)
CREATE INDEX deadline_idx ON nodes (deadline) WHERE deadline IS NOT NULL;

-- Assignee lookup (resource view, follow-up targeting)
CREATE INDEX assigned_to_idx ON nodes (assigned_to);

-- Composite: org + state + deadline (most common briefing query)
CREATE INDEX briefing_query_idx ON nodes (organization_id, state, deadline);

-- Critical path flag (fast filter for critical path queries)
CREATE INDEX critical_path_idx ON nodes (on_critical_path)
  WHERE on_critical_path = true;
```

#### Key Graph Queries (openCypher)

**Critical path traversal:**
```cypher
MATCH path = (g:Goal {id: $goal_id})-[:PART_OF|DEPENDS_ON*]->(t)
WHERE t.state NOT IN ['COMPLETE', 'CANCELLED', 'ARCHIVED']
WITH t, length(path) as depth,
     reduce(effort = 0, n IN nodes(path) | effort + n.estimated_effort) as path_effort
ORDER BY path_effort DESC
LIMIT 1
RETURN nodes(path) as critical_path_nodes
```

**Dependency impact assessment:**
```cypher
MATCH (t:Task {id: $task_id})<-[:DEPENDS_ON*]-(downstream)
WHERE downstream.state NOT IN ['COMPLETE', 'CANCELLED']
RETURN downstream.id, downstream.title, downstream.deadline,
       downstream.assigned_to, count(*) as dependency_depth
ORDER BY dependency_depth DESC
```

**Blocked task escalation:**
```cypher
MATCH (blocker:Task)-[:BLOCKS]->(blocked:Task)
WHERE blocker.state = 'BLOCKED'
  AND blocked.on_critical_path = true
  AND blocked.organization_id = $org_id
RETURN blocker, blocked
ORDER BY blocked.deadline ASC
```

**Resource capacity check:**
```cypher
MATCH (r:Resource {id: $resource_id})<-[:ASSIGNED_TO]-(t:Task)
WHERE t.state IN ['ACTIVE', 'IN_PROGRESS']
  AND t.organization_id = $org_id
RETURN r.id, count(t) as active_task_count,
       collect(t.id) as active_tasks,
       r.capacity.max_concurrent_tasks as capacity_limit
```

---

### 27.4 Vector Index

#### Purpose and Scope

The vector index stores embeddings for task nodes only. It is queried exclusively during the inbound update protocol when an incoming message has no task ID and must be matched to the right node via semantic similarity.

```
What is embedded:
  TaskNode embedding = embed(
    task_title +
    task_description +
    assigned_to_name +
    goal_context +
    key_entities extracted at creation time
  )

What is NOT embedded:
  Goal nodes, Constraint nodes, Resource nodes,
  Check-in nodes — these are never the target of
  an unstructured inbound message match

Embedding model: text-embedding-3-small (OpenAI) or
                 claude embedding equivalent
Dimensions: 1536
```

#### Technology Choice

```
Co-located (startup):    pgvector extension on Postgres
                         -> Same cluster as graph DB (Apache AGE)
                         -> ANN search via HNSW index
                         -> No additional infrastructure

Dedicated (scale):       Pinecone (managed, serverless)
                         OR Weaviate (self-hosted)
                         -> Better ANN throughput at millions of nodes
                         -> Purpose-built for vector workloads
```

#### Vector Search Query Pattern

```python
def match_inbound_to_task(message_text, user_id, org_id):

    # Step 1: Extract semantic signals from message
    signals = extract_signals(message_text)
    # -> entity names, topic keywords, status signals

    # Step 2: Generate query embedding
    query_vector = embed(signals.title + signals.entities + signals.topic)

    # Step 3: ANN search — top 5 candidates
    candidates = vector_index.search(
        vector=query_vector,
        top_k=5,
        filter={"org_id": org_id, "user_id": user_id}
    )
    # Returns: [(node_id, similarity_score), ...]

    # Step 4: Enrich candidates from graph DB
    results = []
    for node_id, sim_score in candidates:
        node = graph_db.get_node(node_id)

        # Step 5: Apply secondary filters
        if node.state in ['COMPLETE', 'CANCELLED', 'ARCHIVED']:
            continue  # not a valid match target

        # Step 6: Score with additional signals
        sender_match = 1.0 if signals.sender_id == node.assigned_to else 0.0
        deadline_match = score_deadline_consistency(signals, node.deadline)

        composite_score = (
            sim_score        * 0.60 +
            sender_match     * 0.30 +
            deadline_match   * 0.10
        )
        results.append((node, composite_score))

    results.sort(key=lambda x: x[1], reverse=True)
    best_match, best_score = results[0]

    if best_score >= 0.82:
        return best_match, "CONFIDENT"
    elif best_score >= 0.65:
        return best_match, "NEEDS_CONFIRMATION"
    else:
        return None, "NO_MATCH"
```

#### Embedding Update Policy

```
Create embedding:   when TaskNode is created
Update embedding:   when task_title or task_description
                    changes by more than a similarity
                    threshold (delta > 0.15)
Delete embedding:   when TaskNode state -> ARCHIVED or CANCELLED
                    (remove from active index, not from history)

Batch re-embedding: nightly job re-embeds any nodes flagged
                    as stale (description changed but embedding
                    not yet updated)
```

---

### 27.5 Relational / Time-Series Store

#### Technology Choice

```
Default: Postgres
  -> state_history, update_log, ConversationThread,
     ScoreExplanation, behavioral training data
  -> Standard relational tables with time-ordered indexes
  -> Familiar operational model

Scale alternative: TimescaleDB (Postgres extension)
  -> Hypertables for time-series data
  -> Automatic partitioning by time range
  -> Better compression and query performance for
     large state_history and update_log tables
```

#### Table Schema

**state_history** — append-only log of all task state transitions:
```sql
CREATE TABLE state_history (
  id              BIGSERIAL PRIMARY KEY,
  node_id         VARCHAR(64) NOT NULL,
  org_id          VARCHAR(64) NOT NULL,
  user_id         VARCHAR(64) NOT NULL,
  from_state      VARCHAR(32),
  to_state        VARCHAR(32) NOT NULL,
  changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  changed_by      VARCHAR(32) NOT NULL,  -- 'AGENT' | 'HUMAN' | 'INBOUND_UPDATE'
  trigger_type    VARCHAR(32),
  reason          TEXT,
  session_id      VARCHAR(64)
);
CREATE INDEX state_history_node_idx ON state_history (node_id, changed_at DESC);
CREATE INDEX state_history_org_time ON state_history (org_id, changed_at DESC);
```

**update_log** — all inbound updates received and how they were processed:
```sql
CREATE TABLE update_log (
  id              BIGSERIAL PRIMARY KEY,
  node_id         VARCHAR(64),           -- null if unmatched
  org_id          VARCHAR(64) NOT NULL,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  channel         VARCHAR(32) NOT NULL,
  sender_id       VARCHAR(64),
  raw_text        TEXT NOT NULL,
  parsed_status   VARCHAR(32),
  parsed_progress INTEGER,
  match_method    VARCHAR(32),           -- 'TASK_ID' | 'VECTOR_SEARCH' | 'UNMATCHED'
  match_confidence FLOAT,
  action_taken    VARCHAR(64),
  session_id      VARCHAR(64)
);
CREATE INDEX update_log_node_idx ON update_log (node_id, received_at DESC);
```

**conversation_thread** — unified cross-channel message history:
```sql
CREATE TABLE conversation_thread (
  message_id      VARCHAR(64) PRIMARY KEY,
  user_id         VARCHAR(64) NOT NULL,
  direction       VARCHAR(16) NOT NULL,  -- 'INBOUND' | 'OUTBOUND'
  channel         VARCHAR(32) NOT NULL,
  content         TEXT NOT NULL,
  content_type    VARCHAR(32) NOT NULL,
  sent_at         TIMESTAMPTZ NOT NULL,
  task_refs       VARCHAR(64)[],
  resolved_intent TEXT,
  session_id      VARCHAR(64)
);
CREATE INDEX conv_thread_user_time ON conversation_thread (user_id, sent_at DESC);
```

**score_explanation** — every scoring pass result for explainability:
```sql
CREATE TABLE score_explanation (
  id              BIGSERIAL PRIMARY KEY,
  node_id         VARCHAR(64) NOT NULL,
  org_id          VARCHAR(64) NOT NULL,
  scored_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  final_score     FLOAT NOT NULL,
  rank_in_org     INTEGER,
  factors_json    JSONB NOT NULL,        -- full factor breakdown
  modifiers_json  JSONB,
  summary         TEXT,
  topology_note   TEXT,
  trigger_type    VARCHAR(32)
);
CREATE INDEX score_exp_node_idx ON score_explanation (node_id, scored_at DESC);
```

---

### 27.6 Critical Path Caching Strategy

Critical path computation is the most expensive recurring query. Running it on every trigger would be prohibitively slow at scale. The cache strategy ensures it runs only when the underlying graph has actually changed.

```
Cache location:   graph/critical_path.md in agent file system
                  (one cache entry per active Goal Node)

Cache entry format:
  goal_id:          GOAL-001
  computed_at:      2025-03-06T09:00:00Z
  critical_path:    [TSK-001, TSK-004, TSK-007, TSK-011]
  path_length_days: 14.5
  float_by_node:    { TSK-002: 2.0, TSK-003: 3.5, ... }
  valid:            true

Invalidation triggers (set valid: false):
  - Any node on the cached critical path changes state
  - estimated_effort changes on any node in the subgraph
  - New DEPENDS_ON edge added to the subgraph
  - Deadline changes on any node in the subgraph

Recomputation:
  - On next agent invocation after invalidation
  - Forced full recompute: once daily pre-briefing
  - Never recomputed mid-session unless a GRAPH_STATE_CHANGE
    trigger fires with a critical path node as subject

Between invalidations:
  - Agent reads cached path from critical_path.md
  - No graph traversal query needed
  - Scoring uses cached on_critical_path flags directly
```

---

### 27.7 Consistency Model

The three stores must stay consistent. A write to the graph DB must eventually be reflected in the vector index (if the node was updated) and the relational store (if a state transition occurred).

```
Write path for a task state transition:

1. Write to Graph DB (primary, authoritative)
   -> Update TaskNode.state, state_history array on node
   -> This is the atomic source of truth

2. Write to Relational DB (async, within 500ms)
   -> Append row to state_history table
   -> This feeds audit trail and behavioral training

3. Update Vector Index (async, conditional)
   -> Only if task_title or task_description changed
   -> Not triggered by state change alone
   -> Batched with other embedding updates if within
      the nightly batch window

Consistency guarantee:
  Graph DB: synchronous write, immediate consistency
  Relational DB: async write, eventual consistency (< 500ms)
  Vector Index: async write, eventual consistency (< 60s
                for real-time updates, or next batch run)

On read:
  Graph DB is always the authoritative source for node state
  Relational DB is authoritative for time-series history
  Vector Index is best-effort (stale by up to 60s acceptable
  for inbound message matching — precision is supplemented
  by the secondary scoring filters)
```

---

### 27.8 Org Isolation at the Database Layer

Every query is org-scoped. Organization boundaries are enforced at the database layer, not just the application layer. This provides defense in depth against data leakage across org boundaries.

```
Graph DB:
  All task, goal, and constraint nodes carry organization_id
  as a property. All queries include organization_id as a
  mandatory filter. Application code cannot query across
  org boundaries without explicitly specifying an org_id.

  Additionally: node-level visibility_grants are enforced
  before returning cross-user query results.

Relational DB:
  All tables include org_id column.
  Row-level security (RLS) policies in Postgres enforce
  that queries from a given user session can only read
  rows matching their authorized org_ids.

Vector Index:
  All embeddings stored with org_id metadata.
  ANN search filter always includes org_id to prevent
  cross-org embedding matches.

Result: even if application-layer org checking had a bug,
the database layer enforces isolation independently.
```

---

### 27.9 Data Retention and Archival Policy

```
Active graph (Graph DB):
  All nodes in states other than ARCHIVED remain in main graph
  Query performance maintained by org-partition indexes

Archived nodes:
  State = ARCHIVED, excluded from scoring cycle queries
  Retained in graph DB indefinitely (never deleted)
  Accessible via explicit archive queries for history/reporting

State history (Relational DB):
  Retained for 24 months by default
  Configurable per org (compliance requirements may extend)
  Older entries compressed to monthly summaries

Conversation thread:
  Last 90 days retained in hot storage (fast query)
  Older messages moved to cold storage (accessible, slower)
  Never deleted (may be needed for audit)

Score explanations:
  Last 30 days retained in hot storage
  Older entries archived to cold storage
  Used for behavioral model training and user review

Embeddings (Vector Index):
  Deleted when node is ARCHIVED or CANCELLED
  Stale embeddings (node changed, embedding not updated)
  cleaned up in nightly batch job
```

---

## 28. Architecture: Multi-Tenant Runtime

### 28.1 Core Decisions

| Concern | Decision | Rationale |
|---------|----------|-----------|
| Compute model | Containerized (Docker) | Portable, vendor-neutral, enterprise-ready, locally testable |
| Orchestration | ECS / EKS / GKE / AKS | Cloud-agnostic manifests, same images across all targets |
| Per-user isolation | One container per user | Hard process boundary, independent scaling, no cross-user interference |
| File system | S3-compatible object storage | Durable, cloud-agnostic, MinIO for on-premise |
| File access speed | Redis cache layer | Eliminates S3 latency for warm invocations |
| Trigger engine | Internal container + optional cloud scheduler | Decoupled, configurable from settings panel |
| Local development | `docker compose up` | Full stack on any developer machine, no cloud account needed |

---

### 28.2 Container Services

The application is packaged as seven containers deployed as a single unit. Each container owns a specific concern. They communicate over an internal Docker/Kubernetes network — only the API server and channel gateway are externally exposed.

```
.---------------------------------------------------------.
|  CONTAINER SERVICES                                    |
|                                                        |
|  [agent-runtime]     LLM calls, MD file reasoning     |
|  [channel-gateway]   WhatsApp / Telegram / Email       |
|  [trigger-engine]    Cron scheduler, queue processor   |
|  [graph-db]          Postgres + Apache AGE             |
|  [relational-db]     Postgres (audit, history)         |
|  [cache]             Redis                             |
|  [api-server]        Settings, graph UI, auth          |
|                                                        |
|  Internal network:   all service-to-service traffic    |
|  External exposure:  api-server, channel-gateway only  |
`---------------------------------------------------------'
```

| Container | External? | What it does |
|-----------|-----------|--------------|
| `agent-runtime` | No | The AI brain. One instance per user. Runs the orchestrating agent's LLM reasoning loop, reads/writes MD files (Redis cache → S3 fallback), processes trigger events from the user's dedicated Redis Stream queue, spawns skill agents as async threads. Scaled to zero when idle. |
| `channel-gateway` | **Yes** | Receives and authenticates inbound messages from all channels (WhatsApp HMAC, Telegram secret token, Email DKIM/IMAP). Normalises to `InboundMessage`, extracts attachments to S3, publishes to SQS. Dispatches outbound messages to the correct channel API. Shared across all users; horizontally scaled behind ALB. |
| `trigger-engine` | No | Cron scheduler (daily briefings, follow-up timers), graph event listener (Postgres NOTIFY on state changes), inbound dispatcher (routes SQS messages to the correct user's trigger queue), DLQ handler. Scheduler is single-instance; queue-processor workers are horizontally scalable. |
| `graph-db` | No | Postgres + Apache AGE extension. Primary store for all node/edge data — task graph, goal hierarchy, org model, alias mappings, visibility grants. Also runs pgvector for embedding-based inbound message resolution. PgBouncer sits in front at scale. |
| `relational-db` | No | Separate Postgres instance for operational data: audit log, state history, conversation archive, user registry, briefing schedules, MCP server registry, A2A key store. Separated from `graph-db` so graph traversal load does not contend with operational reads. |
| `cache` | No | Redis. Per-user namespaced key space: write-through MD file cache (`cache:USER-{id}:*`), active conversation context, trigger queue Redis Streams, JWT jti revocation list, alias resolver results. Redis Cluster (3-node) in production. |
| `api-server` | **Yes** | FastAPI. Settings panel (channels, orgs, LLM providers, schedules, MCP registry), visual graph interface API, OAuth 2.0 callback handler, platform JWT issuance/refresh/revocation, user onboarding provisioning (atomically creates UserNode + S3 prefix + IAM role + SQS queue + container slot). Only container with IAM permission to create user-scoped roles. |

---

### 28.3 Agent Runtime Container

The agent-runtime container is a long-running worker pool. It is not one container per request — it is a persistent container per user that processes trigger events from a dedicated queue.

```
agent-runtime container:
  Language:   Python (Anthropic SDK / direct LLM clients)
  Workers:    N concurrent worker threads (configurable)
  Receives:   Trigger events from trigger-engine queue
  Reads:      MD files from Redis cache -> S3 fallback
  Calls:      LLM API (Anthropic / OpenAI / Google)
  Writes:     Updated MD files back to Redis + S3
  Updates:    Graph DB and Relational DB with state changes
  Sends:      Outbound messages via channel-gateway
```

#### One Container Per User

Each user has their own agent-runtime container instance — the hard isolation boundary.

```
USER-alice  -> container: agent-runtime-USER-alice
USER-bob    -> container: agent-runtime-USER-bob
USER-carol  -> container: agent-runtime-USER-carol

Per container:
  - Dedicated worker pool
  - Reads only from s3://[bucket]/agents/USER-[id]/
  - Redis namespace: cache:USER-[id]:*
  - IAM role: agent-role-USER-[id] (S3 access scoped to prefix)
  - Independent auto-scaling
  - One user's load cannot affect another's
```

#### Container Resource Profile and Scaling

```
Idle (no active triggers):
  CPU:    0.25 vCPU
  Memory: 512MB
  Cost:   ~$3-5/month on ECS Fargate

Active (LLM call in flight):
  CPU:    0.5 - 1.0 vCPU
  Memory: 1GB
  Auto-scales on trigger, returns to idle after

Scale-to-zero policy:
  Personal accounts:   scale to zero overnight (cold start ~2-5s)
  Team / Enterprise:   keep alive during configured working hours

Container lifecycle:
  CREATED:   user onboards -> container provisioned
  RUNNING:   trigger fires -> container active
  IDLE:      no triggers -> minimal resources
  RESTARTED: health check failure -> auto-restart,
             reads heartbeat.md to detect incomplete sessions
  MIGRATED:  platform upgrade -> rolling restart, state
             preserved in S3 across restart
```

#### Cost Projections

```
1,000 users:
  ~1,000 containers (mostly idle, scale-to-zero overnight)
  Compute: ~$3,000-5,000/month (ECS Fargate)
  Dominated by LLM API costs, not compute

10,000 users:
  EKS preferred for bin-packing and spot instance support
  Spot instances for idle containers: ~80% cost reduction
  Compute: ~$15,000-25,000/month
  Still dominated by LLM API costs
```

---

### 28.4 S3 File System — Bucket Structure

All agent state lives in S3-compatible object storage. The bucket is structured by user prefix — isolation enforced at the IAM/ACL layer.

```
s3://[app-bucket]/

  agents/
    USER-[id]/
      main.md
      core/
        soul.md, persona.md, memory.md,
        skills.md, assets.md, reference.md
      state/
        heartbeat.md, context.md, queue.md, locks.md
      user/
        profile.md, behavioral.md, aliases.md, relationships.md
      graph/
        active_goals.md, critical_path.md,
        pending_actions.md, briefing_draft.md
      channels/
        config.md, thread.md, pending_responses.md
      orgs/
        ORG-[id].md
      log/
        decisions.md, overrides.md, learning.md

  workspaces/
    USER-[id]/
      tasks/
        TSK-[id]/
          task.md, output.md, status.md
          context/, artifacts/

  skills/
    USER-[id]/           <- user-defined skills
      [skill-name]/
        SKILL.md, SKILL.v[n].md
    system/              <- platform skills (read-only for all)
      [skill-name]/
        SKILL.md
```

#### S3 IAM Isolation Policy (Per-User Container)

```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
  "Resource": [
    "arn:aws:s3:::app-bucket/agents/USER-[id]/*",
    "arn:aws:s3:::app-bucket/workspaces/USER-[id]/*",
    "arn:aws:s3:::app-bucket/skills/USER-[id]/*"
  ]
},
{
  "Effect": "Allow",
  "Action": ["s3:GetObject"],
  "Resource": "arn:aws:s3:::app-bucket/skills/system/*"
}
```

Even if application code had a path traversal bug, the IAM policy prevents the container from reading any other user's files. Isolation is enforced at the cloud provider level independently of the application layer.

---

### 28.5 Redis Cache Layer

S3 reads carry ~10-50ms latency per file. An agent invocation loads 5-12 files — that is 50-600ms of read latency before reasoning even begins. Redis eliminates this for warm invocations.

#### Cache Namespace and TTL Policy

```
Namespace: cache:USER-[id]:[file-path]

core:soul           -> no expiry (immutable, written once)
core:persona        -> TTL: 1 hour
core:memory         -> TTL: 15 minutes
state:heartbeat     -> TTL: 5 minutes  (hot — changes every invocation)
state:context       -> TTL: 5 minutes  (hot — changes every invocation)
state:queue         -> TTL: 5 minutes  (hot)
state:locks         -> TTL: 5 minutes  (hot)
graph:active_goals  -> TTL: 10 minutes
graph:critical_path -> TTL: 30 minutes
channels:thread     -> TTL: 5 minutes  (hot)
user:behavioral     -> TTL: 1 hour
user:aliases        -> TTL: 30 minutes
```

#### Write-Through Caching

```
Agent writes updated MD file:
  1. Write to S3 (durable, authoritative source of truth)
  2. Update Redis cache entry immediately
  3. Set TTL appropriate to file type

Next invocation reads from Redis (fast, always fresh).
Cache miss (cold start or TTL expired):
  Read from S3 -> populate Redis -> serve from Redis.
```

#### Cache Sizing

```
Per-user footprint (all active MD files cached): ~50-200KB
1,000 active users:   ~50-200MB Redis
10,000 active users:  ~500MB-2GB Redis
Fits comfortably in a single Redis instance up to ~100K users.
Beyond that: Redis Cluster with consistent hashing by USER-[id].
```

---

### 28.6 Trigger Engine

The trigger engine container manages all four trigger types and dispatches events to the correct user's agent-runtime container via per-user Redis Streams queues.

#### Internal Architecture

```
.---------------------------------------------------------.
|  TRIGGER ENGINE CONTAINER                              |
|                                                        |
|  Cron Scheduler                                        |
|    Reads briefing schedules from relational DB         |
|    Fires SCHEDULED_BRIEFING at configured times        |
|    per user per org                                    |
|                                                        |
|  Queue Processor                                       |
|    Polls queue.md entries (or dedicated queue table)   |
|    Fires FOLLOWUP_TIMER when scheduled_fire_at <= now  |
|                                                        |
|  Graph Event Listener                                  |
|    Subscribes to graph DB NOTIFY channel               |
|    Fires GRAPH_STATE_CHANGE on task state transitions, |
|    milestone completions, blocker creation             |
|                                                        |
|  Inbound Dispatcher                                    |
|    Receives authenticated messages from channel-gateway|
|    Fires INBOUND_MESSAGE to correct user queue         |
`---------------------------.----------------------------'
                             |
              Redis Streams: trigger-queue:USER-[id]
                             |
              .--------------v-------------.
              | agent-runtime-USER-[id]    |
              | Consumes from own queue    |
              | One trigger at a time      |
              | Concurrent: queued in order|
              `----------------------------'
```

#### Trigger Event Schema

```json
{
  "trigger_id":    "TRG-[uuid]",
  "trigger_type":  "INBOUND_MESSAGE | SCHEDULED_BRIEFING |
                    FOLLOWUP_TIMER | GRAPH_STATE_CHANGE",
  "user_id":       "USER-[id]",
  "org_id":        "ORG-[id]",
  "priority":      1,
  "scheduled_at":  "2025-03-06T09:00:00Z",
  "expires_at":    "2025-03-06T09:30:00Z",
  "payload": {
    "channel":     "whatsapp",
    "message":     "...",
    "task_id":     "TSK-...",
    "node_id":     "...",
    "change_type": "STATE_TRANSITION"
  }
}
```

#### Briefing Schedule Configuration

Users configure schedules in the settings panel. Stored in the relational DB and read by the cron scheduler.

```sql
CREATE TABLE briefing_schedules (
  id          BIGSERIAL PRIMARY KEY,
  user_id     VARCHAR(64)  NOT NULL,
  org_id      VARCHAR(64)  NOT NULL,
  channel     VARCHAR(32)  NOT NULL,
  cron_expr   VARCHAR(64)  NOT NULL,
  timezone    VARCHAR(64)  NOT NULL,
  active      BOOLEAN      DEFAULT true,
  created_at  TIMESTAMPTZ  DEFAULT NOW(),
  updated_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Example rows:
-- USER-john, ORG-work,     telegram, "0 9 * * 1-5", "America/New_York"
-- USER-john, ORG-personal, whatsapp, "0 7 * * *",   "America/New_York"
-- USER-john, ORG-passion,  email,    "0 19 * * 0",  "America/New_York"
```

#### Optional External Scheduler Integration

For deployments where the customer prefers cloud-managed scheduling:

```
AWS:    EventBridge Scheduler -> POST /internal/trigger
GCP:    Cloud Scheduler       -> POST /internal/trigger
Azure:  Logic Apps Timer      -> POST /internal/trigger

The trigger-engine container exposes /internal/trigger
and dispatches the event to the correct user queue.
Internal cron scheduler disabled in this mode.

Benefit:  managed cron reliability from cloud provider
Tradeoff: slight cloud coupling (optional, not required)
```

---

### 28.7 Per-User Isolation Model — Full Stack

```
.---------------------------------------------------------.
|  PLATFORM ORCHESTRATOR (ECS / EKS / GKE / AKS)        |
|                                                        |
|  User Registry: USER-[id] -> container spec            |
|                                                        |
|  On user onboard:                                      |
|    Provision: agent-runtime-USER-[id] container        |
|    Assign S3 prefix:    /agents/USER-[id]/             |
|    Assign Redis ns:     cache:USER-[id]:*              |
|    Assign IAM role:     agent-role-USER-[id]           |
|    Register queue:      trigger-queue:USER-[id]        |
|    Start container:     min resources                  |
|                                                        |
|  Isolation guarantees:                                 |
|                                                        |
|  Memory:    Separate container process                 |
|             No shared memory between users             |
|                                                        |
|  Files:     S3 IAM scoped to user prefix              |
|             Enforced at cloud provider level           |
|                                                        |
|  Cache:     Redis namespace per user                   |
|             No key collision possible                  |
|                                                        |
|  Compute:   CPU/memory limits per container           |
|             One user cannot starve another            |
|                                                        |
|  Triggers:  Per-user queue                            |
|             High trigger volume for one user          |
|             does not affect other users               |
`---------------------------------------------------------'
```

---

### 28.8 Deployment Targets

The same container images and manifests deploy to any target. Cloud provider differences are abstracted by environment variables and S3-compatible storage APIs.

```
LOCAL DEVELOPMENT
  Command:    docker compose up
  Storage:    LocalStack (S3-compatible) or host-mounted volume
  Cache:      Redis on localhost
  DB:         Postgres on localhost
  No cloud account required. Full stack on any developer machine.

AWS
  Orchestration:    ECS Fargate or EKS
  Storage:          S3
  Cache:            ElastiCache (Redis)
  Graph DB:         RDS Postgres + AGE extension, or Neptune
  Relational DB:    RDS Postgres
  Registry:         ECR
  Cron (optional):  EventBridge Scheduler

GCP
  Orchestration:    GKE or Cloud Run
  Storage:          GCS (S3-compatible API)
  Cache:            Memorystore (Redis)
  Graph DB:         Cloud SQL Postgres + AGE
  Relational DB:    Cloud SQL Postgres
  Registry:         Artifact Registry
  Cron (optional):  Cloud Scheduler

AZURE
  Orchestration:    AKS or Container Apps
  Storage:          Azure Blob Storage (S3-compatible)
  Cache:            Azure Cache for Redis
  Graph DB:         Azure Database for Postgres + AGE
  Relational DB:    Azure Database for Postgres
  Registry:         ACR
  Cron (optional):  Logic Apps

ENTERPRISE ON-PREMISE
  Orchestration:    Self-managed Kubernetes
  Storage:          MinIO (S3-compatible, self-hosted)
  Cache:            Self-managed Redis
  Graph DB:         Self-managed Postgres + AGE
  All within corporate network. No external cloud dependency.
```

The S3-compatible API is the key portability enabler. Every storage target exposes the same interface. Switching cloud providers requires only an environment variable change — no code changes in the application.

---

### 28.9 Full Runtime Architecture Diagram

```
.================================================================.
|  EXTERNAL                                                     |
|  WhatsApp Business API | Telegram Bot API | SMTP/IMAP Email   |
`======================.=========================================`
                        | webhooks / SMTP
.======================v=========================================.
|  CHANNEL GATEWAY CONTAINER                    [external-facing]|
|  HMAC / bot token / DKIM auth                                 |
|  Sender -> USER-[id] resolution                               |
|  Normalize to internal message format                         |
|  Forward to trigger engine                                    |
`======================.=========================================`
                        |
.======================v=========================================.
|  TRIGGER ENGINE CONTAINER                     [internal only] |
|  Cron scheduler (briefing schedules)                          |
|  Queue processor (follow-up timers)                           |
|  Graph event listener (state changes)                         |
|  Inbound dispatcher                                           |
|  -> Writes to: trigger-queue:USER-[id] (Redis Streams)        |
`======================.=========================================`
                        | Redis Streams (per-user queues)
.======================v=========================================.
|  AGENT RUNTIME CONTAINERS          [internal only, per-user]  |
|                                                               |
|  agent-runtime-USER-alice:                                    |
|    1. Read trigger from trigger-queue:USER-alice              |
|    2. Load MD files: Redis cache -> S3 fallback               |
|    3. Acquire lock (locks.md)                                 |
|    4. Call LLM API                                            |
|    5. Reason, update state                                    |
|    6. Write MD files: Redis + S3 (write-through)              |
|    7. Update Graph DB (task states, edges)                    |
|    8. Update Relational DB (state_history, thread)            |
|    9. Dispatch outbound message -> channel-gateway            |
|   10. Release lock, update heartbeat.md                       |
`======================.=========================================`
                        |
     .------------------+-------------------.
     |                  |                   |
.====v=====.     .======v======.    .=======v======.
| S3 /     |     | Redis Cache |    | Graph DB     |
| Object   |     |             |    | Postgres+AGE |
| Storage  |     | cache:USER: |    | + pgvector   |
|          |     | hot files   |    |              |
| /agents/ |     | 5min-1hr TTL|    | Relational DB|
| /worksp/ |     | soul: forever    | Postgres     |
| /skills/ |     |             |    | (audit, logs)|
`==========`     `=============`    `==============`
.================================================================.
|  API SERVER CONTAINER                         [external-facing]|
|  Settings panel (channels, orgs, LLMs, schedules)             |
|  Visual graph interface API                                    |
|  Skill agent management                                        |
|  User authentication (JWT)                                    |
|  Admin panel                                                   |
`================================================================`
```

---

### 28.10 Operational Considerations

#### Health Monitoring

```
Per-container health checks:
  agent-runtime:    /health endpoint, heartbeat.md freshness check
  channel-gateway:  /health endpoint, webhook connectivity check
  trigger-engine:   /health endpoint, queue depth monitoring
  graph-db:         Postgres standard health checks
  cache:            Redis PING

Alerting thresholds:
  Queue depth > 50 triggers for single user -> investigate
  LLM API error rate > 5% -> alert
  S3 write failure -> critical alert (state at risk)
  Container restart > 3 times in 10 minutes -> alert
```

#### Observability

```
Structured logging: all containers emit JSON logs
  Fields: user_id, org_id, trigger_type, session_id,
          duration_ms, llm_tokens_used, action_taken

Metrics:
  Trigger volume per user per type
  LLM API latency and token consumption
  S3 read/write latency
  Redis hit/miss ratio
  Graph DB query latency per query type
  End-to-end trigger-to-response time

Tracing:
  Distributed trace per trigger invocation
  Trace spans: trigger receipt -> file load ->
               LLM call -> state write -> outbound send
```

#### Secret Management

```
LLM API keys:     Never stored in MD files or environment variables
                  Stored in: AWS Secrets Manager / GCP Secret Manager
                  / Azure Key Vault / HashiCorp Vault (on-premise)
                  Referenced in assets.md as KEY-REF-[id] only
                  Injected into container at runtime via secret store

S3 credentials:   IAM roles (no static credentials)
DB passwords:     Secret store, rotated on schedule
JWT secrets:      Secret store, rotated on schedule
```

---

### 28.11 Container Responsibilities and Scaling Analysis (1,000 Users)

This section documents the responsibility of each container and formally records the scaling requirements identified during Phase 3 design review. These requirements are delivered in Phase 5.

#### Container Responsibility Summary

| Container | External? | Scaling model | Primary responsibility |
|-----------|-----------|---------------|------------------------|
| `agent-runtime` | No | One per user, idle-to-zero | LLM reasoning loop, MD file read/write, skill agent threads |
| `channel-gateway` | **Yes** | Horizontal replicas behind ALB | Inbound auth + normalisation, outbound dispatch, attachment S3 upload |
| `trigger-engine` | No | Horizontal queue-processor replicas | Cron scheduler, follow-up timers, graph event listener, DLQ handler |
| `graph-db` | No | Primary + read replica, PgBouncer pool | Postgres + AGE graph store + pgvector embeddings |
| `relational-db` | No | Primary + read replica | Audit log, state history, conversation archive, user registry |
| `cache` | No | Redis Cluster (3-node HA) | Write-through MD file cache, active conversation context, JWT jti revocation, trigger queues |
| `api-server` | **Yes** | Horizontal replicas behind ALB | Settings panel, OAuth callback, JWT issuance, user onboarding provisioning |

#### `agent-runtime` — Scale-to-Zero is Mandatory

At 1,000 users, ~5–15% are active simultaneously during business hours (50–150 warm containers). Keeping 1,000 containers alive at idle cost (~$3–5/user/month) is viable but wasteful. Scale-to-zero is required for personal and team tiers.

```
Cold-start mitigation:
  Problem:   Fargate cold start ≈ 8–12 seconds — acceptable for async
             triggers (briefings, follow-ups) but borderline for real-time chat.
  Mitigation: Pre-warm on first message arrival while sending "typing..."
             indicator to the channel. Container is warm by the time the
             LLM call completes.

Scale-to-zero policy:
  Personal tier:    zero overnight (configurable quiet hours)
  Team tier:        keep alive during configured working hours
  Enterprise tier:  always-on with minimum replica count

Tooling:
  ECS:  Application Auto Scaling with custom metric (trigger queue depth)
  EKS:  KEDA (Kubernetes Event-Driven Autoscaling) on Redis Stream length
```

#### `channel-gateway` — Horizontal Replicas Required

Single container is a bottleneck at 1,000 users during morning message peaks. Design is stateless — any replica can handle any webhook.

```
Scaling approach:
  Deploy 2–4 replicas behind ALB (round-robin).
  WhatsApp and Telegram webhooks: any replica handles any POST.
  IMAP polling: move to SES inbound routing in production to
    eliminate per-mailbox poller threads (one SES rule fires a
    Lambda → POST to gateway, no persistent IMAP connection pool).

IMAP vs SES in production:
  Local dev / testing:  IMAP polling (Phase 1/2 implementation)
  Production (Phase 5): SES inbound → S3 → Lambda → gateway POST
    Eliminates long-lived IMAP connections and connection pool limits.
    SES handles TLS, spam filtering, and delivery guarantees.
```

#### `trigger-engine` — Morning Briefing Spike

The critical risk: if all 1,000 users have briefings at 08:00 UTC, 1,000 trigger events hit the queue processor simultaneously. A single-threaded processor serializes them.

```
Mitigations (in order of priority):
  1. Per-user briefing jitter: daily_briefing_hour_utc in OrgSettings
     already supports per-user configuration. Spread users across
     a 60-minute window to flatten the spike.
  2. Horizontal queue-processor workers: cron scheduler is a single
     instance (lightweight, stateful clock); queue processors are
     stateless consumers on the same Redis Stream consumer group.
     Scale processor replicas independently of the scheduler.
  3. EventBridge Scheduler (AWS) or Cloud Scheduler (GCP) for cron
     reliability — optional but recommended for production.

Queue processor scaling:
  Redis Stream consumer group: multiple processor instances consume
  from the same stream; each message delivered to exactly one consumer.
  No coordination required — add replicas freely.
```

#### `graph-db` — Connection Pool and Read Replica

At 1,000 users with up to 500 tasks each, the graph DB faces two distinct pressures: connection count (1,000 agent-runtime containers × N connections each = thousands of open Postgres connections) and query load during briefing windows.

```
Required before production at 1,000 users:
  1. PgBouncer connection pool in front of Postgres.
     Target: max_connections=200 on Postgres; PgBouncer pools
     to thousands of application connections via transaction mode.
  2. Read replica for scoring/briefing queries (SELECT-heavy).
     Primary handles writes only.
  3. AGE-specific indexes:
     - vlabel index (node type lookups)
     - user_id property index (all queries are user-scoped)
     - state property index (filtering by task state)
     - due_date property index (urgency scoring)
  4. Query timeout: 5-second hard timeout on graph traversals
     to prevent long-running queries blocking the primary.

Scale ceiling:
  Postgres + AGE is validated to ~5,000 users at this task density.
  Beyond 5,000: evaluate Amazon Neptune (managed) or CitusDB
  (distributed Postgres). PRD Design Principle 27 (polyglot
  persistence) ensures this is a backend swap, not an architectural change.
```

#### `relational-db` — Standard Postgres Scaling

Stores audit logs, state history, conversation archive, user registry, MCP server registry, A2A key store. At 1,000 users this is tens of millions of rows — well within single-instance capacity.

```
Actions required at 1,000 users:
  - Read replica for audit log / reporting queries.
  - Partition audit_log table by month (time-series growth pattern).
  - Index on (user_id, created_at) for per-user history queries.
```

#### `cache` (Redis) — HA Cluster

Per-user namespacing is already correct. Memory footprint at 1,000 users:

```
Active conversation context: ~50 KB × 150 simultaneous = 7.5 MB
MD file write-through cache:  ~2 MB × 1,000 users      = 2 GB
JWT revocation list:          sparse                     = negligible
Trigger queues (Redis Streams): ~1 KB/event             = negligible

Total at 1,000 users: ~2–3 GB
Recommended instance: cache.r6g.large (13 GB) — 4–5x headroom.

HA configuration:
  Redis Cluster (3 primary + 3 replica nodes).
  Consistent hashing by USER-[id] prefix.
  No application changes required — same CLIENT interface.
```

#### `api-server` — Standard Horizontal Scaling

Stateless FastAPI. 2–3 replicas behind ALB handle 1,000 users with substantial headroom. The onboarding provisioning path (IAM role creation) is the only operation with external latency (AWS IAM API ~1–2 seconds) — this is async and does not block the HTTP response.

#### Scaling Verdict Summary

```
Container        | 1,000 users    | Required action
-----------------+----------------+------------------------------------------
agent-runtime    | ✅ with config  | Fargate Spot / KEDA idle-to-zero
                 |                | Pre-warm on first message arrival
channel-gateway  | ✅ with replicas| 2–4 replicas + ALB
                 |                | SES inbound (replaces IMAP in prod)
trigger-engine   | ✅ with config  | Per-user briefing jitter
                 |                | Horizontal queue-processor replicas
graph-db         | ✅ with tuning  | PgBouncer + read replica + AGE indexes
relational-db    | ✅ as-is        | Partition audit_log; add read replica
cache            | ✅ as-is        | Redis Cluster (3-node HA)
api-server       | ✅ as-is        | 2–3 replicas + ALB
```

None of these require architectural changes — all are operational and configuration concerns. The per-user container isolation model scales linearly: adding users adds containers proportionally, and the idle-to-zero policy keeps cost proportional to active users, not total users.

---

## 29. Architecture: Channel Integration Layer

### 29.1 Core Decisions

| Concern | Decision | Rationale |
|---------|----------|-----------|
| Deployment | Separate container | Independent scaling, isolated failure domain |
| Secrets | AWS Secrets Manager via IAM service principal | No credentials in env vars, files, or code |
| Message reliability | SQS for all inbound and outbound | At-least-once delivery, 4-day retention, DLQ handling |
| Active conversation state | Redis cache | Fast context reads, TTL-based lifecycle, no DB round-trips |
| Conversation close trigger | Task updates complete OR explicit close OR TTL | Agent holds context as long as the conversation is live |
| Attachment handling | Extracted at gateway, stored to S3 | Agent-runtime never receives raw binary |

---

### 29.2 Container Responsibilities

The channel gateway container owns two distinct concerns: receiving and authenticating inbound messages from all three channels, and dispatching outbound messages to the correct channel API. These are separate internal processes within the same container.

```
.---------------------------------------------------------.
|  CHANNEL GATEWAY CONTAINER                            |
|                                                        |
|  Inbound Receiver (HTTP server)                        |
|    /webhooks/whatsapp  -> authenticate -> normalize    |
|    /webhooks/telegram  -> authenticate -> normalize    |
|    /webhooks/email     -> authenticate -> normalize    |
|    IMAP poller thread  -> authenticate -> normalize    |
|    -> Extract attachments -> S3                        |
|    -> Resolve sender -> USER-[id]                      |
|    -> Write to SQS: inbound-messages                   |
|    -> Return 200 immediately to channel API            |
|                                                        |
|  Outbound Dispatcher (background worker)               |
|    -> Poll SQS: outbound-messages                      |
|    -> Route by channel to correct API                  |
|    -> Retry with backoff on failure                    |
|    -> DLQ after 3 failed attempts                      |
`---------------------------------------------------------'
```

---

### 29.3 Inbound Authentication — Per Channel

#### WhatsApp Business API

WhatsApp uses webhook push. Meta sends an HTTPS POST to the gateway on every incoming message. The gateway verifies authenticity using HMAC-SHA256 before processing.

```
Meta servers
  -> POST https://gateway.app/webhooks/whatsapp
  Headers: X-Hub-Signature-256: sha256=[HMAC of raw body]

Authentication:
  1. Read raw request body (before any parsing)
  2. Fetch WhatsApp App Secret from Secrets Manager:
       /workgraph/channels/whatsapp/app-secret
  3. Compute: HMAC-SHA256(raw_body, app_secret)
  4. Compare with X-Hub-Signature-256 header
     (constant-time comparison to prevent timing attacks)
  5. Mismatch -> 403 Forbidden, drop silently, log attempt
  6. Match -> extract sender phone, proceed to normalization

Webhook verification (one-time setup):
  Meta sends GET with hub.challenge parameter
  Gateway responds with hub.challenge value to confirm ownership
```

#### Telegram Bot API

Telegram uses webhook push with a secret token header for verification.

```
Telegram servers
  -> POST https://gateway.app/webhooks/telegram
  Headers: X-Telegram-Bot-Api-Secret-Token: [secret]

Authentication:
  1. Read X-Telegram-Bot-Api-Secret-Token header
  2. Fetch expected token from Secrets Manager:
       /workgraph/channels/telegram/webhook-secret
  3. Compare header value against stored secret
  4. Mismatch -> 403 Forbidden, drop silently
  5. Match -> extract Telegram user_id, proceed

Bot token (used for outbound API calls only):
  Stored in Secrets Manager: /workgraph/channels/telegram/bot-token
  Never used for inbound verification
```

#### Email — Two Sub-Modes

**Mode A: AWS SES (SaaS and AWS deployments)**
```
Email arrives at agent address (e.g. jd-agent@workgraph.app)
  -> AWS SES receives it
  -> SES verifies SPF and DKIM automatically before delivery
  -> SES rule fires: POST to gateway /webhooks/email
     OR SES writes to S3 -> Lambda -> gateway POST
  -> Gateway trusts SES Authentication-Results header
     (SES has already done the cryptographic verification)
  -> Gateway extracts From: address, proceeds to normalization
```

**Mode B: IMAP Polling (on-premise / non-AWS deployments)**
```
Gateway container runs IMAP polling background thread
  Poll interval: 60 seconds
  For each unread message:
    1. Extract headers: From, Subject, Date, Message-ID
    2. Verify SPF record via DNS lookup on sender domain
    3. Verify DKIM signature from email headers
    4. FAIL -> discard message, log sender
    5. PASS -> proceed to normalization
    6. Mark message as read (prevents re-processing)
    7. Archive processed messages to processed/ mailbox folder
```

---

### 29.4 Normalization — Internal Message Format

After authentication, every message from every channel is normalized into a single internal format. From this point all downstream processing is channel-agnostic.

```json
{
  "message_id":    "MSG-[uuid]",
  "received_at":   "2025-03-06T09:30:00Z",
  "channel":       "whatsapp | telegram | email",
  "direction":     "INBOUND",

  "sender": {
    "channel_id":          "+1234567890",
    "user_id":             "USER-john-doe",
    "verified":            true,
    "verification_method": "HMAC | BOT_TOKEN | DKIM | SES_VERIFIED"
  },

  "content": {
    "type":        "TEXT | VOICE | IMAGE | FILE | EMAIL",
    "text":        "TSK-001 done, report attached",
    "attachments": [{
      "attachment_id":  "ATT-[uuid]",
      "type":           "FILE",
      "filename":       "report.pdf",
      "mime_type":      "application/pdf",
      "size_bytes":     204800,
      "storage_path":   "s3://app-bucket/attachments/USER-[id]/ATT-[uuid]"
    }]
  },

  "routing": {
    "target_user_id":  "USER-john-doe",
    "target_org_id":   "ORG-work",
    "reply_channel":   "whatsapp",
    "reply_address":   "+1234567890"
  }
}
```

**Attachment handling at gateway:** All attachments (images, files, voice notes) are extracted from the raw channel payload, uploaded to S3 immediately at the gateway layer, and replaced with an `s3://` storage path in the normalized message. The agent-runtime never receives raw binary data — it always receives an S3 reference. This keeps the SQS message size small and avoids binary handling in the reasoning layer.

---

### 29.5 SQS Queue Architecture

SQS is the reliability boundary between the gateway and the agent-runtime. Once a message is in SQS it is guaranteed to be delivered even if the agent container is temporarily unavailable, restarting, or scaling.

#### Queue Definitions

```
INBOUND_MESSAGE_QUEUE
  Name:                 inbound-messages (shared, user_id as attribute)
                        OR inbound-messages-USER-[id] (enterprise tier)
  Message:              Normalized InboundMessage JSON
  Visibility timeout:   30 seconds
  Retention period:     4 days
  DLQ:                  inbound-messages-dlq
  DLQ max receives:     3 (move to DLQ after 3 failed attempts)

OUTBOUND_MESSAGE_QUEUE
  Name:                 outbound-messages
  Message:              OutboundMessage JSON
  Visibility timeout:   60 seconds (outbound API calls can be slow)
  Retention period:     24 hours
  DLQ:                  outbound-messages-dlq
  DLQ max receives:     3

TRIGGER_EVENT_QUEUE
  Name:                 trigger-events (shared with user_id attribute)
  Message:              TriggerEvent JSON (all 4 trigger types)
  Visibility timeout:   30 seconds
  Retention period:     4 days
  DLQ:                  trigger-events-dlq
  DLQ max receives:     3
```

#### Shared vs. Per-User Queue Policy

```
Personal / Team tier:
  Shared queue with MessageAttributeValue user_id
  SQS message filtering routes to correct consumer
  Lower operational overhead, no queue lifecycle management

Enterprise tier:
  Per-user dedicated queues
  Hard throughput isolation
  Provisioned on user onboard, deleted on account closure
  Preferable when SLA guarantees per-user response times
```

---

### 29.6 End-to-End Message Flow

#### Inbound Path

```
[Channel API]
  POST to /webhooks/[channel]
  |
[Channel Gateway — Inbound Receiver]
  1. Receive raw HTTP request
  2. Authenticate (HMAC / bot token / DKIM / SES header)
     FAIL -> 403, log, done
     PASS -> continue
  3. Extract sender channel_id
  4. Lookup user_id from sender registry (Postgres)
     NOT FOUND -> provisional handling (Section 29.9)
     FOUND -> continue
  5. Extract attachments -> upload to S3
  6. Normalize to InboundMessage JSON
  7. Write to SQS: inbound-messages
     MessageAttributes: { user_id, org_id, channel, priority }
  8. Return HTTP 200 to channel API immediately
     (channel APIs time out if acknowledgment is slow)
  |
[SQS: inbound-messages]
  Message persisted. At-least-once delivery guaranteed.
  Survives agent container restarts, scaling events, outages.
  |
[Agent Runtime: agent-runtime-USER-[id]]
  1. Long-poll SQS (20s wait, up to 10 messages per batch)
  2. Receive InboundMessage
  3. Load conversation cache: conv:USER-[id]:active
  4. Process via Inbound Update Protocol (Section 8)
  5. Update graph DB and MD files
  6. Update conversation cache (append message, update pending_tasks)
  7. Compose reply -> write OutboundMessage to SQS: outbound-messages
  8. Delete message from SQS (success acknowledgment)
     Processing fails -> message returns after visibility timeout
     -> Retry up to 3 times -> DLQ
```

#### Outbound Path

```
[Agent Runtime]
  1. Compose outbound message
  2. Write OutboundMessage JSON to SQS: outbound-messages
     {
       "message_id":    "MSG-OUT-[uuid]",
       "user_id":       "USER-john-doe",
       "channel":       "whatsapp",
       "recipient":     "+1234567890",
       "content_type":  "TEXT",
       "text":          "Got it. TSK-001 updated to COMPLETE.",
       "created_at":    "2025-03-06T09:30:15Z"
     }
  |
[SQS: outbound-messages]
  |
[Channel Gateway — Outbound Dispatcher]
  1. Poll SQS outbound-messages (continuous background worker)
  2. Read OutboundMessage
  3. Route by channel:
       whatsapp -> POST to WhatsApp Business API
                   using access_token from Secrets Manager
       telegram -> POST to Telegram Bot API
                   using bot_token from Secrets Manager
       email    -> SMTP send via AWS SES
                   using SES credentials from Secrets Manager
  4. SUCCESS:
       Delete message from SQS
       Log delivery to conversation_thread table
  5. TRANSIENT FAILURE (rate limit, 5xx from channel API):
       Do not delete — message returns after visibility timeout
       Retry with exponential backoff: 30s, 60s, 120s
  6. PERMANENT FAILURE (invalid number, account blocked):
       Move to DLQ after 3 attempts
       Alert platform ops
       Notify user via fallback channel if configured:
         "I could not deliver a message to [channel].
          Check your [channel] configuration in settings."
```

---

### 29.7 Active Conversation Cache — Redis Design

The orchestrating agent maintains active conversation context in Redis for the duration of an in-flight conversation. This enables multi-turn reasoning without re-reading the full ConversationThread from Postgres on every message.

#### Cache Entry Schema

```
Redis key:   conv:USER-[id]:active
Redis type:  Hash

Field             Type     Description
conv_id           string   Unique conversation ID
opened_at         ts       When conversation was created
last_message_at   ts       Last inbound or outbound message
active_channel    string   Channel to use for next reply
channel_history   json     All channels used in this conversation
messages          json     Last 20 messages (inbound + outbound)
pending_tasks     json     Task IDs mentioned, not yet resolved
pending_decisions json     Decisions surfaced, awaiting user response
context_summary   text     Agent's rolling summary of thread
status            string   ACTIVE | CLOSING | CLOSED
ttl_seconds       int      Default: 14400 (4 hours)
```

#### Conversation Lifecycle

```
MESSAGE RECEIVED:
  conv:USER-[id]:active exists?
    YES -> append to messages[], update last_message_at, reset TTL
    NO  -> create new entry, set TTL: 14400s

AGENT PROCESSES:
  Load conv:USER-[id]:active into reasoning context
  Enables multi-turn awareness:
    "what did we just discuss?"
    "going back to the task from earlier"
    "did you get my last message?"
  All answered from cache without DB query.

TASK UPDATED (from this conversation):
  Remove resolved task_id from pending_tasks[]
  If pending_tasks[] empty AND pending_decisions[] empty:
    Set status: CLOSING
    Agent confirms with user if appropriate
    TTL reduced to 1800s (30 minutes, short cleanup window)

USER CLOSES CONVERSATION:
  Trigger phrases: "thanks", "got it", "done", "all set", "bye"
  Agent recognizes closure intent
  Set status: CLOSED
  Flush all messages to conversation_thread table in Postgres
  Delete Redis key immediately

TTL EXPIRY (4 hours of inactivity):
  Redis auto-deletes key
  Redis keyspace notification triggers handler:
    Flush conversation to Postgres (durable archive)
    If pending_tasks still open:
      Add note to next briefing:
        "Conversation from earlier today had unresolved items —
         [task list]. Want me to follow up?"

NEW BRIEFING FIRES:
  Checkpoint current active conversation to Postgres
  Briefing runs as a new conversation entry
  Prior conversation accessible in history if needed
```

#### Channel Switch Within Active Conversation

```
Example: user starts on WhatsApp, switches to Telegram mid-thread

09:00  WhatsApp inbound: "What's the status on TSK-001?"
       conv:USER-[id]:active.active_channel = "whatsapp"
       conv:USER-[id]:active.channel_history = ["whatsapp"]
       Agent replies via WhatsApp

09:15  Telegram inbound: "Also, can you follow up with Mike?"
       Gateway normalizes, writes to SQS with channel: telegram
       |
       Agent-runtime processes:
         Reads conv:USER-[id]:active
         Updates: active_channel = "telegram"
         Updates: channel_history = ["whatsapp", "telegram"]
         Appends: new message to messages[]
         Replies: via Telegram (follows the user)

09:20  Next outbound message:
         OutboundMessage.channel = "telegram"
         Agent never replies to WhatsApp in this thread
         unless user explicitly returns to WhatsApp
```

---

### 29.8 Secrets Management — Full Flow

All channel credentials are stored in AWS Secrets Manager and accessed by the gateway container via IAM service principal. No credentials exist in environment variables, config files, or MD files.

#### Secret Naming Convention

```
/workgraph/channels/whatsapp/app-secret
/workgraph/channels/whatsapp/access-token
/workgraph/channels/whatsapp/phone-number-id

/workgraph/channels/telegram/bot-token
/workgraph/channels/telegram/webhook-secret

/workgraph/channels/email/smtp-credentials
/workgraph/channels/email/ses-access-key

/workgraph/channels/USER-[id]/whatsapp-business-account-id
  (per-user channel-specific identifiers stored separately)
```

#### Runtime Secret Access Pattern

```
Container startup:
  1. Container assumes IAM role via ECS task role
  2. Fetches platform-level secrets from Secrets Manager
     (WhatsApp app secret, Telegram bot token, SES credentials)
  3. Caches in container memory: 15-minute TTL
  4. Refreshes before TTL expiry (background thread)

Per-request (HMAC verification):
  1. Read app_secret from in-memory cache
  2. Compute HMAC
  3. Secret never written to disk, never logged

Secret rotation:
  Platform rotates tokens on schedule or on compromise
  Container re-fetches on next cache refresh cycle
  Zero-downtime: old and new tokens valid during rotation window
```

#### IAM Policy for Channel Gateway Container

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:*:*:secret:/workgraph/channels/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility",
        "sqs:GetQueueAttributes"
      ],
      "Resource": [
        "arn:aws:sqs:*:*:inbound-messages*",
        "arn:aws:sqs:*:*:outbound-messages*",
        "arn:aws:sqs:*:*:trigger-events*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::app-bucket/attachments/*"
    }
  ]
}
```

---

### 29.9 Provisional Sender Handling

When a message arrives from a sender not registered in the system (the network growth scenario from Section 22):

```
Gateway receives message, sender lookup fails:
  |
  Check ResourceNode table:
    Is this sender a PROVISIONAL_RESOURCE?
    (was a task ever assigned to this email/phone?)

  PROVISIONAL_RESOURCE found:
    Normalize message normally
    Set routing.target_user_id = owner of the task
      assigned to this resource
    Write to inbound-messages queue
    Agent processes as standard inbound task update
    from that resource

  No match at all (completely unknown sender):
    Write to unmatched-messages queue
    Log: sender identity, channel, timestamp
    No reply sent (do not confirm active address)
    Trigger-engine DLQ handler reviews for abuse patterns
    Platform ops can inspect and manually resolve
```

---

### 29.10 Dead Letter Queue Processing

Messages that fail three processing attempts are moved to the DLQ. The trigger-engine container runs a background DLQ handler.

```
DLQ handler logic (runs in trigger-engine container):

  Read message from DLQ
  Classify failure type:

  TRANSIENT (agent container was down, DB unavailable):
    Re-enqueue to source queue with 5-minute delay
    Increment retry counter on message
    If retry_count > 10: escalate to permanent

  PERMANENT (bad content, unresolvable parsing error):
    Log to error_log table in Postgres:
      { user_id, channel, raw_content, error_type, failed_at }
    Notify user via fallback channel:
      "I received a message I could not process.
       Please resend or contact support."
    Alert platform ops dashboard

DLQ retention: 14 days
DLQ alarm:     alert if depth > 10 messages (CloudWatch / equivalent)
```

---

### 29.11 Channel Gateway Full Architecture Summary

```
.================================================================.
|  EXTERNAL CHANNELS                                            |
|  WhatsApp Business API  |  Telegram Bot API  |  Email / SES  |
`=========.===============.===========.=========.==============`
          |               |           |         |
          v               v           v         v (IMAP poll)
.================================================================.
|  CHANNEL GATEWAY CONTAINER                                    |
|                                                               |
|  INBOUND RECEIVER (HTTP + IMAP)                               |
|  ┌──────────────────────────────────────────────────────────┐ |
|  │ /webhooks/whatsapp  HMAC-SHA256 verify                   │ |
|  │ /webhooks/telegram  X-Telegram-Bot-Api-Secret-Token      │ |
|  │ /webhooks/email     SES Authentication-Results header    │ |
|  │ IMAP poller thread  SPF + DKIM verify                    │ |
|  │                                                          │ |
|  │ All channels -> normalize -> InboundMessage JSON         │ |
|  │ Attachments -> S3 /attachments/USER-[id]/ATT-[uuid]      │ |
|  │ Sender -> USER-[id] lookup (Postgres)                    │ |
|  │ Write to SQS: inbound-messages                           │ |
|  │ Return 200 immediately to channel API                    │ |
|  └──────────────────────────────────────────────────────────┘ |
|                                                               |
|  OUTBOUND DISPATCHER (background worker)                      |
|  ┌──────────────────────────────────────────────────────────┐ |
|  │ Poll SQS: outbound-messages (continuous)                 │ |
|  │ Route: whatsapp -> WhatsApp Business API                 │ |
|  │        telegram -> Telegram Bot API                      │ |
|  │        email    -> AWS SES / SMTP                        │ |
|  │ Success: delete from SQS                                 │ |
|  │ Failure: backoff retry -> DLQ after 3 attempts           │ |
|  └──────────────────────────────────────────────────────────┘ |
|                                                               |
|  SECRETS (all via AWS Secrets Manager, IAM role)              |
|  WhatsApp: app_secret, access_token, phone_number_id          |
|  Telegram: bot_token, webhook_secret_token                    |
|  Email:    SMTP credentials / SES access                      |
|  SQS:      IAM role (no static credentials)                   |
`================================================================`
          |                               |
          v (SQS inbound-messages)        v (SQS outbound-messages)
.=========v==============================.v=====================.
|  AGENT RUNTIME CONTAINERS             |  (consumed here)     |
|  agent-runtime-USER-[id]              |                      |
|  Reads inbound, writes outbound       |                      |
|  Maintains conv:USER-[id]:active      |                      |
|  in Redis for conversation duration   |                      |
`================================================================`
```

---

## 30. Architecture: Skill Agent Runtime

### 30.1 Core Decisions

| Concern | Decision | Rationale |
|---------|----------|-----------|
| Invocation model | Async thread within agent-runtime container | Non-blocking, multiple skill agents can run concurrently per user, no separate infrastructure |
| File system access | boto3 with configurable endpoint URL | Cloud-agnostic (S3, GCS, Azure Blob, MinIO), swap by env var only |
| Status monitoring | S3 event notification -> SQS -> trigger engine | Same pattern as channel messages, no polling, seconds-latency notification |
| Long-running heartbeat | Periodic status.md writes every 5 minutes | Orchestrating agent detects silent failures without polling threads directly |
| A2A authentication | API key per registered agent (hashed in graph DB) | Simple, per-agent scoping, revocable, no shared secrets |
| Failure recovery | Container restart reads graph + S3 status.md to detect and resume | Consistent with overall MD file system recovery model |

---

### 30.2 Invocation Model — Async Thread

The orchestrating agent runs on the main thread of the agent-runtime container. When it detects a task matching a skill agent trigger, it spawns an async worker thread within the same container. The main thread continues processing other triggers without waiting.

```
agent-runtime-USER-[id] container
  |
  Main thread: orchestrating agent
    Processes trigger queue normally
    Detects: TSK-JD-4901 matches research-agent-v1
    Writes task.md to S3 task folder
    Spawns async thread -> SkillAgentWorker(TSK-JD-4901)
    Registers in active_skill_threads dict
    Continues main loop (not blocked)
  |
  Async Thread: SkillAgentWorker — TSK-JD-4901
    Independent execution, shares container IAM role
    Reads S3, calls LLM, writes S3
    Main thread never waits for this thread
  |
  Async Thread: SkillAgentWorker — TSK-JD-4903 (concurrent)
    A second skill agent running in parallel on a different task
    Both threads active simultaneously in the same container
```

#### Thread Registry and Limits

```
active_skill_threads (in-memory dict, per container):
  {
    "TSK-JD-4901-DEL": SkillAgentWorker(
      state=RUNNING, started_at=..., last_heartbeat_at=...),
    "TSK-JD-4903-DEL": SkillAgentWorker(
      state=LOADING, started_at=..., last_heartbeat_at=...)
  }

Max concurrent skill threads per container:
  Default: 5
  Configurable in user settings
  If limit reached:
    New skill task state set to QUEUED in graph DB
    Spawned when a running thread completes and slot opens
    Orchestrating agent notified in next briefing if queue grows long

Thread states:
  SPAWNED    -> created, not yet started
  LOADING    -> reading SKILL.md and context files from S3
  RUNNING    -> LLM call in flight
  WRITING    -> writing output.md and status.md to S3
  COMPLETE   -> finished cleanly, thread exits
  FAILED     -> error state, thread exits with failure written to status.md
  CANCELLED  -> orchestrating agent cancelled the task
```

---

### 30.3 S3 File Access via boto3

All S3 operations use a shared StorageClient instance initialized once at container startup. Credentials come from the container's IAM role — no per-thread or per-skill credential setup.

The `endpoint_url` environment variable makes this fully cloud-agnostic. Switching from AWS S3 to GCS, Azure Blob, or on-premise MinIO requires only an environment variable change — no code changes anywhere in the skill agent logic.

```python
class StorageClient:

    def __init__(self):
        self.client = boto3.client(
            's3',
            endpoint_url=os.environ['STORAGE_ENDPOINT_URL'],
            # AWS S3:       https://s3.amazonaws.com
            # GCS (compat): https://storage.googleapis.com
            # Azure Blob:   https://[account].blob.core.windows.net
            # MinIO:        https://minio.internal:9000
            region_name=os.environ.get('STORAGE_REGION', 'us-east-1')
        )
        self.bucket = os.environ['STORAGE_BUCKET_NAME']

    def read_file(self, key: str) -> str:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].read().decode('utf-8')

    def write_file(self, key: str, content: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content.encode('utf-8'),
            ContentType='text/markdown'
        )

    def list_prefix(self, prefix: str) -> list[str]:
        response = self.client.list_objects_v2(
            Bucket=self.bucket, Prefix=prefix)
        return [obj['Key'] for obj in response.get('Contents', [])]

    def file_exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.NoSuchKey:
            return False
```

---

### 30.4 SkillAgentWorker — Execution Steps

```
STEP 1: Write initial status to S3
  STATUS: IN_PROGRESS, PROGRESS: 0
  -> Triggers S3 event -> SQS -> orchestrating agent knows task started

STEP 2: Load SKILL.md from S3
  Path: skills/USER-[id]/[skill-id]/SKILL.md
  Parse: skill metadata, instructions, context variables, output format

STEP 3: Load task.md (written by orchestrating agent before spawning)
  Path: workspaces/USER-[id]/tasks/[task-id]/task.md
  Contains: objective, deadline, output requirements, completion signal

STEP 4: Load context files (upstream agent outputs if chained)
  Path: workspaces/USER-[id]/tasks/[task-id]/context/
  List all files, read each into context_content dict

STEP 5: Resolve LLM configuration
  Read llm_provider and model from SKILL.md frontmatter
  If "any": use user's default skill LLM from assets.md
  If "fast": resolve to fastest configured model
  If "best": resolve to highest-capability configured model
  Fetch API key reference from assets.md -> retrieve from Secrets Manager

STEP 6: Build prompt
  Inject context variables into SKILL.md instruction template:
    {user.name}           -> from user/profile.md
    {task.description}    -> from task.md
    {task.goal_context}   -> from task.md
    {recipient.name}      -> resolved via aliases.md if needed
    {context.*}           -> from context/ files

STEP 7: Call LLM API (async HTTP, non-blocking)
  Stream response if supported (reduces time-to-first-token)
  Write intermediate status at 25%, 50%, 75% progress
  Each intermediate write triggers S3 event -> SQS -> progress update

STEP 8: Write output.md to S3
  Path: workspaces/USER-[id]/tasks/[task-id]/output.md
  Content: raw LLM output (structured per SKILL.md output format)

STEP 9: Extract and write artifacts (if any)
  Parse output for file-like sections (code blocks, tables, documents)
  Write to: workspaces/USER-[id]/tasks/[task-id]/artifacts/[filename]

STEP 10: Write STATUS: COMPLETE to status.md
  Path: workspaces/USER-[id]/tasks/[task-id]/status.md
  This is the completion signal — triggers SQS notification
  Orchestrating agent reads output.md and proceeds with handoff
```

---

### 30.5 status.md File Format

The status.md file is the live reporting mechanism. Every write triggers an S3 event notification. The orchestrating agent reads this file (single S3 GetObject call) when notified.

```markdown
---
task_id:    TSK-JD-4901-DEL
agent:      research-agent-v1
updated_at: 2025-03-06T10:45:00Z
---

STATUS: IN_PROGRESS
PROGRESS: 60
NOTES: Completed sections 1-3 of research. Working on conclusions.
       No blockers encountered so far.
```

On completion:
```markdown
STATUS: COMPLETE
PROGRESS: 100
OUTPUT: output.md
ARTIFACTS: artifacts/research-notes.md, artifacts/sources.md
NOTES: Research complete. Flagged one area of uncertainty in
       section 4 for user review.
```

On failure:
```markdown
STATUS: BLOCKED
PROGRESS: 45
ERROR_TYPE: LLM_RATE_LIMIT
NOTES: Hit OpenAI rate limit. Retrying in 60s. Attempt 2 of 3.
```

The `STATUS:` line maps directly to the Inbound Update Protocol status signal taxonomy (Section 8). No special parsing needed — the same extraction logic handles skill agent status as any other inbound message.

---

### 30.6 Status Monitoring via S3 Event Notifications + SQS

No polling. Every status.md write fires an S3 event. The event lands in SQS. The trigger engine processes it and dispatches to the user's agent-runtime queue. Notification latency is typically under 5 seconds.

```
S3 Bucket Configuration:
  Event type:   s3:ObjectCreated:Put
  Filter:       key suffix = "status.md"
                key prefix = "workspaces/"
  Destination:  SQS queue: skill-status-events

Event message on SQS:
  {
    "event_type":  "SKILL_STATUS_UPDATE",
    "user_id":     "USER-[id]",
    "task_id":     "TSK-JD-4901-DEL",
    "s3_key":      "workspaces/USER-[id]/tasks/TSK-JD-4901-DEL/status.md",
    "timestamp":   "2025-03-06T10:45:00Z"
  }

Trigger engine:
  Receives from skill-status-events
  Writes TriggerEvent to trigger-events-USER-[id]
  Type: GRAPH_STATE_CHANGE

Orchestrating agent main thread:
  Receives trigger
  Single S3 GetObject: reads status.md
  Parses STATUS line
  If COMPLETE:  runs handoff logic, activates next node
  If IN_PROGRESS: updates task progress in graph DB
  If BLOCKED:   evaluates retry or escalation
  If FAILED:    surfaces in briefing for human decision

Cloud-agnostic equivalent:
  AWS:   S3 Event Notifications -> SQS
  GCS:   GCS Pub/Sub Notifications -> internal queue
  Azure: Blob Storage Events -> Azure Service Bus
  MinIO: Webhook notifications -> internal HTTP endpoint -> SQS
```

---

### 30.7 Long-Running Agent Heartbeat Protocol

For tasks taking 10-30 minutes, the orchestrating agent needs assurance the skill agent is alive and progressing — not silently stuck.

```
SkillAgentWorker heartbeat:
  Interval: every 5 minutes during active LLM calls
  Action: write status.md with current progress percentage
  -> S3 event -> SQS -> orchestrating agent receives update
  -> task node progress updated in graph DB
  -> No follow-up action needed while heartbeats are flowing

Orchestrating agent timeout detection:
  Tracks: active_skill_threads[task_id].last_heartbeat_at
  Check interval: every 5 minutes (via trigger engine timer)

  If now - last_heartbeat_at > 15 minutes AND state != COMPLETE:
    Check: task_id still in active_skill_threads?

    YES (thread alive but not writing):
      Wait another 5 minutes before escalating
      LLM may be in a long generation cycle

    NO (thread gone from registry):
      Thread died silently
      Read S3 status.md to get last known state
      Attempt re-spawn (retry count < 3)
      After 3 re-spawns: mark FAILED in graph DB
      Surface in briefing: "Research agent became unresponsive
        on TSK-JD-4901. Last progress: 45%.
        Options: [Retry] [Reassign] [Handle manually]"
```

---

### 30.8 Failure and Retry Protocol

```
LLM API ERROR (rate limit, timeout, context overflow):
  Worker catches exception
  Writes status.md: STATUS: BLOCKED, ERROR_TYPE: LLM_[error]
  Waits: 60s backoff
  Retries: up to 3 times
  After 3 failures:
    Writes: STATUS: FAILED
    S3 event -> SQS -> orchestrating agent surfaces in briefing

CONTEXT WINDOW OVERFLOW:
  Research or document tasks may exceed model context limit
  Worker detects context size before LLM call
  If oversized:
    Splits task into sub-chunks automatically
    Processes in sequence, merges outputs
    Writes partial progress updates through the process
    If splitting not possible: STATUS: BLOCKED,
      NOTES: "Task exceeds model context. Manual decomposition needed."

S3 WRITE FAILURE:
  Worker retries write up to 3 times with 10s backoff
  After 3 failures:
    Logs error to container stdout (captured by observability layer)
    Writes minimal error state to in-memory fallback
    Orchestrating agent detects missing heartbeat -> checks thread

CONTAINER RESTART MID-TASK:
  Thread dies. Task still IN_PROGRESS in graph DB.
  On container restart:
    Orchestrating agent reads graph/pending_actions.md from S3
    Detects: task IN_PROGRESS with no active thread
    Reads: workspaces/USER-[id]/tasks/[task-id]/status.md from S3
      Last status COMPLETE:
        output.md exists, notification was lost in restart
        Apply completion logic, activate next node
      Last status IN_PROGRESS:
        Re-spawn SkillAgentWorker
        Worker reads existing partial context if resumable
        Otherwise restarts from beginning
      No status.md found:
        Task never started (spawned but died before first write)
        Re-spawn cleanly
```

---

### 30.9 A2A REST Endpoint — External Agent Authentication

External AI agents (running outside the user's container) submit structured status updates via the A2A REST API. Each registered agent has its own API key.

#### API Key Lifecycle

```
Key format:      wg_agent_[32-random-chars]
                 Prefixed for easy identification in logs

Created:         When user registers an AI agent as a ResourceNode
                 Platform generates key, shows once, never again
                 Stored hashed (SHA-256) in ResourceNode.api_key_hash

Rotated:         User requests rotation in settings panel
                 New key generated, old key invalid immediately
                 Agent reconfigured by user with new key

Revoked:         When ResourceNode is deleted or agent is untrusted
                 Hash cleared from ResourceNode
                 All subsequent requests with old key -> 403

Stored in:       ResourceNode.api_key_hash (SHA-256, in graph DB)
                 Plaintext key: never stored anywhere after display
```

#### Authentication Flow

```
POST /api/v1/task-update
X-Agent-Api-Key: wg_agent_[32-chars]
Content-Type: application/json

{
  "task_id":    "TSK-JD-4821-DEL",
  "agent_id":   "RES-research-agent-01",
  "timestamp":  "2025-03-06T10:30:00Z",
  "status":     "IN_PROGRESS",
  "progress":   75,
  "notes":      "Research sections 1-3 complete.",
  "artifacts":  [...]
}

Authentication steps:
  1. Extract X-Agent-Api-Key header
     Missing -> 401

  2. Extract task_id from body
     Missing -> 400

  3. Look up TaskNode in graph DB
     Not found -> 404

  4. Resolve assigned ResourceNode for task
     No agent assigned -> 403

  5. Hash provided key: SHA-256(api_key)
     Compare against ResourceNode.api_key_hash
     (constant-time comparison)
     Mismatch -> 403

  6. Verify agent is assigned to THIS specific task
     (prevents cross-task update injection)
     Mismatch -> 403

  7. Write to SQS: inbound-messages
     routing.source = "A2A_API"
     routing.target_user_id = task.owner

  8. Return 202 Accepted
     (async processing — agent does not wait for graph update)
```

#### A2A Feeds the Standard Pipeline

After authentication, the A2A update is written to the same SQS inbound-messages queue as channel messages. The orchestrating agent processes it through the same Inbound Update Protocol (Section 8) — no special A2A code path exists downstream of authentication.

```
A2A POST authenticated
  -> Write to SQS: inbound-messages
  -> Same pipeline as WhatsApp message
  -> Same Inbound Update Protocol
  -> Same graph state update logic
  -> Same briefing/interrupt decision
  -> 202 returned to calling agent
```

---

### 30.10 Full Skill Agent Runtime Flow Diagram

```
.================================================================.
|  AGENT-RUNTIME-USER-[id] CONTAINER                           |
|                                                               |
|  MAIN THREAD (Orchestrating Agent)                            |
|  ┌──────────────────────────────────────────────────────────┐ |
|  │ Detects skill trigger on TSK-JD-4901                     │ |
|  │ Writes task.md to S3 task folder                         │ |
|  │ Spawns SkillAgentWorker(TSK-JD-4901, research-v1)        │ |
|  │ Registers in active_skill_threads                        │ |
|  │ Continues processing other triggers                      │ |
|  └──────────────────────────────────────────────────────────┘ |
|                                                               |
|  ASYNC THREAD: SkillAgentWorker — TSK-JD-4901                 |
|  ┌──────────────────────────────────────────────────────────┐ |
|  │ Read S3: skills/.../research-v1/SKILL.md                 │ |
|  │ Read S3: workspaces/.../TSK-JD-4901/task.md              │ |
|  │ Read S3: workspaces/.../TSK-JD-4901/context/*.md         │ |
|  │ Resolve LLM config (assets.md -> Secrets Manager)        │ |
|  │ Build prompt (inject context variables)                  │ |
|  │ Call LLM API (async HTTP)                                │ |
|  │ Write status.md every 5min (heartbeat)                   │ |
|  │ Write output.md to S3                                    │ |
|  │ Write STATUS: COMPLETE to status.md                      │ |
|  └───────────────────────┬──────────────────────────────────┘ |
|                           |                                   |
|  ASYNC THREAD: SkillAgentWorker — TSK-JD-4903 (concurrent)    |
|  └─ (email drafter, separate task, runs in parallel) ─────────|
`===========================│===================================`
                            │ S3 PutObject on status.md
.===========================▼===================================.
|  S3 EVENT NOTIFICATION                                       |
|  Filter: key suffix = status.md                              |
|  -> SQS: skill-status-events                                 |
`===========================│==================================`
                            │
.===========================▼===================================.
|  TRIGGER ENGINE                                              |
|  Receives SKILL_STATUS_UPDATE                                |
|  Writes TriggerEvent to trigger-events-USER-[id]             |
`===========================│==================================`
                            │
.===========================▼===================================.
|  MAIN THREAD: Orchestrating Agent (same container)           |
|  Reads status.md (single S3 GetObject)                       |
|  STATUS: COMPLETE -> reads output.md                         |
|  Assembles context for TSK-JD-4902                           |
|  Spawns SkillAgentWorker(TSK-JD-4902, proposal-writer-v1)    |
`=============================================================='

.================================================================.
|  EXTERNAL AI AGENT                                           |
|                                                               |
|  POST /api/v1/task-update                                    |
|  X-Agent-Api-Key: wg_agent_[32-chars]                        |
|  { task_id, status, progress, notes }                        |
|                  │                                           |
|  Auth: hash key -> compare ResourceNode.api_key_hash         |
|        verify agent assigned to this task                    |
|  -> SQS: inbound-messages (same queue as channels)           |
|  -> Same Inbound Update Protocol                             |
|  <- 202 Accepted                                             |
`================================================================`
```

---

### 30.11 Skill Agent Runtime — Configuration Summary

All skill agent runtime configuration is managed through environment variables on the agent-runtime container. No hardcoded values anywhere in the skill agent logic.

```
STORAGE_ENDPOINT_URL      S3 / GCS / Azure Blob / MinIO endpoint
STORAGE_BUCKET_NAME       Target bucket name
STORAGE_REGION            Cloud region (if applicable)

SQS_ENDPOINT_URL          SQS / Azure Service Bus / GCP Pub/Sub
SQS_INBOUND_QUEUE         inbound-messages queue URL
SQS_SKILL_STATUS_QUEUE    skill-status-events queue URL

SECRETS_BACKEND           aws_secrets_manager | hashicorp_vault |
                          azure_key_vault | env_file (local dev)

MAX_SKILL_THREADS         Max concurrent skill agent threads (default: 5)
SKILL_HEARTBEAT_INTERVAL  Seconds between heartbeat writes (default: 300)
SKILL_TIMEOUT_MINUTES     Minutes before thread considered hung (default: 15)
LLM_DEFAULT_PROVIDER      anthropic | openai | google
LLM_DEFAULT_MODEL         Default model for "any" skill provider specs

A2A_API_KEY_HASH_ALGO     sha256 (default, not configurable in production)
A2A_MAX_REQUEST_SIZE_KB   Maximum A2A request body size (default: 512)
```

---

## 31. Architecture: Security, Identity & Secrets

### 31.1 Identity Domains

The system has four distinct identity domains. Each has different principals, trust mechanisms, and lifecycle needs. No domain's credentials cross into another.

| Domain | Who | Mechanism | Lifecycle Owner |
|--------|-----|-----------|-----------------|
| Human users → Platform | End users (browser/mobile) | OAuth 2.0 (Google/Microsoft/GitHub) + Platform JWT | External IdP + platform |
| Containers → AWS services | The seven containers | AWS IAM Roles (ECS Task Roles / EKS Service Accounts) | AWS (automatic rotation) |
| Containers → External APIs | Agent-runtime, channel-gateway | API keys fetched from Secrets Manager at runtime | Secrets Manager |
| External agents → Platform API | External AI agents (A2A) | Per-agent API keys (hashed in graph DB) | Platform + user |

---

### 31.2 IAM Role Architecture — Least Privilege Per Container

Every container has its own IAM role. No two containers share a role. Each role is scoped to exactly the resources and operations that container needs. This is the least-privilege principle enforced at the infrastructure level.

```
Container                  IAM Role
─────────────────────────────────────────────────
agent-runtime-USER-[id]    agent-role-USER-[id]   (one per user, dynamically provisioned)
channel-gateway            channel-gateway-role   (one shared role)
trigger-engine             trigger-engine-role    (one shared role)
graph-db                   graph-db-role          (one shared role)
relational-db              relational-db-role     (one shared role)
cache                      cache-role             (one shared role)
api-server                 api-server-role        (one shared role)
```

#### agent-role-USER-[id]

Scoped entirely to that user's S3 prefix. The prefix condition in the S3 policy means AWS rejects cross-user access at the IAM layer independently of application-level checks.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "OwnS3Prefix",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::app-bucket/agents/USER-[id]/*",
        "arn:aws:s3:::app-bucket/workspaces/USER-[id]/*",
        "arn:aws:s3:::app-bucket/skills/USER-[id]/*"
      ]
    },
    {
      "Sid": "ReadSystemSkills",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::app-bucket/skills/system/*"
    },
    {
      "Sid": "OwnLLMSecrets",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:*:*:secret:/workgraph/llm/USER-[id]/*",
        "arn:aws:secretsmanager:*:*:secret:/workgraph/llm/platform/*"
      ]
    },
    {
      "Sid": "OwnTriggerQueue",
      "Effect": "Allow",
      "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"],
      "Resource": "arn:aws:sqs:*:*:trigger-events-USER-[id]"
    },
    {
      "Sid": "WriteOutboundQueue",
      "Effect": "Allow",
      "Action": "sqs:SendMessage",
      "Resource": "arn:aws:sqs:*:*:outbound-messages"
    },
    {
      "Sid": "ReadSkillStatusQueue",
      "Effect": "Allow",
      "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage"],
      "Resource": "arn:aws:sqs:*:*:skill-status-events"
    }
  ]
}
```

#### channel-gateway-role

```json
{
  "Statement": [
    {
      "Sid": "ChannelSecrets",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:*:*:secret:/workgraph/channels/*"
    },
    {
      "Sid": "InboundWrite",
      "Effect": "Allow",
      "Action": "sqs:SendMessage",
      "Resource": ["arn:aws:sqs:*:*:inbound-messages*", "arn:aws:sqs:*:*:trigger-events*"]
    },
    {
      "Sid": "OutboundRead",
      "Effect": "Allow",
      "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"],
      "Resource": "arn:aws:sqs:*:*:outbound-messages"
    },
    {
      "Sid": "AttachmentUpload",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::app-bucket/attachments/*"
    }
  ]
}
```

#### trigger-engine-role

```json
{
  "Statement": [
    {
      "Sid": "AllTriggerQueues",
      "Effect": "Allow",
      "Action": ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage"],
      "Resource": [
        "arn:aws:sqs:*:*:trigger-events*",
        "arn:aws:sqs:*:*:inbound-messages*",
        "arn:aws:sqs:*:*:skill-status-events"
      ]
    },
    {
      "Sid": "ReadSchedules",
      "Effect": "Allow",
      "Action": "rds-data:ExecuteStatement",
      "Resource": "arn:aws:rds:*:*:cluster:workgraph-relational-db"
    }
  ]
}
```

#### api-server-role

The api-server has the broadest permissions because it handles onboarding provisioning. It can create new user-scoped IAM roles and S3 prefixes — but only within the workgraph namespace.

```json
{
  "Statement": [
    {
      "Sid": "UserSecretProvisioning",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:CreateSecret", "secretsmanager:UpdateSecret",
        "secretsmanager:DeleteSecret", "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:*:*:secret:/workgraph/llm/USER-*"
    },
    {
      "Sid": "AuthSecrets",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:*:*:secret:/workgraph/auth/jwt-signing-key",
        "arn:aws:secretsmanager:*:*:secret:/workgraph/auth/oauth-client-secret"
      ]
    },
    {
      "Sid": "AgentRoleProvisioning",
      "Effect": "Allow",
      "Action": ["iam:CreateRole", "iam:AttachRolePolicy", "iam:PassRole"],
      "Resource": "arn:aws:iam::*:role/workgraph-agent-role-USER-*"
    },
    {
      "Sid": "UserFolderInit",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::app-bucket/agents/*"
    }
  ]
}
```

---

### 31.3 Human User Authentication — OAuth 2.0 + Platform JWT

Users authenticate through an external OAuth 2.0 IdP. The platform issues its own short-lived JWT after the OAuth exchange. This delegates hard identity problems (password management, MFA, account recovery) to established providers while keeping session management under platform control.

#### Supported Identity Providers

```
Google Workspace    -> professional and general users
Microsoft Entra ID  -> enterprise users, Azure-aligned organizations
GitHub              -> developer and technical users
```

#### OAuth Authentication Flow

URL placeholders used below:
  {OAUTH_REDIRECT_BASE_URL}  env var — base URL of the API server (e.g. http://localhost:8000 or https://api.graphclaw.ai)
  {COCKPIT_BASE_URL}         env var — base URL of the cockpit SPA (e.g. http://localhost:3000 or https://app.graphclaw.ai)

```
1. User visits the cockpit (unauthenticated) — redirected to /login

2. User clicks "Sign in with Google"
   Browser → GET {COCKPIT_BASE_URL}/auth/login?provider=google
   (In local dev this goes through the Vite proxy to the API server on :8000)

3. API server redirects browser to IdP authorization endpoint:
     GET https://accounts.google.com/o/oauth2/v2/auth
       ?client_id=[platform-client-id]
       &redirect_uri={OAUTH_REDIRECT_BASE_URL}/auth/callback?provider=google
       &response_type=code
       &scope=openid email profile
       &state=[CSRF-token]                 <- prevents CSRF
       &code_challenge=[PKCE-challenge]    <- prevents code interception
       &code_challenge_method=S256

4. User authenticates at IdP
   (password, MFA, session — entirely the IdP's responsibility)

5. IdP redirects back with authorization code:
     GET {OAUTH_REDIRECT_BASE_URL}/auth/callback?code=[code]&state=[token]

6. API server validates:
   - state matches CSRF token stored in Redis (prevents CSRF)
   - redirect_uri matches registration (from env, not user-controlled)

7. API server exchanges code for IdP tokens:
     POST https://oauth2.googleapis.com/token
       client_id, client_secret,
       code, redirect_uri, grant_type=authorization_code,
       code_verifier (PKCE)

8. API server validates IdP userinfo and resolves or creates UserNode:
   - Extract email, name, provider_user_id from IdP userinfo
   - email -> lookup in graph DB (idempotency key)
   - New user:      provision UserNode + S3 prefix + WorkspaceNode (atomic, with rollback)
   - Existing user: return existing user_id (no duplicate nodes created)

9. API server issues platform JWT:
     {
       "sub":  "USER-{uuid}",    <- platform user ID (not the OAuth subject)
       "iat":  [timestamp],
       "exp":  [timestamp+900],  <- 15-minute expiry
       "jti":  "[uuid]"          <- unique ID for revocation
     }
     Signed with RS256 using RSA private key

10. API server issues a short-lived one-time exchange code (OTC):
      - Generate 32-byte cryptographically random opaque code
      - Store {user_id, access_token, refresh_token, role} in Redis
        Key: auth:otc:{code}   TTL: 30 seconds   (single-use)
      - 302 redirect to {COCKPIT_BASE_URL}/auth/callback?code={OTC}

11. Browser lands on the cockpit SPA at /auth/callback
    JS reads ?code query param, immediately replaces URL state (removes code from history)
    POST /auth/exchange  { "code": "{OTC}" }

12. API server exchange endpoint:
    - Looks up auth:otc:{code} in Redis
    - Returns {access_token, refresh_token, user_id, role} as JSON
    - Deletes the Redis key immediately (enforces single-use)
    - Returns 404 if code not found or already consumed

13. Cockpit stores tokens in memory + sessionStorage
    Navigates to / — user enters the application
```

#### Post-Auth Redirect — Why One-Time Code Exchange

The OAuth callback completes on the backend (port 8000 / API server), but the cockpit SPA
runs on a separate origin in all environments (port 3000 locally, different subdomain or path
in cloud). The callback cannot return JSON tokens directly because the browser context
(React app) is no longer running on the backend's origin.

Three patterns were considered:

```
URL fragment (#access_token=...):
  Pro:  Simple, no extra round-trip
  Con:  Tokens visible in browser history and to all client-side JS on the page
  Decision: Rejected — tokens must not appear in URL history

httpOnly cookie:
  Pro:  XSS-safe, invisible to JS
  Con:  Requires same-origin or SameSite=None+Secure (HTTPS only)
        In local dev the IdP callback lands on :8000, cockpit is on :3000 — different origins
        Cannot set a usable cross-origin httpOnly cookie without HTTPS + same-site config
  Decision: Deferred — viable when cockpit and API share a domain behind nginx/ALB

One-time code exchange (OTC):   ← chosen implementation
  Pro:  Tokens never appear in URL, work across any origin split, no HTTPS requirement in dev
        OTC is worthless after 30 seconds or first use — window for exploitation is minimal
  Con:  One extra round-trip (POST /auth/exchange)
  Decision: Adopted
```

#### Token Storage — SPA Bearer Pattern

```
Access token:
  Storage:  In-memory Zustand store + sessionStorage (cleared on tab close)
  Sent as:  Authorization: Bearer {token} header on every API request
  Expiry:   15 minutes

Refresh token:
  Storage:  sessionStorage only
  Used for: POST /auth/refresh to obtain a new access token before expiry
  Expiry:   7 days rolling

Logout:
  POST /auth/logout (adds jti to Redis revocation set)
  Clear Zustand store and sessionStorage
  Redirect to /login

Note: localStorage is NOT used for tokens — sessionStorage limits the blast radius
of XSS to the current tab session only.
```

#### JWT Lifecycle

```
Access JWT:
  Expiry:     15 minutes
  Algorithm:  RS256 (asymmetric — private key never leaves the server)
  Storage:    In-memory Zustand store + sessionStorage (SPA Bearer pattern)
  Sent as:    Authorization: Bearer {token} on every API request

Refresh token:
  Expiry:     7 days rolling
  Format:     Opaque random string stored in sessionStorage
  Rotation:   New token issued on every use, old token invalidated in Redis

Token refresh (transparent to user):
  Cockpit detects JWT expiry approaching (before 15-minute mark)
  POST /auth/refresh  { refresh_token }
  API server validates refresh token against Redis
  Issues new access JWT + new refresh token (rotation)
  Old refresh token immediately invalidated

Logout:
  POST /auth/logout
  API server adds access JWT jti to Redis revocation set (TTL = remaining JWT lifetime)
  API server deletes refresh token from Redis
  Zustand store and sessionStorage cleared — user redirected to /login

JWT revocation check (all protected endpoints):
  Verify RS256 signature
  Check exp claim
  Check jti against Redis revocation set
  All three must pass — compromised tokens blocked within seconds of logout
```

#### Dev Auth Mode — Local Development Only

The platform includes a dev token endpoint that bypasses OAuth for local development.
This endpoint is disabled in all non-development deployments by two independent gates
so that neither gate alone is a single point of failure.

```
Gate 1 — Backend (runtime):
  ENVIRONMENT=development  → POST /auth/dev-token returns tokens (HTTP 200)
  ENVIRONMENT=production   → POST /auth/dev-token returns HTTP 403
  ENVIRONMENT unset        → treated as production (safe default)

Gate 2 — Frontend (build time):
  VITE_ENABLE_DEV_AUTH=true  → dev login button rendered in LoginPage
  VITE_ENABLE_DEV_AUTH unset → dev login button not rendered (not in DOM)

Result:
  Local dev:   both gates open  → dev auth available
  Cloud:       both gates closed → dev auth invisible and blocked
  Misconfigured cloud (ENVIRONMENT forgotten): frontend gate still closed → safe
  Adversary hits /auth/dev-token directly in prod: backend gate blocks → 403
```

Dev token request accepts an optional `user_id` field:

```
POST /auth/dev-token
  Body (all fields optional):
    user_id:  string   default "USER-dev-001"
    role:     string   default "ADMIN"

Workflow for developers:
  1. First session: use real OAuth once (Google/GitHub) to provision a real UserNode
     Note the USER-{uuid} returned in the response
  2. Subsequent sessions: paste that user_id into the dev login field
     Same user, same graph state, same S3 prefix — no re-provisioning
  3. Blank user_id: falls back to USER-dev-001 (ghost user, no DB state)
     Useful for testing auth-only flows that don't need graph data
```

The dev auth flow does NOT call `provision_new_user()`. The developer must have a
pre-provisioned user in the database (created via real OAuth) for graph-dependent
features to work. This intentionally mirrors production behavior — the dev shortcut
only bypasses the IdP redirect, not the user model.

#### Environment Variable Configuration

All deployment-environment differences are controlled by env vars. No code changes
are required when moving between local dev and cloud deployments.

```
Variable                    Local Dev                   Cloud (same domain)         Cloud (split domain)
──────────────────────────────────────────────────────────────────────────────────────────────────────
ENVIRONMENT                 development                 production                  production
OAUTH_REDIRECT_BASE_URL     http://localhost:8000       https://app.graphclaw.ai    https://api.graphclaw.ai
OAUTH_REDIRECT_ALLOWLIST    (unset → localhost default) https://app.graphclaw.ai    https://api.graphclaw.ai
COCKPIT_BASE_URL            http://localhost:3000       https://app.graphclaw.ai    https://app.graphclaw.ai
OAUTH_GOOGLE_CLIENT_ID      <local dev client id>       <prod client id>            <prod client id>
OAUTH_GOOGLE_CLIENT_SECRET  <local dev secret>          <prod secret>               <prod secret>
VITE_ENABLE_DEV_AUTH        true                        (not set)                   (not set)
```

COCKPIT_BASE_URL default: http://localhost:3000 (safe for local dev if unset).
ENVIRONMENT default: production (safe for cloud if unset).

OAuth app registration per environment:
  Each environment registers its own OAuth app at the IdP developer console.
  Local dev app: redirect URI = http://localhost:8000/auth/callback?provider=google
  Production app: redirect URI = https://api.graphclaw.ai/auth/callback?provider=google
  Multiple redirect URIs can be registered on a single OAuth app if needed.

---

### 31.4 Secrets Manager — Full Namespace

All platform secrets stored under the `/workgraph/` prefix with consistent naming conventions.

```
/workgraph/
  auth/
    jwt-signing-key              RSA private key (PEM) — JWT signing
    jwt-public-key               RSA public key (PEM) — JWT verification
    oauth/
      google-client-secret       Google OAuth client secret
      microsoft-client-secret    Microsoft Entra OAuth client secret
      github-client-secret       GitHub OAuth client secret

  channels/
    whatsapp/
      app-secret                 HMAC-SHA256 verification secret
      access-token               WhatsApp Business API bearer token
      phone-number-id            Registered phone number ID
    telegram/
      bot-token                  Telegram Bot API token
      webhook-secret             Webhook header verification token
    email/
      smtp-credentials           { host, port, username, password }
      ses-credentials            AWS SES access credentials (if used)

  llm/
    platform/
      anthropic-api-key          Platform Anthropic key (default for all users)
      openai-api-key             Platform OpenAI key
      google-api-key             Platform Google key
    USER-[id]/
      anthropic-api-key          User's own BYOK Anthropic key (optional)
      openai-api-key             User's own BYOK OpenAI key (optional)

  database/
    graph-db-password            Postgres + AGE password
    relational-db-password       Postgres relational password
    redis-auth-token             Redis AUTH token

  a2a/
    agents/
      [agent-id]/api-key         Plaintext key (shown once at registration,
                                  then overwritten with sentinel value)
                                  Hash stored in ResourceNode in graph DB
```

#### Secret Rotation Schedule

```
Secret                    Rotation          Method
─────────────────────────────────────────────────────────────────
jwt-signing-key           Quarterly         Manual, 24h overlap window
                                           Old key valid during transition
oauth-client-secrets      On compromise     Manual
channel tokens            On compromise     Manual via settings panel
llm-api-keys (platform)   On compromise     Manual
llm-api-keys (user BYOK)  On user request   Self-service via settings
database passwords        Quarterly         Automated (Secrets Manager rotation)
redis-auth-token          Quarterly         Automated
a2a agent keys            On user request   Self-service, instant old key revocation
```

---

### 31.5 End-to-End Secret Flow — LLM API Call

The complete chain from user configuring an API key to an actual LLM call, showing every touch point and what is stored where.

```
STEP 1: User configures BYOK API key in settings panel
  Browser -> POST /api/settings/llm-keys
             Authorization: Bearer [platform JWT] (httpOnly cookie)
             Body: { provider: "anthropic", key: "sk-ant-..." }

STEP 2: API server validates JWT
  Verifies RS256 signature, exp, jti revocation check
  Extracts USER-[id] from sub claim

STEP 3: API server stores key in Secrets Manager
  Path: /workgraph/llm/USER-[id]/anthropic-api-key
  Value: { "api_key": "sk-ant-..." }
  IAM: api-server-role allows CreateSecret/UpdateSecret on this path

STEP 4: API server writes key reference to assets.md in S3
  Path: agents/USER-[id]/core/assets.md
  Line: api_key_id: KEY-REF-anthropic-USER-[id]
  The file contains only the reference ID — never the plaintext key

STEP 5: Agent-runtime container starts (ECS task)
  AWS assigns agent-role-USER-[id] via ECS task role
  Short-lived STS credentials injected into container environment
  boto3 / AWS SDK uses these automatically — no credential handling in code

STEP 6: Agent reads assets.md (from Redis cache or S3)
  Sees KEY-REF-anthropic-USER-[id]
  Resolves reference -> Secrets Manager path

STEP 7: Agent fetches key from Secrets Manager
  GetSecretValue: /workgraph/llm/USER-[id]/anthropic-api-key
  IAM role has GetSecretValue permission on this path only
  Key loaded into container memory
  Memory cache TTL: 15 minutes

STEP 8: Skill agent worker makes LLM API call
  POST https://api.anthropic.com/v1/messages
  Authorization: Bearer [key from memory, never from disk]
  Connection: HTTPS, TLS 1.3

STEP 9: Response received and processed
  Output written to S3 (content only)
  API key never written to S3, SQS, logs, or any external store

STEP 10: Key rotation
  User rotates key in settings panel (new key stored in Secrets Manager)
  Running container: memory cache expires within 15 minutes
  On next cache refresh: fetches new key automatically
  Zero-downtime — no restart required
```

---

### 31.6 Cloud-Agnostic Secrets Backend

For non-AWS deployments, the secrets backend is configurable. The application code uses an abstraction layer with a pluggable backend.

```
class SecretsClient:
    def get_secret(self, key: str) -> str: ...
    def set_secret(self, key: str, value: str) -> None: ...
    def delete_secret(self, key: str) -> None: ...

Implementations:
  AWSSecretsClient      -> boto3 secretsmanager calls
                           (AWS Secrets Manager)
  HashiCorpVaultClient  -> HashiCorp Vault KV v2
                           (on-premise, any cloud)
  AzureKeyVaultClient   -> Azure Key Vault SDK
                           (Azure deployments)
  GCPSecretClient       -> GCP Secret Manager SDK
                           (GCP deployments)
  EnvFileClient         -> reads from .env file
                           (local development only)

Active backend configured via:
  SECRETS_BACKEND=aws_secrets_manager | hashicorp_vault |
                  azure_key_vault | gcp_secret_manager | env_file
```

For local development, `env_file` loads from a `.env.local` file — same application code runs everywhere.

---

### 31.7 Attack Surface Assessment

#### Surface 1: Webhook Endpoints (Channel Gateway)

```
Risk:         Forged messages from non-channel sources

Mitigations:
  WhatsApp:   HMAC-SHA256 verification of every request
  Telegram:   Webhook secret token header verification
  Email:      DKIM / SES Authentication-Results header
  Transport:  HTTPS only, TLS 1.2+ enforced
  Rate limit: 1,000 requests/minute per source IP
  Failed auth: 403 response, silent drop, incident logged
              (no confirmation that endpoint is active)
```

#### Surface 2: OAuth Callback Endpoint

```
Risk:         CSRF, open redirect, authorization code theft

Mitigations:
  CSRF:        state parameter validated before code exchange
  Redirect:    redirect_uri hardcoded in OAuth client registration
               (user-controlled values rejected)
  Code theft:  PKCE (code_verifier/challenge, S256 method)
               code is useless without code_verifier
  Code replay: codes are single-use and expire in ~60s at IdP
  Transport:   HTTPS only
```

#### Surface 3: Platform JWT

```
Risk:         Token theft, persistent sessions, cross-user access

Mitigations:
  Theft:       httpOnly cookie (XSS cannot read it)
               Secure flag (HTTPS only)
               SameSite=Strict (CSRF cannot use it)
  Persistence: 15-minute expiry (short window if stolen)
               Immediate revocation via Redis jti set on logout
  Forgery:     RS256 asymmetric signing
               Private key never leaves Secrets Manager
  Cross-user:  sub claim locked to USER-[id]
               Server validates sub on every request
               Cannot be used to access another user's data
```

#### Surface 4: S3 Data Isolation

```
Risk:         Cross-user file access

Mitigations:
  IAM:         agent-role-USER-[id] scoped to own prefix only
               AWS enforces at infrastructure level
               Application bugs cannot breach this boundary
  Bucket:      Public access blocked at bucket policy level
  Pre-signed:  No pre-signed URLs with user-controlled paths
  Audit:       AWS CloudTrail logs all S3 API calls
               Anomaly detection on cross-prefix access attempts
```

#### Surface 5: SQS Queue Isolation

```
Risk:         One user's agent reading/writing another user's queue

Mitigations:
  IAM:         agent-role-USER-[id] has ReceiveMessage only on
               trigger-events-USER-[id] — no access to other user queues
  Write scope: SendMessage only to outbound-messages (shared outbound)
               Cannot send to other users' trigger queues
  Queue URLs:  Include AWS account ID and region
               Not guessable from user_id alone
```

#### Surface 6: A2A REST Endpoint

```
Risk:         API key brute force, cross-task update injection

Mitigations:
  Entropy:     wg_agent_[32-random-chars] = 256-bit entropy, not guessable
  Storage:     SHA-256 hash in DB only
               Plaintext never retrievable after initial display
  Scoping:     Key valid only for tasks the agent is assigned to
               Cross-task injection rejected with 403
  Rate limit:  100 requests/minute per API key
               Alert at > 10 consecutive failures per key per minute
```

#### Surface 7: Secrets In Transit

```
Risk:         API keys intercepted between container and AWS services

Mitigations:
  VPC endpoints: Secrets Manager, S3, SQS accessed via
                 AWS private VPC endpoints
                 No traffic routes over public internet
  TLS:           All external API calls (LLM APIs, channel APIs)
                 TLS 1.2+ enforced, certificate validation on
  Logging:       Secret values never written to structured logs
                 Log scrubbing rules reject sk-ant-*, wg_agent_*
                 patterns at the log aggregation layer
```

#### Surface 8: Container-to-Container Traffic

```
Risk:         Internal network sniffing, container escape

Mitigations:
  Network:     All inter-container traffic on private Docker/VPC network
  Docker:      No container has access to Docker socket
               No privileged mode containers
  ECS:         awsvpc network mode (task-level isolation)
  EKS:         NetworkPolicy restricting pod-to-pod communication
               Only permitted communication paths are allowed
  Enterprise:  Service mesh with mTLS for all inter-service calls
               (optional, recommended for regulated environments)
```

#### Surface 9: MD File Content Injection

```
Risk:         Malicious content in task.md or SKILL.md influencing
              agent reasoning via prompt injection

Mitigations:
  Trust model:    MD files written by the platform or the user
                  Both are within the user's trust boundary
  Inbound data:   Message content always treated as untrusted
                  Inbound Update Protocol isolates message content
                  from agent instruction files
  System skills:  Platform-provided skills are read-only in S3
                  Hash verification on load detects tampering
  User skills:    Written by the user — user is the trust boundary
                  Agent soul.md non-negotiables apply regardless
                  of instruction content in any other file
```

---

### 31.8 User Onboarding Provisioning Flow — Identity Perspective

When a new user completes OAuth login for the first time, the platform provisions all their identity and infrastructure resources in a single atomic operation.

```
New user detected (email not in Postgres):

  1. Create UserNode in graph DB

  2. Create S3 prefixes (write initial MD files):
       agents/USER-[id]/main.md
       agents/USER-[id]/core/soul.md
       agents/USER-[id]/state/heartbeat.md
       ... (all initial MD files from onboarding template)

  3. Create IAM role: workgraph-agent-role-USER-[id]
       Attach policy with USER-[id] prefix scope
       Policy generated dynamically with exact user_id

  4. Create SQS queue: trigger-events-USER-[id]
       Dead-letter queue: trigger-events-USER-[id]-dlq

  5. Register user in trigger engine:
       Add initial briefing schedules (defaults, user customizes later)

  6. Provision agent-runtime container:
       ECS task definition with agent-role-USER-[id] task role
       Start with minimum resources (0.25 vCPU, 512MB)

  7. Issue platform JWT and refresh token
       Return to browser — user enters the onboarding flow

All steps in a single provisioning transaction.
If any step fails: rollback all created resources.
User sees a clean error and can retry.
```

---

### 31.9 Non-AWS Deployments — IAM Equivalent Mapping

For deployments outside AWS, the IAM role model maps to equivalent mechanisms:

```
AWS IAM Role (ECS Task Role)
  -> GCP:   Workload Identity (GKE Service Account bound to GCP SA)
  -> Azure: Managed Identity (AKS pod identity or ACA managed identity)
  -> On-premise: Kubernetes Service Account + HashiCorp Vault
                 AppRole authentication (role per service)

In all cases:
  - No static credentials in containers or environment variables
  - Identity is assumed at runtime by the platform
  - Least-privilege principle: each container gets minimum permissions
  - Audit trail: all secret access logged centrally
```

For local development:

```
SECRETS_BACKEND=env_file
  -> .env.local file with development credentials only
  -> Never committed to source control (.gitignore enforced)
  -> Development keys have no production access
  -> No IAM role needed locally — boto3 reads from env vars directly
```

---

## 32. Architecture: Observability & Operations

### 32.1 Core Decisions

| Concern | Decision | Rationale |
|---------|----------|-----------|
| Log aggregation | AWS CloudWatch Logs | Native AWS integration, works with existing IAM roles, no additional auth layer |
| Log isolation | Per-user log groups for agent-runtime; shared groups with mandatory `user_id` field for platform containers | Enables user-scoped queries without 100K+ log groups for shared services |
| Log writes | Asynchronous, batched | Logging never blocks agent reasoning or message delivery |
| Trace correlation | `session_id` UUID threaded through every log entry, SQS message, and S3 write | Full interaction timeline reconstructable via single CloudWatch Logs Insights query |
| LLM cost monitoring | Token counts logged as structured fields; CloudWatch metric filters derive cost metrics | No separate metrics pipeline — cost visibility emerges from the log stream |
| Alerting tiers | Three tiers: P1 (page), P2 (alert 1hr), P3 (dashboard) | Operator fatigue avoided by separating state-loss risks from performance degradation |
| Database backup | RDS automated snapshots + continuous WAL shipping | Point-in-time recovery to any second within retention window |
| Rolling deployment | Replace containers one at a time; in-flight sessions recovered via heartbeat.md + context.md | No maintenance windows required; the MD file recovery model absorbs restarts transparently |

---

### 32.2 CloudWatch Log Group Architecture

```
Log Group Structure:

/workgraph/agent-runtime/USER-[id]      <- one per user (agent-runtime only)
  Streams: session-[date]-[session_id]

/workgraph/channel-gateway              <- shared, user_id in every entry
  Streams: inbound-[date], outbound-[date]

/workgraph/trigger-engine               <- shared
  Streams: scheduler-[date], queue-processor-[date], graph-events-[date]

/workgraph/api-server                   <- shared
  Streams: requests-[date], auth-[date], provisioning-[date]

/workgraph/skill-agents/USER-[id]       <- per-user skill agent logs
  Streams: [task-id]-[date]

/workgraph/platform/errors              <- DLQ failures, P1 alerts, infra errors
  Streams: errors-[date]

/workgraph/platform/audit               <- security-sensitive events only
  Streams: audit-[date]
```

#### Why Per-User Log Groups for Agent-Runtime

The agent-runtime is the most query-heavy container — operators need to debug a specific user's session without wading through other users' logs. Per-user log groups enable:

```
# Everything that happened for USER-john-doe today:
aws logs filter-log-events \
  --log-group-name /workgraph/agent-runtime/USER-john-doe \
  --start-time [today-epoch]

# Single session trace — all containers — via Logs Insights:
fields @timestamp, container, event_type, session_id, message
| filter session_id = "SES-abc123"
| sort @timestamp asc
```

Platform containers (channel-gateway, trigger-engine, api-server) use shared log groups because they process many users' events and the volume doesn't justify per-user isolation — `user_id` as a mandatory structured field achieves the same queryability.

---

### 32.3 Structured Log Schema

Every log entry across every container is a JSON object. No unstructured text logs anywhere. This is what makes CloudWatch Logs Insights queries reliable and metric filters precise.

#### Mandatory Fields (All Containers)

```json
{
  "timestamp":     "2025-03-06T09:30:00.123Z",
  "level":         "INFO | WARN | ERROR | DEBUG",
  "container":     "agent-runtime | channel-gateway | trigger-engine |
                    api-server | skill-agent",
  "user_id":       "USER-john-doe",
  "session_id":    "SES-[uuid]",
  "event_type":    "[see event catalog below]",
  "message":       "human-readable summary",
  "duration_ms":   142
}
```

`session_id` is the distributed trace key. It is generated at the trigger entry point (channel-gateway for inbound messages, trigger-engine for scheduled events) and carried through every downstream log entry for that invocation.

#### Event Catalog — Trigger Events

```json
{
  "event_type":    "TRIGGER_RECEIVED",
  "trigger_type":  "INBOUND_MESSAGE | SCHEDULED_BRIEFING | FOLLOWUP_TIMER | GRAPH_STATE_CHANGE",
  "channel":       "whatsapp | telegram | email | internal",
  "queue_depth":   3,
  "queue_age_ms":  450
}
```

```json
{
  "event_type":    "TRIGGER_DISPATCHED",
  "trigger_type":  "SCHEDULED_BRIEFING",
  "target_user_id": "USER-john-doe",
  "target_org_id":  "ORG-work",
  "delay_ms":      12
}
```

#### Event Catalog — Agent Reasoning

```json
{
  "event_type":     "AGENT_INVOCATION_START",
  "trigger_type":   "INBOUND_MESSAGE",
  "files_to_load":  ["soul.md", "persona.md", "heartbeat.md",
                     "context.md", "queue.md", "channels/thread.md"],
  "recovery_mode":  false
}
```

```json
{
  "event_type":       "FILES_LOADED",
  "files_loaded":     ["soul.md", "persona.md", "heartbeat.md",
                       "context.md", "queue.md", "channels/thread.md"],
  "cache_hits":       5,
  "cache_misses":     1,
  "s3_reads":         1,
  "load_duration_ms": 38
}
```

```json
{
  "event_type":        "LLM_CALL_START",
  "provider":          "anthropic",
  "model":             "claude-opus-4",
  "context_window_pct": 42,
  "prompt_tokens_est":  3200
}
```

```json
{
  "event_type":     "LLM_CALL_COMPLETE",
  "provider":       "anthropic",
  "model":          "claude-opus-4",
  "tokens_input":   3247,
  "tokens_output":  412,
  "tokens_total":   3659,
  "cost_usd":       0.0421,
  "latency_ms":     2840,
  "finish_reason":  "stop | max_tokens | error"
}
```

```json
{
  "event_type":     "GRAPH_STATE_UPDATE",
  "node_id":        "TSK-JD-4901",
  "from_state":     "IN_PROGRESS",
  "to_state":       "COMPLETE",
  "triggered_by":   "INBOUND_UPDATE",
  "cascade_count":  2
}
```

```json
{
  "event_type":      "AGENT_INVOCATION_COMPLETE",
  "actions_taken":   ["GRAPH_UPDATE", "OUTBOUND_MESSAGE_QUEUED"],
  "autonomous":      true,
  "files_written":   ["state/context.md", "channels/thread.md"],
  "total_duration_ms": 3124
}
```

#### Event Catalog — Channel Gateway

```json
{
  "event_type":         "INBOUND_MESSAGE_RECEIVED",
  "channel":            "whatsapp",
  "auth_method":        "HMAC",
  "auth_result":        "PASS | FAIL",
  "sender_resolved":    true,
  "attachment_count":   1,
  "attachment_s3_keys": ["attachments/USER-[id]/ATT-[uuid]"]
}
```

```json
{
  "event_type":      "OUTBOUND_MESSAGE_SENT",
  "channel":         "telegram",
  "recipient_type":  "USER | RESOURCE",
  "content_type":    "TEXT",
  "char_count":      142,
  "delivery_result": "SUCCESS | FAILED | RETRYING",
  "attempt_number":  1
}
```

#### Event Catalog — Skill Agents

```json
{
  "event_type":    "SKILL_AGENT_SPAWNED",
  "task_id":       "TSK-JD-4901-DEL",
  "skill_id":      "research-agent-v1",
  "provider":      "openai",
  "model":         "gpt-4o-mini",
  "thread_count":  2
}
```

```json
{
  "event_type":    "SKILL_AGENT_STATUS",
  "task_id":       "TSK-JD-4901-DEL",
  "skill_id":      "research-agent-v1",
  "status":        "IN_PROGRESS | COMPLETE | BLOCKED | FAILED",
  "progress_pct":  60,
  "tokens_used":   8420,
  "elapsed_ms":    184000
}
```

#### Event Catalog — Security & Audit

```json
{
  "event_type":   "AUTH_LOGIN",
  "idp":          "google",
  "result":       "SUCCESS | FAILED",
  "user_email":   "john@acme.com",
  "ip_address":   "[hashed — not stored raw]"
}
```

```json
{
  "event_type":    "A2A_AUTH_ATTEMPT",
  "agent_id":      "RES-research-agent-01",
  "task_id":       "TSK-JD-4821-DEL",
  "result":        "SUCCESS | FAILED",
  "failure_reason": "INVALID_KEY | WRONG_TASK | MISSING_HEADER"
}
```

```json
{
  "event_type":   "SECRET_FETCH",
  "secret_path":  "/workgraph/llm/USER-[id]/anthropic-api-key",
  "source":       "cache | secrets_manager",
  "result":       "SUCCESS | FAILED"
}
```

**Security rule:** Secret values, API key fragments, JWT content, and raw IP addresses are never written to any log entry. The log scrubbing layer (CloudWatch Logs subscription filter) rejects any entry containing patterns matching `sk-ant-*`, `wg_agent_*`, `Bearer `, or common key formats before they reach durable storage.

---

### 32.4 Asynchronous Log Writing

Log writes never block the main execution path. The logging library buffers entries in a thread-safe in-memory queue and flushes to CloudWatch asynchronously in the background.

```python
class AsyncLogger:

    def __init__(self, log_group: str, stream_prefix: str):
        self.log_group    = log_group
        self.stream_name  = f"{stream_prefix}-{date.today().isoformat()}"
        self.buffer       = queue.Queue(maxsize=10_000)
        self.flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True)
        self.flush_thread.start()

    def log(self, level: str, event_type: str,
            user_id: str, session_id: str, **fields):
        entry = {
            "timestamp":  datetime.utcnow().isoformat() + "Z",
            "level":      level,
            "container":  CONTAINER_NAME,
            "user_id":    user_id,
            "session_id": session_id,
            "event_type": event_type,
            **fields
        }
        try:
            self.buffer.put_nowait(entry)    # non-blocking, never waits
        except queue.Full:
            pass                             # drop log entry — never block agent

    def _flush_loop(self):
        while True:
            batch = []
            deadline = time.time() + 1.0     # flush every 1 second or 100 entries
            while len(batch) < 100 and time.time() < deadline:
                try:
                    entry = self.buffer.get(timeout=0.1)
                    batch.append(entry)
                except queue.Empty:
                    break
            if batch:
                self._send_to_cloudwatch(batch)

    def _send_to_cloudwatch(self, batch: list):
        try:
            cloudwatch_logs.put_log_events(
                logGroupName=self.log_group,
                logStreamName=self.stream_name,
                logEvents=[{
                    "timestamp": int(datetime.fromisoformat(
                        e["timestamp"].rstrip("Z")).timestamp() * 1000),
                    "message": json.dumps(e)
                } for e in batch]
            )
        except Exception:
            pass    # log flush failure never propagates to application code
```

**Design consequence:** if the CloudWatch endpoint is temporarily unavailable, the in-memory buffer absorbs up to 10,000 log entries before dropping begins. The agent continues operating normally throughout. No logging failure can take down the agent.

---

### 32.5 Distributed Trace Model — session_id

The `session_id` is the spine of the distributed trace. It is generated once at the point of trigger entry and propagated through every subsequent component for that invocation.

```
Trigger entry points — where session_id is born:

  Channel gateway receives inbound message:
    session_id = "SES-" + uuid4()
    Written into normalized InboundMessage JSON
    Written into SQS message attribute

  Trigger engine fires scheduled briefing:
    session_id = "SES-" + uuid4()
    Written into TriggerEvent JSON

session_id propagation:
  InboundMessage (SQS) -> agent-runtime reads session_id from message
  Agent-runtime writes session_id into every log entry it produces
  Agent-runtime writes session_id into OutboundMessage (SQS)
  Channel-gateway reads session_id from OutboundMessage
  Channel-gateway writes session_id into its delivery log entry
  Agent-runtime writes session_id into S3 status writes (status.md header)
  Agent-runtime writes session_id into graph DB state_history row

Result: one CloudWatch Logs Insights query reconstructs
the complete end-to-end timeline:

fields @timestamp, container, event_type, message, duration_ms
| filter session_id = "SES-abc123def456"
| sort @timestamp asc

Returns (example):
  09:30:00.012  channel-gateway    INBOUND_MESSAGE_RECEIVED   ...
  09:30:00.045  trigger-engine     TRIGGER_DISPATCHED         ...
  09:30:00.312  agent-runtime      AGENT_INVOCATION_START     ...
  09:30:00.350  agent-runtime      FILES_LOADED               38ms
  09:30:00.351  agent-runtime      LLM_CALL_START             ...
  09:30:03.191  agent-runtime      LLM_CALL_COMPLETE          2840ms
  09:30:03.201  agent-runtime      GRAPH_STATE_UPDATE         ...
  09:30:03.210  agent-runtime      AGENT_INVOCATION_COMPLETE  3124ms total
  09:30:03.245  channel-gateway    OUTBOUND_MESSAGE_SENT      ...
```

#### X-Ray Integration (Optional)

For deployments where visual trace maps are preferred over Logs Insights queries, AWS X-Ray can be layered on top of the same `session_id` without changing the log schema:

```python
# X-Ray segment opened at trigger entry, session_id as annotation
xray_recorder.begin_segment("agent-invocation")
xray_recorder.put_annotation("session_id", session_id)
xray_recorder.put_annotation("user_id", user_id)

# Each major step becomes a subsegment
with xray_recorder.in_subsegment("llm-call"):
    response = call_llm(prompt)

# X-Ray provides the visual waterfall; Logs Insights provides the detail
```

X-Ray is optional infrastructure — the `session_id` in structured logs provides full traceability without it.

---

### 32.6 LLM Token Cost Monitoring

Every LLM call logs `tokens_input`, `tokens_output`, and `cost_usd` as structured fields. CloudWatch metric filters extract these into CloudWatch Metrics automatically — no separate metrics pipeline, no additional instrumentation.

#### Metric Filter Definitions

```
Filter name:    LLMTokensInput
Log group:      /workgraph/agent-runtime/USER-[id]  (applied per user group)
Filter pattern: { $.event_type = "LLM_CALL_COMPLETE" }
Metric:         workgraph/llm/tokens_input
Dimensions:     user_id, model, provider
Value:          $.tokens_input

Filter name:    LLMTokensOutput
Filter pattern: { $.event_type = "LLM_CALL_COMPLETE" }
Metric:         workgraph/llm/tokens_output
Dimensions:     user_id, model, provider
Value:          $.tokens_output

Filter name:    LLMCostUSD
Filter pattern: { $.event_type = "LLM_CALL_COMPLETE" }
Metric:         workgraph/llm/cost_usd
Dimensions:     user_id, model, provider
Value:          $.cost_usd

Filter name:    LLMCallCount
Filter pattern: { $.event_type = "LLM_CALL_COMPLETE" }
Metric:         workgraph/llm/call_count
Dimensions:     user_id, model, provider
Value:          1
```

#### Cost Dashboards

```
CloudWatch Dashboard: LLM Cost by User (daily view)
  Widget 1: Top 10 users by daily token spend (bar chart)
  Widget 2: Cost by model/provider breakdown (stacked area)
  Widget 3: Cost per session p50/p95 (line chart)
  Widget 4: Skill agent token usage vs. orchestrating agent (pie)

CloudWatch Dashboard: LLM Cost by Platform (monthly view)
  Widget 1: Total daily cost trend (line chart)
  Widget 2: Cost by user tier (personal / team / enterprise)
  Widget 3: Model distribution over time (area chart)
  Widget 4: Month-to-date total vs. budget threshold (gauge)
```

#### Cost Anomaly Detection

```
CloudWatch Anomaly Detection on workgraph/llm/cost_usd:
  Baseline: rolling 14-day average per user
  Anomaly threshold: 3 standard deviations above baseline

  Alarm fires when:
    A user's hourly token spend exceeds 3σ above their baseline
    Typical cause: skill agent in retry loop, infinite reasoning loop,
                   very large document processing task

  Alert action:
    P2 alert to ops dashboard
    Agent logs COST_ANOMALY_DETECTED event for that session
    User notified in next briefing:
      "Unusually high AI processing occurred today on [task].
       Total: [tokens] tokens / $[cost]. All completed normally."

Daily budget caps (optional, configurable per user):
  If daily_cost_usd > user.budget_cap:
    Suspend non-critical LLM calls for remainder of day
    Orchestrating agent briefing still runs (high priority)
    Skill agents queued until next day
    User notified: "Daily AI budget reached. Skill agents paused
                    until tomorrow. Adjust limit in settings."
```

---

### 32.7 Alerting Model — Three Tiers

#### P1 — Page Immediately (State at Risk)

These alerts indicate data loss risk or complete service failure. They page the on-call operator regardless of time of day.

```
Alert                         Condition                           Action
─────────────────────────────────────────────────────────────────────────
S3_WRITE_FAILURE              s3:PutObject error rate > 0%        Page immediately
                              on agent state files                Agent state at risk

SQS_DLQ_DEPTH                 inbound-messages-dlq depth > 10    Page immediately
                              OR trigger-events-dlq depth > 10   Messages being lost

CONTAINER_HEALTH_CRITICAL     agent-runtime container fails       Page immediately
                              3 consecutive health checks         User's agent offline

SECRET_FETCH_FAILURE          secretsmanager:GetSecretValue       Page immediately
                              fails for any container             LLM calls impossible

GRAPH_DB_CONNECTION_LOST      Graph DB connection pool            Page immediately
                              exhausted or DB unreachable         All state writes failing

RELATIONAL_DB_CONNECTION_LOST Postgres connection pool            Page immediately
                              exhausted or DB unreachable         Audit trail failing
```

#### P2 — Alert, Investigate Within 1 Hour

These indicate degraded performance or elevated error rates that will affect users if not resolved.

```
Alert                         Condition                           Dashboard
─────────────────────────────────────────────────────────────────────────
LLM_ERROR_RATE_HIGH           LLM API error rate > 5%             Provider status
                              over any 5-minute window            check, retry queue

MESSAGE_DELIVERY_FAILURES     Outbound message failure rate        Channel API status,
                              > 2% over 10-minute window          DLQ inspection

AGENT_TIMEOUT_RATE            > 2% of sessions taking             LLM latency check,
                              > 30s total (p99 threshold)         context size review

QUEUE_DEPTH_ELEVATED          trigger-events queue depth          Trigger engine
                              > 100 for a single user             health, burst capacity

COST_ANOMALY                  User's hourly spend > 3σ            Session detail,
                              above 14-day baseline               skill agent status

REDIS_MEMORY_HIGH             Redis memory utilization > 80%      TTL review,
                                                                  eviction policy
```

#### P3 — Dashboard, Review Daily

These are trend indicators that require attention but not immediate response.

```
Metric                        Review cadence    Purpose
──────────────────────────────────────────────────────────────────
LLM cost per user per day     Daily             Budget planning, anomaly trends
Trigger volume by type        Daily             Understand usage patterns
Session duration p95          Daily             Reasoning efficiency trends
Cache hit ratio               Daily             Redis TTL tuning
DLQ message count             Daily             Integration health
User onboarding completion %  Weekly            Product funnel health
Skill agent success rate      Weekly            Skill quality monitoring
```

#### CloudWatch Alarms Configuration

```
All P1 alarms:
  Evaluation period: 1 minute
  Actions:
    SNS topic: workgraph-p1-alerts
    Subscribers: PagerDuty integration (or equivalent)
                 Slack #p1-alerts channel (backup)

All P2 alarms:
  Evaluation period: 5 minutes
  Actions:
    SNS topic: workgraph-p2-alerts
    Subscribers: Slack #ops-alerts channel
                 Email: ops-team distribution list

All P3 metrics:
  No alarms — appear on CloudWatch dashboards only
  Reviewed in weekly ops review meeting
```

---

### 32.8 Database Backup and Point-in-Time Recovery

Both the graph database (Postgres + AGE) and the relational database (Postgres) run on Amazon RDS. RDS provides automated backup with continuous WAL (Write-Ahead Log) shipping, enabling recovery to any point in time within the retention window.

#### Backup Configuration

```
Graph DB (RDS Postgres + AGE):
  Automated backup:   Enabled
  Backup window:      02:00-03:00 UTC (low-traffic window)
  Retention period:   35 days (maximum for RDS)
  Backup type:        Full daily snapshot + continuous WAL
  PITR resolution:    5-minute granularity (RDS standard)
                      Effectively any second within retention window

Relational DB (RDS Postgres):
  Automated backup:   Enabled
  Backup window:      02:00-03:00 UTC
  Retention period:   35 days
  PITR resolution:    5-minute granularity

S3 (agent MD files, workspaces, skills):
  Versioning:         Enabled on app-bucket
  Retention policy:   Non-current versions retained 90 days
  Replication:        S3 Cross-Region Replication to backup region
  Delete protection:  MFA delete enabled (accidental deletion prevention)

Redis (cache):
  Redis Persistence:  RDB snapshot every 5 minutes
                      AOF (Append-Only File) for durability
  Recovery:           Cache is warm-up state only —
                      full recovery possible by reading S3
                      Redis loss requires cache warm-up only,
                      no data loss (S3 is the source of truth)
```

#### Recovery Procedures

**Scenario A: Accidental data deletion (single user)**

```
Incident: USER-john-doe's task nodes accidentally deleted from graph DB

Recovery steps:
  1. Identify timestamp of deletion from state_history table
     (audit log preserved separately from graph DB)
  2. Restore graph DB to PITR snapshot just before deletion:
     aws rds restore-db-instance-to-point-in-time \
       --source-db-instance-identifier workgraph-graph-db \
       --target-db-instance-identifier workgraph-graph-db-recovery \
       --restore-time [timestamp]
  3. Extract deleted nodes from recovery instance
  4. Re-import into production DB
  5. Verify S3 MD files intact (versioned — no separate restore needed)
  6. Notify user: brief outage window, data fully restored

RTO: < 2 hours
RPO: < 5 minutes
```

**Scenario B: Catastrophic DB failure**

```
Incident: Graph DB instance fails unrecoverably

Recovery steps:
  1. Promote read replica to primary (if Multi-AZ configured):
     Automatic failover — RDS handles this
     RTO: 60-120 seconds
  2. If no read replica: restore from latest automated snapshot + WAL:
     RTO: 30-90 minutes depending on DB size
  3. Update connection string in container environment variables
  4. Agent containers reconnect automatically (connection pool retry logic)

Multi-AZ configuration (recommended for production):
  aws rds modify-db-instance \
    --db-instance-identifier workgraph-graph-db \
    --multi-az
  Standby replica in second AZ
  Automatic failover with no data loss
```

**Scenario C: S3 MD file corruption (single user)**

```
Incident: USER-john-doe's soul.md or behavioral.md corrupted

Recovery steps:
  1. S3 versioning lists all previous versions of the file:
     aws s3api list-object-versions \
       --bucket app-bucket \
       --prefix agents/USER-john-doe/core/soul.md
  2. Restore previous version:
     aws s3api copy-object \
       --copy-source app-bucket/agents/USER-john-doe/core/soul.md?versionId=[id] \
       --bucket app-bucket \
       --key agents/USER-john-doe/core/soul.md
  3. Invalidate Redis cache entry for that file
  4. Next agent invocation reads restored file from S3

RTO: < 5 minutes
```

---

### 32.9 Rolling Deployment — Zero Downtime

New container image versions are deployed one container at a time via ECS rolling update or Kubernetes rolling deployment. No maintenance windows are required.

#### Rolling Update Mechanics

```
ECS Rolling Update (default strategy):
  Minimum healthy percent: 50%
  Maximum percent:         200%
  Deployment:              ECS replaces tasks one at a time
                           New task starts -> health check passes
                           -> old task stopped -> next task replaced

EKS Rolling Update:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0    <- no user agent ever completely offline
```

#### Protecting In-Flight Sessions

The MD file recovery model (Section 26.8) absorbs container replacement transparently. A container replaced mid-session leaves its state written to S3 and Redis. The replacement container finds the incomplete session on startup.

```
Container replacement during active session:

  Old container:
    Agent reasoning 60% complete
    context.md written to S3 at step 3 of 6
    heartbeat.md: result=INCOMPLETE, end=null
    Container receives SIGTERM from ECS
    Background log flush completes (async, 1s max)
    Container exits

  New container starts:
    Reads heartbeat.md -> result: INCOMPLETE
    Loads context.md -> step 3 marked COMPLETE, step 4 IN_PROGRESS
    Resumes from step 4
    Completes reasoning, writes final state
    Updates heartbeat.md: result=COMPLETE

  User perspective:
    Response arrives 2-8 seconds later than normal
    No error, no data loss, no awareness of container replacement

Container replacement is invisible to the user because
the MD file system was designed for exactly this scenario.
```

#### Deployment Sequence for Major Versions

```
1. Pre-deployment:
   Run MD file migration job (if schema changed)
   Validate: all user main.md files at new format version
   Smoke test: deploy new image to staging environment

2. Deploy shared containers first (stateless, low risk):
   trigger-engine -> health check -> proceed
   channel-gateway -> health check -> proceed
   api-server -> health check -> proceed

3. Deploy agent-runtime containers (per-user, rolling):
   ECS updates per-user task definitions with new image
   Replaces idle containers first (lower disruption risk)
   Active containers replaced during low-activity windows
   (ECS respects minimum healthy percent)

4. Post-deployment:
   Monitor P2 alerts for 15 minutes
   Verify LLM call success rate stable
   Verify SQS queue depth returning to baseline
   Mark deployment complete, update version tag in Secrets Manager

5. Rollback (if P1 alert fires post-deploy):
   ECS rollback to previous task definition revision:
     aws ecs update-service \
       --cluster workgraph \
       --service agent-runtime-USER-[id] \
       --task-definition [previous-revision]
   MD files written by new version are forward-compatible
   Old container reads new-format files without issue
   (backward compatibility required of all schema changes)
```

---

### 32.10 MD File Schema Migration

When the agent file format evolves between versions, a migration job runs before new containers start. Migrations are always:
- **Forward only** (no rollback of MD file content)
- **Non-destructive** (old fields preserved, new fields added)
- **Version-stamped** (main.md carries `schema_version` field)

```
Migration job (runs as one-off ECS task before deployment):

  1. List all user agent directories: s3://app-bucket/agents/USER-*/main.md
  2. For each user:
       Read main.md -> extract schema_version
       If schema_version == target: skip (already migrated)
       If schema_version < target: apply migration steps in sequence
  3. Migration step example (v1.0 -> v1.1):
       soul.md: add missing.md reference in loading manifest
       assets.md: add SECRETS_BACKEND field (default: aws_secrets_manager)
       heartbeat.md: add schema_version field
  4. Write updated files back to S3
  5. Update main.md schema_version to target version
  6. Log: MIGRATION_COMPLETE for USER-[id]

Migration is idempotent:
  Can be re-run safely if interrupted
  schema_version check prevents double-migration
  S3 versioning preserves pre-migration state for rollback if needed

Backward compatibility rule:
  New containers must read both old and new schema versions
  Old containers must read new-format files without crashing
  This is enforced: both old and new containers run simultaneously
  during rolling deployment
```

---

### 32.11 CloudWatch Dashboard Summary

```
Dashboard: Workgraph Platform Health

Row 1: Real-time indicators
  [Agent Invocations/min]  [LLM Success Rate %]  [Message Delivery %]
  [P1 Alarms: 0]           [P2 Alarms: 2]        [Queue Depth: normal]

Row 2: LLM Cost (last 24h)
  [Total cost: $142.30]    [Top user by spend]    [Cost by model breakdown]
  [Cost trend (7d)]        [Anomaly detections]

Row 3: Latency
  [Trigger-to-response p50/p95/p99 (line)]
  [LLM call latency by provider (line)]
  [S3 read latency (bar)]

Row 4: Reliability
  [SQS DLQ depth (all queues)]   [DB connection pool utilization]
  [Redis memory utilization]      [Container health check pass rate]

Row 5: User Activity
  [Active users today]  [Triggers by type (pie)]  [Skill agents in flight]
  [New user onboards]   [Conversations active]
```
## 33. Design Principles (Emergent)

| # | Principle | Meaning |
|---|-----------|---------|
| 1 | **Graph over list** | Work is inherently relational — model it that way |
| 2 | **Agent acts, human decides** | Agent does the cognitive heavy lifting; human retains authority |
| 3 | **Explainability by default** | The agent never makes an opaque decision — it can always say why |
| 4 | **Batch over interrupt** | Consolidate outreach and briefings rather than fragmenting attention |
| 5 | **Learn from every interaction** | Every human decision is a signal that improves future reasoning |
| 6 | **Proactive over reactive** | Surface upcoming pressure before it becomes a crisis |
| 7 | **Infer, propose, confirm** | Agent infers structure and goals, proposes them, commits only after confirmation |
| 8 | **Sequential clarity** | In a sequential chain, only the first actionable node is surfaced; downstream urgency rolls up |
| 9 | **Trust gradient** | Autonomy is earned and scoped — AI agent interactions default autonomous, human interactions default require approval |
| 10 | **Compliance is non-negotiable** | Compliance-linked constraint nodes cannot be suppressed or overridden |
| 11 | **Channel follows the user** | The agent responds on whichever registered channel the user initiates from |
| 12 | **Workspaces as isolation boundaries** | Organizations provide hard data isolation; unified view is always pull-based |
| 13 | **Skills are portable** | SKILL.md files are LLM-agnostic — switching providers requires no skill rewrites |
| 14 | **Context travels with the task** | Agent output folders keyed by task ID ensure any downstream agent can find upstream context |
| 15 | **Agents coordinate through the graph** | Multiple agent instances never talk directly — the shared graph is the coordination medium |
| 16 | **Delegation is recruitment** | Every task assigned to someone outside the system is a natural, value-first onboarding opportunity |
| 17 | **Cold start is warm by design** | Recruited users arrive with pre-seeded tasks and existing relationships — never a blank slate |
| 18 | **Channel is transport, not identity** | One agent brain serves all channels; context is maintained above the transport layer |
| 19 | **Artifacts close the loop** | Deliverable submission is a first-class event that drives state transitions, not a side effect of a status message |
| 20 | **Visibility is granted, not assumed** | Cross-user graph access requires explicit permission at the node level — org membership alone does not confer visibility |
| 21 | **Archive, never delete** | Inactive nodes leave the scoring cycle but remain in history — the graph is a permanent record of work |
| 22 | **Hierarchy absorbs complexity** | Program → project → workstream maps to GoalNode nesting — same model scales from 3-person task to 50-person program |
| 23 | **File system is the brain** | Agent state lives in durable MD files, not in memory — any restart or failure is fully recoverable without an external session store |
| 24 | **Stateless invocation, stateful files** | Each agent invocation is an independent LLM call; continuity comes from the file system, not from a persistent process |
| 25 | **Soul is immutable, persona is mutable** | Core operating constraints never change; communication style adapts to the user |
| 26 | **Progressive loading over full context** | Load only what the current trigger needs; page through knowledge rather than filling the context window |
| 27 | **Polyglot persistence by query pattern** | Each storage layer owns the query type it is best at — graph traversal, vector similarity, and time-series are separate concerns |
| 28 | **Isolation at every layer** | Org boundaries enforced at the database layer independently of the application layer — defense in depth against data leakage |
| 29 | **Container per user** | Each user's agent runs in its own container — compute, memory, and file system are hard-isolated at the process level |
| 30 | **Write-through caching** | Every S3 write immediately updates the Redis cache — no stale reads, no cache invalidation complexity |
| 31 | **Cloud-agnostic by default** | S3-compatible storage API, standard container orchestration, and open-source databases eliminate vendor lock-in at every layer |
| 32 | **Local-first development** | `docker compose up` runs the full stack — no cloud account, no mocks, no compromises in local testing |
| 33 | **Secrets never in files** | LLM API keys and credentials live in a secret store and are injected at runtime — MD files reference key IDs only |
| 34 | **SQS as reliability boundary** | Once a message is in SQS it is guaranteed to reach the agent — channel API failures and container restarts cannot lose messages |
| 35 | **Gateway acknowledges first, processes second** | The channel API receives HTTP 200 immediately; all processing happens asynchronously via SQS to prevent channel timeouts |
| 36 | **Conversation lives in cache, history lives in DB** | Redis holds the active thread for fast multi-turn reasoning; Postgres holds the permanent archive after the conversation closes |
| 37 | **Attachments are normalized at the boundary** | Files are extracted and stored to S3 at the gateway — the reasoning layer only ever sees storage paths, never raw binary |
| 38 | **Channel is transport, secrets are runtime** | Credentials are injected at runtime via Secrets Manager — no channel-specific logic bleeds into the agent reasoning layer |
| 39 | **Async threads over synchronous calls** | Skill agents run as async threads — the orchestrating agent is never blocked waiting for a long-running task to complete |
| 40 | **Storage client is the portability seam** | One boto3 client with a configurable endpoint URL makes all file operations cloud-agnostic — swap provider by changing an environment variable |
| 41 | **Event-driven completion, not polling** | S3 event notifications replace polling loops — the orchestrating agent is notified within seconds of a skill agent completing, with zero wasted reads |
| 42 | **Heartbeat as a liveness signal** | Long-running agents write progress every 5 minutes — silence beyond 15 minutes is the failure signal, not an explicit ping/ack protocol |
| 43 | **A2A feeds the standard pipeline** | External agent updates are authenticated at the API boundary then dropped into the same SQS inbound queue as channel messages — no special downstream code path |
| 44 | **API key scoped to assignment** | An external agent's API key is only valid for tasks it is explicitly assigned to — authentication and authorization are verified in the same step |
| 45 | **One role per container** | Each container has its own IAM role scoped to exactly what it needs — no shared roles, no over-provisioned permissions |
| 46 | **IAM enforces isolation, application confirms it** | S3 prefix conditions in IAM policies block cross-user access at the AWS layer independently of application logic |
| 47 | **Secrets are references, never values** | MD files and config files carry only key reference IDs — plaintext credentials exist only in Secrets Manager and container memory |
| 48 | **OAuth delegates identity, JWT controls sessions** | External IdPs own the hard identity problem; the platform owns session lifecycle with short-lived, revocable JWTs |
| 49 | **15 minutes is the theft window** | Access JWTs expire in 15 minutes — even a stolen token has a tightly bounded blast radius, closed immediately by the jti revocation list |
| 50 | **Secrets backend is pluggable** | AWS Secrets Manager, HashiCorp Vault, Azure Key Vault, and GCP Secret Manager are all valid backends — same application code, different environment variable |
| 51 | **Logs are the single source of truth for observability** | Structured JSON to CloudWatch provides tracing, cost monitoring, and alerting from one pipeline — no separate metrics or trace infrastructure required |
| 52 | **session_id is the distributed trace** | One UUID born at the trigger entry point threads through every log entry, SQS message, and S3 write — full end-to-end trace via a single Logs Insights query |
| 53 | **Logging never blocks execution** | Async buffered writes mean a CloudWatch outage cannot slow or halt the agent — log entries are dropped before the agent is impacted |
| 54 | **Cost emerges from logs, not a separate pipeline** | Token counts logged as structured fields are extracted by CloudWatch metric filters — no additional instrumentation required for cost visibility |
| 55 | **User isolation in logs mirrors user isolation in compute** | Per-user log groups for agent-runtime enable scoped debugging without leaking other users' activity into operator queries |
| 56 | **S3 versioning is the MD file backup** | Object versioning on the agent bucket provides point-in-time recovery for any MD file without a separate backup process |
| 57 | **Rolling deployment is safe because restarts are safe** | The heartbeat.md + context.md recovery model means container replacement mid-session is transparent to the user — deployment and recovery use the same mechanism |
| 58 | **Schema migrations are non-destructive and idempotent** | MD file migrations add fields and preserve old ones, carry version stamps, and can be re-run safely — old and new container versions coexist during any deployment |
| 59 | **MCP tools extend, not replace, the agent brain** | MCP servers give the orchestrating agent reach into external services — they are tool-call extensions, not reasoning layers; the agent remains the single decision-maker |
| 60 | **Tool trust is tiered and revocable** | MCP server registrations carry a trust tier (auto-approve / gated / blocked); write-capable tools require explicit user grant; any tier can be revoked instantly from the settings panel |

---

## 34. Architecture: MCP Server Integration

> **Note:** The Web UI components for MCP server management (search, install, trust tier configuration) are documented in `docs/ui-requirements.md` as part of the separate UI project.

The orchestrating agent can interact with external services through the **Model Context Protocol (MCP)**. GraphClaw operates as an MCP client; external service runtimes operate as MCP servers. MCP support is implemented as a first-class subsystem in `src/graphclaw/mcp/` and exposed to the cockpit via `/app/v1/mcp-*` routes.

This extends agent capabilities without changing GraphClaw's core model (task graph + state machine + scoring + trigger loops): MCP calls are another execution primitive alongside skill invocation, graph mutations, and channel output.

---

### 34.1 Design Goals

The MCP architecture is designed around five constraints:

1. **Per-user isolation** - each user has an independent MCP registry and approval queue.
2. **Transport neutrality** - server registration supports `http`, `sse`, and `stdio`.
3. **Trust-tier enforcement at runtime** - execution policy is checked before any tool call.
4. **Graceful degradation** - discovery/listing failures should not crash agent flow.
5. **Operational simplicity** - new servers can be added via registry records, not image rebuilds.

---

### 34.2 MCP Registry (Current Implementation)

Each user has a personal MCP server registry represented by `MCPServerNode` schema and persisted as JSON objects in object storage:

- Path pattern: `{user_id}/mcp/servers/{server_id}.json`
- Service: `MCPRegistry` in `src/graphclaw/mcp/registry.py`
- API surface: `/app/v1/mcp-servers` in `src/graphclaw/api/mcp_registry.py`

Canonical fields:

```
MCPServerNode
  id:             MCP-{identifier}
  name:           string
  transport:      "http" | "sse" | "stdio"
  endpoint_url:   string | null   # required for http/sse
  command:        string | null   # required for stdio
  trust_tier:     "AUTO" | "GATED" | "BLOCKED"
  scope:          [string]
  secret_ref:     string | null
  enabled:        bool
  registered_at:  datetime
  last_used_at:   datetime | null
```

Trust tier semantics:

- `AUTO` - execute directly.
- `GATED` - require human approval before execution.
- `BLOCKED` - reject execution.

Guardrail: direct `BLOCKED -> AUTO` promotion is rejected; transition must pass through `GATED`.

---

### 34.3 API Contract and Lifecycle

`/app/v1/mcp-servers` implements registration and runtime management:

- `GET /mcp-servers` - list registered servers.
- `POST /mcp-servers` - register server.
- `GET /mcp-servers/search` - search official MCP registry.
- `GET /mcp-servers/{server_id}` - retrieve server details.
- `PATCH /mcp-servers/{server_id}` - update `trust_tier` / `enabled`.
- `DELETE /mcp-servers/{server_id}` - deregister server.
- `GET /mcp-servers/{server_id}/tools` - live tools listing (best-effort).

Approval queue API:

- `GET /mcp-approvals` - list pending MCP approval tasks for current user.

Validation rules enforced on register:

- `transport=http|sse` requires `endpoint_url`.
- `transport=stdio` requires `command`.

---

### 34.4 Tool Execution Flow

`MCPClient` (`src/graphclaw/mcp/client.py`) drives runtime interaction:

1. `connect(server)` chooses transport client (`HTTPClientTransport`, `SSEClientTransport`, `StdioClientTransport`).
2. `list_tools()` retrieves live tool manifests from server.
3. `call_tool()` enforces trust tier before execution.
4. `_execute_tool()` issues MCP `tools/call` and normalizes result.
5. `_log_tool_call()` emits structured audit fields (`server_id`, `tool_name`, `trust_tier`, `latency_ms`, `success`).

Trust-tier behavior in `call_tool()`:

- `AUTO` -> execute immediately.
- `GATED` -> create approval task, wait for decision, then execute or deny.
- `BLOCKED` -> raise `MCPToolBlockedError` and do not execute.

---

### 34.5 Human-in-the-Loop Gating

`GatedApprovalService` (`src/graphclaw/mcp/approval.py`) maps GATED tool calls to standard graph tasks:

- Creates `TaskNode` with `task_type=APPROVAL` and user-readable criteria.
- Polls task state until approved (`COMPLETE`) or denied (`CANCELLED`/`BLOCKED`).
- Exposes pending approvals back to cockpit via `/app/v1/mcp-approvals`.

This reuses existing GraphClaw state/task primitives instead of a separate approvals subsystem.

---

### 34.6 Official Registry and Adapter Strategy

GraphClaw supports two MCP server onboarding paths:

1. **Official registry discovery** via `OfficialMCPRegistry` (`src/graphclaw/mcp/official_registry.py`) against `https://registry.modelcontextprotocol.io/v0.1`.
2. **Direct registration** via transport + endpoint/command fields in `/mcp-servers`.

Built-in adapter package structure currently includes:

- `src/graphclaw/mcp/adapters/github/`
- `src/graphclaw/mcp/adapters/google_calendar/`
- `src/graphclaw/mcp/adapters/slack/`

Additional providers (for example Google Drive via stdio Docker command) are supported through direct registration without code changes to the core MCP subsystem.

---

### 34.7 Security and Operations

Security controls:

- Per-user registry isolation by storage prefix.
- Trust-tier enforcement at execution boundary.
- Optional secret reference lifecycle cleanup on deregister.
- Structured logs for MCP call auditability.

Operational behavior:

- Registry search/tools listing degrades to empty results on upstream/transport failure.
- MCP SDK dependency is lazy-loaded; explicit error is returned when SDK is unavailable.
- Server configuration toggling (`enabled`) allows temporary suspension without deletion.

---

### 34.8 Implementation Mapping (Code-Level)

| Concern | Primary module | Notes |
|---------|----------------|-------|
| Registry persistence | `src/graphclaw/mcp/registry.py` | JSON documents in object storage per user |
| MCP transport/session | `src/graphclaw/mcp/client.py` | HTTP/SSE/STDIO support with trust checks |
| Approval workflow | `src/graphclaw/mcp/approval.py` | APPROVAL TaskNode creation + polling |
| Official discovery | `src/graphclaw/mcp/official_registry.py` | Cursor-based search over official registry API |
| REST API | `src/graphclaw/api/mcp_registry.py` | CRUD, search, tools list, approvals list |
| Data model | `src/graphclaw/models/nodes.py` | `MCPServerNode` schema and validators |


---

## 35. Architecture: Application API Layer (Cockpit Backend)

### 35.1 Purpose

The `/app/v1/` API layer is the HTTP interface between the GraphClaw backend and the cockpit web UI (`graphclaw-cockpit/`). It exposes all graph data, agent state, scoring results, and configuration through a structured REST + SSE + WebSocket surface.

Full endpoint specification: `docs/cockpit-backend-api-prd.md`
Cockpit PRD: `graphclaw-cockpit/docs/prd/11-api-contract.md`

### 35.2 Endpoint Surface Summary

| Group | Endpoints | Module | Priority |
|-------|-----------|--------|----------|
| Graph (goals, tasks, resources, edges) | 11 | `api/graph.py` | P1 |
| Scoring (explanation, history, simulate) | 3 | `api/scoring.py` | P1 |
| State machine (history, valid transitions, transition) | 3 | `api/state.py` | P1 |
| Events (SSE stream) | 1 | `api/events.py` | P1 |
| Chat (messages + WebSocket) | 4+WS | `api/chat.py` | P2 |
| Config (JSON config CRUD) | 3 | `api/config.py` | P2 |
| Secrets (CRUD + test + status) | 4 | `api/secrets.py` | P2 |
| Settings (profile, orgs, scoring weights, channels, LLM keys) | +8 | `api/settings.py` | P3 |
| Agent monitoring (status, queue, briefing, triggers) | 6 | `api/agent.py` | P3 |
| Agents / canvas (CRUD + versions + test) | 7 | `api/agents.py` | P3 |
| Skills (feedback, workers, executions, test) | +4 | `api/skill_registry.py` | P4 |
| MCP (tools list, MCP approvals) | +2 | `api/mcp_registry.py` | P4 |
| Admin (members, features, LLM, judge, guardrails, SSO, audit, infra, connectors) | 45 | `api/admin/*.py` | P6 |

**Total:** 104 new endpoints + 18 stub→real fixes = 122 endpoint implementations.

### 35.3 Shared Dependency Injection

All `/app/v1/` endpoints obtain their runtime dependencies via `api/deps.py`, which pulls from `app.state` (populated at startup):

| Dependency | `app.state` attribute | Type |
|------------|-----------------------|------|
| Graph store | `graph_store` | `GraphStore` |
| Query engine | `query_engine` | `GraphQueryEngine` |
| Scoring engine | `scoring_engine` | `ScoringEngine` |
| State machine | — (stateless) | `StateMachine()` |
| Storage client | `storage_client` | `StorageClient` |
| Secrets client | `secrets_client` | `SecretsClient` |
| Redis | `redis` | `redis.asyncio.Redis` |

### 35.4 Real-Time Events Architecture

`GET /app/v1/events` is a Server-Sent Events endpoint backed by Redis pub/sub:

- **Channel name:** `graphclaw:events:{user_id}` (per-user isolation)
- **Publishers:** `StateMachine.transition()`, `ScoringEngine.score_all()`, `AgentLoop`
- **Event types:** `task.state_changed`, `task.scored`, `briefing.ready`, `approval.pending`, `skill.completed`
- **Graceful degradation:** If Redis is absent, the stream emits keepalive pings only — cockpit remains functional without live updates

### 35.5 Authentication

All `/app/v1/` endpoints require a valid RS256 Bearer JWT (15-minute expiry, issued by `/auth/login` flow). Role escalation (`ADMIN` / `OWNER`) is enforced by `require_admin` in `api/deps.py`.

WebSocket (`/app/v1/chat/ws`) accepts the JWT as a `?token=` query parameter because browsers cannot set `Authorization` headers on WebSocket upgrades.

### 35.6 Build Progress

| Wave | Files | Status |
|------|-------|--------|
| Wave 1 — core canvas | `deps.py`, `graph.py`, `scoring.py`, `state.py`, `events.py` | ✅ Complete |
| Wave 2 — stub fixes | `approvals.py`, `settings.py`, `skill_registry.py`, `mcp_registry.py` | ⬜ Pending |
| Wave 3 — chat + config | `chat.py`, `config.py`, `secrets.py` | ⬜ Pending |
| Wave 4 — settings + agent | `settings.py` ext, `agent.py` | ⬜ Pending |
| Wave 5 — skills + MCP + agents | `skill_registry.py` ext, `mcp_registry.py` ext, `agents.py` | ⬜ Pending |
| Wave 6 — admin panel | `admin/` (9 modules) | ⬜ Pending |

---

## 36. Architecture: Node Intelligence Layer

> **Status:** Approved for build — Phase 4.5 (2026-04-12)  
> **Design doc:** `docs/architecture/intelligence-layer.md`

### 36.1 Motivation

Every task node accumulates context over its lifetime: emails sent, replies received, decisions made, updates from Telegram or any other channel. Today this context lives nowhere — it evaporates between agent turns. Briefings lack the narrative. Betty's graph summary shows state and score but not history. The node intelligence field closes this gap.

The intelligence layer introduces three interconnected capabilities:

1. **Node-level `intelligence` field** — a per-task/goal text blob in the graph that accumulates the communication log and decisions across all channels
2. **InboundIntelligenceAgent** — a lightweight inline processor that runs on every inbound message, classifies it, and routes task-specific content to the graph node and general observations to Betty's working memory
3. **Structured S3 log sink** — all agent actions and communication events written as JSONL to MinIO/S3, feedable to CloudWatch, with PII-safe allowlist-only event models

### 36.2 Two-Tier Context Model

| Tier | Storage | Purpose |
|---|---|---|
| **Agent memory** | MinIO `{user_id}/agents/{agent_id}/memory/working/context.md` | Betty's cross-task planning, user behavioral patterns, general discussion — NOT tied to a specific node |
| **Node intelligence** | Graph `TaskNode.intelligence`, `GoalNode.intelligence` | Per-task/goal: channel thread summaries, outbound log, decisions, context for briefings |

Agent memory serves Betty's global context across all tasks. Node intelligence is scoped to a single task and travels with it.

### 36.3 Intelligence Field Schema

Added to `TaskNode` and `GoalNode` in `src/graphclaw/models/nodes.py`:

```python
intelligence: str | None = None
```

Stored as a JSON-encoded string in Apache AGE (same serialization pattern as `update_log` and `state_history`).

**Entry format** — one line per event:

```
[{ISO-date}] {channel} | {direction} | {summary}

Examples:
[2026-03-07] email | outbound | Sent deadline reminder to Soni re: deliverable submission
[2026-04-12] telegram | inbound | Soni confirmed upload by EOD today
[2026-04-13] email | outbound | Sent "Re: Deliverable" to soni@acme.com
```

**Size limit:** ~500 words. When exceeded, oldest entries trimmed and replaced with `... {N} older entries archived`.

### 36.4 InboundIntelligenceAgent

A lightweight inline LLM processor — not a conversational agent, never user-facing.

**Identity in MinIO:**
```
{user_id}/agents/intelligence-processor/
├── config.json           ← model, prompt version, confidence thresholds
└── execution_log/
    └── {YYYY-MM-DD}.jsonl
```

No `profile.md`, no `memory/` tiers. Its output IS its memory — written to task nodes and Betty's `working/context.md`.

**Channel coverage:** Processes inbound messages from all registered channels. `InboundMessage.channel` field ("email", "telegram", "api", "cli", "whatsapp", "teams") determines the channel label in the intelligence entry. No per-channel code changes needed.

**Single LLM call per message:** Returns:
- `task_entry` — 60-word log line for this task (null if not task-specific)
- `memory_note` — one-line behavioral/project observation for Betty's memory (null if nothing to learn)

**LLM model:** Configurable via `INTELLIGENCE_AGENT_MODEL` env var. Default: lightweight model (haiku/mini) for cost efficiency.

### 36.5 Task Resolution Waterfall

Three tiers in priority order. First match wins.

| Tier | Method | Confidence |
|---|---|---|
| 1 | `in_reply_to` / `tg_reply_to_message_id` → Redis checkin lookup | Deterministic |
| 2 | TaskID regex `TSK-[A-Z]+-[0-9]+-[A-Z]+` in message body | 1.0 |
| 3 | Vector embedding cosine search on `node_embeddings` table | Scored (0.0–1.0) |

**Confidence thresholds for Tier 3:**

| Similarity | Action |
|---|---|
| ≥ 0.70 (HIGH) | Update node intelligence directly |
| 0.40–0.70 (MEDIUM) | Update node with `[unverified-match]` tag + note in Betty's context |
| < 0.40 (LOW) | Unmatched |
| Two results within 0.05 | Ambiguous → unmatched |

**Unmatched handling:**
- Known sender (in graph as ResourceNode/UserNode) → Betty actively asks user about the message
- Unknown sender → `inbox/recent/` only, no notification

### 36.6 Outbound Intelligence Logging

When Betty sends an outbound message in context of a task:

1. Appends log line to `task.intelligence`: `[{date}] {channel} | outbound | Sent "{subject[:60]}" to {recipient}`
2. Creates a `CheckinNode` in graph with `outbound_message`, linked via `REFERS_TO` to task
3. Stores `checkin:{original_msg_id} → {checkin_id, task_id}` in Redis (TTL 7 days) for tier-1 resolution of reply

When reply arrives matching a known checkin: `update_checkin_response(checkin_id, body)` completes the `CheckinNode` record.

### 36.7 Inbox Summarize-and-Archive

**Problem:** Storing full email bodies in MinIO for every message would grow unbounded.

**Solution:** Two-track storage per inbound message:

```
{user_id}/inbox/
├── recent/
│   └── {ISO}-{msg_id}.json    ← compact: sender, subject, 150-char preview,
│                                  channel, task_id, signal, archive_ref
└── archive/
    └── {ISO}-{msg_id}.json    ← full original: body, headers, attachments
```

Betty's `check_inbox` tool reads `recent/` only — always small, always fast. Full content available via `archive_ref`.

### 36.8 PII / PHI Safety

**Allowlist-only log events:** Each `event_type` has an explicit set of safe fields. No message body, subject, or raw text is ever written to a durable log sink.

**Message content in logs:** `args_summary` for tool calls replaces known sensitive keys (`body`, `content`, `subject`, `to`, `text`) with `"[{key}: {N} chars]"`.

**Intelligence field scrubbing:** Before writing to graph, regex patterns for SSN, credit card, and phone number formats are replaced with `[REDACTED-PII]`. LLM summarization abstracts content; the regex is a safety net.

**Archive files** (full email bodies) are:
- Encrypted at rest (MinIO SSE-S3 or SSE-KMS)
- Scoped to `{user_id}/inbox/archive/` — covered by GDPR erasure when `{user_id}/` prefix is deleted
- Never indexed beyond the 150-char `body_summary` in the recent entry

### 36.9 Structured Log Sink

**Log folder structure:**

```
_system/logs/{service}/{YYYY-MM-DD}/{HH00Z}.jsonl   ← infra events, no user PII
{user_id}/logs/agent/{YYYY-MM-DD}/{HH00Z}.jsonl     ← tool_call, message, scoring_cycle
{user_id}/logs/inbound/{YYYY-MM-DD}/{HH00Z}.jsonl   ← inbound_processed, intelligence_update
```

Hourly rolling files. Format: newline-delimited JSON (same as existing `AsyncLogger` output).

In local dev these files land in MinIO. In production, `AsyncLogger` continues writing to stdout, which ECS/EKS log drivers ship to CloudWatch automatically — no code change needed for the production path.

### 36.10 Implementation Files

| File | Change |
|---|---|
| `src/graphclaw/infra/logger.py` | Add StorageClient sink, `min_level` filter, `AsyncLogger.create()` factory |
| `src/graphclaw/gateway/deps.py` | Wire storage into AsyncLogger on startup |
| `src/graphclaw/models/nodes.py` | Add `intelligence: str | None` to `TaskNode`, `GoalNode` |
| `src/graphclaw/db/age/repository.py` | Add `update_node_intelligence`, `get_node_intelligence`, `create_checkin_node`, `update_checkin_response` |
| `src/graphclaw/inbound/intelligence_agent.py` | NEW: `InboundIntelligenceAgent` class |
| `src/graphclaw/agent/event_consumer.py` | Wire intelligence agent, fix InboundProcessor call, add direct INBOUND_MESSAGES consumer, add outbound logging |
| `src/graphclaw/agent/loop.py` | Add `_logger`, intelligence snippet in graph summary, `check_inbox` tool |
| `src/graphclaw/infra/storage.py` | Add `agent_inbox_recent_prefix`, `agent_inbox_archive` path helpers |

---

## 37. Architecture: Embedding Pipeline

> **Status:** Phase 4.5 prerequisite — must build before Section 36 Tier-3 resolution works  
> **Design doc:** `docs/architecture/intelligence-layer.md` §6

### 37.1 Current State

The pgvector infrastructure is fully provisioned but entirely disconnected from the application layer.

| Component | Status |
|---|---|
| `CREATE EXTENSION vector` in `init-db.sql` | ✅ Done |
| `node_embeddings (node_id TEXT, embedding vector(1536), computed_at TIMESTAMPTZ)` | ✅ Done |
| `IVFFlat (vector_cosine_ops, lists=100)` index | ✅ Done |
| `EmbeddingInputs` sub-model on `TaskNode` | ✅ Done |
| `EmbeddingClient` — embedding generation code | ❌ Not implemented |
| Trigger on task create/update | ❌ Not implemented |
| `TaskResolver._vector_search()` vector parameter | ⚠️ Stub (passes `None`, always returns unmatched) |
| Table name in `TaskResolver` SQL | ⚠️ Bug (`task_embeddings` should be `node_embeddings`) |

### 37.2 EmbeddingClient

**File:** `src/graphclaw/infra/embeddings.py` (new)

Wraps OpenAI embedding API (or LiteLLM proxy for multi-provider support).

```python
class EmbeddingClient:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"): ...
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
    async def close(self) -> None: ...
```

**Env vars:** `EMBEDDING_MODEL` (default `text-embedding-3-small`, 1536 dimensions), `OPENAI_API_KEY`.

### 37.3 Embedding Text Construction

For a `TaskNode`: `f"{task.title} {task.description} {task.embedding_inputs.goal_context}"` (uses the existing `EmbeddingInputs` model fields).

For inbound message matching: `f"{inbound.subject} {inbound.body[:300]}"`.

### 37.4 Trigger Strategy

**On task create/update:** fire-and-forget via `asyncio.create_task()` so task creation response is not blocked. Upsert into `node_embeddings`:

```sql
INSERT INTO node_embeddings (node_id, embedding, computed_at)
VALUES ($1, $2::vector, NOW())
ON CONFLICT (node_id) DO UPDATE
  SET embedding = EXCLUDED.embedding,
      computed_at = EXCLUDED.computed_at;
```

**Not triggered on:** scoring cycles, briefing generation, or read-only queries.

### 37.5 Resolver Fix

Two changes to `src/graphclaw/inbound/resolver.py`:
1. Table name: `task_embeddings` → `node_embeddings`
2. Generate embedding vector from inbound text, pass as `$1` rather than `None`

Existing confidence thresholds are correct — no change to threshold values.

### 37.6 Confidence Thresholds

Defined in `TaskResolver` (existing, no change needed):

```python
HIGH_THRESHOLD = 0.7    # similarity ≥ 0.7 → HIGH confidence match
MEDIUM_THRESHOLD = 0.4  # 0.4 ≤ similarity < 0.7 → MEDIUM confidence
                        # similarity < 0.4 → LOW (unmatched)
```

Ambiguous results (two matches within 0.05 of each other) are always treated as unmatched regardless of absolute score.

### 37.7 Implementation Files

| File | Change |
|---|---|
| `src/graphclaw/infra/embeddings.py` | NEW: `EmbeddingClient` |
| `src/graphclaw/db/age/repository.py` | Add embedding upsert after `create_node()` / `update_node()` for `TaskNode` |
| `src/graphclaw/inbound/resolver.py` | Fix table name bug; wire `EmbeddingClient` for vector search |

---

## 38. Architecture: Sub-Agent Parallel Orchestration

**Status:** ✅ Delivered — 2026-04-13  
**Design doc:** `docs/architecture/05-data-flow.md` §7

### 38.1 Overview

The orchestrating agent (`AgentLoop`) now delegates tasks to sub-agents that run in parallel as background processes. This closes 8 architectural gaps identified in the original design:

| Gap | Fix |
|-----|-----|
| `delegate_to_agent` was fire-and-forget | Publishes `AgentJobEvent` to `AGENT_JOBS` broker queue |
| No sub-agent run loop | `SubAgentRunner` — mini LLM tool-use loop for delegated tasks |
| No parallel dispatch | `AgentDispatchPlanner` — topological sort over task `DEPENDS_ON` graph |
| No structured update protocol | `AGENT_UPDATES` queue with typed events; `_consume_agent_updates_loop()` |
| No session/context propagation | `AgentJobEvent` carries `session_id`, `parent_task_id`; sub-agent propagates to all events |
| No heartbeat / liveness | `SubAgentRunner` emits `AgentHeartbeatEvent` every 60s; `AgentHealthMonitor` tracks all |
| No fan-in mechanism | `BatchCoordinator` inside `SubAgentPool` counts completions per tier |
| No orchestrator re-engagement | `BatchCoordinator` publishes `DELEGATION_COMPLETE` to `TRIGGER_EVENTS` after final tier |

### 38.2 Data Flow

```
AgentLoop._tool_delegate_to_agent()
  ↓  publishes AgentJobEvent (agent_id, task_id, session_id, batch_id, instructions)
AGENT_JOBS queue
  ↓  consumed by
SubAgentPool (semaphore throttle — max_concurrent_agents)
  ↓  dispatches to
SubAgentRunner.execute()
  ↓  LLM tool-use loop (invoke_skill + call_mcp_tool only — flat delegation)
  ↓  emits to AGENT_UPDATES queue:
       AgentTaskStartedEvent → record_heartbeat()
       AgentTaskProgressEvent → record_heartbeat()
       AgentHeartbeatEvent → record_heartbeat()
       AgentTaskCompletedEvent → ResultCollector.process_agent_result() + StateMachine.transition()
       AgentTaskBlockedEvent → EscalationService.check_and_escalate()
  ↓
BatchCoordinator.record_completion(batch_id)
  ├── tier not complete: no-op
  └── tier complete → dispatch next tier jobs to AGENT_JOBS
       └── final tier complete → publish DELEGATION_COMPLETE to TRIGGER_EVENTS
              ↓
       AgentEventConsumer._consume_loop() handles DELEGATION_COMPLETE
              ↓
       AgentLoop.process_chat_message() — orchestrator re-engagement with batch summary
```

### 38.3 Components

#### SubAgentRunner (`src/graphclaw/agent/sub_agent_runner.py`)
- Lifecycle: IDLE → RUNNING → COMPLETED / FAILED / TIMED_OUT
- Reads delegation context from MinIO (`StoragePaths.agent_memory_working(user_id, agent_id)`)
- LLM tool-use loop up to 15 iterations; available tools: `invoke_skill`, `call_mcp_tool` only
- Uses dedicated `WorkerPool` (sub-agents never share the orchestrator's worker pool)
- Emits heartbeat every `GRAPHCLAW_AGENT_HEARTBEAT_INTERVAL_SECONDS` seconds (default 60)
- All events carry `agent_id + task_id + session_id + batch_id` for audit correlation

#### SubAgentPool (`src/graphclaw/agent/sub_agent_pool.py`)
- Semaphore throttle: at most `GRAPHCLAW_MAX_CONCURRENT_AGENTS` runners active (default 4)
- Consumes `AGENT_JOBS` broker queue; overflow stays queued (never dropped)
- Contains `BatchCoordinator` for fan-in tier tracking
- `register_dispatch_plan(tiers, session_id)` called by `AgentLoop` after `AgentDispatchPlanner.plan()`

#### AgentDispatchPlanner (`src/graphclaw/agent/dispatch_planner.py`)
- Queries `GraphQueryEngine` for `DEPENDS_ON` edges among proposed delegation task IDs
- Kahn's BFS topological sort → ordered tier list `[[task_C], [task_A, task_B], [task_D]]`
- Each tier contains tasks safe to run in parallel; tiers execute sequentially
- Assigns `batch_id` per tier; jobs in same tier share a `batch_id`

#### AgentHealthMonitor (`src/graphclaw/agent/health_monitor.py`)
- Tracks `last_heartbeat` timestamp per `agent_id` (updated by `_consume_agent_updates_loop`)
- Background polling loop every 30s (configurable)
- On timeout: `StateMachine.transition(BLOCKED)` + publish `AgentUpdateEventType.BLOCKED` to `AGENT_UPDATES` + audit log
- Recovery policy: BLOCKED only (no retry — avoids duplicate MCP writes or emails)

#### BatchCoordinator (`src/graphclaw/agent/sub_agent_pool.py`)
- Tracks completion count per `batch_id`
- When all jobs in a tier complete: dispatches next tier jobs to `AGENT_JOBS`
- When final tier completes: publishes `DELEGATION_COMPLETE` to `TRIGGER_EVENTS` with `session_id`

### 38.4 Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPHCLAW_MAX_CONCURRENT_AGENTS` | `4` | Maximum parallel sub-agent runners |
| `GRAPHCLAW_SUBAGENT_WORKER_POOL_SIZE` | `4` | Dedicated WorkerPool size for sub-agents |
| `GRAPHCLAW_AGENT_HEARTBEAT_INTERVAL_SECONDS` | `60` | Sub-agent heartbeat emission interval |
| `GRAPHCLAW_AGENT_HEARTBEAT_TIMEOUT_SECONDS` | `300` | Seconds of silence before BLOCKED escalation |

### 38.5 Broker Queues

| Queue | Publisher | Consumer | Purpose |
|-------|-----------|----------|---------|
| `agent_jobs` | `AgentLoop._tool_delegate_to_agent()` | `SubAgentPool` | Delegation job dispatch |
| `agent_updates` | `SubAgentRunner` + `AgentHealthMonitor` | `AgentEventConsumer._consume_agent_updates_loop()` | Typed sub-agent progress events |

### 38.6 New Audit Event Classes (`src/graphclaw/infra/logger.py`)

| Class | Fields | Purpose |
|-------|--------|---------|
| `AgentTaskStartedEvent` | `agent_id, task_id, session_id, parent_task_id, batch_id` | Sub-agent task pickup |
| `AgentTaskProgressEvent` | `agent_id, task_id, session_id, message, iteration` | LLM iteration progress |
| `AgentTaskCompletedEvent` | `agent_id, task_id, session_id, status, duration_ms, parent_task_id, batch_id` | Task completion |
| `AgentTaskBlockedEvent` | `agent_id, task_id, session_id, reason` | Heartbeat timeout or failure |
| `AgentHeartbeatEvent` | `agent_id, task_id, session_id` | Liveness heartbeat |

### 38.7 Design Constraints

- **Flat delegation (max depth = 2):** Sub-agents' toolset excludes `delegate_to_agent`. Orchestrator → sub-agents only. Prevents infinite delegation chains and simplifies audit.
- **Dedicated worker pools:** Sub-agents use their own `WorkerPool`. Orchestrator pool is never starved by background delegations.
- **No retry on timeout:** Task marked BLOCKED immediately; escalation service surfaces it. Prevents duplicate MCP writes, emails, or other side effects the sub-agent may have already performed.

### 38.8 Files

| File | Type | Purpose |
|------|------|---------|
| `src/graphclaw/agent/sub_agent_runner.py` | New | SubAgentRunner + AgentJobEvent + AgentUpdateEvent models |
| `src/graphclaw/agent/sub_agent_pool.py` | New | SubAgentPool + BatchCoordinator |
| `src/graphclaw/agent/dispatch_planner.py` | New | AgentDispatchPlanner (topological sort) |
| `src/graphclaw/agent/health_monitor.py` | New | AgentHealthMonitor (heartbeat tracking) |
| `src/graphclaw/agent/loop.py` | Modified | Added broker + dispatch_planner + sub_agent_pool params; `_pre_plan_delegation_turn()`; `_tool_delegate_to_agent()` publishes to AGENT_JOBS |
| `src/graphclaw/agent/event_consumer.py` | Modified | Added `_consume_agent_updates_loop()` third background task |
| `src/graphclaw/agent/result_collector.py` | Modified | Added `process_agent_result()` for AgentUpdateEvent completion handling |
| `src/graphclaw/infra/broker.py` | Modified | Added `AGENT_JOBS`, `AGENT_UPDATES` constants |
| `src/graphclaw/infra/config.py` | Modified | Added `AgentPoolConfig` with 4 env vars |
| `src/graphclaw/infra/logger.py` | Modified | Added 5 new audit event classes |
| `src/graphclaw/gateway/app.py` | Modified | Wired SubAgentPool + AgentHealthMonitor + AgentDispatchPlanner into lifespan |
| `tests/test_agent/test_sub_agent_orchestration.py` | New | 27 unit tests covering all Phase 5 components |

---

## 39. Architecture: Unified Logging System

**Status:** Implemented — Waves 1–6 (2026-04-24)
**Decision:** Replace custom `AsyncLogger` with stdlib `logging.handlers.QueueHandler` + `QueueListener` as the single unified logging backend.

### 39.1 Rationale

| Concern | Old (`AsyncLogger`) | New (stdlib `QueueHandler`) |
|---|---|---|
| Consumer runs in | asyncio Task (event loop) | Dedicated OS thread (independent) |
| Event loop starvation | Yes — shares loop with requests | No — separate thread |
| Third-party lib capture | No (uvicorn, httpx bypass it) | Yes — root logger captures all |
| Call-site convention | Custom `_logger.log(event_type, session_id, **fields)` | Standard `logger.info(msg, extra={})` |
| `session_id` threading | Required kwarg on every call | `ContextVar` — set once per request, automatic |
| New dependency | None | None (stdlib since Python 3.2) |

### 39.2 File Structure

**New files (created):**
```
src/graphclaw/infra/logging/
    __init__.py          — configure_logging(), stop_logging(), _listener lifecycle
    formatter.py         — JsonFormatter: LogRecord → single JSONL line
    context.py           — ContextVar[str] for session_id, SessionFilter, set/get_session_id(), generate_session_id()
    events.py            — PII-safe Pydantic models (moved from infra/logger.py)
    middleware.py        — LoggingMiddleware: sets session_id ContextVar, logs every HTTP request
    llm_trace.py         — Isolated "graphclaw.llm.trace" logger with RotatingFileHandler
    handlers/
        __init__.py
        stdout.py        — StdoutJsonHandler(logging.StreamHandler)
        object_storage.py — ObjectStorageHandler: sync boto3 in listener thread, batching
        cloudwatch.py    — CloudWatchHandler wrapping watchtower

src/graphclaw/llm/logging_mixin.py  — LLMTraceMixin injected into Anthropic + OpenAI clients
```

**Deleted files:**
```
src/graphclaw/infra/logger.py              — AsyncLogger class removed; Pydantic models moved to events.py
src/graphclaw/infra/sinks/                 — Entire directory deleted (base, stdout, object_storage, cloudwatch, formatting, __init__)
```

### 39.3 JsonFormatter Field Schema

Every log record is a single JSONL line with these fields:

| Field | Source | Always present |
|---|---|---|
| `timestamp` | `record.created` → ISO-8601 UTC + Z | Yes |
| `level` | `record.levelname` | Yes |
| `service` | configured `service_name` | Yes |
| `logger` | `record.name` (Python logger name) | Yes |
| `message` | `record.getMessage()` | Yes |
| `event_type` | `extra={"event_type": ...}` | When set |
| `session_id` | injected by `SessionFilter` from `ContextVar` | When context is set |
| `user_id`, `task_id`, etc. | `extra={}` keys | When set |
| `exc_info` | formatted traceback | On exceptions |

### 39.4 session_id Propagation

`ContextVar[str]` named `session_id` with default `""`.  
Set once at request/task entry:
- **HTTP requests**: `LoggingMiddleware` reads `X-Session-ID` header or generates `SES-<uuid>`, calls `set_session_id()`
- **Background tasks**: First line of each `SubAgentRunner.execute()`, `BriefingGenerator.generate()`, etc.

`SessionFilter` attached to `logging.getLogger("graphclaw")` injects `record.session_id` from the `ContextVar` on every log call — no parameter threading required.

asyncio `contextvars` propagates the value automatically to all coroutines spawned within the same request context.

### 39.5 LLM Trace Logger

**Logger name:** `graphclaw.llm.trace`  
**`propagate = False`** — content never flows to stdout, S3, or CloudWatch.  
**Output:** `logs/llm-traces.jsonl` (RotatingFileHandler, 50 MB × 10 files)  
**Activation:** `LLM_TRACE=true` env var OR `LOG_LEVEL=DEBUG`

Fields per LLM call:
```
timestamp, session_id, user_id, provider, model, call_type,
messages (full prompt — PII),
params (temperature, max_tokens, tool names only),
response_content, response_tool_calls,
prompt_tokens, completion_tokens, cost_usd,
latency_ms, error
```

`logs/llm-traces.jsonl` must be in `.gitignore`. Never ship this file or include it in log aggregation pipelines without explicit opt-in.

### 39.6 ObjectStorageHandler Design

`QueueListener` calls `emit()` synchronously from its dedicated OS thread. `ObjectStorageHandler` uses **synchronous boto3** directly — no asyncio boundary crossing.

Batching: flush when `batch_size` (50) records accumulated OR `flush_interval` (30s) elapsed.  
Path: `{user_id}/logs/{service}/{date}/{hour}Z.jsonl` (user-scoped) or `system/logs/{service}/{date}/{hour}Z.jsonl`.  
`close()` calls `_flush()` for graceful drain on shutdown.

### 39.7 PII-Safe Event Models

The Pydantic models (`AgentMessageEvent`, `AgentToolCallEvent`, `MCPActionEvent`, `AgentTaskStartedEvent`, etc.) move to `infra/logging/events.py`. They remain as **validation helpers only** — call sites validate the model first, then pass `model.model_dump()` into `extra={}`. The model's field allowlist is still the PII guard. The transport layer never sees raw dicts with arbitrary keys.

### 39.8 Configuration

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG \| INFO \| WARNING \| ERROR` |
| `LOG_SINKS` | `stdout` | Comma-separated: `stdout`, `object_storage`, `cloudwatch` |
| `LLM_TRACE` | `false` | `true` enables LLM prompt/response trace file |
| `LLM_TRACE_PATH` | `logs/llm-traces.jsonl` | Path for the LLM trace rotating file |

`LOG_FORMAT` (jsonl/pipe) removed — JSONL is the only format.

### 39.9 Migration: `get_logger()` FastAPI Dependency

`get_logger()` is deleted from `gateway/deps.py`. No route injects a logger via `Depends()`. All logging uses module-level `logger = logging.getLogger(__name__)` with `extra={}` for structured fields.

### 39.10 Frontend Logger

**File:** `src/lib/logger.ts` (graphclaw-cockpit)  
**Pattern:** Module-level `createLogger(name)` factory, not React context.  
**Level control:** `VITE_LOG_LEVEL` env var (default `INFO`).  
**Output:** Structured JSONL to console in all environments.

Instrumented subsystems:
- `api-client.ts` — every request/response via openapi-fetch middleware
- `sse.ts` — connect, error, reconnect
- `chat-stream.ts` — stream start, complete, error
- `websocket.ts` — open, close, error
- `stores/auth.ts` — tokens set, logout

### 39.11 Files Modified

| File | Change |
|---|---|
| `gateway/deps.py` | Remove `AsyncLogger` + `get_logger()`; call `configure_logging()` |
| `gateway/app.py` | Register `LoggingMiddleware`; call `stop_logging()` in lifespan shutdown |
| `agent/main_orchestrator.py` | Remove `_logger: AsyncLogger` param; migrate 4 `self._logger.log()` calls |
| `agent/sub_agent_runner.py` | Remove `_logger` param; migrate 5 audit helpers; add `set_session_id()` at entry |
| `agent/sub_agent_pool.py` | Remove `_logger` param (no direct log calls) |
| `agent/health_monitor.py` | Remove `_logger` param; migrate 1 call |
| `inbound/intelligence_agent.py` | Remove `_logger` param; migrate 2 calls |
| `inbound/processor.py` | Remove `_logger` param; migrate 1 call |
| `skills/heartbeat.py` | Migrate 2 calls |
| `triggers/briefing.py` | Migrate 1 call; add `set_session_id()` |
| `llm/anthropic/client.py` | Add `LLMTraceMixin`; wrap `complete()` + `stream()` |
| `llm/openai/client.py` | Add `LLMTraceMixin`; wrap `complete()` + `stream()` |
| `infra/__init__.py` | Re-export `generate_session_id`, `set_session_id`, `get_session_id` from new path |
