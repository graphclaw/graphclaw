# 06 — Local Deployment (Docker Compose)

---

## Container Stack

```mermaid
graph TB
    subgraph HOST["Host Machine (localhost)"]
        direction TB

        subgraph PORTS["Exposed Ports"]
            P5432["5432 → Postgres"]
            P6432["6432 → PgBouncer"]
            P6379["6379 → Redis"]
            P9000["9000 → MinIO API"]
            P9001["9001 → MinIO Console"]
            P8000["8000 → Gateway"]
            P5050["5050 → pgAdmin"]
        end

        subgraph COMPOSE["docker_default network"]
            direction TB

            DB["🐘 db\ndocker/Dockerfile.db\nPostgres 18 + AGE + pgvector\nhostname: graph-db"]
            PGB["🔀 pgbouncer\nbitnami/pgbouncer\nConnection pooler\nhostname: pgbouncer"]
            REDIS["⚡ redis\nredis:7-alpine\nCache + Broker"]
            MINIO["🪣 minio\nminio/minio\nObject storage"]
            MINIT["🔧 minio-init\nminio/mc  (one-shot)\nCreates 'graphclaw' bucket"]
            GW["🚀 gateway\ndocker/Dockerfile\nFastAPI + all routes\nhostname: gateway"]
            PGADMIN["🖥️ pgadmin-viewer\ndpage/pgadmin4\nDB browser UI"]
        end
    end

    P5432 --- DB
    P6432 --- PGB
    P6379 --- REDIS
    P9000 --- MINIO
    P9001 --- MINIO
    P8000 --- GW
    P5050 --- PGADMIN

    DB --> PGB
    PGB --> GW
    REDIS --> GW
    MINIO --> GW
    MINIT -->|"creates bucket\n(one-shot)"| MINIO
    DB -.->|pgAdmin| PGADMIN
```

---

## Service Dependency Order

```mermaid
graph LR
    DB["db\n(Postgres)"] -->|healthcheck| PGB["pgbouncer"]
    DB -->|healthcheck| REDIS["redis"]
    PGB -->|service_healthy| GW["gateway"]
    REDIS -->|service_healthy| GW
    MINIO["minio"] -->|service_healthy| MINIT["minio-init\n(bucket creation)"]
    MINIT -->|service_completed_successfully| GW
```

---

## Environment Variables

| Variable | Default | Used By | Description |
|----------|---------|---------|-------------|
| `DB_PASSWORD` | `graphclaw_dev` | db, pgbouncer, gateway | Postgres password |
| `MINIO_PASSWORD` | `graphclaw_dev` | minio, gateway | MinIO secret key |
| `ENVIRONMENT` | `development` | gateway | Enables /auth/dev-token |
| `QUERY_TIMEOUT_MS` | `5000` | gateway | AGE query timeout |
| `PYTHONPATH` | `/app` | gateway | Allows `infra/` imports |
| `AWS_ACCESS_KEY_ID` | `graphclaw` | gateway | MinIO access key |
| `AWS_SECRET_ACCESS_KEY` | `graphclaw_dev` | gateway | MinIO secret key |
| `JWT_PRIVATE_KEY` | *(generated)* | gateway | RS256 signing key |
| `JWT_PUBLIC_KEY` | *(generated)* | gateway | RS256 verify key |
| `OAUTH_GOOGLE_CLIENT_ID` | *(unset)* | gateway | Google OAuth |
| `OAUTH_GITHUB_CLIENT_ID` | *(unset)* | gateway | GitHub OAuth |

---

## Volume Mounts (Gateway)

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `../src` | `/app/src` | Live code (uvicorn --reload) |
| `../infra` | `/app/infra` | Cross-package imports |

The gateway runs with `pip install -e ".[dev]"` (editable install) so that
changes to `src/graphclaw/**` are reflected immediately without rebuilding
the image — uvicorn `--reload` watches `/app/src` for changes.

---

## Quick Start

```bash
# 1. Clone and enter repo
cd graphclaw

# 2. Start full stack
docker compose -f docker/docker-compose.yml up -d

# 3. Check health
curl http://localhost:8000/health

# 4. Run end-to-end tests
python scripts/test_api.py          # 92/92 passing

# 5. Open Swagger UI
open http://localhost:8000/docs

# 6. Open pgAdmin (add server: host=db, port=5432, user=graphclaw, pw=graphclaw_dev)
open http://localhost:5050

# 7. Open MinIO console (user=graphclaw, pw=graphclaw_dev)
open http://localhost:9001
```

---

## Image Build Flow

```mermaid
flowchart TD
    BASE["FROM python:3.12-slim"]
    SYSDEPS["apt-get install\ngcc + libpq-dev"]
    COPY_META["COPY pyproject.toml README.md"]
    PIP_DEPS["pip install --no-cache-dir\n(all 16 dependencies pinned)"]
    COPY_SRC["COPY src/ ./src/"]
    EDITABLE["pip install -e .[dev]\n(editable — live reload via volume)"]
    ENTRY["ENTRYPOINT [graphclaw]"]

    BASE --> SYSDEPS --> COPY_META --> PIP_DEPS --> COPY_SRC --> EDITABLE --> ENTRY
```

> **Why editable install?**  
> `pip install -e` records a `.pth` pointer to `/app/src` in `site-packages`.
> When Docker mounts `../src:/app/src`, Python finds the live source there.
> Without `-e`, Python imports from a copied snapshot in `site-packages` and
> ignores the volume mount entirely.
