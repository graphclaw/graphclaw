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

"""graphclaw.infra.observability.stack — Full observability stack descriptor.

Description
-----------
Aggregates all observability resources (log groups, metric filters, alarms,
dashboards) into a single stack descriptor dict.  The descriptor is consumed
by CDK stacks, Terraform modules, or deployment scripts to provision the
complete CloudWatch observability layer in a single call.

Public API
----------
- build_observability_stack(): Returns the full stack descriptor dict.

Dependencies
------------
- infra.observability.log_groups
- infra.observability.metric_filters
- infra.observability.alarms
- infra.observability.dashboards
"""

from __future__ import annotations

from infra.observability.alarms import ALARM_CONFIGS, AlarmConfig
from infra.observability.dashboards import DASHBOARDS
from infra.observability.log_groups import LOG_GROUP_CONFIGS, LogGroupConfig
from infra.observability.metric_filters import METRIC_FILTERS, MetricFilter


def build_observability_stack() -> dict:
    """Return a full observability stack descriptor dict.

    The returned dict aggregates all CloudWatch resources required for the
    GraphClaw observability layer.  It is intended as input to IaC tooling
    (CDK, Terraform, CloudFormation) that iterates each key to provision
    resources.

    Returns
    -------
    dict
        A dict with the following keys:

        - ``log_groups`` (list[LogGroupConfig]): All log group configurations.
        - ``metric_filters`` (list[MetricFilter]): All metric filter definitions.
        - ``alarms`` (list[AlarmConfig]): All alarm configurations.
        - ``dashboards`` (list[str]): Dashboard names to create.

    Examples
    --------
    >>> stack = build_observability_stack()
    >>> len(stack["log_groups"]) > 0
    True
    >>> "log_groups" in stack and "alarms" in stack
    True
    """
    return {
        "log_groups": list(LOG_GROUP_CONFIGS),
        "metric_filters": list(METRIC_FILTERS),
        "alarms": list(ALARM_CONFIGS),
        "dashboards": list(DASHBOARDS),
    }
