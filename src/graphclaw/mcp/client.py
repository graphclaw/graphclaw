"""graphclaw.mcp.client — MCPClient: connect, list_tools, call_tool.

Description
-----------
Provides ``MCPClient``, which connects to a registered MCP server, discovers
its tools, and executes tool calls with trust tier enforcement:

- AUTO   — execute immediately without user confirmation.
- GATED  — create an APPROVAL task via GatedApprovalService, wait for the user
           to approve, then execute.
- BLOCKED — raise ``MCPToolBlockedError`` immediately; nothing is executed.

Design Patterns
---------------
- Optional MCP SDK: The SDK is imported lazily at runtime; ``connect()`` raises
  ``ImportError`` with install instructions when the package is absent.
- Context Manager: ``MCPClient`` supports ``async with`` for safe resource cleanup.
- Strategy injection: ``GatedApprovalService`` is injected to keep approval
  flow logic separate from transport concerns.

Public API
----------
- MCPToolBlockedError: Raised when a BLOCKED server tool is called.
- MCPApprovalDeniedError: Raised when a GATED tool call is denied by the user.
- MCPApprovalTimeoutError: Raised when a GATED tool call waits too long.
- MCPClient: Connects to an MCP server and executes tool calls.

Dependencies
------------
- graphclaw.mcp.models: MCPTool, MCPToolCall, MCPToolResult.
- graphclaw.models.enums: TrustTier, MCPTransport.
- graphclaw.models.nodes: MCPServerNode.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from graphclaw.mcp.models import MCPTool, MCPToolCall, MCPToolResult
from graphclaw.models.enums import MCPTransport, TrustTier
from graphclaw.models.nodes import MCPServerNode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional MCP SDK import
# ---------------------------------------------------------------------------

try:
    from mcp import ClientSession  # type: ignore[import]
    from mcp.client.http import HTTPClientTransport  # type: ignore[import]
    from mcp.client.sse import SSEClientTransport  # type: ignore[import]
    from mcp.client.stdio import StdioClientTransport  # type: ignore[import]

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    # Define placeholder names so patch() can target these attributes even when
    # the SDK is absent.  The real values are injected by tests via unittest.mock.patch.
    ClientSession = None  # type: ignore[assignment,misc]
    HTTPClientTransport = None  # type: ignore[assignment,misc]
    SSEClientTransport = None  # type: ignore[assignment,misc]
    StdioClientTransport = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MCPToolBlockedError(Exception):
    """Raised when a call is attempted on a BLOCKED trust-tier server."""


class MCPApprovalDeniedError(Exception):
    """Raised when a GATED tool call approval is denied by the user."""


class MCPApprovalTimeoutError(Exception):
    """Raised when a GATED tool call waits too long for user approval."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class MCPClient:
    """Connects to a registered MCP server and executes tool calls.

    Trust tier enforcement
    ----------------------
    - AUTO    — ``call_tool`` executes the tool immediately.
    - GATED   — ``call_tool`` delegates to ``GatedApprovalService``, waits for
                the user to approve, then executes.
    - BLOCKED — ``call_tool`` raises ``MCPToolBlockedError`` without executing.

    Parameters
    ----------
    gated_approval_service:
        Optional ``GatedApprovalService`` instance.  Required when calling tools
        on GATED servers; may be ``None`` if only AUTO servers are used.
    """

    def __init__(self, gated_approval_service=None) -> None:
        self._approval_service = gated_approval_service
        self._session = None  # MCP ClientSession (lazy init in connect())
        self._server: MCPServerNode | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, server: MCPServerNode) -> None:
        """Open an MCP transport connection to *server*.

        Transport is selected based on ``server.transport``:

        - ``MCPTransport.HTTP``  → ``HTTPClientTransport``
        - ``MCPTransport.SSE``   → ``SSEClientTransport``
        - ``MCPTransport.STDIO`` → ``StdioClientTransport``

        Parameters
        ----------
        server:
            The ``MCPServerNode`` to connect to.

        Raises
        ------
        ImportError
            If the ``mcp`` package is not installed.
        ValueError
            If the transport type is not recognised, or if required fields
            (``endpoint_url`` / ``command``) are missing.
        """
        if not _MCP_AVAILABLE:
            raise ImportError("MCP SDK is not installed. Install it with: pip install mcp>=1.0.0")

        self._server = server

        if server.transport == MCPTransport.HTTP:
            if not server.endpoint_url:
                raise ValueError(
                    f"MCPServerNode '{server.id}' uses HTTP transport but has no endpoint_url."
                )
            transport = HTTPClientTransport(server.endpoint_url)
        elif server.transport == MCPTransport.SSE:
            if not server.endpoint_url:
                raise ValueError(
                    f"MCPServerNode '{server.id}' uses SSE transport but has no endpoint_url."
                )
            transport = SSEClientTransport(server.endpoint_url)
        elif server.transport == MCPTransport.STDIO:
            if not server.command:
                raise ValueError(
                    f"MCPServerNode '{server.id}' uses STDIO transport but has no command."
                )
            transport = StdioClientTransport(server.command)
        else:
            raise ValueError(f"Unknown transport '{server.transport}' on server '{server.id}'.")

        self._session = ClientSession(transport)
        await self._session.__aenter__()

    async def disconnect(self) -> None:
        """Close the MCP session and release transport resources."""
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("mcp.client.disconnect.error", extra={"error": str(exc)})
            finally:
                self._session = None
                self._server = None

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[MCPTool]:
        """Return all tools advertised by the connected MCP server.

        Returns
        -------
        list[MCPTool]
            One ``MCPTool`` per entry in the ``tools/list`` response.

        Raises
        ------
        RuntimeError
            If ``connect()`` has not been called.
        """
        if self._session is None or self._server is None:
            raise RuntimeError("MCPClient is not connected. Call connect() first.")

        response = await self._session.list_tools()
        tools: list[MCPTool] = []
        for tool in response.tools:
            tools.append(
                MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema or {},
                    server_id=self._server.id,
                )
            )
        return tools

    # ------------------------------------------------------------------
    # Tool execution with trust enforcement
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
        trust_tier: TrustTier,
        user_id: str,
        server_id: str,
    ) -> MCPToolResult:
        """Execute *tool_name* with trust-tier enforcement.

        Parameters
        ----------
        tool_name:
            Name of the MCP tool to invoke.
        arguments:
            Tool input arguments matching the tool's JSON Schema.
        trust_tier:
            The ``TrustTier`` of the server owning this tool.
        user_id:
            The ``USER-{id}`` initiating the call (for audit logging).
        server_id:
            The ``MCP-{id}`` of the server (for audit logging).

        Returns
        -------
        MCPToolResult

        Raises
        ------
        MCPToolBlockedError
            If *trust_tier* is ``BLOCKED``.
        MCPApprovalDeniedError
            If *trust_tier* is ``GATED`` and the user denies approval.
        MCPApprovalTimeoutError
            If *trust_tier* is ``GATED`` and the approval wait times out.
        RuntimeError
            If ``connect()`` has not been called.
        """
        call_id = str(uuid.uuid4())
        requested_at = datetime.now(timezone.utc)

        call = MCPToolCall(
            call_id=call_id,
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
            trust_tier=trust_tier.value,
            user_id=user_id,
            requested_at=requested_at,
        )

        # BLOCKED — reject immediately
        if trust_tier == TrustTier.BLOCKED:
            blocked_result = MCPToolResult(
                call_id=call_id,
                success=False,
                content="",
                error_message=(
                    f"Tool '{tool_name}' on server '{server_id}' is BLOCKED. "
                    "Update the server trust tier to allow calls."
                ),
            )
            await self._log_tool_call(call, blocked_result)
            raise MCPToolBlockedError(f"Server '{server_id}' is BLOCKED. Tool call rejected.")

        # GATED — require user approval first
        if trust_tier == TrustTier.GATED:
            if self._approval_service is None:
                raise RuntimeError(
                    "GatedApprovalService is required for GATED tool calls but was not provided."
                )
            server_name = self._server.name if self._server else server_id
            approval_task_id = await self._approval_service.request_approval(
                user_id=user_id,
                tool_name=tool_name,
                server_name=server_name,
                arguments=arguments,
            )
            approved = await self._approval_service.wait_for_approval(approval_task_id)
            if not approved:
                denied_result = MCPToolResult(
                    call_id=call_id,
                    success=False,
                    content="",
                    error_message=f"Tool '{tool_name}' approval was denied by user '{user_id}'.",
                )
                await self._log_tool_call(call, denied_result)
                raise MCPApprovalDeniedError(f"Tool '{tool_name}' call denied by user '{user_id}'.")

        # AUTO or approved GATED — execute
        result = await self._execute_tool(tool_name, arguments)
        await self._log_tool_call(call, result)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_tool(self, tool_name: str, arguments: dict) -> MCPToolResult:
        """Send a ``tools/call`` request to the connected MCP server.

        Parameters
        ----------
        tool_name:
            The MCP tool name.
        arguments:
            Input arguments for the tool.

        Returns
        -------
        MCPToolResult
        """
        if self._session is None:
            raise RuntimeError("MCPClient is not connected. Call connect() first.")

        call_id = str(uuid.uuid4())
        start_ms = int(time.monotonic() * 1000)

        try:
            response = await self._session.call_tool(tool_name, arguments)
            latency_ms = int(time.monotonic() * 1000) - start_ms

            # Extract text content from the response
            content_parts: list[str] = []
            result_data: dict = {}
            for item in response.content:
                if hasattr(item, "text"):
                    content_parts.append(item.text)
                elif hasattr(item, "data"):
                    result_data = item.data if isinstance(item.data, dict) else {}

            return MCPToolResult(
                call_id=call_id,
                success=not response.isError,
                content="\n".join(content_parts),
                result_data=result_data,
                error_message=None if not response.isError else "\n".join(content_parts),
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int(time.monotonic() * 1000) - start_ms
            return MCPToolResult(
                call_id=call_id,
                success=False,
                content="",
                error_message=str(exc),
                latency_ms=latency_ms,
            )

    async def _log_tool_call(self, call: MCPToolCall, result: MCPToolResult | None) -> None:
        """Emit a structured log event for an MCP tool call.

        Parameters
        ----------
        call:
            The tool call descriptor.
        result:
            The result (may be ``None`` if call was never executed).
        """
        logger.info(
            "mcp.tool_call",
            extra={
                "call_id": call.call_id,
                "server_id": call.server_id,
                "tool_name": call.tool_name,
                "trust_tier": call.trust_tier,
                "user_id": call.user_id,
                "success": result.success if result else None,
                "latency_ms": result.latency_ms if result else None,
                "error_message": result.error_message if result else None,
            },
        )

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(self, *_) -> None:  # noqa: ANN002
        await self.disconnect()


__all__ = [
    "MCPToolBlockedError",
    "MCPApprovalDeniedError",
    "MCPApprovalTimeoutError",
    "MCPClient",
]
