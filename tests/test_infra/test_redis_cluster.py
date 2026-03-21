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

"""Tests for infra.redis — Redis Cluster configuration and conf generation."""

from __future__ import annotations

import pytest

from infra.redis.cluster_config import (
    CLUSTER_NODES,
    DEFAULT_CLUSTER_CONFIG,
    RedisClusterConfig,
    RedisNodeConfig,
    build_redis_cluster_config,
)
from infra.redis.redis_conf import generate_redis_conf, get_cluster_meet_commands


# ---------------------------------------------------------------------------
# CLUSTER_NODES constant
# ---------------------------------------------------------------------------


def test_cluster_has_3_nodes() -> None:
    """CLUSTER_NODES tuple must contain exactly 3 entries."""
    assert len(CLUSTER_NODES) == 3, (
        f"Expected 3 nodes in CLUSTER_NODES, got {len(CLUSTER_NODES)}"
    )


def test_all_nodes_are_masters() -> None:
    """Every node in CLUSTER_NODES must have role == 'master'."""
    for node in CLUSTER_NODES:
        assert node.role == "master", (
            f"Node {node.node_id!r} has role {node.role!r}, expected 'master'"
        )


# ---------------------------------------------------------------------------
# RedisClusterConfig properties
# ---------------------------------------------------------------------------


def test_cluster_config_node_count() -> None:
    """DEFAULT_CLUSTER_CONFIG.node_count must equal 3."""
    assert DEFAULT_CLUSTER_CONFIG.node_count == 3


def test_cluster_config_master_nodes() -> None:
    """DEFAULT_CLUSTER_CONFIG.master_nodes must return all 3 masters."""
    masters = DEFAULT_CLUSTER_CONFIG.master_nodes
    assert len(masters) == 3, (
        f"Expected 3 master nodes, got {len(masters)}"
    )


def test_user_hash_tag_pattern() -> None:
    """DEFAULT_CLUSTER_CONFIG.user_hash_tag_pattern must start with '{USER-'."""
    assert DEFAULT_CLUSTER_CONFIG.user_hash_tag_pattern.startswith("{USER-"), (
        f"user_hash_tag_pattern {DEFAULT_CLUSTER_CONFIG.user_hash_tag_pattern!r} "
        "must start with '{USER-'"
    )


# ---------------------------------------------------------------------------
# generate_redis_conf output
# ---------------------------------------------------------------------------


def test_redis_conf_has_cluster_enabled() -> None:
    """generate_redis_conf output must contain 'cluster-enabled yes'."""
    node = CLUSTER_NODES[0]
    conf = generate_redis_conf(node, DEFAULT_CLUSTER_CONFIG)
    assert "cluster-enabled yes" in conf, (
        "redis.conf must enable cluster mode with 'cluster-enabled yes'"
    )


def test_redis_conf_has_maxmemory() -> None:
    """generate_redis_conf output must contain 'maxmemory 512mb'."""
    node = CLUSTER_NODES[0]
    conf = generate_redis_conf(node, DEFAULT_CLUSTER_CONFIG)
    assert "maxmemory 512mb" in conf, (
        f"redis.conf must contain 'maxmemory 512mb'; got:\n{conf}"
    )


def test_redis_conf_has_appendonly() -> None:
    """generate_redis_conf output must contain 'appendonly yes'."""
    node = CLUSTER_NODES[0]
    conf = generate_redis_conf(node, DEFAULT_CLUSTER_CONFIG)
    assert "appendonly yes" in conf, (
        "redis.conf must enable AOF persistence with 'appendonly yes'"
    )


# ---------------------------------------------------------------------------
# get_cluster_meet_commands
# ---------------------------------------------------------------------------


def test_cluster_meet_command_contains_all_nodes() -> None:
    """get_cluster_meet_commands must list all 3 node host:port addresses."""
    commands = get_cluster_meet_commands(DEFAULT_CLUSTER_CONFIG)
    assert len(commands) >= 1, "Expected at least one cluster-create command"
    cmd = commands[0]
    for node in CLUSTER_NODES:
        expected_addr = f"{node.host}:{node.port}"
        assert expected_addr in cmd, (
            f"Cluster create command missing node address {expected_addr!r}; "
            f"command: {cmd!r}"
        )


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_cluster_config_frozen() -> None:
    """RedisClusterConfig instances must be immutable (frozen dataclass)."""
    with pytest.raises((AttributeError, TypeError)):
        DEFAULT_CLUSTER_CONFIG.maxmemory_mb = 1024  # type: ignore[misc]


def test_redis_node_config_frozen() -> None:
    """RedisNodeConfig instances must be immutable (frozen dataclass)."""
    node = CLUSTER_NODES[0]
    with pytest.raises((AttributeError, TypeError)):
        node.port = 9999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_redis_cluster_config factory
# ---------------------------------------------------------------------------


def test_build_redis_cluster_config_default() -> None:
    """build_redis_cluster_config with defaults must return a RedisClusterConfig with 3 nodes."""
    config = build_redis_cluster_config()
    assert isinstance(config, RedisClusterConfig)
    assert config.node_count == 3, (
        f"Expected 3 nodes from default build, got {config.node_count}"
    )
