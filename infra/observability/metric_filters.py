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

"""graphclaw.infra.observability.metric_filters — CloudWatch metric filter definitions.

Description
-----------
Defines CloudWatch Logs metric filters per PRD Section 32.6.  Filters extract
structured log fields (JSON) into CloudWatch custom metrics, enabling cost
monitoring, error rate tracking, and task throughput dashboards.

Design Patterns
---------------
- Frozen dataclasses: MetricFilter instances are immutable.
- Data-driven: METRIC_FILTERS drives IaC tooling; no AWS SDK at import time.

Public API
----------
- MetricFilter: Frozen dataclass for a single metric filter definition.
- METRIC_FILTERS: List of all metric filter configurations.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricFilter:
    """CloudWatch Logs metric filter configuration.

    Parameters
    ----------
    filter_name:
        Unique name for the metric filter within the log group.
    log_group_name:
        CloudWatch Logs log group to attach the filter to.
    filter_pattern:
        CloudWatch Logs filter pattern (JSON path syntax or space-delimited
        terms).  See CloudWatch documentation for pattern syntax.
    metric_name:
        Target CloudWatch metric name in the ``metric_namespace`` namespace.
    metric_namespace:
        CloudWatch custom metric namespace.  Defaults to ``"GraphClaw"``.
    metric_value:
        Value to publish per matching log event.  Use ``"1"`` for counters or
        a JSON field reference (e.g. ``"$.cost_usd"``) for gauge values.
    unit:
        CloudWatch metric unit string.  Defaults to ``"Count"``.
    """

    filter_name: str
    log_group_name: str
    filter_pattern: str  # CloudWatch Logs filter pattern
    metric_name: str
    metric_namespace: str = "GraphClaw"
    metric_value: str = "1"
    unit: str = "Count"


# ---------------------------------------------------------------------------
# Metric filter definitions — PRD Sec 32.6
# ---------------------------------------------------------------------------

METRIC_FILTERS: list[MetricFilter] = [
    # LLM token cost monitoring — extract cost_usd from completed LLM calls
    MetricFilter(
        filter_name="llm-token-cost",
        log_group_name="/graphclaw/platform/api-server",
        filter_pattern='{ $.event = "llm.call.completed" }',
        metric_name="LLMTokenCost",
        metric_value="$.cost_usd",
        unit="None",
    ),
    # Auth failure counter — spike detection for credential stuffing
    MetricFilter(
        filter_name="auth-failures",
        log_group_name="/graphclaw/platform/api-server",
        filter_pattern='{ $.event = "auth.failed" }',
        metric_name="AuthFailures",
    ),
    # Task completion throughput — deadlock detection baseline
    MetricFilter(
        filter_name="task-completions",
        log_group_name="/graphclaw/agent-runtime",
        filter_pattern='{ $.event = "task.completed" }',
        metric_name="TaskCompletions",
    ),
    # Agent error rate — P1 alarm trigger
    MetricFilter(
        filter_name="agent-errors",
        log_group_name="/graphclaw/agent-runtime",
        filter_pattern='{ $.level = "ERROR" }',
        metric_name="AgentErrors",
    ),
]
