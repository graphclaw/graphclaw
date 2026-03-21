# GraphClaw Container Auto-Scaling

Auto-scaling configuration for all seven GraphClaw ECS Fargate containers.
Implements Phase 5 scaling hardening from PRD Section 28.11.

---

## Why `agent-runtime` MUST be Scale-to-Zero

### 1,000-User Cost Analysis

At 1,000 active users, `agent-runtime` is the most expensive container tier if
left at a fixed minimum replica count.

| Scenario | Replicas | Fargate vCPU cost/hr | Monthly cost |
|----------|----------|----------------------|--------------|
| Fixed min=1 per user | 1,000 | $0.04048 × 1,000 | ~$29,000 |
| Fixed min=1 platform-wide | 1 | $0.04048 × 1 | ~$30 |
| Scale-to-zero (queue-driven) | 0–50 | actual load | ~$300–$1,500 |

Most agent-runtime containers are idle 95% of the time (tasks are infrequent,
not continuous). Keeping a dedicated replica per user at idle wastes CPU and
memory that could serve active work.

**Scale-to-zero policy:** `min_tasks=0` with KEDA Redis Stream depth trigger
means containers launch only when a message appears in `AGENT_TASKS` and
terminate within 2 minutes of the queue draining (`scale_in_cooldown=120s`).

Cold start latency for Fargate Spot is ~15–30 seconds. This is acceptable for
asynchronous task processing. Real-time user interactions route through
`api-server` (always-on), not `agent-runtime`.

---

## Morning Briefing Spike and `startup_jitter_seconds`

GraphClaw sends daily briefings to all users at their configured local morning
time. Without mitigation, hundreds of `trigger-engine` workers fire
simultaneously at 08:00 in each timezone, creating a thundering-herd spike on
the Redis queue and the agent-runtime fleet.

**Mitigation:** `startup_jitter_seconds=30` on the `trigger-engine` profile
adds a random delay of 0–30 seconds per worker before it pulls its first
trigger. This spreads the briefing spike across a 30-second window per cohort,
reducing peak queue depth by ~10×.

The `trigger-engine` KEDA ScaledObject includes a
`graphclaw.ai/startup-jitter-seconds` annotation that the deployment operator
reads to configure the delay at the Kubernetes level.

---

## KEDA vs ECS Application Auto Scaling

| Attribute | KEDA (Redis Stream) | ECS App Auto Scaling (CPU/Memory) |
|-----------|--------------------|------------------------------------|
| Trigger | Queue depth (lag) | CloudWatch metric threshold |
| Scale-to-zero | Native (`minReplicaCount=0`) | Requires custom Lambda hack |
| Latency | ~5 seconds (KEDA poll interval) | 1–3 minutes (CW evaluation period) |
| Containers | `agent-runtime`, `trigger-engine` | `channel-gateway`, `api-server` |
| Infra required | KEDA operator on Kubernetes | ECS service + CloudWatch alarms |

**Decision:** Queue-based containers (`agent-runtime`, `trigger-engine`) use
KEDA because they need scale-to-zero and sub-minute scale-out. CPU/memory-based
containers (`channel-gateway`, `api-server`, `cache`) use ECS Application Auto
Scaling because they are request-driven and the CloudWatch integration is
simpler.

`graph-db` and `relational-db` are fixed at `max_tasks=1` (single primary)
and are not auto-scaled — read replicas are deployed separately.

---

## How to Apply

### ECS Task Definitions

```python
from infra.scaling.ecs_task_definitions import build_task_definition, CONTAINER_RESOURCES
import boto3, json

ecs = boto3.client("ecs", region_name="us-east-1")

resources = CONTAINER_RESOURCES["agent-runtime"]
td = build_task_definition(
    container_name="agent-runtime",
    image_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/graphclaw/agent-runtime:v1.0.0",
    cpu=resources["cpu"],
    memory_mb=resources["memory_mb"],
    env_vars={"ENVIRONMENT": "production", "REDIS_URL": "redis://..."},
    role_arn="arn:aws:iam::123456789012:role/graphclaw-agent-runtime-task-role",
    log_group="/graphclaw/agent-runtime",
)
# Replace placeholder before registering
td["executionRoleArn"] = td["executionRoleArn"].replace("ACCOUNT_ID", "123456789012")
ecs.register_task_definition(**td)
```

### KEDA ScaledObjects

```python
from infra.scaling.profiles import CONTAINER_SCALING_PROFILES
from infra.scaling.keda_scalers import build_keda_scaled_object
import subprocess, tempfile, os

for name, profile in CONTAINER_SCALING_PROFILES.items():
    if profile.queue_name is None:
        continue  # skip CPU/memory-scaled containers
    yaml_str = build_keda_scaled_object(profile, namespace="graphclaw")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(yaml_str)
        tmp = f.name
    subprocess.run(["kubectl", "apply", "-f", tmp], check=True)
    os.unlink(tmp)
```

### Iterating All Profiles

```python
from infra.scaling import CONTAINER_SCALING_PROFILES, get_scaling_config

# Get a single profile
profile = get_scaling_config("agent-runtime")
print(profile.min_tasks, profile.scale_to_zero)

# Iterate all
for name, profile in CONTAINER_SCALING_PROFILES.items():
    print(f"{name}: min={profile.min_tasks} max={profile.max_tasks}")
```
