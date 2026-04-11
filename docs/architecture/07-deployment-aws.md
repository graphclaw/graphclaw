# 07 — AWS Deployment Architecture

GraphClaw runs fully on AWS using managed services where available, and
ECS Fargate for everything that requires a custom runtime (notably the graph
database, which uses Apache AGE — not available on RDS).

---

## AWS Architecture Overview

```mermaid
graph TB
    subgraph INTERNET["Internet"]
        USER["👤 Users\n(Browser / Mobile)"]
        CHANNELS["📨 External Channels\n(Email·Slack·Teams)"]
    end

    subgraph AWS["AWS Account"]
        subgraph VPC["VPC  (10.0.0.0/16)"]
            subgraph PUBLIC["Public Subnets (2 AZs)"]
                ALB["Application Load Balancer\n(HTTPS :443)\nACM TLS Certificate"]
                NAT["NAT Gateway\n(outbound internet)"]
            end

            subgraph PRIVATE["Private Subnets (2 AZs)"]
                subgraph ECS["ECS Cluster (Fargate)"]
                    GW_SVC["gateway service\n2 tasks × 1vCPU 2GB\nFastAPI + all routes"]
                    APP_SVC["app service\n1 task × 0.5vCPU 1GB\nCLI / background jobs"]
                    DB_SVC["db service\n1 task × 2vCPU 4GB\nPostgres 18 + AGE + pgvector"]
                    PGB_SVC["pgbouncer service\n1 task × 0.25vCPU 0.5GB\nConnection pool"]
                end

                subgraph MANAGED["Managed Services"]
                    ELASTICACHE["ElastiCache Redis\n(cache.t4g.small)\nMulti-AZ"]
                    S3_["S3 Bucket\n(graphclaw-prod)\nObjects + agent files"]
                    SM["Secrets Manager\nAll credentials\n+ LLM API keys"]
                    SES["SES\nEmail send/receive"]
                end

                subgraph STORAGE["Persistent Storage"]
                    EFS["EFS Volume\nPostgres data\n(/var/lib/postgresql)"]
                end
            end
        end

        subgraph SHARED["Shared Services"]
            ECR["ECR\nContainer Registry\n(gateway + db images)"]
            CW["CloudWatch\nLogs + Metrics\n+ Alarms"]
            R53["Route 53\nDNS (api.graphclaw.ai)"]
            ACM["ACM\nTLS Certificate"]
        end
    end

    USER -->|HTTPS| R53 --> ALB
    CHANNELS -->|webhooks| ALB
    ALB -->|/app/v1/* /auth/* /api/v1/*| GW_SVC
    GW_SVC --> PGB_SVC --> DB_SVC
    GW_SVC --> ELASTICACHE
    GW_SVC --> S3_
    GW_SVC --> SM
    GW_SVC --> SES
    DB_SVC --- EFS
    GW_SVC -->|outbound API calls| NAT -->|internet| CHANNELS
    ECR -.->|pull images| ECS
    ECS -.->|logs| CW
```

---

## ECS Task Definitions

```mermaid
graph LR
    subgraph GW_TASK["gateway Task Definition"]
        GW_C["Container: graphclaw-gateway\nImage: ECR/graphclaw-gateway:latest\nPort: 8000\nCPU: 1024  Memory: 2048"]
        GW_ENV["Environment\nENVIRONMENT=production\nPGHOST=pgbouncer.internal\nREDIS_URL=redis://elasticache\nS3_BUCKET=graphclaw-prod\nSECRETS_BACKEND=aws"]
        GW_IAM["Task Role: graphclaw-gateway-role\nS3:GetObject PutObject DeleteObject\nsecretsmanager:GetSecretValue\nses:SendEmail"]
    end

    subgraph DB_TASK["db Task Definition"]
        DB_C["Container: graphclaw-db\nImage: ECR/graphclaw-db:latest\nPort: 5432\nCPU: 2048  Memory: 4096"]
        DB_VOL["EFS Volume Mount\n/var/lib/postgresql"]
        DB_IAM["Task Role: graphclaw-db-role\nelasticfilesystem:ClientMount\nelasticfilesystem:ClientWrite"]
    end

    subgraph PGB_TASK["pgbouncer Task Definition"]
        PGB_C["Container: pgbouncer\nImage: bitnami/pgbouncer\nPort: 5432\nCPU: 256  Memory: 512"]
        PGB_ENV["Environment\nPOSTGRESSQL_HOST=db.internal\nMAX_CLIENT_CONN=100\nDEFAULT_POOL_SIZE=20"]
    end
```

---

## IAM Role Map

| Role | Service | Key Permissions |
|------|---------|----------------|
| `graphclaw-gateway-role` | ECS gateway tasks | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `secretsmanager:GetSecretValue`, `ses:SendEmail`, `ses:SendRawEmail` |
| `graphclaw-db-role` | ECS db tasks | `elasticfilesystem:ClientMount`, `elasticfilesystem:ClientWrite`, `elasticfilesystem:ClientRootAccess` |
| `graphclaw-app-role` | ECS app tasks (CLI) | `s3:GetObject`, `s3:PutObject`, `secretsmanager:GetSecretValue` |
| `graphclaw-deploy-role` | CI/CD pipeline | `ecr:PutImage`, `ecs:RegisterTaskDefinition`, `ecs:UpdateService` |

---

## Network Security Groups

```mermaid
graph LR
    subgraph SG["Security Groups"]
        ALB_SG["alb-sg\nInbound: 443 from 0.0.0.0/0\nOutbound: 8000 to ecs-sg"]
        ECS_SG["ecs-sg\nInbound: 8000 from alb-sg\nInbound: 5432 from ecs-sg\nOutbound: 443 to 0.0.0.0/0"]
        REDIS_SG["redis-sg\nInbound: 6379 from ecs-sg only"]
        EFS_SG["efs-sg\nInbound: 2049 from ecs-sg only"]
    end
```

---

## Data Flow: AWS Production

```mermaid
sequenceDiagram
    participant U as User Browser
    participant R53 as Route 53
    participant ALB as ALB (HTTPS:443)
    participant GW as ECS Gateway Tasks
    participant PGB as ECS PgBouncer
    participant DB as ECS Postgres+AGE
    participant EFS as EFS (pg data)
    participant REDIS as ElastiCache Redis
    participant S3 as S3 Bucket
    participant SM as Secrets Manager

    U->>R53: api.graphclaw.ai
    R53->>ALB: DNS → ALB endpoint
    U->>ALB: HTTPS request (TLS terminated at ALB)
    ALB->>GW: HTTP forward (X-Forwarded-For header)
    GW->>SM: GetSecretValue (JWT keys, LLM API keys) [cached]
    GW->>REDIS: token revocation check + rate limit
    GW->>PGB: psycopg connection from pool
    PGB->>DB: SQL / Cypher query
    DB->>EFS: read/write pg data files
    DB-->>PGB: result rows
    PGB-->>GW: query results
    GW->>S3: GetObject / PutObject (agent files, skill YAMLs)
    GW-->>ALB: JSON response
    ALB-->>U: HTTPS response
```

---

## Secrets Manager Layout

All secrets stored under prefix `graphclaw/` in AWS Secrets Manager:

```
graphclaw/
├── jwt/private-key          RS256 private key (PEM)
├── jwt/public-key           RS256 public key (PEM)
├── db/password              Postgres password
├── redis/auth-token         Redis AUTH token
├── oauth/google/client-secret
├── oauth/github/client-secret
├── oauth/microsoft/client-secret
├── llm/anthropic/api-key
├── llm/openai/api-key
├── minio/secret-key         (local dev only)
└── byok/{user_id}/{key}     User BYOK secrets
```

---

## Scaling Strategy

```mermaid
graph LR
    subgraph AUTO["Auto Scaling"]
        GW_AS["Gateway Service\nMin: 2 tasks\nMax: 10 tasks\nScale on: CPU > 70%\nor RequestCount > 1000/min"]
        DB_AS["DB Service\nMin: 1 task\nMax: 1 task\n(single primary — EFS lock)\nScale vertically via task def"]
        PGB_AS["PgBouncer\nMin: 1 task\nMax: 3 tasks\nScale with gateway"]
    end

    subgraph REDIS_SCALE["Redis Scaling"]
        REDIS_SCALE_D["ElastiCache\nStart: cache.t4g.small\nScale: cache.r7g.large\nAdd read replicas for cache reads"]
    end
```

---

## Deployment Pipeline (CI/CD)

```mermaid
flowchart LR
    GIT["git push\nmain branch"] --> CI["GitHub Actions\nCI pipeline"]
    CI --> TEST["pytest\n1451 tests"]
    TEST -->|pass| BUILD["docker build\ngateway + db images"]
    BUILD --> PUSH["docker push\nto ECR"]
    PUSH --> DEPLOY["ecs update-service\n--force-new-deployment"]
    DEPLOY --> HEALTH["ALB health check\n/health → 200"]
    HEALTH -->|healthy| DONE["Deploy complete\nBlue/Green rollout"]
    TEST -->|fail| STOP["Pipeline stopped\nNo deploy"]
```

---

## Cost Estimate (Small Production)

| Service | Config | Est. Monthly |
|---------|--------|-------------|
| ECS Fargate (gateway ×2) | 1 vCPU, 2GB, 730h | ~$60 |
| ECS Fargate (db ×1) | 2 vCPU, 4GB, 730h | ~$60 |
| ECS Fargate (pgbouncer ×1) | 0.25 vCPU, 0.5GB | ~$8 |
| EFS | 20 GB | ~$6 |
| ElastiCache Redis | cache.t4g.small | ~$25 |
| S3 | 50 GB + requests | ~$5 |
| ALB | 1 ALB + LCU | ~$20 |
| Secrets Manager | 10 secrets | ~$4 |
| Route 53 | 1 hosted zone | ~$1 |
| CloudWatch | Logs + metrics | ~$10 |
| **Total** | | **~$200/mo** |
