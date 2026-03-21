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
"""graphclaw.gateway.channels.slack.sender — Slack outbound message delivery.

Sends text messages and files via the Slack Web API using ``httpx`` async HTTP.

Environment Variables (via SlackConfig)
----------------------------------------
SLACK_BOT_TOKEN   Bot token from Slack app settings.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphclaw.gateway.channels.slack.config import SlackConfig

logger = logging.getLogger(__name__)

_SLACK_API_BASE = "https://slack.com/api"


class SlackSender:
    """Delivers outbound messages and files via the Slack Web API."""

    def __init__(self, config: SlackConfig) -> None:
        self._config = config

    async def send(
        self,
        channel: str,
        text: str,
        blocks: list | None = None,
    ) -> None:
        """Send a text message to a Slack channel.

        Args:
            channel: Slack channel ID or name (e.g. "#general" or "C12345678").
            text: Message content (plain text fallback).
            blocks: Optional list of Slack Block Kit block dicts.

        Raises:
            RuntimeError: If the API call fails or httpx is not installed.
        """
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for the Slack channel. "
                "Install it with: pip install 'httpx>=0.27.0'"
            ) from exc

        payload: dict[str, object] = {
            "channel": channel,
            "text": text,
        }
        if blocks is not None:
            payload["blocks"] = blocks

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{_SLACK_API_BASE}/chat.postMessage",
                json=payload,
                headers={"Authorization": f"Bearer {self._config.bot_token}"},
            )

        if not response.is_success:
            raise RuntimeError(
                f"Slack API chat.postMessage failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )

        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API chat.postMessage error: {data.get('error', 'unknown')}")

        logger.info("Slack message sent to channel=%s", channel)

    async def upload_file(
        self,
        channel: str,
        content: bytes,
        filename: str,
        title: str = "",
    ) -> None:
        """Upload a file to a Slack channel.

        Args:
            channel: Slack channel ID or name.
            content: Raw file bytes to upload.
            filename: Name for the uploaded file.
            title: Optional display title for the file.

        Raises:
            RuntimeError: If the API call fails or httpx is not installed.
        """
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for the Slack channel. "
                "Install it with: pip install 'httpx>=0.27.0'"
            ) from exc

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{_SLACK_API_BASE}/files.upload",
                headers={"Authorization": f"Bearer {self._config.bot_token}"},
                data={
                    "channels": channel,
                    "filename": filename,
                    "title": title or filename,
                },
                files={"file": (filename, content)},
            )

        if not response.is_success:
            raise RuntimeError(
                f"Slack API files.upload failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )

        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API files.upload error: {data.get('error', 'unknown')}")

        logger.info("Slack file uploaded to channel=%s filename=%s", channel, filename)
