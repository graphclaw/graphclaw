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
"""graphclaw.gateway.channels.slack.config — Slack Bot API configuration.

Reads Slack Bot credentials from environment variables.
All variables are optional at import time — missing values cause the
adapter to skip startup with a warning rather than crashing.

Environment Variables
---------------------
SLACK_BOT_TOKEN        Bot token from Slack app settings (e.g. "xoxb-...").
SLACK_SIGNING_SECRET   Signing secret for webhook signature verification.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SlackConfig:
    """Validated Slack Bot API configuration."""

    bot_token: str
    signing_secret: str
    default_channel: str = "#general"

    @classmethod
    def from_env(cls) -> SlackConfig | None:
        """Build from environment variables; return None if bot token is missing."""
        token = os.environ.get("SLACK_BOT_TOKEN", "")
        if not token:
            return None

        signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
        default_channel = os.environ.get("SLACK_DEFAULT_CHANNEL", "#general")

        return cls(
            bot_token=token,
            signing_secret=signing_secret,
            default_channel=default_channel,
        )
