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
"""tests.test_load.test_thresholds — Unit tests for load-test threshold config.

Description
-----------
Verifies the structural integrity of ``tests/load/locustfile.py`` without
executing Locust itself.  Tests cover:

- The ``THRESHOLDS`` dict contains all required keys with correct values.
- ``GatewayUser`` exposes at least 5 task-decorated methods.
- ``HeavyUser`` exposes at least 3 task-decorated methods.
- The module is importable (i.e., no syntax errors or bad top-level imports).

Design Patterns
---------------
- Pure unit tests: No network I/O, no Locust runner, no pytest-asyncio.
- Introspection via ``inspect.getmembers``: detects ``@task``-decorated
  methods without hard-coding method names, so adding new tasks never
  requires updating this file.

Dependencies
------------
- pytest: Test runner (third-party).
- inspect: Method introspection (stdlib).
- tests.load.locustfile: Module under test.
"""

from __future__ import annotations

import inspect

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
from tests.load.locustfile import THRESHOLDS, GatewayUser, HeavyUser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_methods(cls: type) -> list[str]:
    """Return names of methods decorated with ``@task`` on *cls*.

    Locust marks task methods by setting a ``locust_task_weight`` attribute
    on the function object.  We detect this attribute to enumerate tasks
    without hard-coding method names.
    """
    return [
        name
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if hasattr(member, "locust_task_weight")
    ]


# ---------------------------------------------------------------------------
# THRESHOLDS dict
# ---------------------------------------------------------------------------


def test_thresholds_defined() -> None:
    """THRESHOLDS dict must contain all three required keys."""
    required_keys = {"p99_latency_ms", "error_rate_pct", "min_throughput_rps"}
    assert required_keys.issubset(THRESHOLDS.keys()), (
        f"THRESHOLDS is missing keys: {required_keys - set(THRESHOLDS.keys())}"
    )


def test_p99_threshold_is_2000ms() -> None:
    """P99 latency threshold must be 2 000 ms (PRD Phase 5 requirement)."""
    assert THRESHOLDS["p99_latency_ms"] == 2000, (
        f"Expected p99_latency_ms=2000, got {THRESHOLDS['p99_latency_ms']}"
    )


def test_error_rate_threshold_is_1pct() -> None:
    """Error rate threshold must be 1.0 % (PRD Phase 5 requirement)."""
    assert THRESHOLDS["error_rate_pct"] == 1.0, (
        f"Expected error_rate_pct=1.0, got {THRESHOLDS['error_rate_pct']}"
    )


def test_min_throughput_100_rps() -> None:
    """Minimum throughput threshold must be 100 req/s (PRD Phase 5 requirement)."""
    assert THRESHOLDS["min_throughput_rps"] == 100, (
        f"Expected min_throughput_rps=100, got {THRESHOLDS['min_throughput_rps']}"
    )


# ---------------------------------------------------------------------------
# GatewayUser
# ---------------------------------------------------------------------------


def test_gateway_user_tasks() -> None:
    """GatewayUser must define at least 5 @task-decorated methods."""
    tasks = _task_methods(GatewayUser)
    assert len(tasks) >= 5, (
        f"GatewayUser has only {len(tasks)} @task method(s); expected >= 5. Found: {tasks}"
    )


# ---------------------------------------------------------------------------
# HeavyUser
# ---------------------------------------------------------------------------


def test_heavy_user_tasks() -> None:
    """HeavyUser must define at least 3 @task-decorated methods."""
    tasks = _task_methods(HeavyUser)
    assert len(tasks) >= 3, (
        f"HeavyUser has only {len(tasks)} @task method(s); expected >= 3. Found: {tasks}"
    )


# ---------------------------------------------------------------------------
# Importability smoke test
# ---------------------------------------------------------------------------


def test_locust_file_importable() -> None:
    """Importing THRESHOLDS, GatewayUser, HeavyUser from locustfile must succeed."""
    # The import at the top of this module already exercises this; if it
    # failed the entire module would fail to load.  This explicit assertion
    # makes the intent clear in pytest output.
    assert THRESHOLDS is not None
    assert GatewayUser is not None
    assert HeavyUser is not None
