# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.inbound.identity_drift — Identity drift detector (FR-RES-004).

Description
-----------
When an inbound message is matched to a task/conversation via email address or
alias and the sender's *current* identity differs from the stored alias,
``IdentityDriftDetector`` logs a drift event and optionally triggers an alias
auto-registration (FR-ID-005).

Design Patterns
---------------
- Strategy: Drift detector can be configured with different resolution strategies.
- Single responsibility: Only detects and records drift — resolution delegated to
  the alias registration tool.

Public API
----------
- DriftEvent: Immutable drift record.
- IdentityDriftDetector: Main detector class.
- IdentityDriftDetector.check_and_record(msg_sender, matched_node_id, store): Detect + record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DriftEvent:
    """An identity drift record.

    Attributes
    ----------
    node_id:
        The node whose stored identity differs from the inbound sender.
    sender_value:
        The actual sender value from the inbound message.
    matched_alias:
        The alias that *was* used to match the node.
    drift_type:
        ``"new_email"`` | ``"new_alias"`` | ``"display_name_changed"``.
    detected_at:
        When the drift was detected.
    auto_registered:
        Whether the new alias was auto-registered.
    """

    node_id: str
    sender_value: str
    matched_alias: str
    drift_type: str
    detected_at: datetime
    auto_registered: bool = False


class IdentityDriftDetector:
    """Detect and record identity drift on inbound messages (FR-RES-004).

    Parameters
    ----------
    store:
        GraphStore for reading node aliases.
    alias_register_fn:
        Optional callable ``(node_id, alias, source, added_by, store)`` for
        auto-registering new aliases (defaults to ``register_alias`` tool fn).
    """

    def __init__(
        self,
        store: Any,
        alias_register_fn: Any | None = None,
    ) -> None:
        self._store = store
        self._alias_register_fn = alias_register_fn

    async def check_and_record(
        self,
        sender_value: str,
        matched_node_id: str,
        *,
        auto_register: bool = True,
        added_by: str = "system_drift_detector",
    ) -> DriftEvent | None:
        """Check if *sender_value* is a new identity for *matched_node_id*.

        Parameters
        ----------
        sender_value:
            Email / alias from the inbound message.
        matched_node_id:
            Node ID that was matched.
        auto_register:
            If True, automatically register the new alias (FR-ID-005).
        added_by:
            Source label for the auto-registered alias.

        Returns
        -------
        DriftEvent | None
            A drift event if drift was detected; ``None`` if no drift.
        """
        if not sender_value or not matched_node_id:
            return None

        try:
            node_raw = await self._store.get_node(matched_node_id, include_archived=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("identity_drift.get_node_failed: %s", exc)
            return None

        if node_raw is None:
            return None

        # Extract all known aliases
        if isinstance(node_raw, dict):
            aliases = node_raw.get("aliases", []) or []
        else:
            aliases = getattr(node_raw, "aliases", []) or []

        alias_values = set()
        for a in aliases:
            if isinstance(a, dict):
                alias_values.add(a.get("value", "").lower())
            else:
                alias_values.add(str(a).lower())

        sender_lower = sender_value.lower()
        if sender_lower in alias_values:
            return None  # No drift

        # Drift detected
        drift_type = "new_email" if "@" in sender_value else "new_alias"
        auto_registered = False

        if auto_register and self._alias_register_fn is not None:
            try:
                await self._alias_register_fn(
                    node_id=matched_node_id,
                    alias=sender_value,
                    source=added_by,
                    added_by=added_by,
                    store=self._store,
                )
                auto_registered = True
                logger.info(
                    "identity_drift.auto_registered",
                    extra={"node_id": matched_node_id, "alias": sender_value},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("identity_drift.auto_register_failed: %s", exc)

        event = DriftEvent(
            node_id=matched_node_id,
            sender_value=sender_value,
            matched_alias=next(iter(alias_values), ""),
            drift_type=drift_type,
            detected_at=datetime.now(timezone.utc),
            auto_registered=auto_registered,
        )
        logger.info(
            "identity_drift.detected",
            extra={
                "node_id": matched_node_id,
                "drift_type": drift_type,
                "auto_registered": auto_registered,
            },
        )
        return event
