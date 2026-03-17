-- =============================================================================
-- GraphClaw seed data for local development
-- Run manually after init-db.sql has executed:
--   docker compose exec db psql -U graphclaw -d graphclaw -f /scripts/seed-data.sql
-- =============================================================================

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- =============================================================================
-- UserNode
-- =============================================================================

SELECT * FROM cypher('graphclaw', $$
    CREATE (:UserNode {
        id:         'user-001',
        name:       'Alice Dev',
        email:      'alice@graphclaw.ai',
        timezone:   'America/New_York',
        created_at: '2026-03-17T00:00:00Z'
    })
$$) AS (v agtype);

-- =============================================================================
-- GoalNode  (Priority 1)
-- =============================================================================

SELECT * FROM cypher('graphclaw', $$
    CREATE (:GoalNode {
        id:          'goal-001',
        title:       'Ship GraphClaw Phase 0 MVP',
        description: 'Deliver a working core-loop proof: task graph CRUD, scoring, CLI',
        priority:    1,
        status:      'active',
        due_date:    '2026-04-30T23:59:59Z',
        created_at:  '2026-03-17T00:00:00Z'
    })
$$) AS (v agtype);

-- =============================================================================
-- TaskNodes
-- =============================================================================

-- Task 1 — Research (open, not yet started)
SELECT * FROM cypher('graphclaw', $$
    CREATE (:TaskResearch {
        id:          'task-001',
        title:       'Research Apache AGE Cypher query patterns',
        status:      'open',
        priority:    2,
        effort_hrs:  4,
        created_at:  '2026-03-17T00:00:00Z'
    })
$$) AS (v agtype);

-- Task 2 — Atomic (in_progress, depends on task-001)
SELECT * FROM cypher('graphclaw', $$
    CREATE (:TaskAtomic {
        id:          'task-002',
        title:       'Implement AGE query helpers in db/queries',
        status:      'in_progress',
        priority:    2,
        effort_hrs:  6,
        created_at:  '2026-03-17T00:00:00Z'
    })
$$) AS (v agtype);

-- Task 3 — Atomic (open, depends on task-002)
SELECT * FROM cypher('graphclaw', $$
    CREATE (:TaskAtomic {
        id:          'task-003',
        title:       'Implement Pydantic node schemas',
        status:      'open',
        priority:    2,
        effort_hrs:  4,
        created_at:  '2026-03-17T00:00:00Z'
    })
$$) AS (v agtype);

-- Task 4 — Composite (open, part of goal-001)
SELECT * FROM cypher('graphclaw', $$
    CREATE (:TaskComposite {
        id:          'task-004',
        title:       'Build CLI command suite',
        status:      'open',
        priority:    1,
        effort_hrs:  8,
        created_at:  '2026-03-17T00:00:00Z'
    })
$$) AS (v agtype);

-- Task 5 — Approval (blocked — waiting on task-003 and task-004)
SELECT * FROM cypher('graphclaw', $$
    CREATE (:TaskApproval {
        id:            'task-005',
        title:         'Code review: Phase 0 core loop',
        status:        'blocked',
        priority:      1,
        effort_hrs:    2,
        blocked_by_id: 'task-003',
        created_at:    '2026-03-17T00:00:00Z'
    })
$$) AS (v agtype);

-- Task 6 — Recurring (snoozed until next Monday)
SELECT * FROM cypher('graphclaw', $$
    CREATE (:TaskRecurring {
        id:            'task-006',
        title:         'Weekly sync: graphclaw progress',
        status:        'snoozed',
        priority:      3,
        recurrence:    'weekly',
        snoozed_until: '2026-03-23T09:00:00Z',
        effort_hrs:    1,
        created_at:    '2026-03-17T00:00:00Z'
    })
$$) AS (v agtype);

-- =============================================================================
-- Edges
-- =============================================================================

-- task-002 DEPENDS_ON task-001 (can't implement until research is done)
SELECT * FROM cypher('graphclaw', $$
    MATCH (a:TaskResearch {id: 'task-001'}),
          (b:TaskAtomic   {id: 'task-002'})
    CREATE (b)-[:DEPENDS_ON {created_at: '2026-03-17T00:00:00Z'}]->(a)
$$) AS (e agtype);

-- task-003 DEPENDS_ON task-002
SELECT * FROM cypher('graphclaw', $$
    MATCH (a:TaskAtomic {id: 'task-002'}),
          (b:TaskAtomic {id: 'task-003'})
    CREATE (b)-[:DEPENDS_ON {created_at: '2026-03-17T00:00:00Z'}]->(a)
$$) AS (e agtype);

-- task-004 DEPENDS_ON task-002
SELECT * FROM cypher('graphclaw', $$
    MATCH (a:TaskAtomic    {id: 'task-002'}),
          (b:TaskComposite {id: 'task-004'})
    CREATE (b)-[:DEPENDS_ON {created_at: '2026-03-17T00:00:00Z'}]->(a)
$$) AS (e agtype);

-- task-005 DEPENDS_ON task-003 AND task-004
SELECT * FROM cypher('graphclaw', $$
    MATCH (a:TaskAtomic    {id: 'task-003'}),
          (b:TaskApproval  {id: 'task-005'})
    CREATE (b)-[:DEPENDS_ON {created_at: '2026-03-17T00:00:00Z'}]->(a)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (a:TaskComposite {id: 'task-004'}),
          (b:TaskApproval  {id: 'task-005'})
    CREATE (b)-[:DEPENDS_ON {created_at: '2026-03-17T00:00:00Z'}]->(a)
$$) AS (e agtype);

-- task-003 BLOCKS task-005  (explicit blocking relationship for testing)
SELECT * FROM cypher('graphclaw', $$
    MATCH (a:TaskAtomic   {id: 'task-003'}),
          (b:TaskApproval {id: 'task-005'})
    CREATE (a)-[:BLOCKS {created_at: '2026-03-17T00:00:00Z'}]->(b)
$$) AS (e agtype);

-- All tasks ASSIGNED_TO user-001
SELECT * FROM cypher('graphclaw', $$
    MATCH (u:UserNode    {id: 'user-001'}),
          (t:TaskResearch {id: 'task-001'})
    CREATE (t)-[:ASSIGNED_TO {assigned_at: '2026-03-17T00:00:00Z'}]->(u)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (u:UserNode  {id: 'user-001'}),
          (t:TaskAtomic {id: 'task-002'})
    CREATE (t)-[:ASSIGNED_TO {assigned_at: '2026-03-17T00:00:00Z'}]->(u)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (u:UserNode  {id: 'user-001'}),
          (t:TaskAtomic {id: 'task-003'})
    CREATE (t)-[:ASSIGNED_TO {assigned_at: '2026-03-17T00:00:00Z'}]->(u)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (u:UserNode      {id: 'user-001'}),
          (t:TaskComposite {id: 'task-004'})
    CREATE (t)-[:ASSIGNED_TO {assigned_at: '2026-03-17T00:00:00Z'}]->(u)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (u:UserNode    {id: 'user-001'}),
          (t:TaskApproval {id: 'task-005'})
    CREATE (t)-[:ASSIGNED_TO {assigned_at: '2026-03-17T00:00:00Z'}]->(u)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (u:UserNode     {id: 'user-001'}),
          (t:TaskRecurring {id: 'task-006'})
    CREATE (t)-[:ASSIGNED_TO {assigned_at: '2026-03-17T00:00:00Z'}]->(u)
$$) AS (e agtype);

-- All tasks PART_OF goal-001
SELECT * FROM cypher('graphclaw', $$
    MATCH (g:GoalNode     {id: 'goal-001'}),
          (t:TaskResearch {id: 'task-001'})
    CREATE (t)-[:PART_OF {created_at: '2026-03-17T00:00:00Z'}]->(g)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (g:GoalNode  {id: 'goal-001'}),
          (t:TaskAtomic {id: 'task-002'})
    CREATE (t)-[:PART_OF {created_at: '2026-03-17T00:00:00Z'}]->(g)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (g:GoalNode  {id: 'goal-001'}),
          (t:TaskAtomic {id: 'task-003'})
    CREATE (t)-[:PART_OF {created_at: '2026-03-17T00:00:00Z'}]->(g)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (g:GoalNode      {id: 'goal-001'}),
          (t:TaskComposite {id: 'task-004'})
    CREATE (t)-[:PART_OF {created_at: '2026-03-17T00:00:00Z'}]->(g)
$$) AS (e agtype);

SELECT * FROM cypher('graphclaw', $$
    MATCH (g:GoalNode    {id: 'goal-001'}),
          (t:TaskApproval {id: 'task-005'})
    CREATE (t)-[:PART_OF {created_at: '2026-03-17T00:00:00Z'}]->(g)
$$) AS (e agtype);
