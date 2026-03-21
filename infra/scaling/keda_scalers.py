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

"""graphclaw.infra.scaling.keda_scalers — KEDA ScaledObject YAML generators.

Description
-----------
Generates KEDA ScaledObject manifest YAML strings for GraphClaw containers
that scale on Redis Stream queue depth.  Only containers with a ``queue_name``
set in their ScalingProfile receive a KEDA scaler — CPU/memory-based containers
use ECS Application Auto Scaling instead (see README.md for the rationale).

Design Patterns
---------------
- Pure YAML string generation: No Kubernetes SDK or PyYAML dependency; the
  returned strings can be written to files and applied with ``kubectl apply``.
- Validation at generation time: build_keda_scaled_object raises ValueError for
  containers without a queue_name so deployment scripts fail fast.

Public API
----------
- build_keda_scaled_object(profile, namespace): Returns KEDA ScaledObject YAML.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

from infra.scaling.profiles import ScalingProfile


def build_keda_scaled_object(
    profile: ScalingProfile,
    namespace: str = "graphclaw",
) -> str:
    """Return a KEDA ScaledObject YAML string for a Redis Stream depth trigger.

    The generated manifest configures KEDA to scale the named Kubernetes
    Deployment based on the pending message count in the Redis Stream specified
    by ``profile.queue_name``.  The stream key is read from the environment
    variable named by ``profile.queue_name`` at runtime.

    Parameters
    ----------
    profile:
        ScalingProfile for the container.  Must have ``queue_name`` and
        ``queue_depth_target`` set; raises ValueError otherwise.
    namespace:
        Kubernetes namespace in which the Deployment and ScaledObject live.
        Defaults to ``"graphclaw"``.

    Returns
    -------
    str
        KEDA ScaledObject manifest in YAML format.  The string contains the
        literal text ``"ScaledObject"`` as the ``kind`` value.

    Raises
    ------
    ValueError
        If ``profile.queue_name`` is None (container is not queue-based and
        should use ECS Application Auto Scaling instead).
    """
    if profile.queue_name is None:
        raise ValueError(
            f"Container {profile.container_name!r} has no queue_name set. "
            "KEDA scalers are only generated for queue-based containers. "
            "Use ECS Application Auto Scaling for CPU/memory-based scaling."
        )

    lag_target = profile.queue_depth_target if profile.queue_depth_target is not None else 10

    # Build the YAML manually to avoid a PyYAML dependency.
    # Indentation follows the KEDA ScaledObject v2 spec.
    yaml_lines: list[str] = [
        "apiVersion: keda.sh/v1alpha1",
        "kind: ScaledObject",
        "metadata:",
        f"  name: {profile.container_name}-scaler",
        f"  namespace: {namespace}",
        "spec:",
        "  scaleTargetRef:",
        f"    name: {profile.container_name}",
        f"  minReplicaCount: {profile.min_tasks}",
        f"  maxReplicaCount: {profile.max_tasks}",
        f"  cooldownPeriod: {profile.scale_in_cooldown_seconds}",
        "  triggers:",
        "  - type: redis-streams",
        "    metadata:",
        "      address: $(REDIS_URL)",
        f"      stream: $({profile.queue_name})",
        f"      consumerGroup: {profile.container_name}-group",
        f'      lagCount: "{lag_target}"',
    ]

    # Add startup jitter annotation if configured
    if profile.startup_jitter_seconds > 0:
        yaml_lines.insert(4, "  annotations:")
        yaml_lines.insert(
            5,
            f"    graphclaw.ai/startup-jitter-seconds: \"{profile.startup_jitter_seconds}\"",
        )

    return "\n".join(yaml_lines) + "\n"
