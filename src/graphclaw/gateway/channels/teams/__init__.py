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
"""graphclaw.gateway.channels.teams — Microsoft Teams Bot Framework channel plugin."""

from __future__ import annotations

from graphclaw.gateway.channels.teams.adapter import TeamsAdapter
from graphclaw.gateway.channels.teams.adapter import TeamsAdapter as Adapter
from graphclaw.gateway.channels.teams.config import TeamsConfig
from graphclaw.gateway.channels.teams.normalizer import normalize_teams as TeamsNormalizer
from graphclaw.gateway.channels.teams.sender import TeamsSender

__all__ = ["Adapter", "TeamsAdapter", "TeamsConfig", "TeamsSender", "TeamsNormalizer"]
