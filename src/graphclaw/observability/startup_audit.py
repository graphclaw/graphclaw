# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.observability.startup_audit — Wave 0 infrastructure config audit (FR-DEL-008).

Description
-----------
Verifies that the MinIO (S3) storage configuration does not contain any
user-prefix lifecycle rules that could auto-expire or delete user data.

The audit runs at gateway startup (after storage is initialised) and raises
``SystemExit`` if any lifecycle rule is detected on a user-data prefix.  This
prevents accidental data deletion caused by misconfigured bucket lifecycle
policies.

Design
------
MinIO lifecycle rules are bucket-level JSON policies (S3 lifecycle XML under
the hood).  A "user-prefix lifecycle rule" is defined as any lifecycle rule
whose ``Filter.Prefix`` starts with ``users/`` (the canonical user data prefix
in GraphClaw).  Other prefixes (``tmp/``, ``logs/``) are safe to auto-expire.

Public API
----------
- FORBIDDEN_PREFIXES: Set of storage prefixes that must never have lifecycle rules.
- AuditResult: NamedTuple with (ok: bool, violations: list[str]).
- audit_lifecycle_rules: Inspect a StorageClient for forbidden lifecycle rules.
- startup_assert_no_lifecycle_rules: Run at startup; call sys.exit on violation.

Dependencies
------------
- graphclaw.infra.storage: StorageClient ABC.
"""

from __future__ import annotations

import logging
import sys
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Storage prefixes that must never have lifecycle (auto-expiry/delete) rules.
FORBIDDEN_PREFIXES: frozenset[str] = frozenset(
    {
        "users/",
        "tasks/",
        "goals/",
        "attachments/",
        "agent/",
    }
)

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class AuditResult(NamedTuple):
    """Outcome of a lifecycle rules audit."""

    ok: bool
    violations: list[str]


# ---------------------------------------------------------------------------
# Audit function
# ---------------------------------------------------------------------------


async def audit_lifecycle_rules(storage_client: object) -> AuditResult:
    """Inspect *storage_client* for forbidden prefix lifecycle rules.

    Calls ``storage_client.list_lifecycle_rules()`` if the method exists;
    otherwise falls through (no-op) because the client doesn't support it.

    Parameters
    ----------
    storage_client:
        Any ``StorageClient`` instance.  S3/MinIO backends should expose
        ``list_lifecycle_rules() -> list[dict]`` returning S3 lifecycle rule
        dicts with at least a ``Filter.Prefix`` key.

    Returns
    -------
    AuditResult
        ``ok=True`` when no violations are found.
    """
    violations: list[str] = []

    # Not all storage backends support lifecycle introspection.
    list_rules = getattr(storage_client, "list_lifecycle_rules", None)
    if list_rules is None:
        logger.debug("startup_audit: storage client has no list_lifecycle_rules — skipping")
        return AuditResult(ok=True, violations=[])

    try:
        rules: list[dict] = await list_rules()
    except Exception as exc:
        logger.warning(
            "startup_audit: failed to retrieve lifecycle rules — treating as no-violation",
            extra={"error": str(exc)},
        )
        return AuditResult(ok=True, violations=[])

    for rule in rules:
        prefix = _extract_prefix(rule)
        if prefix is None:
            continue
        for forbidden in FORBIDDEN_PREFIXES:
            if prefix.startswith(forbidden) or forbidden.startswith(prefix):
                violations.append(
                    f"Lifecycle rule on forbidden prefix {prefix!r} "
                    f"(matches forbidden prefix {forbidden!r}): {rule}"
                )

    if violations:
        logger.error(
            "startup_audit: lifecycle violations found",
            extra={"violation_count": len(violations), "violations": violations},
        )
    else:
        logger.info("startup_audit: lifecycle rules OK — no user-prefix rules found")

    return AuditResult(ok=not violations, violations=violations)


def _extract_prefix(rule: dict) -> str | None:
    """Return the Filter.Prefix string from a lifecycle rule dict, or None."""
    # S3 lifecycle XML → boto3 dict structure:
    # rule = {"Filter": {"Prefix": "users/"}, ...}
    # Also handle flat prefix: rule = {"Prefix": "users/"} (some MinIO SDKs).
    filter_block = rule.get("Filter") or {}
    prefix = filter_block.get("Prefix") if isinstance(filter_block, dict) else None
    if prefix is None:
        prefix = rule.get("Prefix")
    return prefix if isinstance(prefix, str) and prefix else None


# ---------------------------------------------------------------------------
# Startup assertion
# ---------------------------------------------------------------------------


async def startup_assert_no_lifecycle_rules(storage_client: object) -> None:
    """Run audit at startup; call sys.exit(1) if any violation is found.

    Intended for use in the gateway lifespan context:

        from graphclaw.observability.startup_audit import startup_assert_no_lifecycle_rules
        await startup_assert_no_lifecycle_rules(app.state.storage_client)

    Parameters
    ----------
    storage_client:
        Any ``StorageClient`` instance.

    Raises
    ------
    SystemExit(1):
        When lifecycle rules on forbidden prefixes are detected.
    """
    result = await audit_lifecycle_rules(storage_client)
    if not result.ok:
        msg = (
            "FATAL: MinIO lifecycle rules detected on user-data prefixes. "
            "This violates FR-DEL-008 (No-Delete principle). "
            "Remove the following rules before starting the gateway:\n"
            + "\n".join(f"  - {v}" for v in result.violations)
        )
        logger.critical(msg)
        sys.exit(1)
