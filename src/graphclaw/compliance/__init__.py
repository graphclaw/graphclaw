"""graphclaw.compliance — GDPR compliance, audit logging, and data export.

Description
-----------
Provides the compliance layer for GraphClaw, covering GDPR right-to-erasure
requests, structured audit logging (PRD Section 32.3), and user data export.

Public API
----------
- GDPRService: Orchestrates erasure requests and anonymisation.
- AuditLogger: Writes and retrieves structured audit events from object storage.
- DataExportService: Exports all user data to S3 as a single JSON archive.
- ErasureRequest: Frozen dataclass representing a single erasure request.
- ErasureStatus: Enum of erasure lifecycle states.

Dependencies
------------
- graphclaw.compliance.gdpr: GDPRService.
- graphclaw.compliance.audit: AuditLogger.
- graphclaw.compliance.export: DataExportService.
- graphclaw.compliance.models: ErasureRequest, ErasureStatus.
"""

from __future__ import annotations

from graphclaw.compliance.audit import AuditLogger
from graphclaw.compliance.export import DataExportService
from graphclaw.compliance.gdpr import GDPRService
from graphclaw.compliance.models import ErasureRequest, ErasureStatus

__all__ = [
    "GDPRService",
    "AuditLogger",
    "DataExportService",
    "ErasureRequest",
    "ErasureStatus",
]
