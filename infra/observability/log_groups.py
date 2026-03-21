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

"""graphclaw.infra.observability.log_groups — CloudWatch log group configuration.

Description
-----------
Defines CloudWatch Logs log group configurations per PRD Section 32.2.
Agent-runtime logs are isolated per user to enforce data segregation; all other
platform containers use shared log groups.

Design Patterns
---------------
- Frozen dataclasses: LogGroupConfig instances are immutable.
- Data-driven: LOG_GROUP_CONFIGS drives IaC tooling; no AWS SDK at import time.

Public API
----------
- LogGroupConfig: Frozen dataclass for a single log group's metadata.
- LOG_GROUP_CONFIGS: List of all log group configurations.
- SCRUB_PATTERNS: Regex fragments that must be redacted before log storage.
- get_log_group_name(container, user_id): Resolves the log group name.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LogGroupConfig:
    """CloudWatch log group configuration for a single container tier.

    Parameters
    ----------
    name:
        Log group name or name prefix.  For per-user groups this is the
        prefix; the full name is ``{name}/{user_id}``.
    retention_days:
        CloudWatch log retention period in days.
    is_per_user:
        When True the log group is instantiated per user with the user_id
        appended as a path segment.  IaC tooling creates these groups on
        demand during user onboarding.
    scrub_patterns:
        List of regex patterns whose matches must be redacted before logs
        are durably stored.  Populated at the group level for custom
        per-container overrides; global patterns live in SCRUB_PATTERNS.
    """

    name: str  # e.g. /graphclaw/agent-runtime/{user_id} or /graphclaw/platform/channel-gateway
    retention_days: int = 30
    is_per_user: bool = False
    scrub_patterns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Log group definitions — PRD Sec 32.2
# ---------------------------------------------------------------------------

# Per PRD Sec 32.2: per-user groups for agent-runtime, shared for platform containers.
LOG_GROUP_CONFIGS: list[LogGroupConfig] = [
    # Platform containers — shared log groups
    LogGroupConfig("/graphclaw/platform/channel-gateway", retention_days=30),
    LogGroupConfig("/graphclaw/platform/trigger-engine",  retention_days=30),
    LogGroupConfig("/graphclaw/platform/api-server",      retention_days=30),
    LogGroupConfig("/graphclaw/platform/graph-db",        retention_days=7),
    # Per-user agent-runtime groups use pattern /graphclaw/agent-runtime/{user_id}
    LogGroupConfig("/graphclaw/agent-runtime", retention_days=90, is_per_user=True),
]

# ---------------------------------------------------------------------------
# Scrub patterns — PRD Sec 32.3
# ---------------------------------------------------------------------------

# These patterns must be matched and redacted (replaced with "[REDACTED]") by
# the log shipping layer before any log event is written to CloudWatch.
SCRUB_PATTERNS: list[str] = [
    "sk-ant-",       # Anthropic API keys
    "wg_agent_",     # GraphClaw agent API keys (A2A)
    "Bearer ",       # Authorization header values
    "password=",     # URL / query string passwords
    "secret=",       # Generic secret query params
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_PLATFORM_PREFIX = "/graphclaw/platform/"
_AGENT_RUNTIME_BASE = "/graphclaw/agent-runtime"

# Map container name -> log group config for fast lookup
_LOG_GROUP_BY_CONTAINER: dict[str, LogGroupConfig] = {}
for _cfg in LOG_GROUP_CONFIGS:
    # Derive container name from group name
    if _cfg.is_per_user:
        _LOG_GROUP_BY_CONTAINER["agent-runtime"] = _cfg
    else:
        # e.g. /graphclaw/platform/channel-gateway -> channel-gateway
        _container_key = _cfg.name.split("/")[-1]
        _LOG_GROUP_BY_CONTAINER[_container_key] = _cfg


def get_log_group_name(container: str, user_id: str | None = None) -> str:
    """Return the CloudWatch log group name for a container.

    For per-user containers (``agent-runtime``) a *user_id* is required and
    is appended as the final path segment.  For shared platform containers
    the *user_id* argument is ignored.

    Parameters
    ----------
    container:
        Container name, e.g. ``"agent-runtime"`` or ``"channel-gateway"``.
    user_id:
        GraphClaw user identifier.  Required when ``is_per_user=True`` for
        the container; ignored otherwise.

    Returns
    -------
    str
        Fully resolved log group name.

    Raises
    ------
    ValueError
        If *container* is unknown, or if *user_id* is required but not given.
    """
    cfg = _LOG_GROUP_BY_CONTAINER.get(container)
    if cfg is None:
        raise ValueError(
            f"No log group configured for container {container!r}. "
            f"Valid containers: {sorted(_LOG_GROUP_BY_CONTAINER)}"
        )

    if cfg.is_per_user:
        if not user_id:
            raise ValueError(
                f"Container {container!r} uses per-user log groups; "
                "user_id is required."
            )
        return f"{cfg.name}/{user_id}"

    return cfg.name
