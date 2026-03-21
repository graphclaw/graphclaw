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

"""graphclaw.infra.observability — CloudWatch observability stack.

Re-exports the public API so callers import from ``infra.observability`` directly.
"""

from __future__ import annotations

from infra.observability.alarms import AlarmConfig, AlarmTier, ALARM_CONFIGS
from infra.observability.dashboards import DASHBOARDS, build_dashboard_body
from infra.observability.log_groups import LogGroupConfig, LOG_GROUP_CONFIGS
from infra.observability.metric_filters import MetricFilter, METRIC_FILTERS
from infra.observability.stack import build_observability_stack

# DashboardConfig is a type alias for the dashboard name string; no class needed.
DashboardConfig = str

__all__ = [
    "LogGroupConfig",
    "MetricFilter",
    "AlarmConfig",
    "AlarmTier",
    "DashboardConfig",
    "build_dashboard_body",
    "build_observability_stack",
]
