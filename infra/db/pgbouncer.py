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

"""infra.db.pgbouncer — PgBouncer connection pool configuration for production.

Description
-----------
Defines ``PgBouncerConfig``, a frozen dataclass that encapsulates every
PgBouncer tuning knob relevant to the GraphClaw deployment.  The dataclass
doubles as documentation (fields have inline rationale comments) and as a
code-generation source: ``ini_content`` renders a ready-to-mount
``pgbouncer.ini`` file.

Design Patterns
---------------
- Frozen Dataclass: All fields are immutable after construction so configs
  can be used as dict keys or passed across process boundaries safely.
- Code Generation: ``ini_content`` property produces the full .ini file
  content, keeping configuration in Python rather than scattered across
  templates.

Public API
----------
- PgBouncerConfig: Frozen dataclass with all PgBouncer tuning knobs.
- PRODUCTION_PGBOUNCER: Ready-to-use production configuration instance.

Dependencies
------------
- dataclasses: dataclass, field.

Notes
-----
Transaction pooling is mandatory for Apache AGE because AGE uses
``search_path`` and ``LOAD 'age'`` which are session-level.  With session
pooling PgBouncer holds the physical connection open for the entire client
session, which defeats the purpose of pooling at 1000+ users.  Re-running
AGE setup on each checkout (see ``connection.py``) compensates for the
stateless nature of transaction pooling.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PgBouncerConfig:
    """PgBouncer connection pool configuration for production.

    Parameters
    ----------
    pool_mode:
        ``"transaction"`` is required for AGE; session pooling defeats
        pooling at scale.
    max_client_conn:
        Maximum simultaneous client connections across all pools.  Set to
        1000 to match the projected user ceiling per PRD Sec 28.11.
    default_pool_size:
        Physical Postgres connections per (database, user) pair.  20 keeps
        Postgres connection count well below its default ``max_connections``
        of 100 even with multiple services connecting.
    min_pool_size:
        Connections kept warm to avoid cold-start latency on traffic spikes.
    reserve_pool_size:
        Extra connections available when ``default_pool_size`` is exhausted.
    reserve_pool_timeout:
        Seconds a client waits for a reserve connection before receiving an
        error.
    server_idle_timeout:
        Seconds before PgBouncer closes an idle physical connection.
    client_idle_timeout:
        Seconds before PgBouncer closes an idle client connection.  0 means
        no limit (handled at the application layer instead).
    query_timeout:
        Hard per-query timeout in seconds.  5 seconds per PRD Sec 28.11 to
        prevent runaway Cypher queries from monopolising pool slots.
    query_wait_timeout:
        Seconds a query waits for a free pool slot before being rejected.
    stats_period:
        Interval in seconds for PgBouncer to log pool statistics.
    """

    # Pool sizing
    pool_mode: str = "transaction"       # transaction pooling (best for AGE)
    max_client_conn: int = 1000          # max connections from all clients
    default_pool_size: int = 20          # connections per (db, user) pair
    min_pool_size: int = 5
    reserve_pool_size: int = 5
    reserve_pool_timeout: float = 3.0    # seconds before using reserve

    # Health / timeouts
    server_idle_timeout: int = 600       # seconds before idle server conn closed
    client_idle_timeout: int = 0         # 0 = no limit
    query_timeout: int = 5               # 5-second query timeout per PRD Sec 28.11
    query_wait_timeout: int = 120

    # Stats
    stats_period: int = 60

    @property
    def ini_content(self) -> str:
        """Generate pgbouncer.ini content string."""
        return f"""[databases]
graphclaw = host=graph-db port=5432 dbname=graphclaw

[pgbouncer]
listen_addr = *
listen_port = 6432
auth_type = md5
pool_mode = {self.pool_mode}
max_client_conn = {self.max_client_conn}
default_pool_size = {self.default_pool_size}
min_pool_size = {self.min_pool_size}
reserve_pool_size = {self.reserve_pool_size}
reserve_pool_timeout = {self.reserve_pool_timeout}
server_idle_timeout = {self.server_idle_timeout}
query_timeout = {self.query_timeout}
query_wait_timeout = {self.query_wait_timeout}
stats_period = {self.stats_period}
"""


PRODUCTION_PGBOUNCER = PgBouncerConfig()
