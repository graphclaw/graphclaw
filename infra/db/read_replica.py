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

"""infra.db.read_replica — Read replica routing configuration.

Description
-----------
Defines ``ReadReplicaConfig`` and the ``should_use_replica`` routing helper.
Read replicas offload expensive, read-only query workloads (scoring, briefing
generation, analytics) from the primary Postgres node, keeping write latency
low under sustained load.

Design Patterns
---------------
- Frozen Dataclass: Configuration is immutable; the routing helper is a
  pure function so both are safe to use in concurrent async contexts.
- Strategy: The ``replica_query_patterns`` tuple acts as a strategy list;
  adding a new query family to the replica requires only a config change,
  not code changes.

Public API
----------
- ReadReplicaConfig: Frozen dataclass describing primary/replica URLs and
  routing rules.
- should_use_replica: Pure function returning True when a query type should
  be directed to the read replica.

Dependencies
------------
- dataclasses: dataclass.

Notes
-----
Replication lag is monitored separately (CloudWatch / Prometheus).  The
``max_replication_lag_seconds`` field is informational metadata used by
alerting configuration; this module does not enforce the lag threshold at
query time.  If lag exceeds the threshold the alerting layer should route
all traffic back to primary until replication catches up.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReadReplicaConfig:
    """Read replica routing configuration for scoring/briefing queries.

    Parameters
    ----------
    primary_url:
        DSN for the primary (read-write) Postgres instance.
    replica_url:
        DSN for the read replica Postgres instance.
    replica_query_patterns:
        Tuple of substrings.  A query type string containing any of these
        substrings is routed to the replica.
    max_replication_lag_seconds:
        Informational threshold.  Alerting fires when observed lag exceeds
        this value; actual enforcement is outside this module.
    """

    primary_url: str
    replica_url: str
    # Route these query types to replica
    replica_query_patterns: tuple[str, ...] = (
        "scoring",      # score calculation queries
        "briefing",     # morning briefing generation
        "analytics",    # aggregate/reporting queries
    )
    max_replication_lag_seconds: int = 10  # alert if lag exceeds


def should_use_replica(query_type: str, config: ReadReplicaConfig) -> bool:
    """Return True if this query type should be routed to the read replica.

    Parameters
    ----------
    query_type:
        A string identifier for the query, e.g. ``"scoring_query"``,
        ``"briefing_generation"``, ``"create_task"``.
    config:
        ``ReadReplicaConfig`` instance containing the routing patterns.

    Returns
    -------
    bool
        ``True`` when ``query_type`` contains any pattern from
        ``config.replica_query_patterns``; ``False`` otherwise.

    Examples
    --------
    >>> cfg = ReadReplicaConfig(primary_url="...", replica_url="...")
    >>> should_use_replica("scoring_query", cfg)
    True
    >>> should_use_replica("create_task", cfg)
    False
    """
    return any(p in query_type for p in config.replica_query_patterns)
