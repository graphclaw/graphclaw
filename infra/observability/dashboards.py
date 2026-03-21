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

"""graphclaw.infra.observability.dashboards — CloudWatch dashboard builders.

Description
-----------
Generates CloudWatch Dashboard body dicts for the five GraphClaw dashboards
per PRD Section 32.11.  Each dashboard body can be serialised to JSON and
passed to ``boto3.client('cloudwatch').put_dashboard(DashboardBody=...)``.

Design Patterns
---------------
- Pure data generation: No AWS SDK imports; CloudWatch interactions belong in
  deployment scripts.
- Five fixed dashboards: Names are the canonical reference in DASHBOARDS list.

Public API
----------
- build_dashboard_body(dashboard_name): Returns CloudWatch Dashboard body dict.
- DASHBOARDS: Canonical list of the five dashboard names.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Dashboard names — PRD Sec 32.11
# ---------------------------------------------------------------------------

# 5 dashboards per PRD Sec 32.11
DASHBOARDS: list[str] = [
    "platform-health",
    "llm-cost",
    "latency",
    "reliability",
    "user-activity",
]

_VALID_DASHBOARDS = frozenset(DASHBOARDS)

# ---------------------------------------------------------------------------
# Widget templates
# ---------------------------------------------------------------------------

def _metric_widget(
    title: str,
    metrics: list[list],
    stat: str = "Sum",
    period: int = 300,
    width: int = 12,
    height: int = 6,
) -> dict:
    """Return a CloudWatch metric widget dict."""
    return {
        "type": "metric",
        "properties": {
            "title": title,
            "metrics": metrics,
            "stat": stat,
            "period": period,
            "view": "timeSeries",
        },
        "width": width,
        "height": height,
    }


def _alarm_widget(title: str, alarm_arns: list[str], width: int = 24, height: int = 2) -> dict:
    """Return a CloudWatch alarm status widget dict."""
    return {
        "type": "alarm",
        "properties": {
            "title": title,
            "alarms": alarm_arns,
        },
        "width": width,
        "height": height,
    }


def _text_widget(markdown: str, width: int = 24, height: int = 1) -> dict:
    """Return a CloudWatch text/markdown widget dict."""
    return {
        "type": "text",
        "properties": {"markdown": markdown},
        "width": width,
        "height": height,
    }


# ---------------------------------------------------------------------------
# Dashboard body builders
# ---------------------------------------------------------------------------

def _build_platform_health() -> dict:
    return {
        "widgets": [
            _text_widget("## Platform Health — Container Status"),
            _metric_widget(
                "ECS Running Task Count",
                [
                    ["ECS/ContainerInsights", "RunningTaskCount", "ServiceName", "agent-runtime"],
                    ["ECS/ContainerInsights", "RunningTaskCount", "ServiceName", "channel-gateway"],
                    ["ECS/ContainerInsights", "RunningTaskCount", "ServiceName", "api-server"],
                    ["ECS/ContainerInsights", "RunningTaskCount", "ServiceName", "trigger-engine"],
                ],
                stat="Average",
            ),
            _metric_widget(
                "Agent Errors",
                [["GraphClaw", "AgentErrors"]],
                stat="Sum",
            ),
            _metric_widget(
                "Auth Failures",
                [["GraphClaw", "AuthFailures"]],
                stat="Sum",
            ),
        ]
    }


def _build_llm_cost() -> dict:
    return {
        "widgets": [
            _text_widget("## LLM Cost — Token Spend Monitoring"),
            _metric_widget(
                "LLM Token Cost (USD)",
                [["GraphClaw", "LLMTokenCost"]],
                stat="Sum",
                period=3600,
                width=24,
            ),
            _metric_widget(
                "LLM Calls Completed",
                [["GraphClaw", "LLMTokenCost"]],
                stat="SampleCount",
                period=3600,
            ),
        ]
    }


def _build_latency() -> dict:
    return {
        "widgets": [
            _text_widget("## Latency — Request Response Time Distribution"),
            _metric_widget(
                "API P50 Latency (ms)",
                [["GraphClaw", "RequestLatencyP50"]],
                stat="p50",
                width=8,
            ),
            _metric_widget(
                "API P95 Latency (ms)",
                [["GraphClaw", "RequestLatencyP95"]],
                stat="p95",
                width=8,
            ),
            _metric_widget(
                "API P99 Latency (ms)",
                [["GraphClaw", "RequestLatencyP99"]],
                stat="p99",
                width=8,
            ),
        ]
    }


def _build_reliability() -> dict:
    return {
        "widgets": [
            _text_widget("## Reliability — Task Completions and Error Rates"),
            _metric_widget(
                "Task Completions",
                [["GraphClaw", "TaskCompletions"]],
                stat="Sum",
            ),
            _metric_widget(
                "Agent Error Rate",
                [["GraphClaw", "AgentErrors"]],
                stat="Sum",
            ),
            _alarm_widget(
                "Active Alarms",
                [
                    "arn:aws:cloudwatch:REGION:ACCOUNT_ID:alarm:agent-error-rate-p1",
                    "arn:aws:cloudwatch:REGION:ACCOUNT_ID:alarm:auth-failure-spike-p1",
                    "arn:aws:cloudwatch:REGION:ACCOUNT_ID:alarm:task-completion-drop-p2",
                ],
            ),
        ]
    }


def _build_user_activity() -> dict:
    return {
        "widgets": [
            _text_widget("## User Activity — Inbound Messages and Task Creation"),
            _metric_widget(
                "Task Completions by Hour",
                [["GraphClaw", "TaskCompletions"]],
                stat="Sum",
                period=3600,
                width=24,
            ),
            _metric_widget(
                "Auth Events",
                [
                    ["GraphClaw", "AuthFailures"],
                ],
                stat="Sum",
            ),
        ]
    }


_DASHBOARD_BUILDERS: dict[str, object] = {
    "platform-health": _build_platform_health,
    "llm-cost":        _build_llm_cost,
    "latency":         _build_latency,
    "reliability":     _build_reliability,
    "user-activity":   _build_user_activity,
}


def build_dashboard_body(dashboard_name: str) -> dict:
    """Return a CloudWatch Dashboard body dict (widgets list).

    The returned dict should be serialised to JSON and passed as the
    ``DashboardBody`` argument to ``boto3.client('cloudwatch').put_dashboard()``.

    Parameters
    ----------
    dashboard_name:
        One of the five dashboard names in ``DASHBOARDS``.

    Returns
    -------
    dict
        CloudWatch Dashboard body with a ``"widgets"`` key.

    Raises
    ------
    ValueError
        If *dashboard_name* is not a known dashboard.
    """
    if dashboard_name not in _VALID_DASHBOARDS:
        raise ValueError(
            f"Unknown dashboard {dashboard_name!r}. "
            f"Valid dashboards: {sorted(_VALID_DASHBOARDS)}"
        )
    builder = _DASHBOARD_BUILDERS[dashboard_name]
    return builder()  # type: ignore[operator]
