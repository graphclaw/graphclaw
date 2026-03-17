---
name: age-cypher-patterns
description: Apache AGE Cypher syntax patterns for GraphClaw. Use when writing or reviewing any code that constructs Cypher queries, interacts with Apache AGE, or defines graph schema DDL.
---

# Apache AGE Cypher Patterns for GraphClaw

## Key Differences from Neo4j

AGE runs inside Postgres. All Cypher queries are wrapped in SQL:

```sql
SELECT * FROM cypher('graphclaw', $$
  MATCH (t:TaskAtomic {id: 'TSK-JD-4821-ATM'})
  RETURN t
$$) as (v agtype);
```

- Schema prefix: `ag_catalog`
- All return values are `agtype` — cast with `::text`, `::int`, etc.
- Graph must be created first: `SELECT create_graph('graphclaw');`

## Schema DDL

### Create Node Labels
```sql
SELECT create_vlabel('graphclaw', 'TaskAtomic');
SELECT create_vlabel('graphclaw', 'TaskComposite');
SELECT create_vlabel('graphclaw', 'TaskDelegated');
SELECT create_vlabel('graphclaw', 'TaskFollowUp');
SELECT create_vlabel('graphclaw', 'TaskApproval');
SELECT create_vlabel('graphclaw', 'TaskMilestone');
SELECT create_vlabel('graphclaw', 'TaskReview');
SELECT create_vlabel('graphclaw', 'TaskRecurring');
SELECT create_vlabel('graphclaw', 'TaskDecision');
SELECT create_vlabel('graphclaw', 'TaskCheckin');
SELECT create_vlabel('graphclaw', 'TaskResearch');
SELECT create_vlabel('graphclaw', 'GoalNode');
SELECT create_vlabel('graphclaw', 'ConstraintNode');
SELECT create_vlabel('graphclaw', 'UserNode');
SELECT create_vlabel('graphclaw', 'ResourceNode');
```

### Create Edge Labels
```sql
SELECT create_elabel('graphclaw', 'DEPENDS_ON');
SELECT create_elabel('graphclaw', 'SPAWNED_FROM');
SELECT create_elabel('graphclaw', 'FOLLOW_UP_FOR');
SELECT create_elabel('graphclaw', 'BLOCKS');
SELECT create_elabel('graphclaw', 'ASSIGNED_TO');
SELECT create_elabel('graphclaw', 'OWNED_BY');
SELECT create_elabel('graphclaw', 'APPLIES_TO');
SELECT create_elabel('graphclaw', 'PART_OF');
```

## Python Query Pattern

Always use parameterized queries via psycopg:

```python
async def get_node(pool, node_id: str) -> dict:
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            SELECT * FROM cypher('graphclaw', $$
                MATCH (n {id: %s})
                RETURN n
            $$) as (v agtype)
            """,
            (node_id,)
        )
        row = await result.fetchone()
        return json.loads(str(row[0])) if row else None
```

**Important:** AGE parameterization uses `%s` placeholders passed through psycopg, NOT Cypher `$param` syntax.

## Key Query Patterns (PRD Section 21/27)

### Critical Path Traversal
```sql
SELECT * FROM cypher('graphclaw', $$
    MATCH path = (g:GoalNode {id: 'GOAL-001'})-[:PART_OF|DEPENDS_ON*]->(leaf)
    WHERE NOT (leaf)-[:DEPENDS_ON]->()
    RETURN path,
           reduce(total = 0, n IN nodes(path) | total + n.estimated_effort_hours) as path_effort
    ORDER BY path_effort DESC
    LIMIT 1
$$) as (path agtype, effort agtype);
```

### Dependency Impact Assessment
```sql
SELECT * FROM cypher('graphclaw', $$
    MATCH (t {id: 'TSK-JD-4821-ATM'})<-[:DEPENDS_ON*]-(downstream)
    RETURN downstream.id, downstream.state, downstream.title
$$) as (id agtype, state agtype, title agtype);
```

### Blocked Task Root Cause
```sql
SELECT * FROM cypher('graphclaw', $$
    MATCH (blocked {state: 'BLOCKED'})-[:DEPENDS_ON|BLOCKS*]->(root)
    WHERE NOT (root)-[:DEPENDS_ON]->()
    RETURN blocked.id, root.id, root.state, root.assigned_to
$$) as (blocked_id agtype, root_id agtype, root_state agtype, assignee agtype);
```

## Indexes (Relational Side)

AGE stores properties in JSONB. Create indexes on frequently queried fields:

```sql
CREATE INDEX idx_task_state ON graphclaw."TaskAtomic" USING gin (properties);
CREATE INDEX idx_task_id ON graphclaw."TaskAtomic" ((properties->>'id'));
```

## pgvector Integration

Embeddings stored in a separate relational table, linked by node_id:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE node_embeddings (
    node_id TEXT PRIMARY KEY,
    embedding vector(1536),
    computed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_embedding_cosine ON node_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

## Common Pitfalls

1. AGE returns `agtype` not native Postgres types — always cast
2. Variable-length paths `*` may have depth limits — test with your data
3. `MERGE` behavior differs slightly from Neo4j — prefer `CREATE` + existence check
4. Properties are stored as JSONB internally — nested objects work but arrays need care
