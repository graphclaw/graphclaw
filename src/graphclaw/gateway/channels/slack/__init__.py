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
"""graphclaw.gateway.channels.slack — Slack Events API channel plugin."""

from __future__ import annotations

from graphclaw.gateway.channels.slack.adapter import SlackAdapter
from graphclaw.gateway.channels.slack.adapter import SlackAdapter as Adapter
from graphclaw.gateway.channels.slack.config import SlackConfig
from graphclaw.gateway.channels.slack.normalizer import normalize_slack as SlackNormalizer
from graphclaw.gateway.channels.slack.sender import SlackSender

__all__ = ["Adapter", "SlackAdapter", "SlackConfig", "SlackSender", "SlackNormalizer"]
