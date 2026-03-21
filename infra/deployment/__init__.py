"""graphclaw.infra.deployment — Deployment strategy configuration for ECS services.

Re-exports the public API from ``infra.deployment.models`` and
``infra.deployment.ecs_deploy`` so callers can import directly from
``infra.deployment``.

Public API
----------
- DeploymentConfig: Frozen dataclass with per-service ECS deployment parameters.
- RolloutStrategy: Enum of supported ECS rollout strategies.
- HealthCheck: Frozen dataclass with ALB health-check parameters.
- DEPLOYMENT_CONFIGS: dict[str, DeploymentConfig] — pre-configured per-service configs.
- build_deployment_config: Alias for ``DEPLOYMENT_CONFIGS.__getitem__`` lookup helper.
- build_ecs_service_config: Build ECS CreateService/UpdateService request dict.
- build_deployment_circuit_breaker: Return ECS DeploymentCircuitBreaker config dict.
- generate_appspec_yaml: Generate CodeDeploy AppSpec YAML for blue/green deployments.
"""

# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

from infra.deployment.ecs_deploy import (
    build_deployment_circuit_breaker,
    build_ecs_service_config,
    generate_appspec_yaml,
)
from infra.deployment.models import (
    DEPLOYMENT_CONFIGS,
    DeploymentConfig,
    HealthCheck,
    RolloutStrategy,
)


def build_deployment_config(service_name: str) -> DeploymentConfig:
    """Return the pre-configured ``DeploymentConfig`` for *service_name*.

    Parameters
    ----------
    service_name:
        One of the keys in ``DEPLOYMENT_CONFIGS``
        (``"agent-runtime"``, ``"channel-gateway"``, ``"trigger-engine"``,
        ``"api-server"``).

    Raises
    ------
    KeyError
        If *service_name* is not in ``DEPLOYMENT_CONFIGS``.
    """
    return DEPLOYMENT_CONFIGS[service_name]


__all__ = [
    "DeploymentConfig",
    "RolloutStrategy",
    "HealthCheck",
    "DEPLOYMENT_CONFIGS",
    "build_deployment_config",
    "build_ecs_service_config",
    "build_deployment_circuit_breaker",
    "generate_appspec_yaml",
]
