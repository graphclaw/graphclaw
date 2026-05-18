# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_mcp.test_official_registry — Unit tests for OfficialMCPRegistry.

Description
-----------
Tests ``OfficialMCPRegistry.search``, ``get_versions``, and pagination using
a mocked ``httpx.AsyncClient`` so no real HTTP calls are made.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock, MagicMock, patch.
- graphclaw.mcp.official_registry: OfficialMCPRegistry.
- graphclaw.mcp.models: MCPServerListing, MCPServerVersion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.mcp.models import MCPServerListing, MCPServerVersion
from graphclaw.mcp.official_registry import OfficialMCPRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_response(payload: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


SERVER_PAYLOAD_1 = {
    "name": "io.example/calendar",
    "description": "Google Calendar integration",
    "publisher": "Example Corp",
    "version": "1.0.0",
    "transport": "sse",
    "tags": ["calendar", "productivity"],
    "url": "https://registry.modelcontextprotocol.io/v0.1/servers/io.example/calendar",
}

SERVER_PAYLOAD_2 = {
    "name": "io.example/github",
    "description": "GitHub integration",
    "publisher": "Example Corp",
    "version": "2.0.0",
    "transport": "http",
    "tags": ["vcs", "dev"],
    "url": "https://registry.modelcontextprotocol.io/v0.1/servers/io.example/github",
}


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestOfficialMCPRegistrySearch:
    @pytest.mark.asyncio
    async def test_search_returns_server_listings(self):
        payload = {"servers": [SERVER_PAYLOAD_1, SERVER_PAYLOAD_2], "nextCursor": None}

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(return_value=make_mock_response(payload))

        results = await registry.search("calendar", limit=10)
        await registry.close()

        assert len(results) == 2
        assert isinstance(results[0], MCPServerListing)
        assert results[0].name == "io.example/calendar"
        assert results[0].transport == "sse"
        assert "calendar" in results[0].tags

    @pytest.mark.asyncio
    async def test_search_respects_limit(self):
        payload = {"servers": [SERVER_PAYLOAD_1, SERVER_PAYLOAD_2], "nextCursor": None}

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(return_value=make_mock_response(payload))

        results = await registry.search(limit=1)
        await registry.close()

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_returns_empty_when_no_servers(self):
        payload = {"servers": [], "nextCursor": None}

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(return_value=make_mock_response(payload))

        results = await registry.search()
        await registry.close()

        assert results == []

    @pytest.mark.asyncio
    async def test_pagination_fetches_next_page_on_cursor(self):
        """When first page has nextCursor and limit not yet satisfied, fetch next page."""
        page1 = {
            "servers": [SERVER_PAYLOAD_1],
            "nextCursor": "cursor-abc",
        }
        page2 = {
            "servers": [SERVER_PAYLOAD_2],
            "nextCursor": None,
        }

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(
            side_effect=[
                make_mock_response(page1),
                make_mock_response(page2),
            ]
        )

        results = await registry.search(limit=5)
        await registry.close()

        # Should have fetched both pages
        assert len(results) == 2
        assert registry._http.get.call_count == 2
        names = [r.name for r in results]
        assert "io.example/calendar" in names
        assert "io.example/github" in names

    @pytest.mark.asyncio
    async def test_pagination_stops_when_limit_reached_before_next_cursor(self):
        """If limit is reached during first page processing, second page is not fetched."""
        page1 = {
            "servers": [SERVER_PAYLOAD_1, SERVER_PAYLOAD_2],
            "nextCursor": "cursor-abc",
        }

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(return_value=make_mock_response(page1))

        results = await registry.search(limit=1)
        await registry.close()

        assert len(results) == 1
        # Only one HTTP call since limit was satisfied
        assert registry._http.get.call_count == 1


# ---------------------------------------------------------------------------
# get_versions
# ---------------------------------------------------------------------------


class TestOfficialMCPRegistryGetVersions:
    @pytest.mark.asyncio
    async def test_get_versions_returns_sorted_list(self):
        payload = {
            "versions": [
                {
                    "version": "1.0.0",
                    "releasedAt": "2026-01-01T00:00:00+00:00",
                    "changelog": "Initial release",
                },
                {
                    "version": "2.0.0",
                    "releasedAt": "2026-03-01T00:00:00+00:00",
                    "changelog": "Major release",
                },
            ]
        }

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(return_value=make_mock_response(payload))

        versions = await registry.get_versions("io.example/calendar")
        await registry.close()

        assert len(versions) == 2
        # Sorted newest-first
        assert versions[0].version == "2.0.0"
        assert versions[1].version == "1.0.0"
        assert isinstance(versions[0], MCPServerVersion)

    @pytest.mark.asyncio
    async def test_get_versions_handles_missing_released_at(self):
        payload = {
            "versions": [
                {"version": "0.1.0"},
            ]
        }

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(return_value=make_mock_response(payload))

        versions = await registry.get_versions("io.example/bare")
        await registry.close()

        assert len(versions) == 1
        assert versions[0].released_at is None

    @pytest.mark.asyncio
    async def test_get_latest_returns_newest_version(self):
        payload = {
            "versions": [
                {"version": "1.0.0", "releasedAt": "2025-01-01T00:00:00+00:00"},
                {"version": "3.0.0", "releasedAt": "2026-03-01T00:00:00+00:00"},
                {"version": "2.0.0", "releasedAt": "2026-01-01T00:00:00+00:00"},
            ]
        }

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(return_value=make_mock_response(payload))

        latest = await registry.get_latest("io.example/server")
        await registry.close()

        assert latest.version == "3.0.0"

    @pytest.mark.asyncio
    async def test_get_latest_raises_when_no_versions(self):
        payload = {"versions": []}

        registry = OfficialMCPRegistry()
        registry._http = AsyncMock()
        registry._http.get = AsyncMock(return_value=make_mock_response(payload))

        with pytest.raises(ValueError, match="No versions found"):
            await registry.get_latest("io.example/empty")
        await registry.close()
