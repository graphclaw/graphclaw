# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.db.age.utils — Shared AGE/Cypher utility functions and constants.

Description
-----------
Provides the canonical implementations of the three helper functions and the
graph name constant used across all database modules (graph_repository,
dependencies, critical_path, scoring_queries).  Centralising them here eliminates
copy-paste duplication and ensures consistent escaping and agtype parsing behaviour
across all query modules.

Design Patterns
---------------
- Utility Module: Pure functions with no imports from the graphclaw domain;
  safe to import from any layer without circular dependency risk.

Public API
----------
- GRAPH_NAME: Name of the AGE property graph (``"graphclaw"``).
- _parse_agtype: Convert an AGE agtype column value to a native Python object.
- _escape: Escape a string for safe embedding inside Cypher string literals.
- _extract_properties: Pull the ``properties`` dict out of a parsed AGE vertex/edge.

Dependencies
------------
- json: Used by _parse_agtype.

Notes
-----
``_escape`` must be applied to every user-supplied string before embedding it in
a Cypher query because AGE does not support ``$1`` bind parameters inside ``$$``
blocks.  The order of replacements (backslash first, then single quote) is critical:
reversing the order would cause existing backslashes to be double-escaped.
"""

from __future__ import annotations

import json
from typing import Any

# Name of the AGE property graph — must match the graph created via
# ``SELECT create_graph('graphclaw')``.
GRAPH_NAME = "graphclaw"


def _parse_agtype(value: Any) -> Any:
    """Convert an agtype column value to a native Python object.

    psycopg represents agtype as a string with an optional ``::vertex``,
    ``::edge``, or other type suffix.  We strip the suffix, then parse
    the remaining JSON.
    """
    if value is None:
        return None
    raw = str(value)
    # Strip AGE type suffixes like ::vertex, ::edge, ::path
    for suffix in ("::vertex", "::edge", "::path"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Scalar strings from AGE are sometimes unquoted — return as-is.
        return raw


def _escape(value: str) -> str:
    """Escape a string value for safe embedding inside Cypher string literals.

    AGE does not support parameterised queries (``$1``, ``%(name)s``) inside
    ``$$ ... $$`` blocks — every value must be inlined as a literal.  This
    function prevents Cypher injection by escaping the two characters that
    would otherwise break out of a single-quoted string:

    - Backslash (``\\``) is doubled first so it doesn't accidentally escape
      the quote that follows.
    - Single quote (``'``) is backslash-escaped.

    The returned value is safe to embed as ``'<escaped_value>'`` in Cypher.
    """
    # Order matters: escape backslashes before quotes so that an existing
    # backslash is not interpreted as an escape for the single-quote step.
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _extract_properties(agtype_node: Any) -> dict:
    """Pull the ``properties`` dict out of a parsed AGE vertex/edge object."""
    parsed = _parse_agtype(agtype_node)
    if isinstance(parsed, dict):
        return parsed.get("properties", parsed)
    return {}
