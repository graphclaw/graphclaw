---
name: graphclaw-docker-dev
description: Docker Compose configuration for GraphClaw local dev stack (Postgres + AGE + pgvector). Use when creating or modifying Docker files or local dev infrastructure.
---

# GraphClaw Docker Development Environment

## Stack

- **db**: Postgres 16 + Apache AGE + pgvector
- **app**: Python 3.12+ application

## Dockerfile.db (Custom Postgres Image)

```dockerfile
FROM apache/age:PG16_latest

# Install pgvector
RUN apt-get update && \
    apt-get install -y postgresql-16-pgvector && \
    rm -rf /var/lib/apt/lists/*

COPY scripts/init-db.sql /docker-entrypoint-initdb.d/
```

If `postgresql-16-pgvector` package is not available, compile from source:

```dockerfile
RUN apt-get update && apt-get install -y build-essential git postgresql-server-dev-16 && \
    git clone --branch v0.7.0 https://github.com/pgvector/pgvector.git /tmp/pgvector && \
    cd /tmp/pgvector && make && make install && \
    rm -rf /tmp/pgvector /var/lib/apt/lists/*
```

## docker-compose.yml

```yaml
services:
  db:
    build:
      context: .
      dockerfile: docker/Dockerfile.db
    environment:
      POSTGRES_DB: graphclaw
      POSTGRES_USER: graphclaw
      POSTGRES_PASSWORD: ${DB_PASSWORD:-graphclaw_dev}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U graphclaw -d graphclaw"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build:
      context: .
      dockerfile: docker/Dockerfile
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://graphclaw:${DB_PASSWORD:-graphclaw_dev}@db:5432/graphclaw
      SECRETS_BACKEND: env_file
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
    volumes:
      - ./src:/app/src
      - ./tests:/app/tests

volumes:
  pgdata:
```

## init-db.sql

```sql
-- Load extensions
CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;

-- Load AGE
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- Create graph
SELECT create_graph('graphclaw');

-- Create node labels (Phase 0 subset)
SELECT create_vlabel('graphclaw', 'TaskAtomic');
SELECT create_vlabel('graphclaw', 'TaskComposite');
SELECT create_vlabel('graphclaw', 'TaskDelegated');
SELECT create_vlabel('graphclaw', 'TaskFollowUp');
SELECT create_vlabel('graphclaw', 'GoalNode');
SELECT create_vlabel('graphclaw', 'ConstraintNode');
SELECT create_vlabel('graphclaw', 'UserNode');
SELECT create_vlabel('graphclaw', 'ResourceNode');

-- Create edge labels
SELECT create_elabel('graphclaw', 'DEPENDS_ON');
SELECT create_elabel('graphclaw', 'SPAWNED_FROM');
SELECT create_elabel('graphclaw', 'FOLLOW_UP_FOR');
SELECT create_elabel('graphclaw', 'BLOCKS');
SELECT create_elabel('graphclaw', 'ASSIGNED_TO');
SELECT create_elabel('graphclaw', 'OWNED_BY');
SELECT create_elabel('graphclaw', 'APPLIES_TO');
SELECT create_elabel('graphclaw', 'PART_OF');

-- Embedding storage table
CREATE TABLE IF NOT EXISTS node_embeddings (
    node_id TEXT PRIMARY KEY,
    embedding vector(1536),
    computed_at TIMESTAMPTZ DEFAULT NOW()
);
```

## .env.example

```
DB_PASSWORD=graphclaw_dev
ANTHROPIC_API_KEY=sk-ant-your-key-here
SECRETS_BACKEND=env_file
```

## Convention

- `docker compose up` must start the full stack with zero manual steps
- `SECRETS_BACKEND=env_file` for local dev (from CLAUDE.md)
- Health check on Postgres before app starts
- Mount src/ and tests/ for live reload during development
