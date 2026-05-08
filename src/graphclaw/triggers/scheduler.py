# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.triggers.scheduler — In-memory cron-like scheduled trigger checker.

Description
-----------
``TriggerScheduler`` maintains an in-memory registry of ``TriggerConfig`` objects
and evaluates which are due on each tick.  It is intentionally stateless with
respect to persistence: the surrounding engine layer is responsible for loading
configs from the database and persisting changes back.

For TIME_BASED triggers with a ``cron_expression``, the scheduler computes the
next occurrence after each firing using a lightweight cron parser that supports
the daily pattern used by the MVP (e.g. ``"0 8 * * *"`` for 8 AM timezone.utc daily).

Design Patterns
---------------
- Registry: ``_triggers`` is a dict keyed by ``trigger_id`` for O(1) lookup on
  ``register``, ``unregister``, and ``advance`` operations.
- Strategy: ``_compute_next_cron`` is an isolated private method that can be
  swapped or extended without touching the public API.

Public API
----------
- TriggerScheduler.register: Add a TriggerConfig to the registry.
- TriggerScheduler.unregister: Remove a TriggerConfig by ID.
- TriggerScheduler.get_due_triggers: Return all enabled, due TriggerConfigs.
- TriggerScheduler.advance: Update next_fire_at after a trigger fires.

Dependencies
------------
- datetime: datetime, timedelta, timezone.
- graphclaw.triggers.models: TriggerConfig, TriggerType.

Notes
-----
The MVP cron parser only handles patterns of the form ``"M H * * *"`` where M
and H are integers and the remaining three fields are ``*``.  More complex
expressions (ranges, step values, specific day-of-week) are not supported and
will raise ``ValueError``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graphclaw.triggers.models import TriggerConfig, TriggerType


class TriggerScheduler:
    """Checks which triggers are due and manages their next-fire timestamps.

    Usage::

        scheduler = TriggerScheduler()
        scheduler.register(config)
        due = scheduler.get_due_triggers(utcnow())
        for cfg in due:
            # fire the trigger …
            scheduler.advance(cfg.trigger_id, utcnow())
    """

    def __init__(self) -> None:
        self._triggers: dict[str, TriggerConfig] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, config: TriggerConfig) -> None:
        """Register a trigger configuration.

        If a trigger with the same ``trigger_id`` already exists it is replaced.

        Args:
            config: The TriggerConfig to register.
        """
        self._triggers[config.trigger_id] = config

    def unregister(self, trigger_id: str) -> None:
        """Remove a trigger configuration by ID.

        Silently ignores unknown IDs.

        Args:
            trigger_id: The ID of the trigger to remove.
        """
        self._triggers.pop(trigger_id, None)

    def get_due_triggers(self, now: datetime) -> list[TriggerConfig]:
        """Return all enabled triggers whose next_fire_at is at or before *now*.

        A trigger without a ``next_fire_at`` is never considered due.

        Args:
            now: The current timezone.utc datetime used as the comparison baseline.

        Returns:
            A list of TriggerConfig objects that are due to fire.
        """
        due: list[TriggerConfig] = []
        for config in self._triggers.values():
            if not config.enabled:
                continue
            if config.next_fire_at is None:
                continue
            # Normalise to timezone.utc for comparison
            fire_at = _ensure_utc(config.next_fire_at)
            if fire_at <= _ensure_utc(now):
                due.append(config)
        return due

    def advance(self, trigger_id: str, now: datetime) -> None:
        """Update next_fire_at after a trigger fires.

        For TIME_BASED triggers with a ``cron_expression`` the next occurrence
        is computed from *now*.  For other trigger types (or when no cron
        expression is present) ``next_fire_at`` is set to ``None`` so the
        trigger will not fire again until explicitly re-scheduled.

        Args:
            trigger_id: The ID of the trigger to advance.
            now: The current timezone.utc datetime (used as the base for cron computation).
        """
        config = self._triggers.get(trigger_id)
        if config is None:
            return

        if config.trigger_type == TriggerType.TIME_BASED and config.cron_expression is not None:
            next_fire = self._compute_next_cron(config.cron_expression, now)
        else:
            next_fire = None

        # Pydantic models are immutable by default; rebuild with updated fields.
        self._triggers[trigger_id] = config.model_copy(
            update={
                "next_fire_at": next_fire,
                "last_fired_at": now,
            }
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_next_cron(self, cron_expr: str, after: datetime) -> datetime:
        """Compute the next fire datetime for a cron expression after *after*.

        Supports a limited subset of cron syntax::

            "M H * * *"  —  fire at minute M of hour H every day.

        The day-of-month, month, and day-of-week fields must be ``*``.

        Args:
            cron_expr: A 5-field cron string (e.g. ``"0 8 * * *"``).
            after: The datetime after which the next occurrence is computed.

        Returns:
            The next timezone.utc datetime matching the cron expression.

        Raises:
            ValueError: If the cron expression cannot be parsed or uses
                unsupported field values.
        """
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            raise ValueError(f"Unsupported cron expression '{cron_expr}': expected 5 fields.")

        minute_str, hour_str, dom, month, dow = fields
        if dom != "*" or month != "*" or dow != "*":
            raise ValueError(
                f"Unsupported cron expression '{cron_expr}': "
                "day-of-month, month, and day-of-week must be '*'."
            )

        try:
            target_minute = int(minute_str)
            target_hour = int(hour_str)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported cron expression '{cron_expr}': "
                f"minute and hour must be integers. ({exc})"
            ) from exc

        # Candidate: today at target_hour:target_minute (timezone.utc)
        base = _ensure_utc(after).replace(
            hour=target_hour,
            minute=target_minute,
            second=0,
            microsecond=0,
        )
        # If the candidate is not strictly after *after*, advance by one day.
        if base <= _ensure_utc(after):
            base += timedelta(days=1)
        return base


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    """Return *dt* as a timezone.utc-aware datetime, assuming timezone.utc if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
