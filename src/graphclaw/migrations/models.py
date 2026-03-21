"""graphclaw.migrations.models — Migration data models.

Description
-----------
Defines ``Migration``, ``MigrationStatus``, and ``MigrationError`` — the core
data types for the GraphClaw forward-only schema migration system.

Design Patterns
---------------
- Frozen dataclass: ``Migration`` is immutable by design; version strings and
  SQL are fixed at definition time and must not be mutated at runtime.
- Forward-only: There is no ``sql_down`` field.  The PRD (Section 32) requires
  forward-only, non-destructive migrations to support rolling deployments.

Public API
----------
- MigrationStatus: Enum of possible migration run outcomes.
- Migration: Frozen dataclass representing a single schema migration.
- MigrationError: Exception raised when a migration cannot be applied.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MigrationStatus(str, Enum):
    """Possible outcomes of attempting to apply a single migration."""

    PENDING = "PENDING"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # already applied (idempotent re-run)


class MigrationError(Exception):
    """Raised when a migration cannot be applied.

    This covers two cases:
    1. A migration with ``is_destructive=True`` is passed to
       :meth:`~graphclaw.migrations.runner.MigrationRunner.apply` — destructive
       migrations are forbidden by the PRD forward-only policy.
    2. The underlying DDL statement fails at the database level — the exception
       is caught, the failure is recorded in ``graphclaw_migrations``, and
       ``MigrationError`` is re-raised with the original cause attached.
    """


@dataclass(frozen=True)
class Migration:
    """A single forward-only schema migration.

    Attributes
    ----------
    version:
        Zero-padded 4-digit string that determines application order,
        e.g. ``"0001"``, ``"0002"``.
    name:
        Short snake_case descriptor, e.g. ``"add_visibility_grant_node"``.
    description:
        Human-readable sentence explaining the purpose of this migration.
    sql_up:
        Forward DDL to execute (``ALTER TABLE``, ``CREATE INDEX``, AGE label
        creation calls, etc.).  Must be non-destructive.
    is_destructive:
        Must remain ``False``.  Set to ``True`` only to signal an error
        condition — ``MigrationRunner.apply`` will raise ``MigrationError``
        before executing any SQL.
    applied_at:
        Populated by ``MigrationRunner`` after a successful apply; ``None``
        in the catalogue definition.
    """

    version: str  # e.g. "0001", "0002" — zero-padded 4 digits
    name: str  # e.g. "add_visibility_grant_node"
    description: str
    sql_up: str  # forward-only DDL
    # No sql_down — PRD requires forward-only, non-destructive migrations
    is_destructive: bool = False  # must be False; raise if True at apply time
    applied_at: datetime | None = None
