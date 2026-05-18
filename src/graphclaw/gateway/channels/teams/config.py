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
"""graphclaw.gateway.channels.teams.config — Microsoft Teams Bot configuration.

Reads Teams Bot credentials from environment variables.
All variables are optional at import time — missing values cause the
adapter to skip startup with a warning rather than crashing.

Environment Variables
---------------------
TEAMS_TENANT_ID        Azure AD tenant ID for the bot app registration.
TEAMS_CLIENT_ID        Azure AD application (client) ID.
TEAMS_CLIENT_SECRET    Azure AD client secret for token acquisition.
TEAMS_WEBHOOK_URL      Incoming webhook URL for sending messages (optional).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TeamsConfig:
    """Validated Microsoft Teams Bot configuration."""

    tenant_id: str
    client_id: str
    client_secret: str
    webhook_url: str = ""

    @classmethod
    def from_env(cls) -> TeamsConfig | None:
        """Build from environment variables; return None if tenant_id is missing."""
        tenant_id = os.environ.get("TEAMS_TENANT_ID", "")
        if not tenant_id:
            return None

        client_id = os.environ.get("TEAMS_CLIENT_ID", "")
        client_secret = os.environ.get("TEAMS_CLIENT_SECRET", "")
        webhook_url = os.environ.get("TEAMS_WEBHOOK_URL", "")

        return cls(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            webhook_url=webhook_url,
        )
