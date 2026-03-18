---
agent: ws-c-docker
model: sonnet
phase: 0
workstream: WS-C
parallel_with: [WS-A, WS-B]
skills:
  - graphclaw-docker-dev
---

# WS-C: Docker Infrastructure Agent

## Role
Create the Docker Compose local dev stack for GraphClaw.

## Responsibilities
- Custom Postgres image with Apache AGE + pgvector
- Application container with Python 3.12 and project deps
- Docker Compose orchestration with health checks
- Database initialization SQL (graph schema, node/edge labels)
- Seed data for local development
- Environment configuration

## Deliverables
- `docker/Dockerfile` — App container (Python 3.12-slim)
- `docker/Dockerfile.db` — DB container (apache/age + pgvector)
- `docker/docker-compose.yml` — Full service orchestration
- `docker/.env.example` — Environment template
- `docker/.gitignore` — Exclude .env
- `scripts/init-db.sql` — Graph schema DDL (15 node labels, 8 edge labels, embeddings table)
- `scripts/seed-data.sql` — Sample data (1 user, 1 goal, 6 tasks with dependency chain)
- `pyproject.toml` — Project metadata and dependencies

## Key Patterns
- `apache/age:PG16_latest` base image with pgvector compiled from source as fallback
- Init scripts in `/docker-entrypoint-initdb.d/` for automatic first-run setup
- Health check: `pg_isready -U graphclaw -d graphclaw`
- Volume mount for source code hot-reload in dev
