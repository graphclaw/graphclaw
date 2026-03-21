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

"""Tests for infra.scaling — container auto-scaling profiles and KEDA scalers."""

from __future__ import annotations

import pytest

from infra.scaling.profiles import (
    CONTAINER_SCALING_PROFILES,
    ScalingProfile,
    get_scaling_config,
)
from infra.scaling.keda_scalers import build_keda_scaled_object


# ---------------------------------------------------------------------------
# Profile catalogue tests
# ---------------------------------------------------------------------------


def test_all_7_containers_have_profiles() -> None:
    """CONTAINER_SCALING_PROFILES must define exactly 7 container profiles."""
    assert len(CONTAINER_SCALING_PROFILES) == 7, (
        f"Expected 7 container profiles, got {len(CONTAINER_SCALING_PROFILES)}. "
        f"Keys: {sorted(CONTAINER_SCALING_PROFILES)}"
    )


def test_all_expected_container_names_present() -> None:
    """All seven canonical container names must be keys in the profiles dict."""
    expected = {
        "agent-runtime",
        "channel-gateway",
        "trigger-engine",
        "api-server",
        "graph-db",
        "relational-db",
        "cache",
    }
    assert set(CONTAINER_SCALING_PROFILES.keys()) == expected


# ---------------------------------------------------------------------------
# agent-runtime: idle-to-zero + queue-based scaling
# ---------------------------------------------------------------------------


def test_agent_runtime_scale_to_zero() -> None:
    """agent-runtime must have min_tasks=0 and scale_to_zero=True."""
    profile = CONTAINER_SCALING_PROFILES["agent-runtime"]
    assert profile.min_tasks == 0, "agent-runtime min_tasks must be 0 for idle-to-zero"
    assert profile.scale_to_zero is True, "agent-runtime scale_to_zero must be True"


def test_agent_runtime_queue_scaler() -> None:
    """agent-runtime must have both queue_name and queue_depth_target set."""
    profile = CONTAINER_SCALING_PROFILES["agent-runtime"]
    assert profile.queue_name is not None, "agent-runtime must have a queue_name"
    assert profile.queue_depth_target is not None, (
        "agent-runtime must have a queue_depth_target"
    )


def test_agent_runtime_fast_scale_out() -> None:
    """agent-runtime scale_out_cooldown must be short (≤30s) for responsive scaling."""
    profile = CONTAINER_SCALING_PROFILES["agent-runtime"]
    assert profile.scale_out_cooldown_seconds <= 30


# ---------------------------------------------------------------------------
# trigger-engine: startup jitter
# ---------------------------------------------------------------------------


def test_trigger_engine_has_jitter() -> None:
    """trigger-engine must have startup_jitter_seconds > 0 to prevent briefing spikes."""
    profile = CONTAINER_SCALING_PROFILES["trigger-engine"]
    assert profile.startup_jitter_seconds > 0, (
        "trigger-engine must have startup_jitter_seconds > 0 "
        "to mitigate morning briefing thundering-herd"
    )


def test_trigger_engine_has_queue_scaler() -> None:
    """trigger-engine must have queue_name and queue_depth_target set."""
    profile = CONTAINER_SCALING_PROFILES["trigger-engine"]
    assert profile.queue_name is not None
    assert profile.queue_depth_target is not None


# ---------------------------------------------------------------------------
# Fixed-size containers
# ---------------------------------------------------------------------------


def test_graph_db_max_tasks_is_one() -> None:
    """graph-db must have max_tasks=1 (single primary; read replica is separate)."""
    profile = CONTAINER_SCALING_PROFILES["graph-db"]
    assert profile.max_tasks == 1


def test_relational_db_max_tasks_is_one() -> None:
    """relational-db must have max_tasks=1."""
    profile = CONTAINER_SCALING_PROFILES["relational-db"]
    assert profile.max_tasks == 1


def test_cache_min_tasks_is_three() -> None:
    """cache must have min_tasks=3 for Redis Cluster minimum quorum."""
    profile = CONTAINER_SCALING_PROFILES["cache"]
    assert profile.min_tasks == 3, "Redis Cluster requires at least 3 nodes"


# ---------------------------------------------------------------------------
# get_scaling_config helper
# ---------------------------------------------------------------------------


def test_get_scaling_config_valid() -> None:
    """get_scaling_config returns the correct ScalingProfile for a known container."""
    profile = get_scaling_config("api-server")
    assert isinstance(profile, ScalingProfile)
    assert profile.container_name == "api-server"
    assert profile is CONTAINER_SCALING_PROFILES["api-server"]


def test_get_scaling_config_unknown() -> None:
    """get_scaling_config raises ValueError for an unknown container name."""
    with pytest.raises(ValueError, match="No scaling profile defined for container"):
        get_scaling_config("nonexistent-container")


def test_get_scaling_config_all_containers() -> None:
    """get_scaling_config succeeds for every container in CONTAINER_SCALING_PROFILES."""
    for name in CONTAINER_SCALING_PROFILES:
        profile = get_scaling_config(name)
        assert profile.container_name == name


# ---------------------------------------------------------------------------
# KEDA scaler generation
# ---------------------------------------------------------------------------


def test_keda_scaler_output_is_yaml() -> None:
    """build_keda_scaled_object must return a string containing 'ScaledObject'."""
    profile = CONTAINER_SCALING_PROFILES["agent-runtime"]
    yaml_str = build_keda_scaled_object(profile)
    assert isinstance(yaml_str, str)
    assert "ScaledObject" in yaml_str, (
        "KEDA output must contain 'ScaledObject' as the kind value"
    )


def test_keda_scaler_contains_container_name() -> None:
    """KEDA scaler YAML must reference the correct container name."""
    profile = CONTAINER_SCALING_PROFILES["agent-runtime"]
    yaml_str = build_keda_scaled_object(profile)
    assert "agent-runtime" in yaml_str


def test_keda_scaler_contains_min_max_replicas() -> None:
    """KEDA scaler YAML must include minReplicaCount and maxReplicaCount."""
    profile = CONTAINER_SCALING_PROFILES["agent-runtime"]
    yaml_str = build_keda_scaled_object(profile)
    assert "minReplicaCount" in yaml_str
    assert "maxReplicaCount" in yaml_str


def test_keda_scaler_custom_namespace() -> None:
    """build_keda_scaled_object must honour the namespace parameter."""
    profile = CONTAINER_SCALING_PROFILES["trigger-engine"]
    yaml_str = build_keda_scaled_object(profile, namespace="staging")
    assert "namespace: staging" in yaml_str


def test_keda_scaler_only_for_queue_containers() -> None:
    """build_keda_scaled_object must raise ValueError for non-queue containers."""
    non_queue_containers = [
        name
        for name, p in CONTAINER_SCALING_PROFILES.items()
        if p.queue_name is None
    ]
    assert non_queue_containers, "Expected at least one non-queue container"

    for name in non_queue_containers:
        profile = CONTAINER_SCALING_PROFILES[name]
        with pytest.raises(ValueError, match="no queue_name"):
            build_keda_scaled_object(profile)


def test_keda_scaler_trigger_engine_has_jitter_annotation() -> None:
    """trigger-engine KEDA manifest must include startup jitter annotation."""
    profile = CONTAINER_SCALING_PROFILES["trigger-engine"]
    yaml_str = build_keda_scaled_object(profile)
    assert "startup-jitter" in yaml_str


# ---------------------------------------------------------------------------
# Profile immutability
# ---------------------------------------------------------------------------


def test_scaling_profile_is_frozen() -> None:
    """ScalingProfile instances must be immutable (frozen dataclass)."""
    profile = CONTAINER_SCALING_PROFILES["api-server"]
    with pytest.raises((AttributeError, TypeError)):
        profile.min_tasks = 99  # type: ignore[misc]
