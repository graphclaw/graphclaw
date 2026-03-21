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

"""graphclaw.infra.scaling.profiles — Container auto-scaling configuration profiles.

Description
-----------
Defines auto-scaling parameters for all seven GraphClaw ECS Fargate containers.
Profiles express intent (idle-to-zero, queue depth targets, cooldown periods) in
pure Python so they can be consumed by ECS Application Auto Scaling, KEDA
ScaledObject generators, or infrastructure-as-code tooling without importing any
cloud SDK at import time.

Design Patterns
---------------
- Data-driven configuration: All scaling knobs live here; deployment tooling
  iterates CONTAINER_SCALING_PROFILES to generate cloud resources.
- Frozen dataclasses: ScalingProfile instances are immutable after creation to
  prevent accidental mutation during multi-threaded IaC generation runs.

Public API
----------
- ScalingProfile: Frozen dataclass holding per-container scaling parameters.
- CONTAINER_SCALING_PROFILES: Dict mapping container name to ScalingProfile.
- get_scaling_config(container_name): Returns ScalingProfile or raises ValueError.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class ScalingProfile:
    """Auto-scaling configuration for a single container.

    Parameters
    ----------
    container_name:
        ECS service / Kubernetes Deployment name.
    min_tasks:
        Minimum running task count. Set to 0 for idle-to-zero behaviour.
    max_tasks:
        Maximum task count the auto-scaler may scale up to.
    scale_to_zero:
        When True the container may scale to zero tasks when idle.
        Only safe for stateless workloads (agent-runtime).
    cpu_target_pct:
        Target CPU utilisation percentage for ECS Application Auto Scaling
        step/target tracking policies. Ignored when queue_name is set.
    memory_target_pct:
        Target memory utilisation percentage (used by cache tier).
    queue_name:
        Environment variable name holding the queue URL / Redis stream key.
        When set, KEDA scales on queue depth rather than CPU.
    queue_depth_target:
        Number of queued items per desired replica (KEDA lagTarget).
    startup_jitter_seconds:
        Random delay (0..startup_jitter_seconds) added to each task's first
        invocation to prevent thundering-herd on scheduled briefing spikes.
    scale_in_cooldown_seconds:
        Seconds to wait after a scale-in event before evaluating another.
    scale_out_cooldown_seconds:
        Seconds to wait after a scale-out event before evaluating another.
    """

    container_name: str
    min_tasks: int
    max_tasks: int
    scale_to_zero: bool  # idle-to-zero policy
    cpu_target_pct: int = 70  # target CPU utilisation %
    memory_target_pct: int = 70
    # KEDA-style: scale on queue depth
    queue_name: str | None = None
    queue_depth_target: int | None = None  # tasks per replica
    # Jitter for briefing spike prevention (seconds)
    startup_jitter_seconds: int = 0
    # Cooldown periods
    scale_in_cooldown_seconds: int = 300
    scale_out_cooldown_seconds: int = 60


# ---------------------------------------------------------------------------
# Scaling profiles for all 7 GraphClaw containers
# ---------------------------------------------------------------------------

CONTAINER_SCALING_PROFILES: dict[str, ScalingProfile] = {
    "agent-runtime": ScalingProfile(
        container_name="agent-runtime",
        min_tasks=0,  # idle-to-zero: MANDATORY at 1000 users
        max_tasks=50,
        scale_to_zero=True,
        queue_name="AGENT_TASKS",
        queue_depth_target=1,  # 1 task per container
        scale_in_cooldown_seconds=120,
        scale_out_cooldown_seconds=10,
    ),
    "channel-gateway": ScalingProfile(
        container_name="channel-gateway",
        min_tasks=2,
        max_tasks=8,
        scale_to_zero=False,
        cpu_target_pct=60,
        scale_in_cooldown_seconds=300,
    ),
    "trigger-engine": ScalingProfile(
        container_name="trigger-engine",
        min_tasks=1,
        max_tasks=10,
        scale_to_zero=False,
        queue_name="TRIGGER_QUEUE",
        queue_depth_target=50,
        startup_jitter_seconds=30,  # prevent morning briefing spike
        scale_in_cooldown_seconds=300,
    ),
    "api-server": ScalingProfile(
        container_name="api-server",
        min_tasks=1,
        max_tasks=10,
        scale_to_zero=False,
        cpu_target_pct=70,
    ),
    "graph-db": ScalingProfile(
        container_name="graph-db",
        min_tasks=1,
        max_tasks=1,  # single primary; read replica separate
        scale_to_zero=False,
        cpu_target_pct=80,
    ),
    "relational-db": ScalingProfile(
        container_name="relational-db",
        min_tasks=1,
        max_tasks=1,
        scale_to_zero=False,
        cpu_target_pct=80,
    ),
    "cache": ScalingProfile(
        container_name="cache",
        min_tasks=3,  # Redis Cluster minimum
        max_tasks=9,
        scale_to_zero=False,
        memory_target_pct=75,
    ),
}

_VALID_CONTAINERS = frozenset(CONTAINER_SCALING_PROFILES)


def get_scaling_config(container_name: str) -> ScalingProfile:
    """Return ScalingProfile for *container_name*, raise ValueError if unknown.

    Parameters
    ----------
    container_name:
        One of the seven GraphClaw container names.

    Returns
    -------
    ScalingProfile
        The frozen scaling configuration for the requested container.

    Raises
    ------
    ValueError
        If *container_name* is not present in CONTAINER_SCALING_PROFILES.
    """
    if container_name not in _VALID_CONTAINERS:
        raise ValueError(
            f"No scaling profile defined for container {container_name!r}. "
            f"Valid containers: {sorted(_VALID_CONTAINERS)}"
        )
    return CONTAINER_SCALING_PROFILES[container_name]
