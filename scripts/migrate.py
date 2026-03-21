#!/usr/bin/env python3
"""Run GraphClaw database migrations.

Usage
-----
::

    DATABASE_URL=postgresql://graphclaw:graphclaw@localhost:5432/graphclaw \\
        python scripts/migrate.py

Environment Variables
---------------------
DATABASE_URL:
    psycopg3-compatible DSN for the target database.
    Defaults to ``postgresql://graphclaw:graphclaw@localhost:5432/graphclaw``.

Exit Codes
----------
0   All migrations applied or already up to date.
1   One or more migrations failed.
"""

# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from graphclaw.migrations.catalogue import MIGRATIONS
from graphclaw.migrations.models import MigrationStatus
from graphclaw.migrations.runner import MigrationRunner


async def main() -> int:
    """Apply all pending migrations and print a status line for each.

    Returns
    -------
    int
        0 if all migrations succeeded (or were skipped), 1 if any failed.
    """
    conn = os.environ.get(
        "DATABASE_URL",
        "postgresql://graphclaw:graphclaw@localhost:5432/graphclaw",
    )
    runner = MigrationRunner(conn)
    await runner.ensure_migrations_table()
    results = await runner.apply_all(MIGRATIONS)

    failed = False
    for migration, status in results:
        print(f"[{status.value:8}] {migration.version} — {migration.name}")
        if status == MigrationStatus.FAILED:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
