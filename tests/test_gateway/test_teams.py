# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_gateway.test_teams — Unit tests for Microsoft Teams channel adapter.

Tests the normalizer, config, sender, and adapter independently.
No real Teams API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.gateway.channels.teams.config import TeamsConfig
from graphclaw.gateway.channels.teams.normalizer import normalize_teams
from graphclaw.gateway.schemas import InboundMessage

# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestTeamsConfig:
    def test_teams_config_from_env(self, monkeypatch):
        monkeypatch.setenv("TEAMS_TENANT_ID", "tenant-abc")
        monkeypatch.setenv("TEAMS_CLIENT_ID", "client-xyz")
        monkeypatch.setenv("TEAMS_CLIENT_SECRET", "secret-123")
        monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://outlook.office.com/webhook/...")
        config = TeamsConfig.from_env()
        assert config is not None
        assert config.tenant_id == "tenant-abc"
        assert config.client_id == "client-xyz"
        assert config.client_secret == "secret-123"
        assert config.webhook_url == "https://outlook.office.com/webhook/..."

    def test_teams_config_from_env_missing_tenant_id_returns_none(self, monkeypatch):
        monkeypatch.delenv("TEAMS_TENANT_ID", raising=False)
        assert TeamsConfig.from_env() is None

    def test_teams_config_webhook_url_defaults_to_empty(self, monkeypatch):
        monkeypatch.setenv("TEAMS_TENANT_ID", "tenant-abc")
        monkeypatch.setenv("TEAMS_CLIENT_ID", "client-xyz")
        monkeypatch.setenv("TEAMS_CLIENT_SECRET", "secret-123")
        monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
        config = TeamsConfig.from_env()
        assert config is not None
        assert config.webhook_url == ""


# ---------------------------------------------------------------------------
# Normalizer tests
# ---------------------------------------------------------------------------


_SAMPLE_TEAMS_ACTIVITY = {
    "type": "message",
    "id": "1700000000123",
    "timestamp": "2024-01-01T12:00:00Z",
    "text": "Hello from Teams",
    "from": {
        "id": "29:1abc123",
        "name": "Alice Smith",
    },
    "channelData": {
        "channel": {
            "id": "19:abc123@thread.skype",
        }
    },
}


class TestTeamsNormalizer:
    def test_teams_normalizer_plain_message(self):
        msg = normalize_teams(_SAMPLE_TEAMS_ACTIVITY)
        assert msg is not None
        assert isinstance(msg, InboundMessage)
        assert msg.channel == "teams"
        assert msg.sender == "29:1abc123"
        assert msg.body == "Hello from Teams"
        assert msg.message_id == "1700000000123"

    def test_teams_normalizer_subject_contains_sender_name(self):
        msg = normalize_teams(_SAMPLE_TEAMS_ACTIVITY)
        assert msg is not None
        assert "Alice Smith" in msg.subject

    def test_teams_normalizer_raw_headers_populated(self):
        msg = normalize_teams(_SAMPLE_TEAMS_ACTIVITY)
        assert msg is not None
        assert msg.raw_headers.get("teams_sender_name") == "Alice Smith"
        assert msg.raw_headers.get("teams_activity_id") == "1700000000123"
        assert msg.raw_headers.get("teams_channel_id") == "19:abc123@thread.skype"

    def test_teams_normalizer_strips_mention(self):
        activity = {
            "type": "message",
            "id": "act-001",
            "timestamp": "2024-01-01T12:00:00Z",
            "text": "<at>GraphClawBot</at> hello",
            "from": {"id": "29:abc", "name": "Bob"},
        }
        msg = normalize_teams(activity)
        assert msg is not None
        assert msg.body == "hello"
        assert "<at>" not in msg.body

    def test_teams_normalizer_strips_multiple_mentions(self):
        activity = {
            "type": "message",
            "id": "act-002",
            "timestamp": "2024-01-01T12:00:00Z",
            "text": "<at>Bot</at> and <at>Alice</at> please review",
            "from": {"id": "29:xyz", "name": "Carol"},
        }
        msg = normalize_teams(activity)
        assert msg is not None
        assert "<at>" not in msg.body
        assert "please review" in msg.body

    def test_teams_normalizer_session_id_set(self):
        msg = normalize_teams(_SAMPLE_TEAMS_ACTIVITY)
        assert msg is not None
        assert msg.session_id.startswith("SES-")

    def test_teams_normalizer_skips_non_message_activity(self):
        activity = {
            "type": "conversationUpdate",
            "id": "act-003",
            "from": {"id": "29:abc"},
        }
        assert normalize_teams(activity) is None

    def test_teams_normalizer_skips_missing_sender(self):
        activity = {
            "type": "message",
            "id": "act-004",
            "text": "orphan message",
            "from": {},
        }
        assert normalize_teams(activity) is None

    def test_teams_normalizer_malformed_payload_returns_none(self):
        assert normalize_teams({}) is None
        assert normalize_teams({"type": "message"}) is None


# ---------------------------------------------------------------------------
# Sender tests
# ---------------------------------------------------------------------------


class TestTeamsSender:
    @pytest.mark.asyncio
    async def test_teams_sender_send(self):
        from graphclaw.gateway.channels.teams.sender import TeamsSender

        sender = TeamsSender()
        webhook_url = "https://outlook.office.com/webhook/test"

        mock_response = MagicMock()
        mock_response.is_success = True

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await sender.send(webhook_url=webhook_url, text="Hello Teams")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == webhook_url
        payload = call_args[1]["json"]
        assert payload["type"] == "message"
        attachments = payload["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["contentType"] == "application/vnd.microsoft.card.adaptive"
        card_body = attachments[0]["content"]["body"]
        assert any(b.get("text") == "Hello Teams" for b in card_body)

    @pytest.mark.asyncio
    async def test_teams_sender_send_with_title(self):
        from graphclaw.gateway.channels.teams.sender import TeamsSender

        sender = TeamsSender()
        webhook_url = "https://outlook.office.com/webhook/test"

        mock_response = MagicMock()
        mock_response.is_success = True

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await sender.send(webhook_url=webhook_url, text="Body text", title="My Title")

        payload = mock_client.post.call_args[1]["json"]
        card_body = payload["attachments"][0]["content"]["body"]
        texts = [b.get("text") for b in card_body]
        assert "My Title" in texts
        assert "Body text" in texts

    @pytest.mark.asyncio
    async def test_teams_sender_raises_on_http_error(self):
        from graphclaw.gateway.channels.teams.sender import TeamsSender

        sender = TeamsSender()

        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="HTTP 400"):
                await sender.send(webhook_url="https://example.com", text="test")


# ---------------------------------------------------------------------------
# Adapter tests
# ---------------------------------------------------------------------------


class TestTeamsAdapter:
    def test_teams_adapter_channel_name(self):
        from graphclaw.gateway.channels.teams.adapter import TeamsAdapter

        assert TeamsAdapter().channel_name == "teams"

    @pytest.mark.asyncio
    async def test_start_sets_config(self, monkeypatch):
        monkeypatch.setenv("TEAMS_TENANT_ID", "tenant-abc")
        monkeypatch.setenv("TEAMS_CLIENT_ID", "client-xyz")
        monkeypatch.setenv("TEAMS_CLIENT_SECRET", "secret-123")

        from graphclaw.gateway.channels.teams.adapter import TeamsAdapter

        broker = AsyncMock()
        adapter = TeamsAdapter()
        await adapter.start(broker)
        assert adapter._config is not None
        assert adapter._sender is not None
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_start_skips_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("TEAMS_TENANT_ID", raising=False)

        from graphclaw.gateway.channels.teams.adapter import TeamsAdapter

        broker = AsyncMock()
        adapter = TeamsAdapter()
        await adapter.start(broker)
        assert adapter._config is None

    @pytest.mark.asyncio
    async def test_handle_activity_publishes_message(self):
        from graphclaw.gateway.channels.teams.adapter import TeamsAdapter

        broker = AsyncMock()
        adapter = TeamsAdapter()
        adapter._config = TeamsConfig(tenant_id="t", client_id="c", client_secret="s")
        adapter._broker = broker

        msg = await adapter.handle_activity(_SAMPLE_TEAMS_ACTIVITY)
        assert msg is not None
        broker.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_activity_non_message_not_published(self):
        from graphclaw.gateway.channels.teams.adapter import TeamsAdapter

        broker = AsyncMock()
        adapter = TeamsAdapter()
        adapter._config = TeamsConfig(tenant_id="t", client_id="c", client_secret="s")
        adapter._broker = broker

        msg = await adapter.handle_activity({"type": "conversationUpdate"})
        assert msg is None
        broker.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_activity_no_broker(self):
        from graphclaw.gateway.channels.teams.adapter import TeamsAdapter

        adapter = TeamsAdapter()
        # _broker is None
        msg = await adapter.handle_activity(_SAMPLE_TEAMS_ACTIVITY)
        # Message normalizes fine but broker.publish is skipped; msg may still be returned
        assert msg is None or isinstance(msg, InboundMessage)
