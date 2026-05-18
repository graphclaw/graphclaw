# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.mcp.models — Dataclasses for MCP tool calls and server listings.

Description
-----------
Defines lightweight, frozen dataclasses representing the data objects exchanged
during MCP tool discovery and invocation.  These are transport-agnostic value
objects; persistence and execution concerns live in registry.py and client.py.

Design Patterns
---------------
- Frozen Dataclasses: All types are ``frozen=True`` to enforce immutability
  and allow safe use as dict keys or in sets.
- Value Objects: MCPTool, MCPToolCall, MCPToolResult, and MCPServerListing
  carry no behaviour; they are pure data containers.

Public API
----------
- MCPTool: A tool exposed by an MCP server (from tools/list response).
- MCPToolCall: A pending or completed tool call.
- MCPToolResult: Result of an executed tool call.
- MCPServerListing: A server entry from the official MCP registry.
- MCPServerVersion: Version record for a server in the official registry.

Dependencies
------------
- dataclasses: dataclass, field.
- datetime: datetime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class MCPTool:
    """A tool exposed by an MCP server (from tools/list response)."""

    name: str
    description: str
    input_schema: dict  # JSON Schema for arguments
    server_id: str


@dataclass(frozen=True)
class MCPToolCall:
    """A pending or completed tool call."""

    call_id: str
    server_id: str
    tool_name: str
    arguments: dict
    trust_tier: str  # TrustTier value
    user_id: str
    requested_at: datetime
    approved_by: str | None = None  # USER-{id} | "auto" | "denied"
    executed_at: datetime | None = None


@dataclass(frozen=True)
class MCPToolResult:
    """Result of an executed tool call."""

    call_id: str
    success: bool
    content: str  # text content from MCP response
    result_data: dict = field(default_factory=dict)
    error_message: str | None = None
    latency_ms: int = 0


@dataclass(frozen=True)
class MCPServerListing:
    """A server from the official MCP registry."""

    name: str
    description: str
    publisher: str
    version: str
    transport: str  # "sse" | "http" | "stdio"
    tags: list[str]
    registry_url: str
    install_command: str | None = None


@dataclass(frozen=True)
class MCPServerVersion:
    """Version record for a server in the official MCP registry."""

    server_name: str
    version: str
    released_at: datetime | None
    changelog: str = ""


__all__ = [
    "MCPTool",
    "MCPToolCall",
    "MCPToolResult",
    "MCPServerListing",
    "MCPServerVersion",
]
