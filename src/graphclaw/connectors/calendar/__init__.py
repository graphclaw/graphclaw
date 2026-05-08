# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.connectors.calendar — Calendar connector subpackage.

Provides ``CalendarConnector`` (ABC), ``CalendarEvent``, ``FreeBusySlot``,
and the concrete ``GoogleCalendarConnector`` / ``OutlookCalendarConnector``
adapters.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

from graphclaw.connectors.calendar.base import CalendarConnector
from graphclaw.connectors.calendar.models import CalendarEvent, FreeBusySlot

__all__ = [
    "CalendarConnector",
    "CalendarEvent",
    "FreeBusySlot",
]
