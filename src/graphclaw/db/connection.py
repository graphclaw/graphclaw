"""graphclaw.db.connection — Shim re-exporting from db.age.connection.

Description
-----------
Backward-compatibility shim.  All real implementation has moved to
``graphclaw.db.age.connection``.  Importing from this module continues to work
without changes to existing call sites.

Design Patterns
---------------
- Shim / Compatibility Layer: Re-exports keep the old import path alive while
  the implementation lives in the backend-specific package.

Public API
----------
- create_pool: Re-exported from graphclaw.db.age.connection.
- get_connection: Re-exported from graphclaw.db.age.connection.
- _setup_age: Re-exported from graphclaw.db.age.connection.

Dependencies
------------
- graphclaw.db.age.connection: The real implementation module.

Notes
-----
New code should import directly from ``graphclaw.db.age.connection`` or use
``graphclaw.db.create_pool`` / ``graphclaw.db.get_connection``.
"""

from __future__ import annotations

from graphclaw.db.age.connection import _setup_age, create_pool, get_connection

__all__ = ["create_pool", "get_connection", "_setup_age"]
