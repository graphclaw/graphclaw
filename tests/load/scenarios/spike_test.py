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
"""tests.load.scenarios.spike_test — Morning briefing spike simulation.

Description
-----------
Simulates the morning briefing spike where a large cohort of users all hit
the settings endpoint within a short window (typically 30 seconds after the
daily briefing is dispatched).

500 users each issuing requests at ``constant_pacing(0.1)`` (10 req/s per
user) produces up to 5 000 req/s at peak ramp-up, exercising the server's
ability to handle sudden traffic bursts without latency degradation.

Usage
-----
    locust -f tests/load/scenarios/spike_test.py \\
        --host=http://localhost:8000 \\
        --users=500 \\
        --spawn-rate=100 \\
        --headless \\
        --run-time=30s

Dependencies
------------
- locust>=2.20.0: Load testing framework (third-party).
"""
from __future__ import annotations

import os

from locust import HttpUser, constant_pacing, task


class BriefingSpike(HttpUser):
    """Simulates a morning-briefing spike hitting the settings endpoint.

    ``constant_pacing(0.1)`` ensures each user issues one request every 0.1
    seconds (10 req/s per user), generating a high-frequency burst across all
    concurrent users to stress-test the settings read path and any associated
    caching layer.
    """

    wait_time = constant_pacing(0.1)  # 10 req/s per user — spike pattern

    def on_start(self) -> None:
        """Set up auth headers."""
        token = os.environ.get("LOAD_TEST_TOKEN", "load-test-token")
        self.headers = {"Authorization": f"Bearer {token}"}

    @task
    def get_briefing(self) -> None:
        """GET /app/v1/settings — fetch settings as part of morning briefing."""
        self.client.get(
            "/app/v1/settings",
            headers=self.headers,
            name="GET /app/v1/settings [spike]",
        )
