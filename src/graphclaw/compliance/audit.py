"""graphclaw.compliance.audit — Structured audit logging for compliance (PRD 32.3).

Description
-----------
Provides ``AuditLogger``, which persists structured audit event records to
object storage and optionally publishes them to a message broker queue for
real-time consumption.  Log entries are organised by user and month so they
can be efficiently listed and filtered.

Sensitive values (API keys, bearer tokens, passwords) are stripped from all
audit payloads before writing via ``scrub_sensitive``, satisfying the log
scrubbing requirement in PRD Section 32.3.

Design Patterns
---------------
- Dependency Injection: ``StorageClient`` and optional ``MessageBroker`` are
  injected so the logger is backend-agnostic and fully testable.
- Path Convention: ``audit/{user_id}/{YYYY-MM}/{event_id}.json`` keeps per-user
  audit trails partitioned by month, enabling efficient range queries via
  prefix listing.

Public API
----------
- AuditLogger: Main class — log, get_events, scrub_sensitive.

Dependencies
------------
- graphclaw.compliance.models: AuditEvent.
- graphclaw.infra.storage: StorageClient ABC.
- graphclaw.infra.broker: MessageBroker ABC, AUDIT_EVENTS queue name.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from graphclaw.compliance.models import AuditEvent
from graphclaw.infra.storage import StorageClient

logger = logging.getLogger(__name__)

# Queue name for audit events published to the message broker.
AUDIT_EVENTS: str = "audit_events"

# ---------------------------------------------------------------------------
# Sensitive value patterns for scrubbing (PRD Section 32.3)
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
)

_SENSITIVE_VALUE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"sk-ant-", re.IGNORECASE),
    re.compile(r"wg_agent_", re.IGNORECASE),
    re.compile(r"Bearer ", re.IGNORECASE),
)


def _key_is_sensitive(key: str) -> bool:
    return any(p.search(key) for p in _SENSITIVE_KEY_PATTERNS)


def _value_is_sensitive(value: str) -> bool:
    return any(p.search(value) for p in _SENSITIVE_VALUE_PATTERNS)


class AuditLogger:
    """Writes and retrieves structured audit events from object storage.

    Parameters
    ----------
    storage:
        Object-storage backend used to persist ``AuditEvent`` records.
    broker:
        Optional message broker.  When provided, each logged event is also
        published to the ``AUDIT_EVENTS`` queue for real-time consumers.
    """

    def __init__(
        self,
        storage: StorageClient,
        broker: object | None = None,  # MessageBroker | None
    ) -> None:
        self._storage = storage
        self._broker = broker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def log(self, event: AuditEvent) -> None:
        """Persist *event* to object storage and optionally publish to broker.

        The object is written to
        ``audit/{user_id}/{YYYY-MM}/{event_id}.json``.

        Parameters
        ----------
        event:
            The ``AuditEvent`` to persist.  Metadata is scrubbed of
            sensitive values before serialisation.
        """
        scrubbed_metadata = self.scrub_sensitive(dict(event.metadata))
        payload: dict = {
            "event_id": event.event_id,
            "user_id": event.user_id,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "timestamp": event.timestamp.isoformat(),
            "ip_address": event.ip_address,
            "metadata": scrubbed_metadata,
        }
        month_prefix = event.timestamp.strftime("%Y-%m")
        path = f"audit/{event.user_id}/{month_prefix}/{event.event_id}.json"
        data = json.dumps(payload, default=str).encode()
        await self._storage.write(path, data, content_type="application/json")
        logger.debug(
            "audit: wrote event",
            extra={"event_id": event.event_id, "action": event.action},
        )

        if self._broker is not None:
            try:
                await self._broker.publish(AUDIT_EVENTS, json.dumps(payload, default=str))
            except Exception:  # noqa: BLE001
                logger.warning(
                    "audit: broker publish failed for event_id=%s",
                    event.event_id,
                    exc_info=True,
                )

    async def get_events(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        action_filter: str | None = None,
    ) -> list[AuditEvent]:
        """Return audit events for *user_id* between *start* and *end*.

        Lists objects under ``audit/{user_id}/`` for each calendar month in
        the requested range, loads each JSON file, and filters by timestamp
        and optional *action_filter*.

        Parameters
        ----------
        user_id:
            The user whose audit trail to retrieve.
        start:
            Inclusive lower bound (timezone.utc).
        end:
            Exclusive upper bound (timezone.utc).
        action_filter:
            When provided, only events whose ``action`` equals this string
            are returned.

        Returns
        -------
        list[AuditEvent]
            Matching events sorted by timestamp ascending.
        """
        # Build the set of YYYY-MM month prefixes to scan.
        months: set[str] = set()
        cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cursor < end:
            months.add(cursor.strftime("%Y-%m"))
            # Advance by one month
            if cursor.month == 12:
                cursor = cursor.replace(year=cursor.year + 1, month=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1)

        events: list[AuditEvent] = []
        for month in sorted(months):
            prefix = f"audit/{user_id}/{month}/"
            try:
                keys = await self._storage.list_objects(prefix)
            except Exception:  # noqa: BLE001
                logger.warning("audit: list_objects failed for prefix=%s", prefix, exc_info=True)
                continue

            for key in keys:
                try:
                    raw = await self._storage.read(key)
                    payload = json.loads(raw.decode())
                    ts = datetime.fromisoformat(payload["timestamp"])
                    # Normalise to aware timezone.utc for comparison
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < start or ts >= end:
                        continue
                    if action_filter is not None and payload.get("action") != action_filter:
                        continue
                    events.append(
                        AuditEvent(
                            event_id=payload["event_id"],
                            user_id=payload["user_id"],
                            action=payload["action"],
                            resource_type=payload["resource_type"],
                            resource_id=payload["resource_id"],
                            timestamp=ts,
                            ip_address=payload.get("ip_address"),
                            metadata=payload.get("metadata", {}),
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("audit: failed to parse event from key=%s", key, exc_info=True)

        events.sort(key=lambda e: e.timestamp)
        return events

    @staticmethod
    def scrub_sensitive(data: dict) -> dict:
        """Recursively strip sensitive keys and values from *data*.

        Sensitive keys (``password``, ``secret``, ``token``) are replaced
        with ``"[REDACTED]"``.  String values matching sensitive patterns
        (``sk-ant-*``, ``wg_agent_*``, ``Bearer <...>``) are also replaced
        with ``"[REDACTED]"``.  Non-string values are left unchanged.

        Parameters
        ----------
        data:
            The dict to scrub (not mutated; a new dict is returned).

        Returns
        -------
        dict
            A new dict with all sensitive entries replaced.
        """
        result: dict = {}
        for key, value in data.items():
            if _key_is_sensitive(str(key)):
                result[key] = "[REDACTED]"
            elif isinstance(value, dict):
                result[key] = AuditLogger.scrub_sensitive(value)
            elif isinstance(value, str) and _value_is_sensitive(value):
                result[key] = "[REDACTED]"
            else:
                result[key] = value
        return result


__all__ = ["AuditLogger", "AUDIT_EVENTS"]
