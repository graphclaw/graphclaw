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

"""graphclaw.infra.scaling.ecs_task_definitions — ECS task definition builders.

Description
-----------
Generates ECS RegisterTaskDefinition request dicts (plain Python, no AWS SDK
required) for each GraphClaw container.  Callers can pass the returned dict
directly to ``boto3.client('ecs').register_task_definition(**td)`` or serialise
it to JSON for CDK / Terraform ``aws_ecs_task_definition`` data sources.

Design Patterns
---------------
- Pure data generation: No AWS SDK imports.  All cloud interactions belong in
  deployment scripts, not in this module.
- Single source of truth: CONTAINER_RESOURCES defines default CPU/memory for
  each container; callers can override when building environment-specific
  definitions.

Public API
----------
- build_task_definition(...): Returns an ECS RegisterTaskDefinition dict.
- CONTAINER_RESOURCES: Default CPU (CPU units) and memory (MB) per container.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Default resource allocations (ECS CPU units and memory in MiB)
# ---------------------------------------------------------------------------

CONTAINER_RESOURCES: dict[str, dict] = {
    "agent-runtime":   {"cpu": 1024, "memory_mb": 2048},
    "channel-gateway": {"cpu": 512,  "memory_mb": 1024},
    "trigger-engine":  {"cpu": 512,  "memory_mb": 1024},
    "api-server":      {"cpu": 512,  "memory_mb": 1024},
    "graph-db":        {"cpu": 2048, "memory_mb": 4096},
    "relational-db":   {"cpu": 1024, "memory_mb": 2048},
    "cache":           {"cpu": 512,  "memory_mb": 1024},
}


def build_task_definition(
    container_name: str,
    image_uri: str,
    cpu: int,
    memory_mb: int,
    env_vars: dict[str, str],
    role_arn: str,
    log_group: str,
) -> dict:
    """Return an ECS RegisterTaskDefinition request dict.

    The returned dict is suitable for use as keyword arguments to
    ``boto3.client('ecs').register_task_definition()`` or as input to CDK /
    Terraform ECS task definition resources.

    Parameters
    ----------
    container_name:
        Logical container name (e.g. ``"agent-runtime"``).  Used as both the
        task family name and the container definition name.
    image_uri:
        Full ECR image URI including tag, e.g.
        ``"123456789012.dkr.ecr.us-east-1.amazonaws.com/graphclaw/agent-runtime:v1.2.3"``.
    cpu:
        CPU units to allocate to the task (1024 = 1 vCPU).
    memory_mb:
        Memory in MiB to allocate to the task.
    env_vars:
        Environment variables injected into the container at launch time.
        Secrets should be referenced via ``secrets`` (AWS Secrets Manager) in
        production; plain env_vars are acceptable for non-sensitive config.
    role_arn:
        ARN of the ECS task role (``taskRoleArn``).  Use the role ARNs defined
        in ``infra/iam/roles.py``.
    log_group:
        CloudWatch Logs log group name for ``awslogs`` driver output.

    Returns
    -------
    dict
        ECS RegisterTaskDefinition request dict with keys: ``family``,
        ``networkMode``, ``requiresCompatibilities``, ``cpu``, ``memory``,
        ``taskRoleArn``, ``executionRoleArn``, and ``containerDefinitions``.

    Notes
    -----
    ``executionRoleArn`` is left as a placeholder string
    ``"arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole"``; callers should
    substitute the real account ID before registering.
    """
    family = f"graphclaw-{container_name}"
    environment = [{"name": k, "value": v} for k, v in sorted(env_vars.items())]

    container_def: dict = {
        "name": container_name,
        "image": image_uri,
        "essential": True,
        "environment": environment,
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group,
                "awslogs-region": "${AWS_REGION}",
                "awslogs-stream-prefix": container_name,
            },
        },
    }

    return {
        "family": family,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": str(cpu),
        "memory": str(memory_mb),
        "taskRoleArn": role_arn,
        # Execution role must be substituted by deployment tooling
        "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole",
        "containerDefinitions": [container_def],
    }
