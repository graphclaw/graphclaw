"""tests.test_mcp.test_mcp_client — Unit tests for MCPClient.

Description
-----------
Tests trust-tier enforcement, MCP SDK import guard, and the connect/
call_tool/disconnect lifecycle using mock objects.  The real MCP SDK is
never required to be installed.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock, MagicMock, patch.
- graphclaw.mcp.client: MCPClient, MCPToolBlockedError, MCPApprovalDeniedError,
  MCPApprovalTimeoutError.
- graphclaw.models.enums: MCPTransport, TrustTier.
- graphclaw.models.nodes: MCPServerNode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.mcp.client import (
    MCPApprovalDeniedError,
    MCPClient,
    MCPToolBlockedError,
)
from graphclaw.mcp.models import MCPToolResult
from graphclaw.models.enums import MCPTransport, TrustTier
from graphclaw.models.nodes import MCPServerNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_server_node(
    transport: MCPTransport = MCPTransport.HTTP,
    endpoint_url: str = "https://example.com/mcp",
) -> MCPServerNode:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return MCPServerNode(
        id="MCP-AAAABBBB",
        name="Test Server",
        transport=transport,
        endpoint_url=endpoint_url,
        trust_tier=TrustTier.AUTO,
        scope=[],
        enabled=True,
        registered_at=now,
        created_at=now,
        updated_at=now,
        version=0,
    )


def make_approval_service(approved: bool = True) -> AsyncMock:
    svc = AsyncMock()
    svc.request_approval = AsyncMock(return_value="TSK-AL-0001-APR")
    svc.wait_for_approval = AsyncMock(return_value=approved)
    return svc


def make_tool_result(success: bool = True) -> MCPToolResult:
    return MCPToolResult(
        call_id="result-001",
        success=success,
        content="ok",
        latency_ms=10,
    )


# ---------------------------------------------------------------------------
# Trust tier enforcement — BLOCKED
# ---------------------------------------------------------------------------


class TestMCPClientBlocked:
    @pytest.mark.asyncio
    async def test_blocked_raises_without_executing(self):
        client = MCPClient()
        client._server = make_server_node()

        execute_mock = AsyncMock(return_value=make_tool_result())
        client._execute_tool = execute_mock
        client._log_tool_call = AsyncMock()

        with pytest.raises(MCPToolBlockedError):
            await client.call_tool(
                tool_name="list_events",
                arguments={},
                trust_tier=TrustTier.BLOCKED,
                user_id="USER-alice",
                server_id="MCP-AAAABBBB",
            )

        execute_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocked_logs_the_call(self):
        client = MCPClient()
        client._server = make_server_node()
        client._execute_tool = AsyncMock(return_value=make_tool_result())
        client._log_tool_call = AsyncMock()

        with pytest.raises(MCPToolBlockedError):
            await client.call_tool(
                tool_name="list_events",
                arguments={},
                trust_tier=TrustTier.BLOCKED,
                user_id="USER-alice",
                server_id="MCP-AAAABBBB",
            )

        client._log_tool_call.assert_awaited_once()


# ---------------------------------------------------------------------------
# Trust tier enforcement — AUTO
# ---------------------------------------------------------------------------


class TestMCPClientAuto:
    @pytest.mark.asyncio
    async def test_auto_calls_execute_immediately(self):
        client = MCPClient()
        client._server = make_server_node()

        expected_result = make_tool_result()
        client._execute_tool = AsyncMock(return_value=expected_result)
        client._log_tool_call = AsyncMock()

        result = await client.call_tool(
            tool_name="list_events",
            arguments={"calendar": "primary"},
            trust_tier=TrustTier.AUTO,
            user_id="USER-alice",
            server_id="MCP-AAAABBBB",
        )

        client._execute_tool.assert_awaited_once_with("list_events", {"calendar": "primary"})
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_auto_does_not_call_approval_service(self):
        approval_svc = make_approval_service()
        client = MCPClient(gated_approval_service=approval_svc)
        client._server = make_server_node()
        client._execute_tool = AsyncMock(return_value=make_tool_result())
        client._log_tool_call = AsyncMock()

        await client.call_tool(
            tool_name="list_events",
            arguments={},
            trust_tier=TrustTier.AUTO,
            user_id="USER-alice",
            server_id="MCP-AAAABBBB",
        )

        approval_svc.request_approval.assert_not_awaited()


# ---------------------------------------------------------------------------
# Trust tier enforcement — GATED
# ---------------------------------------------------------------------------


class TestMCPClientGated:
    @pytest.mark.asyncio
    async def test_gated_approved_calls_execute(self):
        approval_svc = make_approval_service(approved=True)
        client = MCPClient(gated_approval_service=approval_svc)
        client._server = make_server_node()

        expected = make_tool_result()
        client._execute_tool = AsyncMock(return_value=expected)
        client._log_tool_call = AsyncMock()

        result = await client.call_tool(
            tool_name="create_event",
            arguments={"title": "Meeting"},
            trust_tier=TrustTier.GATED,
            user_id="USER-alice",
            server_id="MCP-AAAABBBB",
        )

        approval_svc.request_approval.assert_awaited_once()
        approval_svc.wait_for_approval.assert_awaited_once_with("TSK-AL-0001-APR")
        client._execute_tool.assert_awaited_once()
        assert result is expected

    @pytest.mark.asyncio
    async def test_gated_denied_raises_approval_denied_error(self):
        approval_svc = make_approval_service(approved=False)
        client = MCPClient(gated_approval_service=approval_svc)
        client._server = make_server_node()
        client._execute_tool = AsyncMock(return_value=make_tool_result())
        client._log_tool_call = AsyncMock()

        with pytest.raises(MCPApprovalDeniedError):
            await client.call_tool(
                tool_name="delete_event",
                arguments={"event_id": "ev-001"},
                trust_tier=TrustTier.GATED,
                user_id="USER-alice",
                server_id="MCP-AAAABBBB",
            )

        client._execute_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gated_without_approval_service_raises_runtime_error(self):
        client = MCPClient(gated_approval_service=None)
        client._server = make_server_node()
        client._execute_tool = AsyncMock(return_value=make_tool_result())
        client._log_tool_call = AsyncMock()

        with pytest.raises(RuntimeError, match="GatedApprovalService"):
            await client.call_tool(
                tool_name="delete_event",
                arguments={},
                trust_tier=TrustTier.GATED,
                user_id="USER-alice",
                server_id="MCP-AAAABBBB",
            )


# ---------------------------------------------------------------------------
# MCP SDK import guard
# ---------------------------------------------------------------------------


class TestMCPClientImportGuard:
    @pytest.mark.asyncio
    async def test_connect_raises_import_error_when_sdk_missing(self):
        """When _MCP_AVAILABLE is False, connect() must raise ImportError."""
        server = make_server_node()
        client = MCPClient()

        with patch("graphclaw.mcp.client._MCP_AVAILABLE", False):
            with pytest.raises(ImportError, match="pip install mcp"):
                await client.connect(server)

    @pytest.mark.asyncio
    async def test_connect_uses_http_transport_for_http_server(self):
        """When SDK is 'available', connect selects HTTPClientTransport."""
        server = make_server_node(transport=MCPTransport.HTTP)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        MockSession = MagicMock(return_value=mock_session)

        mock_transport = MagicMock()
        MockHTTPTransport = MagicMock(return_value=mock_transport)

        with (
            patch("graphclaw.mcp.client._MCP_AVAILABLE", True),
            patch("graphclaw.mcp.client.ClientSession", MockSession),
            patch("graphclaw.mcp.client.HTTPClientTransport", MockHTTPTransport),
        ):
            client = MCPClient()
            await client.connect(server)

        MockHTTPTransport.assert_called_once_with(server.endpoint_url)
        MockSession.assert_called_once_with(mock_transport)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestMCPClientContextManager:
    @pytest.mark.asyncio
    async def test_aexit_calls_disconnect(self):
        client = MCPClient()
        client.disconnect = AsyncMock()

        async with client:
            pass

        client.disconnect.assert_awaited_once()
