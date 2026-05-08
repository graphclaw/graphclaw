# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.migrations — Forward-only schema migration system.

Re-exports the public API from the sub-modules so callers can import directly
from ``graphclaw.migrations``.

Public API
----------
- MigrationRunner: Async runner that applies migrations to a PostgreSQL database.
- Migration: Frozen dataclass representing a single forward-only migration.
- MigrationStatus: Enum of possible migration run outcomes.
- MigrationError: Exception raised when a migration cannot be applied.
"""

# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

from graphclaw.migrations.models import Migration, MigrationError, MigrationStatus
from graphclaw.migrations.runner import MigrationRunner

__all__ = [
    "MigrationRunner",
    "Migration",
    "MigrationStatus",
    "MigrationError",
]
