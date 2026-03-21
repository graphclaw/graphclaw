# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""graphclaw.infra.observability.alarms — CloudWatch alarm definitions.

Description
-----------
Defines the three-tier CloudWatch alarm model per PRD Section 32.7:

- P1: Page on-call immediately — task graph state is at risk.
- P2: Alert, investigate within 1 hour — system is degraded.
- P3: Dashboard trend, no immediate action — monitor for patterns.

Design Patterns
---------------
- Enum for tier: AlarmTier provides type-safe tier values.
- Frozen dataclasses: AlarmConfig instances are immutable.
- Data-driven: ALARM_CONFIGS drives IaC tooling without AWS SDK imports.

Public API
----------
- AlarmTier: Enum of P1, P2, P3 severity tiers.
- AlarmConfig: Frozen dataclass for a single alarm definition.
- ALARM_CONFIGS: List of all alarm configurations.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlarmTier(str, Enum):
    """Severity tier for a CloudWatch alarm.

    P1
        Page on-call immediately — task graph state is at risk.
    P2
        Alert, investigate within 1 hour — system is degraded.
    P3
        Dashboard trend, no immediate action required.
    """

    P1 = "P1"  # page on-call immediately
    P2 = "P2"  # alert, investigate within 1h
    P3 = "P3"  # dashboard trend, no action required


@dataclass(frozen=True)
class AlarmConfig:
    """CloudWatch alarm configuration.

    Parameters
    ----------
    alarm_name:
        Unique CloudWatch alarm name.
    tier:
        Severity tier (P1, P2, P3) determining SNS notification routing.
    metric_name:
        CloudWatch metric name to evaluate.
    metric_namespace:
        CloudWatch metric namespace (e.g. ``"GraphClaw"``).
    comparison_operator:
        CloudWatch ComparisonOperator string, e.g.
        ``"GreaterThanThreshold"`` or ``"LessThanThreshold"``.
    threshold:
        Alarm threshold value.
    evaluation_periods:
        Number of consecutive periods that must breach the threshold before
        the alarm fires.
    period_seconds:
        Evaluation period length in seconds.  Defaults to 60.
    description:
        Human-readable description shown in the CloudWatch console and
        included in SNS notifications.
    """

    alarm_name: str
    tier: AlarmTier
    metric_name: str
    metric_namespace: str
    comparison_operator: str  # e.g. GreaterThanThreshold
    threshold: float
    evaluation_periods: int
    period_seconds: int = 60
    description: str = ""


# ---------------------------------------------------------------------------
# Alarm definitions — PRD Sec 32.7: Three-tier alerting
# ---------------------------------------------------------------------------

ALARM_CONFIGS: list[AlarmConfig] = [
    # ------------------------------------------------------------------
    # P1 — state at risk; page on-call immediately
    # ------------------------------------------------------------------
    AlarmConfig(
        alarm_name="agent-error-rate-p1",
        tier=AlarmTier.P1,
        metric_name="AgentErrors",
        metric_namespace="GraphClaw",
        comparison_operator="GreaterThanThreshold",
        threshold=10,
        evaluation_periods=2,
        description="High agent error rate — task graph state at risk",
    ),
    AlarmConfig(
        alarm_name="auth-failure-spike-p1",
        tier=AlarmTier.P1,
        metric_name="AuthFailures",
        metric_namespace="GraphClaw",
        comparison_operator="GreaterThanThreshold",
        threshold=50,
        evaluation_periods=1,
        description="Auth failure spike — possible credential stuffing",
    ),
    # ------------------------------------------------------------------
    # P2 — degraded; alert, investigate within 1h
    # ------------------------------------------------------------------
    AlarmConfig(
        alarm_name="llm-cost-anomaly-p2",
        tier=AlarmTier.P2,
        metric_name="LLMTokenCost",
        metric_namespace="GraphClaw",
        comparison_operator="GreaterThanThreshold",
        threshold=100.0,
        evaluation_periods=3,
        period_seconds=3600,
        description="LLM cost exceeds daily budget cap",
    ),
    AlarmConfig(
        alarm_name="task-completion-drop-p2",
        tier=AlarmTier.P2,
        metric_name="TaskCompletions",
        metric_namespace="GraphClaw",
        comparison_operator="LessThanThreshold",
        threshold=1,
        evaluation_periods=5,
        description="Task completions dropped — possible agent deadlock",
    ),
    # ------------------------------------------------------------------
    # P3 — trends; dashboard only, no immediate action required
    # ------------------------------------------------------------------
    AlarmConfig(
        alarm_name="p99-latency-p3",
        tier=AlarmTier.P3,
        metric_name="RequestLatencyP99",
        metric_namespace="GraphClaw",
        comparison_operator="GreaterThanThreshold",
        threshold=2000,
        evaluation_periods=10,
        description="P99 latency trending high",
    ),
]
