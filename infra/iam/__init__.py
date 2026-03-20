"""graphclaw.infra.iam — IAM role definitions and policy helpers for ECS containers.

Description
-----------
Re-exports the public symbols from ``roles`` so callers can import directly from
``infra.iam`` without knowing the internal module layout.

Public API
----------
- ECS_TASK_TRUST_POLICY: Trust policy document for ECS Fargate task roles.
- ROLE_NAMES: Mapping of container name to IAM role name.
- POLICY_FILES: Mapping of container name to policy JSON file path.
- get_role_definition: Helper returning full role config dict for a container.
"""

from infra.iam.roles import (  # noqa: F401
    ECS_TASK_TRUST_POLICY,
    POLICY_FILES,
    ROLE_NAMES,
    get_role_definition,
)

__all__ = [
    "ECS_TASK_TRUST_POLICY",
    "POLICY_FILES",
    "ROLE_NAMES",
    "get_role_definition",
]
