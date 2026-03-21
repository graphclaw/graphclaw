# GraphClaw CloudWatch Observability Stack

Full CloudWatch observability implementation for GraphClaw, covering log groups,
metric filters, three-tier alarms, and dashboards per PRD Sections 32.2–32.11.

---

## Three-Tier Alerting Model

GraphClaw uses a three-tier severity model for CloudWatch alarms:

| Tier | Action | Examples |
|------|--------|---------|
| **P1** | Page on-call immediately via PagerDuty/SNS | High agent error rate, auth failure spike |
| **P2** | Slack alert, investigate within 1 hour | LLM cost anomaly, task completion drop |
| **P3** | Dashboard trend only, no immediate action | P99 latency trending high |

All alarm configurations live in `alarms.py`. The `tier` field on each
`AlarmConfig` drives SNS topic routing in the CDK/Terraform deployment:

```python
from infra.observability.alarms import ALARM_CONFIGS, AlarmTier

p1_alarms = [a for a in ALARM_CONFIGS if a.tier == AlarmTier.P1]
p2_alarms = [a for a in ALARM_CONFIGS if a.tier == AlarmTier.P2]
```

---

## Log Scrubbing Requirements (PRD Sec 32.3)

The following patterns **must** be redacted before log events reach CloudWatch
durable storage. Redaction applies at the log-shipping layer (e.g. Fluent Bit
`grep` + `rewrite_tag` filters or a Lambda log processor):

| Pattern | Matches |
|---------|---------|
| `sk-ant-` | Anthropic API keys |
| `wg_agent_` | GraphClaw A2A agent API keys |
| `Bearer ` | Authorization header values |
| `password=` | URL/query string passwords |
| `secret=` | Generic secret query params |

```python
from infra.observability.log_groups import SCRUB_PATTERNS

for pattern in SCRUB_PATTERNS:
    print(f"Redact: {pattern}")
```

All matched substrings must be replaced with `[REDACTED]` before the event is
shipped. Do **not** drop the entire log line — preserve the surrounding context
for debugging.

---

## Per-User vs Shared Log Groups (PRD Sec 32.2)

| Container | Log Group Pattern | Rationale |
|-----------|-------------------|-----------|
| `agent-runtime` | `/graphclaw/agent-runtime/{user_id}` | Per-user isolation; enables per-user log retention, IAM resource policies, and cost attribution |
| `channel-gateway` | `/graphclaw/platform/channel-gateway` | Shared; handles messages from all users without user-specific state |
| `trigger-engine` | `/graphclaw/platform/trigger-engine` | Shared |
| `api-server` | `/graphclaw/platform/api-server` | Shared |
| `graph-db` | `/graphclaw/platform/graph-db` | Shared; 7-day retention (DB logs are verbose) |

Per-user log groups for `agent-runtime` are created during user onboarding via
the provisioning flow. Use `get_log_group_name("agent-runtime", user_id=uid)`
to resolve the group name at runtime.

**Retention:** Agent-runtime logs are retained 90 days (task history). Platform
logs are retained 30 days. Graph-DB logs are retained 7 days.

---

## Deploying the Observability Stack

### 1. Get the stack descriptor

```python
from infra.observability import build_observability_stack

stack = build_observability_stack()
# Keys: log_groups, metric_filters, alarms, dashboards
```

### 2. Create log groups (shared platform containers)

```python
import boto3
logs = boto3.client("logs", region_name="us-east-1")

for cfg in stack["log_groups"]:
    if cfg.is_per_user:
        continue  # Created on user onboarding
    logs.create_log_group(logGroupName=cfg.name)
    logs.put_retention_policy(
        logGroupName=cfg.name,
        retentionInDays=cfg.retention_days,
    )
```

### 3. Create metric filters

```python
for mf in stack["metric_filters"]:
    logs.put_metric_filter(
        logGroupName=mf.log_group_name,
        filterName=mf.filter_name,
        filterPattern=mf.filter_pattern,
        metricTransformations=[{
            "metricName": mf.metric_name,
            "metricNamespace": mf.metric_namespace,
            "metricValue": mf.metric_value,
            "unit": mf.unit,
        }],
    )
```

### 4. Create alarms

```python
import json
cw = boto3.client("cloudwatch", region_name="us-east-1")

# Map tiers to SNS topic ARNs
sns_arns = {
    "P1": "arn:aws:sns:us-east-1:ACCOUNT_ID:graphclaw-p1-alerts",
    "P2": "arn:aws:sns:us-east-1:ACCOUNT_ID:graphclaw-p2-alerts",
    "P3": "arn:aws:sns:us-east-1:ACCOUNT_ID:graphclaw-p3-alerts",
}

for alarm in stack["alarms"]:
    cw.put_metric_alarm(
        AlarmName=alarm.alarm_name,
        AlarmDescription=alarm.description,
        MetricName=alarm.metric_name,
        Namespace=alarm.metric_namespace,
        ComparisonOperator=alarm.comparison_operator,
        Threshold=alarm.threshold,
        EvaluationPeriods=alarm.evaluation_periods,
        Period=alarm.period_seconds,
        Statistic="Sum",
        AlarmActions=[sns_arns[alarm.tier.value]],
    )
```

### 5. Create dashboards

```python
import json
from infra.observability.dashboards import build_dashboard_body

for name in stack["dashboards"]:
    body = build_dashboard_body(name)
    cw.put_dashboard(
        DashboardName=f"graphclaw-{name}",
        DashboardBody=json.dumps(body),
    )
```
