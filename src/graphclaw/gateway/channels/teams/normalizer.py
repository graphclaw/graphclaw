# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
"""graphclaw.gateway.channels.teams.normalizer — Teams Activity → InboundMessage.

Translates Microsoft Teams Bot Framework Activity payloads (JSON dicts) into
the channel-agnostic ``InboundMessage`` schema used throughout the gateway.

Teams Activity payload structure (simplified):
    {
      "type": "message",
      "id": "1700000000123",
      "timestamp": "2024-01-01T00:00:00Z",
      "text": "<at>BotName</at> hello",
      "from": {
        "id": "29:1abc...",
        "name": "Alice Smith"
      },
      "channelData": {
        "channel": {
          "id": "19:abc...@thread.skype"
        }
      }
    }

Bot mention syntax ``<at>BotName</at>`` is stripped from the text.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from graphclaw.gateway.schemas import InboundMessage

# Regex for Teams @mention XML tags: <at>BotName</at>
_AT_MENTION_RE = re.compile(r"<at>[^<]*</at>")


def normalize_teams(payload: dict[str, Any]) -> InboundMessage | None:
    """Extract a message from a Teams Bot Framework Activity payload.

    Args:
        payload: Parsed JSON body of a Teams Bot Framework Activity.

    Returns:
        An ``InboundMessage`` for message activities, or ``None`` for other
        activity types or malformed payloads.
    """
    try:
        activity_type = payload.get("type", "")
        if activity_type != "message":
            return None

        from_obj = payload.get("from", {})
        sender_id = from_obj.get("id", "")
        sender_name = from_obj.get("name", sender_id)

        if not sender_id:
            return None

        text = payload.get("text", "")
        # Strip Teams @mention tags from text
        cleaned_text = _AT_MENTION_RE.sub("", text).strip()

        activity_id = payload.get("id", "")
        timestamp_str = payload.get("timestamp", "")

        received_at: datetime
        if timestamp_str:
            try:
                # Teams timestamps are ISO 8601; strip trailing Z if present
                ts_clean = timestamp_str.rstrip("Z").replace("Z", "")
                received_at = datetime.fromisoformat(ts_clean).replace(tzinfo=timezone.utc)
            except ValueError:
                received_at = datetime.now(timezone.utc)
        else:
            received_at = datetime.now(timezone.utc)

        # Extract channel ID from channelData if available
        channel_data = payload.get("channelData", {})
        teams_channel_id = (
            channel_data.get("channel", {}).get("id", "") if isinstance(channel_data, dict) else ""
        )

        raw_headers: dict[str, str] = {
            "teams_sender_name": sender_name,
            "teams_activity_id": activity_id,
        }
        if teams_channel_id:
            raw_headers["teams_channel_id"] = teams_channel_id

        return InboundMessage(
            message_id=activity_id or f"teams-{uuid.uuid4().hex}",
            channel="teams",
            sender=sender_id,
            subject=f"Teams message from {sender_name}",
            body=cleaned_text,
            received_at=received_at,
            raw_headers=raw_headers,
            session_id=f"SES-{uuid.uuid4()}",
        )
    except (KeyError, TypeError, ValueError):
        return None
