# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.mcp.adapters.base — Abstract base class for pre-built MCP adapters.

Description
-----------
Defines ``MCPAdapterABC``, the contract that every pre-built MCP adapter must
satisfy.  Concrete adapters (Google Calendar, GitHub, Slack, …) subclass this
ABC to declare their server name, transport, trust defaults, available scopes,
and tool manifests.

Design Patterns
---------------
- ABC + ClassVar: Server metadata is declared as class variables so the
  information is available without instantiating the adapter.
- Factory method: ``build_server_node`` constructs an ``MCPServerNode``-shaped
  dict (or real model when importable) from class-level defaults plus caller-
  supplied credentials and user_id.

Public API
----------
- MCPAdapterABC: Abstract base for all pre-built MCP adapters.

Dependencies
------------
- abc: ABC, abstractmethod (stdlib).
- typing: ClassVar (stdlib).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class MCPAdapterABC(ABC):
    """Abstract base class for pre-built MCP server adapters.

    Subclasses declare server-level metadata as ``ClassVar`` attributes and
    implement ``get_install_instructions`` to provide user-facing setup guidance.

    Class Variables
    ---------------
    server_name:
        Unique lower-snake-case identifier for the MCP server (e.g.
        ``"google_calendar"``).
    default_transport:
        Default MCP transport protocol — ``"http"``, ``"sse"``, or ``"stdio"``.
    default_trust_read:
        Default ``TrustTier`` string for read-only tool calls (``"AUTO"`` or
        ``"GATED"``).
    default_trust_write:
        Default ``TrustTier`` string for write/mutation tool calls.
    default_scope:
        List of capability scope strings the adapter declares.
    """

    server_name: ClassVar[str]
    default_transport: ClassVar[str] = "http"
    default_trust_read: ClassVar[str] = "AUTO"
    default_trust_write: ClassVar[str] = "GATED"
    default_scope: ClassVar[list[str]] = []

    @classmethod
    def build_server_node(
        cls,
        user_id: str,
        credentials: dict,  # noqa: ARG003  — reserved for future use
        server_id: str | None = None,
    ) -> dict:
        """Build an MCPServerNode config dict for this adapter.

        Parameters
        ----------
        user_id:
            Platform user ID that will own this MCP server registration.
        credentials:
            Credential dict (currently reserved; will be stored via SecretsClient
            in a future phase).
        server_id:
            Optional explicit server ID.  When ``None`` a random ``MCP-<hex>``
            ID is generated.

        Returns
        -------
        dict:
            A dict matching the ``MCPServerNode`` field shape that can be
            passed to ``MCPServerNode(**result)`` or stored directly.
        """
        from uuid import uuid4

        sid = server_id or f"MCP-{uuid4().hex[:12]}"
        return {
            "id": sid,
            "name": cls.server_name,
            "transport": cls.default_transport,
            "trust_tier": cls.default_trust_write,
            "scope": list(cls.default_scope),
            "user_id": user_id,
        }

    @classmethod
    @abstractmethod
    def get_install_instructions(cls) -> str:
        """Return user-facing setup instructions for this MCP server.

        Returns
        -------
        str:
            Multi-line string describing how to obtain credentials and register
            this server with GraphClaw.
        """
        ...
