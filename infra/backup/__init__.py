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

"""graphclaw.infra.backup — Backup policies, recovery runbooks, and AWS stack descriptors.

Description
-----------
Re-exports the public symbols from the ``backup`` submodules so callers can
import directly from ``infra.backup`` without knowing the internal module
layout.

Public API
----------
- BackupConfig: Frozen dataclass describing a single backup target's policy.
- RecoveryRunbook: Frozen dataclass representing a full disaster-recovery procedure.
- build_backup_stack: Assemble the full backup stack descriptor dict.
- BACKUP_CONFIGS: Ordered list of :class:`BackupConfig` for all four targets.
"""

from __future__ import annotations

from infra.backup.configs import BACKUP_CONFIGS  # noqa: F401
from infra.backup.models import BackupConfig, RecoveryRunbook  # noqa: F401
from infra.backup.stack import build_backup_stack  # noqa: F401

__all__ = [
    "BackupConfig",
    "RecoveryRunbook",
    "build_backup_stack",
    "BACKUP_CONFIGS",
]
