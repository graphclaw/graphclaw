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
"""graphclaw.gateway.channels.teams.sender — Microsoft Teams outbound message delivery.

Sends messages to Teams channels via incoming webhooks using ``httpx`` async HTTP.
Messages are formatted as Adaptive Cards for rich rendering in Teams.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TeamsSender:
    """Delivers outbound messages to Microsoft Teams via incoming webhooks."""

    async def send(
        self,
        webhook_url: str,
        text: str,
        title: str = "",
    ) -> None:
        """Send a text message to a Teams channel via an incoming webhook.

        Formats the message as an Adaptive Card for proper Teams rendering.

        Args:
            webhook_url: Teams incoming webhook URL.
            text: Message body text.
            title: Optional title shown above the text body.

        Raises:
            RuntimeError: If the API call fails or httpx is not installed.
        """
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for the Teams channel. "
                "Install it with: pip install 'httpx>=0.27.0'"
            ) from exc

        body_blocks: list[dict] = []
        if title:
            body_blocks.append(
                {
                    "type": "TextBlock",
                    "text": title,
                    "weight": "bolder",
                    "size": "medium",
                    "wrap": True,
                }
            )
        body_blocks.append(
            {
                "type": "TextBlock",
                "text": text,
                "wrap": True,
            }
        )

        adaptive_card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": body_blocks,
                    },
                }
            ],
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(webhook_url, json=adaptive_card)

        if not response.is_success:
            raise RuntimeError(
                f"Teams webhook POST failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )

        logger.info("Teams message sent via webhook")
