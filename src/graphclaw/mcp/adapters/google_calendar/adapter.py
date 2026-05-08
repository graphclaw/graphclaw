# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.mcp.adapters.google_calendar.adapter — Google Calendar MCP adapter.

Description
-----------
Pre-built adapter for Google Calendar API integration via the MCP protocol.
Declares tools for listing events, creating events, and checking free/busy
status, all using SSE transport.

Design Patterns
---------------
- ClassVar manifest: ``TOOLS`` is a class-level list of MCP tool descriptors
  that the MCP executor uses to discover available operations without
  instantiating the adapter.

Public API
----------
- GoogleCalendarMCPAdapter: Pre-built adapter for Google Calendar.

Dependencies
------------
- graphclaw.mcp.adapters.base: MCPAdapterABC (stdlib-like ABC layer).
"""

from __future__ import annotations

from typing import ClassVar

from graphclaw.mcp.adapters.base import MCPAdapterABC


class GoogleCalendarMCPAdapter(MCPAdapterABC):
    """Pre-built MCP adapter for Google Calendar API.

    Connects via SSE transport.  Credentials (OAuth2 refresh token) must be
    stored in the SecretsManager under the path described in
    ``get_install_instructions``.

    Class Variables
    ---------------
    TOOLS:
        MCP tool descriptors for list_events, create_event, and
        check_free_busy.
    """

    server_name: ClassVar[str] = "google_calendar"
    default_transport: ClassVar[str] = "sse"
    default_trust_read: ClassVar[str] = "AUTO"
    default_trust_write: ClassVar[str] = "GATED"
    default_scope: ClassVar[list[str]] = [
        "read_events",
        "create_event",
        "check_free_busy",
        "update_event",
        "delete_event",
    ]

    TOOLS: ClassVar[list[dict]] = [
        {
            "name": "list_events",
            "description": "List calendar events in a date range",
            "input_schema": {
                "type": "object",
                "properties": {
                    "calendar_id": {"type": "string", "default": "primary"},
                    "time_min": {
                        "type": "string",
                        "description": "ISO 8601 datetime",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "ISO 8601 datetime",
                    },
                    "max_results": {"type": "integer", "default": 10},
                },
                "required": ["time_min", "time_max"],
            },
        },
        {
            "name": "create_event",
            "description": "Create a calendar event",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "start": {
                        "type": "string",
                        "description": "ISO 8601 datetime",
                    },
                    "end": {
                        "type": "string",
                        "description": "ISO 8601 datetime",
                    },
                    "description": {"type": "string"},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["summary", "start", "end"],
            },
        },
        {
            "name": "check_free_busy",
            "description": "Check free/busy status for a time range",
            "input_schema": {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string"},
                    "time_max": {"type": "string"},
                },
                "required": ["time_min", "time_max"],
            },
        },
    ]

    @classmethod
    def get_install_instructions(cls) -> str:
        """Return setup instructions for the Google Calendar MCP server.

        Returns
        -------
        str:
            Step-by-step instructions for enabling the Google Calendar API,
            creating OAuth credentials, and storing them in Secrets Manager.
        """
        return (
            "Google Calendar MCP Server setup:\n"
            "1. Enable Google Calendar API in Google Cloud Console\n"
            "2. Create OAuth 2.0 credentials (client_id, client_secret)\n"
            "3. Complete OAuth flow to get refresh_token\n"
            "4. Store credentials in Secrets Manager under"
            " /workgraph/USER-{id}/mcp/{server_id}\n"
            "5. The server connects via SSE transport to the Google Calendar API"
        )
