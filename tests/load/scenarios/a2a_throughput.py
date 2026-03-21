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
"""tests.load.scenarios.a2a_throughput — A2A agent throughput test.

Description
-----------
Simulates 100 autonomous A2A agents posting concurrent task-update
notifications to the ``/api/v1/task-update`` endpoint.  Each agent uses its
own API key header (``X-Agent-Api-Key``) rather than a user JWT, mirroring
real-world machine-to-machine traffic patterns.

The 0.5-1.0 s wait time between requests models an agent that completes a
subtask roughly every second, producing 100-200 req/s aggregate throughput
for this endpoint alone.

Usage
-----
    locust -f tests/load/scenarios/a2a_throughput.py \\
        --host=http://localhost:8000 \\
        --users=100 \\
        --spawn-rate=20 \\
        --headless \\
        --run-time=60s

Dependencies
------------
- locust>=2.20.0: Load testing framework (third-party).
"""
from __future__ import annotations

import random
import string

from locust import HttpUser, between, task


def _rand(n: int = 6) -> str:
    """Return a random lowercase ASCII string of length *n*."""
    return "".join(random.choices(string.ascii_lowercase, k=n))


class A2AAgent(HttpUser):
    """Simulates an autonomous A2A agent posting task-update notifications.

    Uses ``X-Agent-Api-Key`` header authentication matching the A2A inbound
    route's expected auth scheme.  Each request generates a unique task_id to
    avoid graph uniqueness constraint violations during the test run.
    """

    wait_time = between(0.5, 1.0)

    @task
    def post_update(self) -> None:
        """POST /api/v1/task-update — submit a JSON-RPC 2.0 task-update notification."""
        self.client.post(
            "/api/v1/task-update",
            json={
                "jsonrpc": "2.0",
                "method": "task.update",
                "params": {
                    "task_id": f"TASK-{_rand(6)}",
                    "status": random.choice(["completed", "in_progress", "failed"]),
                    "message": f"A2A throughput test update from agent {_rand(4)}",
                },
                "id": "1",
            },
            headers={"X-Agent-Api-Key": "load-test-key"},
            name="POST /api/v1/task-update [a2a]",
        )
