"""graphclaw.mcp.adapters.slack.adapter — Slack MCP adapter.

Description
-----------
Pre-built adapter for Slack API integration via the MCP protocol.
Declares tools for listing channels, reading messages, and posting messages,
using HTTP transport with a Bot Token.

Design Patterns
---------------
- ClassVar manifest: ``TOOLS`` is a class-level list of MCP tool descriptors
  that the MCP executor uses to discover available operations without
  instantiating the adapter.

Public API
----------
- SlackMCPAdapter: Pre-built adapter for the Slack API.

Dependencies
------------
- graphclaw.mcp.adapters.base: MCPAdapterABC (stdlib-like ABC layer).
"""
from __future__ import annotations

from typing import ClassVar

from graphclaw.mcp.adapters.base import MCPAdapterABC


class SlackMCPAdapter(MCPAdapterABC):
    """Pre-built MCP adapter for the Slack API.

    Connects via HTTP transport.  Credentials (Bot Token ``xoxb-…``) must be
    stored in the SecretsManager under the path described in
    ``get_install_instructions``.

    Class Variables
    ---------------
    TOOLS:
        MCP tool descriptors for list_channels, read_messages, and
        post_message.
    """

    server_name: ClassVar[str] = "slack"
    default_transport: ClassVar[str] = "http"
    default_trust_read: ClassVar[str] = "AUTO"
    default_trust_write: ClassVar[str] = "GATED"
    default_scope: ClassVar[list[str]] = [
        "read_channels",
        "read_messages",
        "post_message",
        "list_users",
    ]

    TOOLS: ClassVar[list[dict]] = [
        {
            "name": "list_channels",
            "description": "List Slack channels",
            "input_schema": {
                "type": "object",
                "properties": {
                    "types": {
                        "type": "string",
                        "default": "public_channel",
                    },
                    "limit": {"type": "integer", "default": 20},
                },
            },
        },
        {
            "name": "read_messages",
            "description": "Read messages from a Slack channel",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel ID or name",
                    },
                    "limit": {"type": "integer", "default": 20},
                    "oldest": {
                        "type": "string",
                        "description": "Unix timestamp",
                    },
                },
                "required": ["channel"],
            },
        },
        {
            "name": "post_message",
            "description": "Post a message to a Slack channel",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "text": {"type": "string"},
                    "thread_ts": {
                        "type": "string",
                        "description": "Reply to thread if provided",
                    },
                },
                "required": ["channel", "text"],
            },
        },
    ]

    @classmethod
    def get_install_instructions(cls) -> str:
        """Return setup instructions for the Slack MCP server.

        Returns
        -------
        str:
            Step-by-step instructions for creating a Slack App, adding OAuth
            scopes, and storing the Bot Token in Secrets Manager.
        """
        return (
            "Slack MCP Server setup:\n"
            "1. Create a Slack App at api.slack.com/apps\n"
            "2. Add required OAuth scopes: channels:read, chat:write,"
            " channels:history\n"
            "3. Install to workspace, get Bot Token (xoxb-...)\n"
            "4. Store token in Secrets Manager under"
            " /workgraph/USER-{id}/mcp/{server_id}"
        )
