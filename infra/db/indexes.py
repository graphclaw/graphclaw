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

"""infra.db.indexes — AGE performance index catalogue and query timeout helpers.

Description
-----------
Documents the production JSONB property indexes applied to the Apache AGE
vertex tables in migration 0004.  Each entry carries a ``rationale`` key
so the purpose of every index is self-evident during code review and schema
audits.

Also exposes ``QUERY_TIMEOUT_MS`` and ``get_set_timeout_sql`` for use by
the connection layer to enforce the 5-second hard query timeout mandated by
PRD Sec 28.11.

Design Patterns
---------------
- Catalogue Pattern: ``AGE_PRODUCTION_INDEXES`` is a list-of-dicts rather
  than individual constants so tooling can iterate over all indexes without
  knowing them by name.

Public API
----------
- AGE_PRODUCTION_INDEXES: Catalogue of index definitions with rationale.
- QUERY_TIMEOUT_MS: Hard per-statement timeout in milliseconds.
- get_set_timeout_sql: Returns the SQL string to set the session timeout.

Dependencies
------------
None — this module is pure Python constants.

Notes
-----
Indexes are applied via migration 0004 (``scripts/migrations/0004_age_indexes.sql``).
This file serves as the canonical source of truth so the migration SQL and
the documented rationale stay in sync.  When adding a new index, update
both this catalogue and the corresponding migration.
"""
from __future__ import annotations


# AGE performance indexes per PRD Sec 28.11
# These are applied via migration 0004 but also documented here
AGE_PRODUCTION_INDEXES: list[dict] = [
    {
        "name": "idx_graphclaw_state",
        "column": "properties->>'state'",
        "method": "btree",
        "rationale": "Filter tasks by state (PENDING/IN_PROGRESS/COMPLETE)",
    },
    {
        "name": "idx_graphclaw_owner_id",
        "column": "properties->>'owner_id'",
        "method": "btree",
        "rationale": "Fetch all tasks for a user efficiently",
    },
    {
        "name": "idx_graphclaw_due_date",
        "column": "properties->>'due_date'",
        "method": "btree",
        "rationale": "Range queries for overdue/upcoming tasks",
    },
    {
        "name": "idx_graphclaw_score",
        "column": "(properties->>'score')::numeric",
        "method": "btree",
        "rationale": "Sort by priority score for briefing generation",
    },
]

QUERY_TIMEOUT_MS = 5000   # 5-second hard timeout per PRD Sec 28.11


def get_set_timeout_sql() -> str:
    """Return SQL to set statement_timeout for the session.

    Returns
    -------
    str
        A ``SET statement_timeout`` SQL statement using ``QUERY_TIMEOUT_MS``.

    Examples
    --------
    >>> get_set_timeout_sql()
    'SET statement_timeout = 5000;'
    """
    return f"SET statement_timeout = {QUERY_TIMEOUT_MS};"
