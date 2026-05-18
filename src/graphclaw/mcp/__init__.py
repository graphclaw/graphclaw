# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.mcp — MCP Server Registry and Client Runtime.

Description
-----------
The ``graphclaw.mcp`` package provides everything needed to register, discover,
and invoke tools from Model Context Protocol (MCP) servers:

- ``MCPRegistry``        — CRUD for user-owned MCPServerNode records in the graph.
- ``MCPClient``          — Connects to an MCP server, lists tools, and executes
                           tool calls with trust-tier enforcement.
- ``OfficialMCPRegistry``— Searches the public registry.modelcontextprotocol.io.
- ``GatedApprovalService``— Creates APPROVAL tasks for GATED tool calls.
- ``MCPAdapterABC``      — Base class for pre-built server adapters.

Public API
----------
Re-exports all user-facing symbols from sub-modules so callers can import from
``graphclaw.mcp`` directly.
"""

from __future__ import annotations

# Adapters
from graphclaw.mcp.adapters.base import MCPAdapterABC

# Services
from graphclaw.mcp.approval import GatedApprovalService
from graphclaw.mcp.client import (
    MCPApprovalDeniedError,
    MCPApprovalTimeoutError,
    MCPClient,
    MCPToolBlockedError,
)

# Value objects
from graphclaw.mcp.models import (
    MCPServerListing,
    MCPServerVersion,
    MCPTool,
    MCPToolCall,
    MCPToolResult,
)
from graphclaw.mcp.official_registry import OfficialMCPRegistry
from graphclaw.mcp.registry import MCPRegistry

# Domain models (imported from canonical locations; re-exported for convenience)
from graphclaw.models.enums import MCPTransport, TrustTier
from graphclaw.models.nodes import MCPServerNode

__all__ = [
    # Value objects
    "MCPTool",
    "MCPToolCall",
    "MCPToolResult",
    "MCPServerListing",
    "MCPServerVersion",
    # Services
    "MCPRegistry",
    "MCPClient",
    "OfficialMCPRegistry",
    "GatedApprovalService",
    # Adapter ABC
    "MCPAdapterABC",
    # Exceptions
    "MCPToolBlockedError",
    "MCPApprovalDeniedError",
    "MCPApprovalTimeoutError",
    # Domain models
    "TrustTier",
    "MCPTransport",
    "MCPServerNode",
]
