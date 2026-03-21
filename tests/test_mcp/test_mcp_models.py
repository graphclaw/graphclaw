"""tests.test_mcp.test_mcp_models — Unit tests for MCP dataclasses.

Description
-----------
Verifies that ``MCPTool``, ``MCPToolCall``, ``MCPToolResult``, and
``MCPServerListing`` are correctly frozen (immutable) and that their fields
are stored with the expected values.

Dependencies
------------
- pytest: Test runner.
- graphclaw.mcp.models: MCPTool, MCPToolCall, MCPToolResult, MCPServerListing,
  MCPServerVersion.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from graphclaw.mcp.models import (
    MCPServerListing,
    MCPServerVersion,
    MCPTool,
    MCPToolCall,
    MCPToolResult,
)

# ---------------------------------------------------------------------------
# MCPTool
# ---------------------------------------------------------------------------


class TestMCPTool:
    def test_fields_stored_correctly(self):
        tool = MCPTool(
            name="list_events",
            description="List calendar events",
            input_schema={"type": "object", "properties": {}},
            server_id="MCP-ABCDEF12",
        )
        assert tool.name == "list_events"
        assert tool.description == "List calendar events"
        assert tool.input_schema == {"type": "object", "properties": {}}
        assert tool.server_id == "MCP-ABCDEF12"

    def test_immutable(self):
        tool = MCPTool(
            name="list_events",
            description="List calendar events",
            input_schema={},
            server_id="MCP-ABCDEF12",
        )
        with pytest.raises((AttributeError, TypeError)):
            tool.name = "other"  # type: ignore[misc]

    def test_equality(self):
        t1 = MCPTool(
            name="get_repo",
            description="Fetch repo info",
            input_schema={"type": "object"},
            server_id="MCP-11111111",
        )
        t2 = MCPTool(
            name="get_repo",
            description="Fetch repo info",
            input_schema={"type": "object"},
            server_id="MCP-11111111",
        )
        assert t1 == t2


# ---------------------------------------------------------------------------
# MCPToolCall
# ---------------------------------------------------------------------------


class TestMCPToolCall:
    def _make(self, **kwargs) -> MCPToolCall:
        defaults = dict(
            call_id="call-001",
            server_id="MCP-ABCDEF12",
            tool_name="list_events",
            arguments={"calendar_id": "primary"},
            trust_tier="AUTO",
            user_id="USER-alice",
            requested_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        defaults.update(kwargs)
        return MCPToolCall(**defaults)

    def test_fields_stored_correctly(self):
        call = self._make()
        assert call.call_id == "call-001"
        assert call.server_id == "MCP-ABCDEF12"
        assert call.tool_name == "list_events"
        assert call.arguments == {"calendar_id": "primary"}
        assert call.trust_tier == "AUTO"
        assert call.user_id == "USER-alice"
        assert call.approved_by is None
        assert call.executed_at is None

    def test_optional_fields_default_to_none(self):
        call = self._make()
        assert call.approved_by is None
        assert call.executed_at is None

    def test_with_approved_by(self):
        call = self._make(approved_by="auto")
        assert call.approved_by == "auto"

    def test_immutable(self):
        call = self._make()
        with pytest.raises((AttributeError, TypeError)):
            call.tool_name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MCPToolResult
# ---------------------------------------------------------------------------


class TestMCPToolResult:
    def test_success_result(self):
        result = MCPToolResult(
            call_id="call-001",
            success=True,
            content="event1, event2",
            latency_ms=42,
        )
        assert result.success is True
        assert result.content == "event1, event2"
        assert result.error_message is None
        assert result.latency_ms == 42
        assert result.result_data == {}

    def test_failure_result(self):
        result = MCPToolResult(
            call_id="call-002",
            success=False,
            content="",
            error_message="Connection refused",
        )
        assert result.success is False
        assert result.error_message == "Connection refused"

    def test_default_result_data(self):
        result = MCPToolResult(call_id="x", success=True, content="ok")
        assert result.result_data == {}

    def test_immutable(self):
        result = MCPToolResult(call_id="x", success=True, content="ok")
        with pytest.raises((AttributeError, TypeError)):
            result.success = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MCPServerListing
# ---------------------------------------------------------------------------


class TestMCPServerListing:
    def test_fields_stored_correctly(self):
        listing = MCPServerListing(
            name="io.example/my-server",
            description="An example MCP server",
            publisher="Example Corp",
            version="1.2.3",
            transport="sse",
            tags=["productivity", "calendar"],
            registry_url="https://registry.modelcontextprotocol.io/v0.1/servers/io.example/my-server",
            install_command="npx my-server",
        )
        assert listing.name == "io.example/my-server"
        assert listing.publisher == "Example Corp"
        assert listing.version == "1.2.3"
        assert listing.transport == "sse"
        assert "productivity" in listing.tags
        assert listing.install_command == "npx my-server"

    def test_optional_install_command_defaults_none(self):
        listing = MCPServerListing(
            name="io.example/bare",
            description="",
            publisher="Anon",
            version="0.1.0",
            transport="http",
            tags=[],
            registry_url="https://example.com",
        )
        assert listing.install_command is None

    def test_immutable(self):
        listing = MCPServerListing(
            name="io.example/bare",
            description="",
            publisher="Anon",
            version="0.1.0",
            transport="http",
            tags=[],
            registry_url="https://example.com",
        )
        with pytest.raises((AttributeError, TypeError)):
            listing.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MCPServerVersion
# ---------------------------------------------------------------------------


class TestMCPServerVersion:
    def test_fields_stored_correctly(self):
        v = MCPServerVersion(
            server_name="io.example/my-server",
            version="2.0.0",
            released_at=datetime(2026, 3, 1, tzinfo=UTC),
            changelog="Major release",
        )
        assert v.server_name == "io.example/my-server"
        assert v.version == "2.0.0"
        assert v.changelog == "Major release"

    def test_released_at_none_allowed(self):
        v = MCPServerVersion(
            server_name="io.example/my-server",
            version="0.0.1",
            released_at=None,
        )
        assert v.released_at is None
