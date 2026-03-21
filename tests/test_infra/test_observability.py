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

"""Tests for infra.observability — CloudWatch log groups, metrics, alarms, dashboards."""

from __future__ import annotations

import pytest

from infra.observability.alarms import ALARM_CONFIGS, AlarmTier
from infra.observability.dashboards import DASHBOARDS, build_dashboard_body
from infra.observability.log_groups import (
    LOG_GROUP_CONFIGS,
    SCRUB_PATTERNS,
    get_log_group_name,
)
from infra.observability.metric_filters import METRIC_FILTERS
from infra.observability.stack import build_observability_stack


# ---------------------------------------------------------------------------
# Log group tests
# ---------------------------------------------------------------------------


def test_log_groups_defined() -> None:
    """LOG_GROUP_CONFIGS must be non-empty."""
    assert LOG_GROUP_CONFIGS, "LOG_GROUP_CONFIGS must not be empty"


def test_agent_runtime_is_per_user() -> None:
    """The agent-runtime log group config must have is_per_user=True."""
    agent_groups = [cfg for cfg in LOG_GROUP_CONFIGS if "agent-runtime" in cfg.name]
    assert agent_groups, "No agent-runtime log group found"
    for cfg in agent_groups:
        assert cfg.is_per_user is True, (
            f"agent-runtime log group {cfg.name!r} must have is_per_user=True"
        )


def test_platform_containers_shared() -> None:
    """channel-gateway log group must have is_per_user=False (shared)."""
    gw_groups = [cfg for cfg in LOG_GROUP_CONFIGS if "channel-gateway" in cfg.name]
    assert gw_groups, "No channel-gateway log group found"
    for cfg in gw_groups:
        assert cfg.is_per_user is False, (
            f"channel-gateway log group {cfg.name!r} must have is_per_user=False"
        )


def test_agent_runtime_retention_is_90_days() -> None:
    """agent-runtime logs must be retained for 90 days (task history)."""
    agent_groups = [cfg for cfg in LOG_GROUP_CONFIGS if "agent-runtime" in cfg.name]
    assert agent_groups
    for cfg in agent_groups:
        assert cfg.retention_days == 90, (
            f"agent-runtime log group must retain logs for 90 days, got {cfg.retention_days}"
        )


def test_all_log_group_configs_have_names() -> None:
    """Every LogGroupConfig must have a non-empty name."""
    for cfg in LOG_GROUP_CONFIGS:
        assert cfg.name, f"LogGroupConfig with empty name: {cfg!r}"


# ---------------------------------------------------------------------------
# Scrub pattern tests
# ---------------------------------------------------------------------------


def test_scrub_patterns_include_sk_ant() -> None:
    """SCRUB_PATTERNS must include 'sk-ant-' to redact Anthropic API keys."""
    assert "sk-ant-" in SCRUB_PATTERNS, (
        "'sk-ant-' must be in SCRUB_PATTERNS to redact Anthropic API keys before storage"
    )


def test_scrub_patterns_include_wg_agent() -> None:
    """SCRUB_PATTERNS must include 'wg_agent_' to redact A2A agent keys."""
    assert "wg_agent_" in SCRUB_PATTERNS, (
        "'wg_agent_' must be in SCRUB_PATTERNS to redact A2A agent API keys"
    )


def test_scrub_patterns_include_bearer() -> None:
    """SCRUB_PATTERNS must include 'Bearer ' to redact Authorization headers."""
    assert "Bearer " in SCRUB_PATTERNS


def test_scrub_patterns_non_empty() -> None:
    """SCRUB_PATTERNS must have at least 5 entries."""
    assert len(SCRUB_PATTERNS) >= 5


# ---------------------------------------------------------------------------
# get_log_group_name tests
# ---------------------------------------------------------------------------


def test_get_log_group_name_per_user() -> None:
    """get_log_group_name for agent-runtime must substitute user_id as path segment."""
    name = get_log_group_name("agent-runtime", user_id="user-abc-123")
    assert name.endswith("/user-abc-123"), (
        f"Per-user log group name must end with the user_id, got {name!r}"
    )
    assert "agent-runtime" in name


def test_get_log_group_name_per_user_requires_user_id() -> None:
    """get_log_group_name must raise ValueError when user_id is omitted for per-user group."""
    with pytest.raises(ValueError, match="user_id"):
        get_log_group_name("agent-runtime")


def test_get_log_group_name_shared_container() -> None:
    """get_log_group_name for a shared container returns fixed group name."""
    name = get_log_group_name("channel-gateway")
    assert "channel-gateway" in name
    assert "{" not in name, "Shared log group name must not contain template placeholders"


def test_get_log_group_name_unknown_container() -> None:
    """get_log_group_name must raise ValueError for unknown container."""
    with pytest.raises(ValueError):
        get_log_group_name("nonexistent-container")


# ---------------------------------------------------------------------------
# Metric filter tests
# ---------------------------------------------------------------------------


def test_metric_filters_defined() -> None:
    """METRIC_FILTERS must be non-empty."""
    assert METRIC_FILTERS, "METRIC_FILTERS must not be empty"


def test_metric_filters_all_have_filter_name() -> None:
    """Every MetricFilter must have a non-empty filter_name."""
    for mf in METRIC_FILTERS:
        assert mf.filter_name, f"MetricFilter with empty filter_name: {mf!r}"


def test_metric_filters_all_have_pattern() -> None:
    """Every MetricFilter must have a non-empty filter_pattern."""
    for mf in METRIC_FILTERS:
        assert mf.filter_pattern, f"MetricFilter with empty filter_pattern: {mf!r}"


def test_llm_token_cost_filter_exists() -> None:
    """A metric filter for LLM token cost must be present."""
    names = [mf.filter_name for mf in METRIC_FILTERS]
    assert "llm-token-cost" in names, "llm-token-cost metric filter is required"


def test_auth_failures_filter_exists() -> None:
    """A metric filter for auth failures must be present."""
    names = [mf.filter_name for mf in METRIC_FILTERS]
    assert "auth-failures" in names


def test_agent_errors_filter_exists() -> None:
    """A metric filter for agent errors must be present."""
    names = [mf.filter_name for mf in METRIC_FILTERS]
    assert "agent-errors" in names


# ---------------------------------------------------------------------------
# Alarm tests
# ---------------------------------------------------------------------------


def test_alarm_configs_all_tiers() -> None:
    """ALARM_CONFIGS must include at least one alarm for each of P1, P2, P3."""
    tiers_present = {alarm.tier for alarm in ALARM_CONFIGS}
    for tier in AlarmTier:
        assert tier in tiers_present, (
            f"Alarm tier {tier.value} has no alarms in ALARM_CONFIGS"
        )


def test_p1_alarms_exist() -> None:
    """At least 2 P1 alarms must be defined (state-at-risk scenarios)."""
    p1_alarms = [a for a in ALARM_CONFIGS if a.tier == AlarmTier.P1]
    assert len(p1_alarms) >= 2, (
        f"Expected at least 2 P1 alarms, got {len(p1_alarms)}"
    )


def test_alarm_configs_have_descriptions() -> None:
    """Every AlarmConfig should have a non-empty description."""
    for alarm in ALARM_CONFIGS:
        assert alarm.description, f"Alarm {alarm.alarm_name!r} has no description"


def test_alarm_configs_have_valid_comparison_operators() -> None:
    """Comparison operators must be valid CloudWatch strings."""
    valid_operators = {
        "GreaterThanThreshold",
        "GreaterThanOrEqualToThreshold",
        "LessThanThreshold",
        "LessThanOrEqualToThreshold",
        "LessThanLowerOrGreaterThanUpperThreshold",
    }
    for alarm in ALARM_CONFIGS:
        assert alarm.comparison_operator in valid_operators, (
            f"Alarm {alarm.alarm_name!r} has invalid operator: {alarm.comparison_operator!r}"
        )


def test_alarm_configs_evaluation_periods_positive() -> None:
    """All alarms must have evaluation_periods > 0."""
    for alarm in ALARM_CONFIGS:
        assert alarm.evaluation_periods > 0


# ---------------------------------------------------------------------------
# build_observability_stack tests
# ---------------------------------------------------------------------------


def test_build_observability_stack_keys() -> None:
    """build_observability_stack must return a dict with the four required keys."""
    stack = build_observability_stack()
    assert isinstance(stack, dict)
    expected_keys = {"log_groups", "metric_filters", "alarms", "dashboards"}
    assert set(stack.keys()) == expected_keys, (
        f"Stack keys mismatch. Expected {expected_keys}, got {set(stack.keys())}"
    )


def test_build_observability_stack_log_groups_non_empty() -> None:
    """Stack log_groups list must be non-empty."""
    stack = build_observability_stack()
    assert stack["log_groups"]


def test_build_observability_stack_metric_filters_non_empty() -> None:
    """Stack metric_filters list must be non-empty."""
    stack = build_observability_stack()
    assert stack["metric_filters"]


def test_build_observability_stack_alarms_non_empty() -> None:
    """Stack alarms list must be non-empty."""
    stack = build_observability_stack()
    assert stack["alarms"]


def test_build_observability_stack_dashboards_non_empty() -> None:
    """Stack dashboards list must be non-empty."""
    stack = build_observability_stack()
    assert stack["dashboards"]


# ---------------------------------------------------------------------------
# Dashboard tests
# ---------------------------------------------------------------------------


def test_dashboards_count() -> None:
    """Exactly 5 dashboards must be defined per PRD Sec 32.11."""
    assert len(DASHBOARDS) == 5, (
        f"Expected 5 dashboards, got {len(DASHBOARDS)}: {DASHBOARDS}"
    )


def test_dashboards_expected_names() -> None:
    """Dashboard names must match the five PRD-specified dashboards."""
    expected = {
        "platform-health",
        "llm-cost",
        "latency",
        "reliability",
        "user-activity",
    }
    assert set(DASHBOARDS) == expected


def test_build_dashboard_body_returns_dict_with_widgets() -> None:
    """build_dashboard_body must return a dict containing a 'widgets' key."""
    for name in DASHBOARDS:
        body = build_dashboard_body(name)
        assert isinstance(body, dict), f"Dashboard {name!r} body must be a dict"
        assert "widgets" in body, f"Dashboard {name!r} body must have 'widgets' key"
        assert body["widgets"], f"Dashboard {name!r} must have at least one widget"


def test_build_dashboard_body_unknown_raises() -> None:
    """build_dashboard_body must raise ValueError for unknown dashboard name."""
    with pytest.raises(ValueError, match="Unknown dashboard"):
        build_dashboard_body("nonexistent-dashboard")
