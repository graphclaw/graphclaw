# GraphClaw IAM Roles

IAM task role definitions for GraphClaw ECS Fargate containers. Implements the
**one IAM role per container, least-privilege** principle from the GraphClaw
security architecture.

---

## Container Roles

### 1. `agent-runtime` → `graphclaw-agent-runtime-task-role`

The orchestrating AI agent that processes inbound messages and executes tasks.

| Service | Actions | Scope |
|---------|---------|-------|
| S3 (`graphclaw-tasks`) | GetObject, PutObject, DeleteObject, ListBucket | `users/${aws:PrincipalTag/UserId}/*` only |
| SQS (`graphclaw-inbound-messages`) | ReceiveMessage, DeleteMessage, GetQueueAttributes | Single queue |
| Secrets Manager | GetSecretValue | `graphclaw/llm/*` and `graphclaw/byok/user-*` |

### 2. `channel-gateway` → `graphclaw-channel-gateway-task-role`

Inbound gateway for email, Telegram, and WhatsApp channels.

| Service | Actions | Scope |
|---------|---------|-------|
| SQS (`graphclaw-inbound-messages`) | SendMessage, GetQueueAttributes, GetQueueUrl | Single queue |
| S3 (`graphclaw-attachments`) | PutObject, GetObject | `inbound/*` prefix only |
| Secrets Manager | GetSecretValue | `graphclaw/channels/email/*`, `graphclaw/channels/telegram/*`, `graphclaw/channels/whatsapp/*` |

### 3. `trigger-engine` → `graphclaw-trigger-engine-task-role`

Evaluates scheduled and event-driven triggers. Acts at system level, so S3
access is not user-scoped (it reads task files for any user to evaluate
trigger conditions).

| Service | Actions | Scope |
|---------|---------|-------|
| SQS (`graphclaw-trigger-events`) | SendMessage, GetQueueAttributes, GetQueueUrl | Single queue |
| S3 (`graphclaw-tasks`) | GetObject, ListBucket | `users/*` prefix (read-only) |
| Secrets Manager | GetSecretValue | `graphclaw/trigger/*` |

### 4. `api-server` → `graphclaw-api-server-task-role`

FastAPI REST API serving end-user requests, including file uploads and
on-demand trigger dispatch.

| Service | Actions | Scope |
|---------|---------|-------|
| S3 (`graphclaw-tasks`, `graphclaw-attachments`) | Full multipart upload lifecycle | `users/${aws:PrincipalTag/UserId}/*` only |
| SQS (`graphclaw-trigger-events`) | SendMessage, GetQueueAttributes, GetQueueUrl | Single queue |
| Secrets Manager | GetSecretValue | `graphclaw/auth/jwt-private-key*` |

---

## Containers Without IAM Roles

Three containers do **not** have AWS IAM task roles:

| Container | Reason |
|-----------|--------|
| `graph-db` (Postgres + Apache AGE) | No direct AWS API calls. DB credentials are read from Secrets Manager by the **application containers** (agent-runtime, api-server) via `SecretsClient`, then passed as connection strings. |
| `relational-db` (Postgres) | Same as graph-db. Credentials are injected at startup by the app layer. |
| `cache` (Redis) | No AWS API calls. Auth token (if any) is injected by the app layer via environment variable or `SecretsClient`. |

This approach avoids granting AWS credentials to infrastructure containers that
only speak network protocols (TCP/TLS), eliminating an entire category of
credential exfiltration risk.

---

## Applying the Roles (AWS CLI)

### 1. Create the IAM role with the ECS trust policy

```bash
aws iam create-role \
  --role-name graphclaw-agent-runtime-task-role \
  --assume-role-policy-document file://infra/iam/roles.py  # use trust policy JSON
```

In practice, export the trust policy from `roles.py` or copy the
`ECS_TASK_TRUST_POLICY` dict to a `trust-policy.json` file first:

```bash
python - <<'EOF'
import json
from infra.iam import ECS_TASK_TRUST_POLICY
print(json.dumps(ECS_TASK_TRUST_POLICY, indent=2))
EOF > /tmp/ecs-trust-policy.json

aws iam create-role \
  --role-name graphclaw-agent-runtime-task-role \
  --assume-role-policy-document file:///tmp/ecs-trust-policy.json
```

### 2. Attach the inline policy

```bash
aws iam put-role-policy \
  --role-name graphclaw-agent-runtime-task-role \
  --policy-name graphclaw-agent-runtime-inline-policy \
  --policy-document file://infra/iam/policies/agent-runtime-policy.json
```

Repeat for each container, substituting the role name and policy file from
`ROLE_NAMES` and `POLICY_FILES` in `roles.py`.

---

## Note on `aws:PrincipalTag/UserId`

The S3 user-scoped conditions in `agent-runtime-policy.json` and
`api-server-policy.json` use the condition key `aws:PrincipalTag/UserId`.
This tag must be set on the **ECS task** at launch time — it is not
automatically derived from the IAM role.

To set the tag, pass `--tags` when calling `ecs:StartTask` or configure the
task definition's `tags` field in the ECS service:

```json
"tags": [
  {"key": "UserId", "value": "<graphclaw-user-id>"}
]
```

Without this tag the condition evaluates to a null/empty string and S3 denies
all requests that depend on it. The `trigger-engine` container deliberately
omits this condition because it operates at system level across all users.
