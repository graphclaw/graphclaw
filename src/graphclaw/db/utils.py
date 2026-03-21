"""graphclaw.db.utils — Shim re-exporting from db.age.utils.

Description
-----------
Backward-compatibility shim.  All real implementation has moved to
``graphclaw.db.age.utils``.  Importing from this module continues to work
without changes to existing call sites.

Design Patterns
---------------
- Shim / Compatibility Layer: Re-exports keep the old import path alive while
  the implementation lives in the backend-specific package.

Public API
----------
- GRAPH_NAME: Re-exported from graphclaw.db.age.utils.
- _parse_agtype: Re-exported from graphclaw.db.age.utils.
- _escape: Re-exported from graphclaw.db.age.utils.
- _extract_properties: Re-exported from graphclaw.db.age.utils.

Dependencies
------------
- graphclaw.db.age.utils: The real implementation module.

Notes
-----
New code should import directly from ``graphclaw.db.age.utils``.
"""

from __future__ import annotations

from graphclaw.db.age.utils import GRAPH_NAME, _escape, _extract_properties, _parse_agtype

__all__ = ["GRAPH_NAME", "_parse_agtype", "_escape", "_extract_properties"]
