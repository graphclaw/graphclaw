# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.db.age.queries — Specialised graph query functions for the AGE backend.

Description
-----------
Re-exports all public query functions from the AGE query modules so callers
can do ``from graphclaw.db.age.queries import find_critical_path`` without
knowing the internal file layout.

Design Patterns
---------------
- Facade: Aggregates exports from sibling modules behind a single import surface.

Public API
----------
- find_critical_path: Longest-path (critical path) query for a goal.
- get_blocked_root_causes: Root causes for every BLOCKED task.
- get_downstream_dependents: All tasks that transitively depend on a node.
- get_upstream_blockers: All tasks a node transitively depends on.
- get_active_tasks_for_scoring: Non-terminal, non-snoozed tasks for a user.
- get_assigned_resource: ResourceNode assigned to a task.
- get_constraints_for_task: ConstraintNodes linked to a task.

Dependencies
------------
- graphclaw.db.age.queries.critical_path: find_critical_path.
- graphclaw.db.age.queries.dependencies: dependency traversal functions.
- graphclaw.db.age.queries.scoring_queries: scoring support functions.

Notes
-----
None.
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
