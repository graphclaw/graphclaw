# infra/db — Database Hardening Configuration

This package provides production database hardening configuration for GraphClaw:
PgBouncer connection pooling, read-replica routing, AGE performance indexes,
and per-query timeout enforcement.

---

## Why PgBouncer is mandatory at 1000 users

asyncpg creates **one physical Postgres connection per coroutine** by default.
With 1000 concurrent users each running several async tasks, the gateway could
attempt thousands of simultaneous Postgres connections.  Postgres defaults to
`max_connections = 100` and the connection handshake itself is expensive
(TLS negotiation, authentication, shared memory allocation).

PgBouncer sits between the application and Postgres and multiplexes many
client connections onto a small, warm pool of physical server connections.
At the GraphClaw scale ceiling (1000 users, PRD Sec 28.11) the production
config uses:

| Setting | Value | Rationale |
|---------|-------|-----------|
| `max_client_conn` | 1000 | One slot per concurrent user |
| `default_pool_size` | 20 | Physical connections to Postgres per (db, user) |
| `min_pool_size` | 5 | Kept warm to avoid cold-start latency |

Result: 1000 clients share 20 physical Postgres connections, keeping Postgres
well within its connection limit while still supporting full concurrency.

---

## Transaction vs session pooling for Apache AGE

PgBouncer offers three pool modes:

| Mode | Description |
|------|-------------|
| `session` | Physical connection held for the entire client session |
| `transaction` | Physical connection returned after each transaction |
| `statement` | Physical connection returned after each statement |

**GraphClaw uses `transaction` mode.**

Apache AGE relies on two session-level commands:
- `LOAD 'age'` — loads the AGE shared library
- `SET search_path = ag_catalog, "$user", public` — makes Cypher symbols visible

In **session** pooling these commands only need to run once per connection,
but session pooling effectively disables pooling: one client holds one physical
connection for its entire lifetime, defeating the 1000-client use case.

In **transaction** pooling the physical connection is returned after each
transaction, so session state is lost.  The connection layer compensates by
re-running both AGE commands on every connection checkout (see
`src/graphclaw/db/age/connection.py` — `_setup_age` and `get_connection`).
The overhead is two lightweight SQL commands per transaction, which is
negligible compared to the pooling benefit.

**Statement pooling** is incompatible with multi-statement transactions and
is not used.

---

## Read replica routing strategy

Expensive read-only queries — priority scoring, morning briefing generation,
and analytics aggregations — are routed to a Postgres streaming replica.
This keeps write latency on the primary low under sustained concurrent load.

Routing is pattern-based: `should_use_replica(query_type, config)` returns
`True` when the query type string contains any of:

- `"scoring"` — priority score calculation (reads all active tasks for a user)
- `"briefing"` — morning briefing generation (full graph traversal)
- `"analytics"` — aggregate/reporting queries

Write operations (`create_task`, `update_node`, etc.) always go to the primary.

Replication lag is monitored externally.  When lag exceeds
`max_replication_lag_seconds` (default 10 s) the alerting layer should
disable replica routing until replication catches up.  This module does not
enforce the threshold at query time; enforcement is the responsibility of the
operations layer.

---

## 5-second query timeout

PRD Sec 28.11 mandates a 5-second hard limit on all Cypher queries to prevent
runaway graph traversals from monopolising connection pool slots.

The timeout is enforced at the Postgres session level via:

```sql
SET statement_timeout = 5000;
```

This is set by `get_set_timeout_sql()` and applied in the asyncpg `init`
callback in `create_pgbouncer_pool()`.  Any query that exceeds 5 seconds
receives a `QueryCanceledError` from Postgres, which the application layer
converts to an HTTP 503 with a `Retry-After` header.

PgBouncer also enforces `query_timeout = 5` (seconds) as a second line of
defence in case the session-level setting is lost after a connection reset.

---

## Index rationale per query pattern

All four indexes target JSONB property columns on the AGE vertex tables.

| Index | Column | Method | Query pattern |
|-------|--------|--------|---------------|
| `idx_graphclaw_state` | `properties->>'state'` | btree | Filter tasks by state (PENDING / IN_PROGRESS / COMPLETE) — used in virtually every task list query |
| `idx_graphclaw_owner_id` | `properties->>'owner_id'` | btree | Fetch all tasks for a specific user — primary access pattern for the task inbox |
| `idx_graphclaw_due_date` | `properties->>'due_date'` | btree | Range queries for overdue and upcoming tasks — used by the briefing and notification triggers |
| `idx_graphclaw_score` | `(properties->>'score')::numeric` | btree | Sort tasks by priority score — used by briefing generation to rank the day's top tasks |

Indexes are applied by migration `0004_age_indexes.sql`.  This README and
`infra/db/indexes.py` are the canonical source of truth; keep them in sync
when adding new indexes.
