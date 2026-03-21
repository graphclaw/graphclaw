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

"""infra.db.hardening — Aggregate database hardening configuration.

Description
-----------
Combines PgBouncer, index catalogue, query timeout, and optional read-replica
routing into a single ``DatabaseHardeningConfig`` dataclass.  The
``build_db_hardening_config`` factory constructs the recommended production
configuration from minimal inputs (primary and replica DSNs).

Design Patterns
---------------
- Facade: ``DatabaseHardeningConfig`` aggregates multiple sub-configs into a
  single object so callers have one import and one constructor call.
- Factory Function: ``build_db_hardening_config`` encapsulates the assembly
  logic, choosing sane defaults when optional parameters are omitted.

Public API
----------
- DatabaseHardeningConfig: Aggregate frozen dataclass for all DB hardening.
- build_db_hardening_config: Factory returning a production-ready config.

Dependencies
------------
- infra.db.pgbouncer: PgBouncerConfig, PRODUCTION_PGBOUNCER.
- infra.db.read_replica: ReadReplicaConfig.
- infra.db.indexes: AGE_PRODUCTION_INDEXES, QUERY_TIMEOUT_MS.

Notes
-----
``enable_read_replica`` defaults to ``False`` so the config is safe to use
in environments without a replica.  Pass ``replica_url`` to activate replica
routing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from infra.db.indexes import AGE_PRODUCTION_INDEXES, QUERY_TIMEOUT_MS
from infra.db.pgbouncer import PgBouncerConfig, PRODUCTION_PGBOUNCER
from infra.db.read_replica import ReadReplicaConfig


@dataclass(frozen=True)
class DatabaseHardeningConfig:
    """Aggregate database hardening configuration for production.

    Parameters
    ----------
    pgbouncer:
        PgBouncer pool settings.
    indexes:
        List of index definition dicts (from ``AGE_PRODUCTION_INDEXES``).
    query_timeout_ms:
        Hard per-statement timeout in milliseconds.
    enable_read_replica:
        When ``True``, replica routing is active and ``read_replica`` must be
        set.
    read_replica:
        ``ReadReplicaConfig`` instance.  Required when
        ``enable_read_replica`` is ``True``.
    """

    pgbouncer: PgBouncerConfig
    indexes: list[dict]
    query_timeout_ms: int
    enable_read_replica: bool = False
    read_replica: ReadReplicaConfig | None = None


def build_db_hardening_config(
    primary_url: str = "",
    replica_url: str = "",
) -> DatabaseHardeningConfig:
    """Build production DB hardening config.

    Parameters
    ----------
    primary_url:
        DSN for the primary Postgres instance.  Used when constructing a
        ``ReadReplicaConfig``.  May be empty when replica routing is not
        needed.
    replica_url:
        DSN for the read replica.  When non-empty, read-replica routing is
        enabled.

    Returns
    -------
    DatabaseHardeningConfig
        Fully assembled hardening config using production defaults.
    """
    enable_replica = bool(replica_url)
    read_replica: ReadReplicaConfig | None = None
    if enable_replica:
        read_replica = ReadReplicaConfig(
            primary_url=primary_url,
            replica_url=replica_url,
        )

    return DatabaseHardeningConfig(
        pgbouncer=PRODUCTION_PGBOUNCER,
        indexes=AGE_PRODUCTION_INDEXES,
        query_timeout_ms=QUERY_TIMEOUT_MS,
        enable_read_replica=enable_replica,
        read_replica=read_replica,
    )
