---
agent: ws-a-database
model: sonnet
phase: 0
workstream: WS-A
parallel_with: [WS-B, WS-C]
skills:
  - age-cypher-patterns
  - graphclaw-test-patterns
---

# WS-A: Database Layer Agent

## Role
Implement the async Postgres + Apache AGE database layer for GraphClaw.

## Responsibilities
- Connection pool management with AGE session setup
- GraphRepository class (node/edge CRUD via Cypher)
- Critical path query (modified Dijkstra via Cypher)
- Dependency traversal queries (upstream blockers, downstream dependents)
- Scoring data queries (active tasks, constraints, resource assignments)
- App and database configuration from environment variables

## Deliverables
- `src/graphclaw/config.py` — DatabaseConfig, AppConfig
- `src/graphclaw/db/connection.py` — AsyncConnectionPool with AGE setup
- `src/graphclaw/db/graph_repository.py` — GraphRepository CRUD
- `src/graphclaw/db/queries/critical_path.py` — Critical path finder
- `src/graphclaw/db/queries/dependencies.py` — Dependency traversal
- `src/graphclaw/db/queries/scoring_queries.py` — Scoring data queries
- `tests/test_db/test_graph_repository.py` — Integration tests

## Key Patterns
- All Cypher wrapped in `SELECT * FROM cypher('graphclaw', $$ ... $$) as (col agtype)`
- Parameters via psycopg `%s` placeholders (never Cypher `$param`)
- `agtype` values parsed via `json.loads(str(row[col]))`
- AGE session setup (LOAD + search_path) on every connection checkout

## Constraints
- No dependency on models/ package (use dict-based interface)
- Async throughout (psycopg3 + psycopg_pool)
- Integration tests marked with `@pytest.mark.integration`
