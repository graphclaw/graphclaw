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
"""graphclaw.gateway.channels.slack.normalizer — Slack event payload → InboundMessage.

Translates Slack Events API event payloads (JSON dicts) into the
channel-agnostic ``InboundMessage`` schema used throughout the gateway.

Slack event payload structure (simplified):
    {
      "type": "event_callback",
      "event": {
        "type": "message",
        "channel": "C12345678",
        "user": "U12345678",
        "text": "Hello <@U87654321>",
        "ts": "1700000000.123456",
        "thread_ts": "1700000000.000000"   # optional, present in thread replies
      }
    }

Bot messages (identified by the presence of ``bot_id``) are silently skipped
by returning ``None``.  Mention syntax ``<@USERID>`` is stripped from text.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from graphclaw.gateway.schemas import InboundMessage

# Regex for Slack mention syntax: <@U12345> or <@U12345|displayname>
_MENTION_RE = re.compile(r"<@[A-Z0-9]+(?:\|[^>]*)?>")


def normalize_slack(payload: dict[str, Any]) -> InboundMessage | None:
    """Extract a message from a Slack event payload.

    Args:
        payload: Parsed JSON body of a Slack Events API callback.

    Returns:
        An ``InboundMessage`` for human messages, or ``None`` for bot messages
        and non-message events.
    """
    try:
        event = payload.get("event", payload)

        # Skip bot messages
        if event.get("bot_id"):
            return None

        event_type = event.get("type", "")
        if event_type not in ("message", "app_mention"):
            return None

        user = event.get("user", "")
        if not user:
            return None

        channel = event.get("channel", "")
        text = event.get("text", "")
        ts = event.get("ts", "")
        thread_ts = event.get("thread_ts", "")

        # Strip Slack mention syntax from text
        cleaned_text = _MENTION_RE.sub("", text).strip()

        # Convert Slack timestamp (Unix float string) to datetime
        received_at: datetime
        try:
            received_at = datetime.fromtimestamp(float(ts), tz=UTC)
        except (ValueError, TypeError):
            received_at = datetime.now(UTC)

        raw_headers: dict[str, str] = {
            "slack_channel": channel,
            "slack_ts": ts,
        }
        if thread_ts:
            raw_headers["slack_thread_ts"] = thread_ts

        return InboundMessage(
            message_id=ts or f"slack-{uuid.uuid4().hex}",
            channel="slack",
            sender=user,
            subject=f"Slack message from {user}",
            body=cleaned_text,
            received_at=received_at,
            raw_headers=raw_headers,
            session_id=f"SES-{uuid.uuid4()}",
        )
    except (KeyError, TypeError, ValueError):
        return None
