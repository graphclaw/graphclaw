"""graphclaw.infra.iam.roles — IAM trust policies and role definitions for ECS containers.

Description
-----------
Defines the IAM trust policies and role metadata for each GraphClaw ECS Fargate
container that requires an AWS IAM task role.  Four of the seven containers need
explicit IAM roles; the remaining three (graph-db, relational-db, cache) have no
direct AWS API access and receive credentials only through the application layer
via the SecretsClient abstraction.

Design Patterns
---------------
- Data-driven configuration: Role names and policy file paths are expressed as
  plain dicts so they can be iterated by deployment scripts, CDK stacks, or
  Terraform modules without importing cloud SDKs at import time.
- Single source of truth: All role metadata lives here; ``__init__.py`` re-exports
  the public symbols so callers import from ``infra.iam`` directly.

Public API
----------
- ECS_TASK_TRUST_POLICY: Trust policy dict granting ecs-tasks.amazonaws.com the
  right to assume any of the container task roles.
- ROLE_NAMES: Mapping of container name to IAM role name string.
- POLICY_FILES: Mapping of container name to relative policy JSON file path.
- get_role_definition(container_name): Returns a dict with ``trust_policy`` and
  ``policy_file`` keys for the given container.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Trust policy — shared by all ECS task roles
# ---------------------------------------------------------------------------

ECS_TASK_TRUST_POLICY: dict = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

# ---------------------------------------------------------------------------
# Role names — one per container that needs direct AWS API access
# ---------------------------------------------------------------------------

ROLE_NAMES: dict[str, str] = {
    "agent-runtime": "graphclaw-agent-runtime-task-role",
    "channel-gateway": "graphclaw-channel-gateway-task-role",
    "trigger-engine": "graphclaw-trigger-engine-task-role",
    "api-server": "graphclaw-api-server-task-role",
}

# ---------------------------------------------------------------------------
# Policy file paths — relative to repository root
# ---------------------------------------------------------------------------

POLICY_FILES: dict[str, str] = {
    "agent-runtime": "infra/iam/policies/agent-runtime-policy.json",
    "channel-gateway": "infra/iam/policies/channel-gateway-policy.json",
    "trigger-engine": "infra/iam/policies/trigger-engine-policy.json",
    "api-server": "infra/iam/policies/api-server-policy.json",
}

# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

_VALID_CONTAINERS = frozenset(ROLE_NAMES)


def get_role_definition(container_name: str) -> dict:
    """Return the trust policy and policy file path for a container's IAM role.

    Parameters
    ----------
    container_name:
        One of ``"agent-runtime"``, ``"channel-gateway"``, ``"trigger-engine"``,
        or ``"api-server"``.

    Returns
    -------
    dict
        A dict with the following keys:

        - ``role_name`` (str): The IAM role name to create.
        - ``trust_policy`` (dict): The trust relationship document for
          ``aws iam create-role --assume-role-policy-document``.
        - ``policy_file`` (str): Relative path to the inline policy JSON file
          for ``aws iam put-role-policy --policy-document``.

    Raises
    ------
    ValueError
        If *container_name* is not one of the four containers with IAM roles.
    """
    if container_name not in _VALID_CONTAINERS:
        raise ValueError(
            f"No IAM role defined for container {container_name!r}. "
            f"Valid containers: {sorted(_VALID_CONTAINERS)}"
        )
    return {
        "role_name": ROLE_NAMES[container_name],
        "trust_policy": ECS_TASK_TRUST_POLICY,
        "policy_file": POLICY_FILES[container_name],
    }
