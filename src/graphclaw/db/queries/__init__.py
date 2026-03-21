"""graphclaw.db.queries — Shim re-exporting from db.age.queries.

Description
-----------
Backward-compatibility shim.  All real implementation has moved to
``graphclaw.db.age.queries``.  Importing query functions from this module
continues to work without changes to existing call sites.

Design Patterns
---------------
- Shim / Compatibility Layer: Re-exports keep the old import path alive while
  the implementation lives in the backend-specific package.

Public API
----------
- find_critical_path: Re-exported from graphclaw.db.age.queries.critical_path.
- get_blocked_root_causes: Re-exported from graphclaw.db.age.queries.dependencies.
- get_downstream_dependents: Re-exported from graphclaw.db.age.queries.dependencies.
- get_upstream_blockers: Re-exported from graphclaw.db.age.queries.dependencies.
- get_active_tasks_for_scoring: Re-exported from graphclaw.db.age.queries.scoring_queries.
- get_assigned_resource: Re-exported from graphclaw.db.age.queries.scoring_queries.
- get_constraints_for_task: Re-exported from graphclaw.db.age.queries.scoring_queries.

Dependencies
------------
- graphclaw.db.age.queries: The real implementation package.

Notes
-----
New code should import directly from ``graphclaw.db.age.queries`` or use the
``AgeGraphQueryEngine`` class.
"""

from __future__ import annotations

from graphclaw.db.age.queries.critical_path import find_critical_path
from graphclaw.db.age.queries.dependencies import (
    get_blocked_root_causes,
    get_downstream_dependents,
    get_upstream_blockers,
)
from graphclaw.db.age.queries.scoring_queries import (
    get_active_tasks_for_scoring,
    get_assigned_resource,
    get_constraints_for_task,
)

__all__ = [
    "find_critical_path",
    "get_blocked_root_causes",
    "get_downstream_dependents",
    "get_upstream_blockers",
    "get_active_tasks_for_scoring",
    "get_assigned_resource",
    "get_constraints_for_task",
]
