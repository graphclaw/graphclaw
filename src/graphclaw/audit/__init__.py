# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.audit — Immutable audit-log package.

Provides an append-only, tamper-evident audit log (FR-DEL-006/007/003).

Exports
-------
- AuditLog: main append-only log class backed by MinIO/S3.
- AuditEventType: well-known event types.
- AuditEntry: Pydantic model for a single log entry.
"""

from graphclaw.audit.immutable_log import AuditEntry, AuditEventType, AuditLog

__all__ = ["AuditEntry", "AuditEventType", "AuditLog"]
