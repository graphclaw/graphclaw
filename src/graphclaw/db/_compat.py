"""graphclaw.db._compat — Backward-compatibility alias for GraphRepository.

Description
-----------
Provides the ``GraphRepository`` name as a direct alias for ``AgeGraphStore``
so that existing code importing ``GraphRepository`` continues to work without
modification.  No deprecation warning is raised on import or construction —
the alias is transparent to existing callers.

Design Patterns
---------------
- Alias: A simple name binding so isinstance checks, type annotations, and
  construction calls all work without changes.

Public API
----------
- GraphRepository: Alias for AgeGraphStore.

Dependencies
------------
- graphclaw.db.age.repository: AgeGraphStore (the real implementation).

Notes
-----
This module exists solely to preserve backward compatibility.  New code should
import ``AgeGraphStore`` directly from ``graphclaw.db.age`` or use the
``create_graph_store`` factory.
"""

from __future__ import annotations

from graphclaw.db.age.repository import AgeGraphStore

# Direct alias so isinstance checks and existing code work without changes.
GraphRepository = AgeGraphStore
