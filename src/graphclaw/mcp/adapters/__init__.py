"""graphclaw.mcp.adapters — Pre-built MCP server adapters.

Exports
-------
- MCPAdapterABC: Abstract base class for all adapters.
- GoogleCalendarMCPAdapter: Adapter for Google Calendar API.
- GitHubMCPAdapter: Adapter for GitHub REST API.
- SlackMCPAdapter: Adapter for Slack API.
"""

from __future__ import annotations

from graphclaw.mcp.adapters.base import MCPAdapterABC
from graphclaw.mcp.adapters.github.adapter import GitHubMCPAdapter
from graphclaw.mcp.adapters.google_calendar.adapter import GoogleCalendarMCPAdapter
from graphclaw.mcp.adapters.slack.adapter import SlackMCPAdapter

__all__ = [
    "MCPAdapterABC",
    "GoogleCalendarMCPAdapter",
    "GitHubMCPAdapter",
    "SlackMCPAdapter",
]
