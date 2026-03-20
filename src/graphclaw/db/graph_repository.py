"""graphclaw.db.graph_repository — Shim re-exporting GraphRepository.

Description
-----------
Backward-compatibility shim.  All real implementation has moved to
``graphclaw.db.age.repository`` (as ``AgeGraphStore``).  Importing
``GraphRepository`` from this module continues to work without changes to
existing call sites.

Design Patterns
---------------
- Shim / Compatibility Layer: Re-exports keep the old import path alive while
  the implementation lives in the backend-specific package.

Public API
----------
- GraphRepository: Re-exported alias for AgeGraphStore from graphclaw.db._compat.

Dependencies
------------
- graphclaw.db._compat: GraphRepository alias.

Notes
-----
New code should import ``AgeGraphStore`` from ``graphclaw.db.age`` or use the
``create_graph_store`` factory from ``graphclaw.db``.
"""
from __future__ import annotations

from graphclaw.db._compat import GraphRepository

__all__ = ["GraphRepository"]
