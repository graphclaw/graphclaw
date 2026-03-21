"""graphclaw.infra.deployment.ecs_deploy — ECS service config builders.

Description
-----------
Generates ECS ``CreateService`` / ``UpdateService`` request dicts and CodeDeploy
AppSpec YAML documents from ``DeploymentConfig`` instances.  These helpers are
consumed by deployment automation (CDK stacks, CI/CD scripts) and keep the
deployment logic separated from the infrastructure-as-code layer.

Design Patterns
---------------
- Pure functions: Each builder takes only what it needs and returns a plain
  ``dict`` or ``str`` — no side effects, no cloud SDK imports at module level.
- Strategy dispatch: ``build_ecs_service_config`` inspects
  ``config.strategy`` to produce strategy-specific ECS parameters.

Public API
----------
- build_ecs_service_config(config): Return ECS CreateService/UpdateService dict.
- build_deployment_circuit_breaker(): Return ECS DeploymentCircuitBreaker config.
- generate_appspec_yaml(service_name, task_definition_arn): Generate CodeDeploy
  AppSpec YAML for ECS blue/green deployment.

Dependencies
------------
- infra.deployment.models: DeploymentConfig, RolloutStrategy.
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

import textwrap

from infra.deployment.models import DeploymentConfig, RolloutStrategy


def build_deployment_circuit_breaker() -> dict:
    """Return an ECS DeploymentCircuitBreaker configuration dict.

    Returns
    -------
    dict
        ``{"Enable": True, "Rollback": True}`` — enables the ECS circuit
        breaker so a failed deployment automatically reverts to the last
        stable task definition revision.
    """
    return {"Enable": True, "Rollback": True}


def build_ecs_service_config(config: DeploymentConfig) -> dict:
    """Return an ECS CreateService / UpdateService request dict.

    Constructs the ``deploymentConfiguration`` block from *config*.  The
    caller is responsible for merging this dict with the rest of the ECS
    service request (cluster, task definition, load balancer config, etc.).

    Parameters
    ----------
    config:
        ``DeploymentConfig`` for the target service.

    Returns
    -------
    dict
        ECS service configuration dict with ``deploymentConfiguration``,
        ``deploymentController``, and ``healthCheckGracePeriodSeconds`` keys.

    Notes
    -----
    - Rolling deployments use the ``ECS`` controller type with
      ``minimumHealthyPercent`` / ``maximumPercent`` bounds.
    - Blue/green deployments use the ``CODE_DEPLOY`` controller type; the
      traffic shifting is handled by CodeDeploy and the AppSpec produced by
      :func:`generate_appspec_yaml`.
    - Canary deployments use the ``ECS`` controller type and rely on a
      weighted target-group rule at the ALB level (not modelled here).
    """
    deployment_configuration: dict = {
        "minimumHealthyPercent": config.min_healthy_percent,
        "maximumPercent": config.max_percent,
    }

    if config.deployment_circuit_breaker:
        deployment_configuration["deploymentCircuitBreaker"] = (
            build_deployment_circuit_breaker()
        )

    if config.strategy == RolloutStrategy.BLUE_GREEN:
        controller_type = "CODE_DEPLOY"
    else:
        # ROLLING and CANARY both use the native ECS rolling controller
        controller_type = "ECS"

    return {
        "serviceName": config.service_name,
        "deploymentConfiguration": deployment_configuration,
        "deploymentController": {"type": controller_type},
        "healthCheckGracePeriodSeconds": (
            config.health_check.interval_seconds
            * config.health_check.unhealthy_threshold
        ),
    }


def generate_appspec_yaml(service_name: str, task_definition_arn: str) -> str:
    """Generate a CodeDeploy AppSpec YAML string for an ECS blue/green deployment.

    The generated AppSpec is suitable for use with ``aws deploy create-deployment
    --app-spec-content`` or as a file in the CodeDeploy S3 revision bundle.

    Parameters
    ----------
    service_name:
        ECS service name (used to construct the CodeDeploy target-group hook names
        and as a comment in the output).
    task_definition_arn:
        Fully qualified ARN of the new ECS task definition revision to deploy,
        e.g. ``arn:aws:ecs:us-east-1:123456789012:task-definition/my-app:42``.

    Returns
    -------
    str
        A YAML-formatted AppSpec document ready for CodeDeploy.

    Example
    -------
    ::

        version: 0.0
        Resources:
          - TargetService:
              Type: AWS::ECS::Service
              Properties:
                TaskDefinition: "arn:aws:ecs:..."
                LoadBalancerInfo:
                  ContainerName: "api-server"
                  ContainerPort: 8000
    """
    appspec = textwrap.dedent(f"""\
        version: 0.0
        # CodeDeploy AppSpec for ECS blue/green deployment of {service_name}
        # Generated by infra/deployment/ecs_deploy.py — do not edit manually.
        Resources:
          - TargetService:
              Type: AWS::ECS::Service
              Properties:
                TaskDefinition: "{task_definition_arn}"
                LoadBalancerInfo:
                  ContainerName: "{service_name}"
                  ContainerPort: 8000
        Hooks:
          - BeforeInstall: "{service_name}-BeforeInstall"
          - AfterInstall: "{service_name}-AfterInstall"
          - AfterAllowTestTraffic: "{service_name}-AfterAllowTestTraffic"
          - BeforeAllowTraffic: "{service_name}-BeforeAllowTraffic"
          - AfterAllowTraffic: "{service_name}-AfterAllowTraffic"
    """)
    return appspec
