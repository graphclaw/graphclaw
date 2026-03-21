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

"""graphclaw.infra.scaling — Container auto-scaling configuration.

Re-exports the public API so callers import from ``infra.scaling`` directly.
"""

from __future__ import annotations

from infra.scaling.profiles import (
    CONTAINER_SCALING_PROFILES,
    ScalingProfile,
    get_scaling_config,
)

__all__ = [
    "ScalingProfile",
    "get_scaling_config",
    "CONTAINER_SCALING_PROFILES",
]
