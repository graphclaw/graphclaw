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

"""graphclaw.infra.redis.cluster_config — Redis Cluster node and cluster configuration.

Description
-----------
Defines frozen dataclass configuration for a 3-node Redis Cluster deployment.
Per PRD Sec 28.11, consistent hashing with ``{USER-<id>}`` hash tags ensures
all keys for a given user land on the same shard, enabling atomic multi-key
operations within a user's keyspace.

Design Patterns
---------------
- Frozen dataclasses: All configuration objects are immutable after creation.
- Data-driven: Cluster topology is expressed as pure Python; no cloud SDK
  required at import time.

Public API
----------
- RedisNodeConfig: Configuration for a single Redis Cluster node.
- RedisClusterConfig: Full cluster configuration (nodes + policy knobs).
- CLUSTER_NODES: Default 3-master tuple (redis-1, redis-2, redis-3).
- DEFAULT_CLUSTER_CONFIG: Ready-to-use RedisClusterConfig for the default nodes.
- build_redis_cluster_config: Factory function for constructing a cluster config.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RedisNodeConfig:
    """Configuration for a single Redis Cluster node."""

    node_id: str        # e.g. "node-1"
    host: str
    port: int = 6379
    role: str = "master"   # "master" or "replica"


@dataclass(frozen=True)
class RedisClusterConfig:
    """3-node Redis Cluster with consistent hashing.

    Per PRD Sec 28.11: USER-id prefix consistent hashing ensures
    all keys for a user land on the same shard.

    Parameters
    ----------
    nodes:
        Tuple of RedisNodeConfig instances forming the cluster.
    cluster_enabled:
        Must be True for Redis Cluster mode.
    cluster_node_timeout_ms:
        Milliseconds before a node is considered unreachable.
    min_replicas_to_write:
        0 = write even if no replicas available (minimal viable cluster).
    min_replicas_max_lag:
        Maximum replication lag in seconds before writes are blocked.
    user_hash_tag_pattern:
        Hash tag prefix for user-scoped keys. Keys matching ``{USER-<id>}``
        always map to the same hash slot, enabling atomic cross-key operations.
    maxmemory_policy:
        Redis eviction policy applied when maxmemory is reached.
    maxmemory_mb:
        Per-node memory limit in megabytes.
    """

    nodes: tuple[RedisNodeConfig, ...]
    cluster_enabled: bool = True
    cluster_node_timeout_ms: int = 5000
    min_replicas_to_write: int = 0   # 0 = write even if no replicas available
    min_replicas_max_lag: int = 10   # seconds
    # Hash tag for user-scoped keys: {USER-<id>}:key → always same slot
    user_hash_tag_pattern: str = "{USER-"
    maxmemory_policy: str = "allkeys-lru"
    maxmemory_mb: int = 512           # per node

    @property
    def node_count(self) -> int:
        """Total number of nodes in the cluster."""
        return len(self.nodes)

    @property
    def master_nodes(self) -> tuple[RedisNodeConfig, ...]:
        """Subset of nodes with role == 'master'."""
        return tuple(n for n in self.nodes if n.role == "master")


# 3-node cluster: 3 masters, 0 replicas (minimal viable cluster)
# Upgrade path: 3 masters + 3 replicas for full HA
CLUSTER_NODES = (
    RedisNodeConfig("node-1", "redis-1", 6379, "master"),
    RedisNodeConfig("node-2", "redis-2", 6379, "master"),
    RedisNodeConfig("node-3", "redis-3", 6379, "master"),
)

DEFAULT_CLUSTER_CONFIG = RedisClusterConfig(nodes=CLUSTER_NODES)


def build_redis_cluster_config(
    nodes: tuple[RedisNodeConfig, ...] = CLUSTER_NODES,
    maxmemory_mb: int = 512,
) -> RedisClusterConfig:
    """Construct a RedisClusterConfig from the given nodes and memory limit.

    Parameters
    ----------
    nodes:
        Tuple of RedisNodeConfig instances. Defaults to the 3-master
        ``CLUSTER_NODES`` constant.
    maxmemory_mb:
        Per-node memory cap in megabytes. Defaults to 512.

    Returns
    -------
    RedisClusterConfig
        Frozen cluster configuration ready for use by redis_conf generators
        or deployment tooling.
    """
    return RedisClusterConfig(nodes=nodes, maxmemory_mb=maxmemory_mb)
