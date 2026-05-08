# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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

"""Tests for infra.db — database hardening configuration (WS-5-G).

Covers PgBouncerConfig, ReadReplicaConfig, AGE index catalogue,
query timeout constants, and the build_db_hardening_config factory.
"""

from __future__ import annotations

from infra.db.hardening import DatabaseHardeningConfig, build_db_hardening_config
from infra.db.indexes import AGE_PRODUCTION_INDEXES, QUERY_TIMEOUT_MS, get_set_timeout_sql
from infra.db.pgbouncer import PRODUCTION_PGBOUNCER, PgBouncerConfig
from infra.db.read_replica import ReadReplicaConfig, should_use_replica

# ---------------------------------------------------------------------------
# PgBouncerConfig tests
# ---------------------------------------------------------------------------


def test_pgbouncer_ini_content_has_transaction_mode() -> None:
    """ini_content must specify transaction pooling mode."""
    cfg = PgBouncerConfig()
    assert "pool_mode = transaction" in cfg.ini_content


def test_pgbouncer_query_timeout_5_seconds() -> None:
    """query_timeout must be 5 seconds per PRD Sec 28.11."""
    cfg = PgBouncerConfig()
    assert cfg.query_timeout == 5


def test_pgbouncer_max_client_conn_1000() -> None:
    """max_client_conn must be 1000 to support the user ceiling."""
    cfg = PgBouncerConfig()
    assert cfg.max_client_conn == 1000


def test_pgbouncer_ini_has_databases_section() -> None:
    """ini_content must contain a [databases] section."""
    cfg = PgBouncerConfig()
    assert "[databases]" in cfg.ini_content


def test_production_pgbouncer_default_pool_size() -> None:
    """PRODUCTION_PGBOUNCER.default_pool_size must be 20."""
    assert PRODUCTION_PGBOUNCER.default_pool_size == 20


# ---------------------------------------------------------------------------
# ReadReplicaConfig / should_use_replica tests
# ---------------------------------------------------------------------------


def _make_replica_config() -> ReadReplicaConfig:
    return ReadReplicaConfig(
        primary_url="postgresql://graphclaw@primary:5432/graphclaw",
        replica_url="postgresql://graphclaw@replica:5432/graphclaw",
    )


def test_read_replica_routes_scoring() -> None:
    """Queries containing 'scoring' must be routed to the replica."""
    cfg = _make_replica_config()
    assert should_use_replica("scoring_query", cfg) is True


def test_read_replica_does_not_route_writes() -> None:
    """Write queries must not be routed to the replica."""
    cfg = _make_replica_config()
    assert should_use_replica("create_task", cfg) is False


def test_read_replica_routes_briefing() -> None:
    """Queries containing 'briefing' must be routed to the replica."""
    cfg = _make_replica_config()
    assert should_use_replica("briefing_generation", cfg) is True


# ---------------------------------------------------------------------------
# AGE index catalogue tests
# ---------------------------------------------------------------------------


def test_age_indexes_have_rationale() -> None:
    """Every index definition dict must contain a 'rationale' key."""
    for index in AGE_PRODUCTION_INDEXES:
        assert "rationale" in index, f"Index {index.get('name')!r} is missing 'rationale'"


# ---------------------------------------------------------------------------
# Query timeout constant tests
# ---------------------------------------------------------------------------


def test_query_timeout_constant() -> None:
    """QUERY_TIMEOUT_MS must be 5000 (5 seconds)."""
    assert QUERY_TIMEOUT_MS == 5000


def test_set_timeout_sql_correct() -> None:
    """get_set_timeout_sql() must return the exact expected SQL string."""
    assert get_set_timeout_sql() == "SET statement_timeout = 5000;"


# ---------------------------------------------------------------------------
# DatabaseHardeningConfig / build_db_hardening_config tests
# ---------------------------------------------------------------------------


def test_build_hardening_config_returns_config() -> None:
    """build_db_hardening_config must return a DatabaseHardeningConfig instance."""
    config = build_db_hardening_config()
    assert isinstance(config, DatabaseHardeningConfig)


def test_build_hardening_config_no_replica_by_default() -> None:
    """Without a replica_url, enable_read_replica must be False."""
    config = build_db_hardening_config()
    assert config.enable_read_replica is False
    assert config.read_replica is None


def test_build_hardening_config_with_replica_enabled() -> None:
    """Providing a replica_url must enable read-replica routing."""
    config = build_db_hardening_config(
        primary_url="postgresql://graphclaw@primary:5432/graphclaw",
        replica_url="postgresql://graphclaw@replica:5432/graphclaw",
    )
    assert config.enable_read_replica is True
    assert config.read_replica is not None
    assert isinstance(config.read_replica, ReadReplicaConfig)
