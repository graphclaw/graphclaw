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
"""tests.load.locustfile — GraphClaw 1000-User Load Test.

Description
-----------
Locust load test simulating 1,000 concurrent users hitting the GraphClaw API.
Covers the full endpoint surface: health checks, settings, approvals, skill
registry, MCP registry, A2A keys, auth, and inbound A2A task-update webhooks.

Two user classes are provided:

- ``GatewayUser`` (weight=9, 90% of pool): Typical read-heavy usage with
  realistic 1-3 s think time between tasks.
- ``HeavyUser`` (weight=1, 10% of pool): Power-user pattern — writes, MCP
  registration, and compliance exports with 2-5 s think time.

Pass/Fail Thresholds (PRD Phase 5)
------------------------------------
- P99 latency < 2 000 ms for all endpoints
- Error rate < 1 %
- Throughput > 100 req/s at 1 000 concurrent users

Usage
-----
    locust -f tests/load/locustfile.py \\
        --host=http://localhost:8000 \\
        --users=1000 \\
        --spawn-rate=50

    # Headless CI run (60 seconds):
    locust -f tests/load/locustfile.py \\
        --host=http://localhost:8000 \\
        --users=1000 \\
        --spawn-rate=50 \\
        --headless \\
        --run-time=60s

Dependencies
------------
- locust>=2.20.0: Load testing framework (third-party).
"""

from __future__ import annotations

import random
import string

from locust import HttpUser, between, events, task
from locust.env import Environment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def random_string(n: int = 8) -> str:
    """Return a random lowercase ASCII string of length *n*."""
    return "".join(random.choices(string.ascii_lowercase, k=n))


# ---------------------------------------------------------------------------
# User classes
# ---------------------------------------------------------------------------


class GatewayUser(HttpUser):
    """Simulates a typical user interacting with the GraphClaw gateway.

    Task weights reflect realistic usage patterns — health checks are hit
    frequently by load-balancer probes, while write operations are rare.
    Wait time of 1-3 seconds between tasks mirrors human browsing cadence.
    """

    wait_time = between(1, 3)
    weight = 9  # 90% of the user pool

    # Populated in on_start
    access_token: str = ""
    headers: dict[str, str] = {}

    def on_start(self) -> None:
        """Authenticate before tasks begin.

        In a real load-test environment the token would be obtained via the
        OAuth flow or injected through an environment variable.  For baseline
        load tests we use a synthetic bearer token; the server's auth
        middleware will reject it with 401, which is intentionally recorded
        as a failure so operators can see the auth overhead.  To test with
        valid credentials, set ``LOAD_TEST_TOKEN`` in the environment.
        """
        import os

        self.access_token = os.environ.get("LOAD_TEST_TOKEN", "load-test-token")
        self.headers = {"Authorization": f"Bearer {self.access_token}"}

    # --- Health check (lightweight, high frequency) -------------------------

    @task(10)
    def health_check(self) -> None:
        """GET /healthz — liveness probe, no auth required."""
        self.client.get("/healthz", name="GET /healthz")

    # --- Settings -----------------------------------------------------------

    @task(3)
    def get_settings(self) -> None:
        """GET /app/v1/settings — fetch current user settings."""
        self.client.get(
            "/app/v1/settings",
            headers=self.headers,
            name="GET /app/v1/settings",
        )

    # --- Approvals ----------------------------------------------------------

    @task(2)
    def list_approvals(self) -> None:
        """GET /app/v1/approvals — list pending approvals."""
        self.client.get(
            "/app/v1/approvals",
            headers=self.headers,
            name="GET /app/v1/approvals",
        )

    # --- Skill registry -----------------------------------------------------

    @task(2)
    def list_skill_sources(self) -> None:
        """GET /app/v1/skills/sources — list registered skill sources."""
        self.client.get(
            "/app/v1/skills/sources",
            headers=self.headers,
            name="GET /app/v1/skills/sources",
        )

    @task(1)
    def search_skills(self) -> None:
        """GET /app/v1/skills/search — semantic skill search."""
        q = random.choice(["outreach", "report", "calendar", "notes"])
        self.client.get(
            f"/app/v1/skills/search?q={q}",
            headers=self.headers,
            name="GET /app/v1/skills/search",
        )

    # --- MCP registry -------------------------------------------------------

    @task(2)
    def list_mcp_servers(self) -> None:
        """GET /app/v1/mcp/servers — list registered MCP servers."""
        self.client.get(
            "/app/v1/mcp/servers",
            headers=self.headers,
            name="GET /app/v1/mcp/servers",
        )

    # --- A2A agents ---------------------------------------------------------

    @task(1)
    def list_a2a_agents(self) -> None:
        """GET /app/v1/a2a-keys — list registered agent API keys."""
        self.client.get(
            "/app/v1/a2a-keys",
            headers=self.headers,
            name="GET /app/v1/a2a-keys",
        )

    # --- Auth ---------------------------------------------------------------

    @task(1)
    def get_me(self) -> None:
        """GET /auth/me — retrieve authenticated user profile."""
        self.client.get(
            "/auth/me",
            headers=self.headers,
            name="GET /auth/me",
        )

    # --- Inbound webhook simulation (A2A) -----------------------------------

    @task(5)
    def simulate_a2a_update(self) -> None:
        """POST /api/v1/task-update — simulate an inbound A2A task update."""
        payload = {
            "jsonrpc": "2.0",
            "method": "task.update",
            "params": {
                "task_id": f"TASK-{random_string(8)}",
                "status": random.choice(["completed", "in_progress"]),
                "message": f"Update from load test agent {random_string(4)}",
            },
            "id": random_string(6),
        }
        self.client.post(
            "/api/v1/task-update",
            json=payload,
            headers={"X-Agent-Api-Key": "load-test-key"},
            name="POST /api/v1/task-update",
        )


class HeavyUser(HttpUser):
    """Simulates a power user performing more intensive write operations.

    Represents approximately 10% of the user pool (weight=1 vs GatewayUser
    weight=9).  Uses a longer 2-5 s think time to avoid overwhelming write
    paths while still exercising them under concurrent load.
    """

    wait_time = between(2, 5)
    weight = 1  # 10% of the user pool

    def on_start(self) -> None:
        """Set up auth headers."""
        import os

        token = os.environ.get("LOAD_TEST_TOKEN", "load-test-token")
        self.headers = {"Authorization": f"Bearer {token}"}

    @task(3)
    def patch_settings(self) -> None:
        """PATCH /app/v1/settings — update user settings."""
        self.client.patch(
            "/app/v1/settings",
            json={"briefing_time": "08:00", "timezone": "UTC"},
            headers=self.headers,
            name="PATCH /app/v1/settings",
        )

    @task(2)
    def register_mcp_server(self) -> None:
        """POST /app/v1/mcp/servers — register a new MCP server."""
        self.client.post(
            "/app/v1/mcp/servers",
            json={
                "name": f"test-server-{random_string(4)}",
                "endpoint_url": "https://example.com/mcp",
                "transport": "HTTP",
                "trust_tier": "GATED",
                "capabilities": ["read"],
            },
            headers=self.headers,
            name="POST /app/v1/mcp/servers",
        )

    @task(1)
    def compliance_export(self) -> None:
        """GET /app/v1/compliance/export — download compliance audit export."""
        self.client.get(
            "/app/v1/compliance/export",
            headers=self.headers,
            name="GET /app/v1/compliance/export",
        )


# ---------------------------------------------------------------------------
# Pass/Fail thresholds
# ---------------------------------------------------------------------------

THRESHOLDS: dict[str, float] = {
    "p99_latency_ms": 2000,
    "error_rate_pct": 1.0,
    "min_throughput_rps": 100,
}


@events.quitting.add_listener
def check_thresholds(environment: Environment, **kwargs: object) -> None:
    """Assert pass/fail thresholds at the end of the load-test run.

    Invoked automatically by Locust when the runner exits.  Sets
    ``environment.process_exit_code = 1`` when any threshold is violated so
    that CI pipelines can detect failures via the process exit code.
    """
    stats = environment.stats.total

    if stats.num_requests == 0:
        print("WARNING  No requests made — skipping threshold check")
        return

    failures: list[str] = []

    # Error rate
    error_rate = (stats.num_failures / stats.num_requests) * 100
    if error_rate > THRESHOLDS["error_rate_pct"]:
        failures.append(
            f"Error rate {error_rate:.1f}% exceeds threshold {THRESHOLDS['error_rate_pct']}%"
        )

    # P99 latency — Locust exposes response_times as a percentile method
    p99 = stats.get_response_time_percentile(0.99)
    if p99 and p99 > THRESHOLDS["p99_latency_ms"]:
        failures.append(f"P99 latency {p99}ms exceeds threshold {THRESHOLDS['p99_latency_ms']}ms")

    # Throughput
    if stats.total_rps < THRESHOLDS["min_throughput_rps"]:
        failures.append(
            f"Throughput {stats.total_rps:.1f} rps below threshold"
            f" {THRESHOLDS['min_throughput_rps']} rps"
        )

    if failures:
        print("\nLOAD TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        environment.process_exit_code = 1
    else:
        print(
            f"\nLOAD TEST PASSED"
            f" — error_rate={error_rate:.2f}%,"
            f" p99={p99}ms,"
            f" rps={stats.total_rps:.1f}"
        )
