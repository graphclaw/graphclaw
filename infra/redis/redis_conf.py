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

"""graphclaw.infra.redis.redis_conf — Redis configuration file and cluster-init generators.

Description
-----------
Generates redis.conf content for each cluster node and produces the
``redis-cli --cluster create`` command needed to initialise the cluster.
Output is pure strings so it can be written to disk, embedded in a
ConfigMap, or consumed by infrastructure-as-code tooling.

Public API
----------
- generate_redis_conf: Render a redis.conf string for one cluster node.
- get_cluster_meet_commands: Return the list of redis-cli commands to form
  the cluster from its nodes.

Dependencies
------------
- infra.redis.cluster_config (stdlib-only; no cloud SDK).
"""

from __future__ import annotations

from infra.redis.cluster_config import RedisClusterConfig, RedisNodeConfig


def generate_redis_conf(node: RedisNodeConfig, cluster_config: RedisClusterConfig) -> str:
    """Generate redis.conf content for a cluster node.

    Parameters
    ----------
    node:
        The specific node this configuration file is for.
    cluster_config:
        The full cluster configuration supplying timeout, memory, and
        policy settings.

    Returns
    -------
    str
        Multi-line redis.conf content ready to be written to disk or
        injected into a container image.
    """
    return (
        f"# GraphClaw Redis Cluster Node: {node.node_id}\n"
        f"cluster-enabled yes\n"
        f"cluster-config-file nodes-{node.node_id}.conf\n"
        f"cluster-node-timeout {cluster_config.cluster_node_timeout_ms}\n"
        f"cluster-announce-hostname {node.host}\n"
        f"cluster-announce-port {node.port}\n"
        f"appendonly yes\n"
        f"maxmemory {cluster_config.maxmemory_mb}mb\n"
        f"maxmemory-policy {cluster_config.maxmemory_policy}\n"
        f"bind 0.0.0.0\n"
        f"protected-mode no\n"
    )


def get_cluster_meet_commands(config: RedisClusterConfig) -> list[str]:
    """Return redis-cli commands to form the cluster.

    Produces a single ``redis-cli --cluster create`` invocation that
    joins all configured nodes with zero replicas (minimal viable cluster).
    Add ``--cluster-replicas 1`` and double the node count for full HA.

    Parameters
    ----------
    config:
        The cluster configuration whose nodes will be joined.

    Returns
    -------
    list[str]
        A list containing the shell command(s) needed to initialise the
        cluster. Currently a single-element list.
    """
    nodes_str = " ".join(f"{n.host}:{n.port}" for n in config.nodes)
    return [
        f"redis-cli --cluster create {nodes_str} --cluster-replicas 0 --cluster-yes"
    ]
