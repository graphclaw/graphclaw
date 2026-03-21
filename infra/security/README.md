# GraphClaw — Security Infrastructure

This directory contains infrastructure-as-code descriptors and configuration
builders for the GraphClaw security stack. All configs are expressed as
immutable Python dataclasses so they can be validated in CI before deployment.

---

## Rate Limiting (Application Layer)

Rate limiting is enforced by `RateLimitMiddleware` in
`src/graphclaw/gateway/rate_limiter.py` using a Redis-backed sliding window
algorithm (sorted sets).

| Category | Key | Limit |
|---|---|---|
| Unauthenticated | Source IP | 30 req/min |
| Authenticated user | JWT `sub` claim | 300 req/min |
| A2A agent | `X-Agent-Api-Key` header (first 16 chars) | 60 req/min |
| Webhook source | Source IP on `/webhooks/*` | 120 req/min |

---

## WAF (AWS WAFv2 Layer)

Defined in `waf.py`. The `build_waf_web_acl()` function returns a dict
suitable for `boto3`'s `wafv2.create_web_acl`.

| Rule | Priority | Action | Purpose |
|---|---|---|---|
| AWSManagedRulesCommonRuleSet | 1 | BLOCK | OWASP Top 10 — SQL injection, XSS, path traversal |
| AWSManagedRulesKnownBadInputsRuleSet | 2 | BLOCK | Log4Shell, Spring4Shell, and other known bad inputs |
| RateLimitRule | 10 | BLOCK | Block IPs exceeding 2,000 req per 5 min at WAF layer |
| GeoBlockRule | 20 | COUNT | Geo-based monitoring; switch to BLOCK for data residency |

**DDoS Protection**: The `RateLimitRule` at WAF layer (2,000 req/5 min) sits
above the application-level limits and protects against volumetric attacks
before requests reach the application servers. AWS Shield Standard is enabled
by default on all ALBs.

---

## Encryption at Rest

Defined in `encryption.py`. `PRODUCTION_ENCRYPTION` is the default config.

| Storage | Mechanism | Notes |
|---|---|---|
| S3 | SSE-KMS (`aws:kms`) | S3 Bucket Key enabled to reduce KMS API call costs |
| RDS (Postgres) | KMS — `alias/graphclaw-rds` | Encrypts data, automated backups, and snapshots |
| ElastiCache (Redis) | At-rest encryption enabled | AES-256 managed by ElastiCache |

---

## TLS in Transit

| Connection | Enforcement |
|---|---|
| Client → ALB | HTTPS only; HTTP redirected to HTTPS |
| ALB → ECS tasks | TLS (ACM certificate on ALB, internal cert on task) |
| ECS → RDS | SSL required (`sslmode=require` in connection string) |
| ECS → ElastiCache | TLS enabled (`elasticache_in_transit_encryption=True`) |
| ECS → S3 / Secrets Manager | AWS SDK uses HTTPS by default |

---

## Secrets Management

ECS tasks receive secrets via AWS Secrets Manager (`ecs_secrets_from = "aws_secrets_manager"`).
Secrets are never embedded as plaintext environment variables in task definitions.
The `SecretsClient` abstraction (`src/graphclaw/infra/secrets/`) supports local
`env_file` backend for development and AWS Secrets Manager in production.
