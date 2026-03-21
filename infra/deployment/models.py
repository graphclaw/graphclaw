"""graphclaw.infra.deployment.models — Deployment strategy models for ECS services.

Description
-----------
Defines ``DeploymentConfig``, ``RolloutStrategy``, and ``HealthCheck`` dataclasses
that encode the deployment strategy for each GraphClaw ECS Fargate service.  The
``DEPLOYMENT_CONFIGS`` dict is the single source of truth for per-service deployment
parameters consumed by ``ecs_deploy.py`` and CDK/Terraform stacks.

Design Patterns
---------------
- Data-driven configuration: All per-service parameters are expressed as frozen
  dataclasses so they can be iterated by deployment automation without cloud SDKs.
- Single source of truth: ``DEPLOYMENT_CONFIGS`` lives here; callers import from
  ``infra.deployment`` directly via the re-export in ``__init__.py``.

Public API
----------
- RolloutStrategy: Enum of supported ECS rollout strategies.
- HealthCheck: Frozen dataclass holding ALB health-check parameters.
- DeploymentConfig: Frozen dataclass holding per-service deployment parameters.
- DEPLOYMENT_CONFIGS: dict[str, DeploymentConfig] — pre-configured per-service configs.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

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

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RolloutStrategy(str, Enum):
    """Supported ECS deployment rollout strategies."""

    ROLLING = "rolling"        # replace instances one at a time
    BLUE_GREEN = "blue_green"  # full new stack, swap DNS
    CANARY = "canary"          # 10% → 50% → 100%


@dataclass(frozen=True)
class HealthCheck:
    """ALB / ECS target-group health-check parameters."""

    path: str = "/healthz"
    interval_seconds: int = 10
    timeout_seconds: int = 5
    healthy_threshold: int = 2
    unhealthy_threshold: int = 3


@dataclass(frozen=True)
class DeploymentConfig:
    """Per-service ECS deployment configuration.

    Attributes
    ----------
    service_name:
        ECS service name (matches Docker Compose service key).
    strategy:
        Rollout strategy to use when deploying a new task definition revision.
    min_healthy_percent:
        ECS minimum healthy percent — how many tasks must stay running during deploy.
        Set to 0 for scale-to-zero services; 100 for always-on services.
    max_percent:
        ECS maximum percent — upper bound on total tasks (running + starting) during
        deploy.  200 means ECS can spin up a full duplicate fleet before draining old.
    health_check:
        ALB health-check configuration.  Defaults to ``HealthCheck()`` (``/healthz``).
    deployment_circuit_breaker:
        When True, ECS automatically rolls back to the last stable task definition
        revision if the new revision fails to reach a steady state.
    rollback_on_failure:
        Passed as ``Rollback`` inside the ECS DeploymentCircuitBreaker config dict.
    """

    service_name: str
    strategy: RolloutStrategy
    min_healthy_percent: int   # ECS minimum healthy percent during deploy
    max_percent: int           # ECS maximum percent during deploy
    health_check: HealthCheck = field(default_factory=HealthCheck)
    deployment_circuit_breaker: bool = True
    rollback_on_failure: bool = True


# ---------------------------------------------------------------------------
# Per-service deployment configs
# ---------------------------------------------------------------------------

DEPLOYMENT_CONFIGS: dict[str, DeploymentConfig] = {
    "agent-runtime": DeploymentConfig(
        "agent-runtime",
        RolloutStrategy.ROLLING,
        min_healthy_percent=0,   # can go to zero — scale-to-zero service
        max_percent=200,
        deployment_circuit_breaker=True,
        rollback_on_failure=True,
    ),
    "channel-gateway": DeploymentConfig(
        "channel-gateway",
        RolloutStrategy.ROLLING,
        min_healthy_percent=100,  # always keep 100% capacity
        max_percent=200,
    ),
    "trigger-engine": DeploymentConfig(
        "trigger-engine",
        RolloutStrategy.ROLLING,
        min_healthy_percent=50,
        max_percent=200,
    ),
    "api-server": DeploymentConfig(
        "api-server",
        RolloutStrategy.BLUE_GREEN,
        min_healthy_percent=100,
        max_percent=200,
    ),
}
