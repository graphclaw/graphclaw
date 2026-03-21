"""tests.test_gateway.test_slack — Unit tests for Slack channel adapter.

Tests the normalizer, config, sender, and adapter independently.
No real Slack API calls are made.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.gateway.channels.slack.config import SlackConfig
from graphclaw.gateway.channels.slack.normalizer import normalize_slack
from graphclaw.gateway.schemas import InboundMessage


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSlackConfig:
    def test_slack_config_from_env(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing-secret")
        config = SlackConfig.from_env()
        assert config is not None
        assert config.bot_token == "xoxb-test-token"
        assert config.signing_secret == "test-signing-secret"
        assert config.default_channel == "#general"

    def test_slack_config_from_env_missing_token_returns_none(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        assert SlackConfig.from_env() is None

    def test_slack_config_default_channel_override(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-tok")
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
        monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#alerts")
        config = SlackConfig.from_env()
        assert config is not None
        assert config.default_channel == "#alerts"


# ---------------------------------------------------------------------------
# Normalizer tests
# ---------------------------------------------------------------------------


_SAMPLE_SLACK_PAYLOAD = {
    "type": "event_callback",
    "event": {
        "type": "message",
        "channel": "C12345678",
        "user": "U87654321",
        "text": "Hello world",
        "ts": "1700000000.123456",
    },
}


class TestSlackNormalizer:
    def test_slack_normalizer_plain_message(self):
        msg = normalize_slack(_SAMPLE_SLACK_PAYLOAD)
        assert msg is not None
        assert isinstance(msg, InboundMessage)
        assert msg.channel == "slack"
        assert msg.sender == "U87654321"
        assert msg.body == "Hello world"
        assert msg.message_id == "1700000000.123456"

    def test_slack_normalizer_skips_bot_message(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C12345678",
                "user": "U12345678",
                "bot_id": "B12345678",
                "text": "I am a bot",
                "ts": "1700000001.000000",
            },
        }
        result = normalize_slack(payload)
        assert result is None

    def test_slack_normalizer_strips_mentions(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C12345678",
                "user": "U11111111",
                "text": "<@U12345> hello",
                "ts": "1700000002.000000",
            },
        }
        msg = normalize_slack(payload)
        assert msg is not None
        assert msg.body == "hello"
        assert "<@" not in msg.body

    def test_slack_normalizer_strips_mention_with_display_name(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C12345678",
                "user": "U11111111",
                "text": "<@U12345|alice> please review",
                "ts": "1700000003.000000",
            },
        }
        msg = normalize_slack(payload)
        assert msg is not None
        assert msg.body == "please review"

    def test_slack_normalizer_session_id_set(self):
        msg = normalize_slack(_SAMPLE_SLACK_PAYLOAD)
        assert msg is not None
        assert msg.session_id.startswith("SES-")

    def test_slack_normalizer_raw_headers_populated(self):
        msg = normalize_slack(_SAMPLE_SLACK_PAYLOAD)
        assert msg is not None
        assert msg.raw_headers.get("slack_channel") == "C12345678"
        assert msg.raw_headers.get("slack_ts") == "1700000000.123456"

    def test_slack_normalizer_skips_non_message_event(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "reaction_added",
                "user": "U12345678",
                "reaction": "thumbsup",
            },
        }
        assert normalize_slack(payload) is None

    def test_slack_normalizer_thread_ts_in_raw_headers(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C12345678",
                "user": "U11111111",
                "text": "reply in thread",
                "ts": "1700000005.000000",
                "thread_ts": "1700000000.000000",
            },
        }
        msg = normalize_slack(payload)
        assert msg is not None
        assert msg.raw_headers.get("slack_thread_ts") == "1700000000.000000"


# ---------------------------------------------------------------------------
# Webhook signature tests
# ---------------------------------------------------------------------------


class TestSlackWebhookSignature:
    def _make_adapter_with_secret(self, secret: str):
        from graphclaw.gateway.channels.slack.adapter import SlackAdapter

        adapter = SlackAdapter()
        adapter._config = SlackConfig(
            bot_token="xoxb-tok",
            signing_secret=secret,
        )
        return adapter

    def _compute_signature(self, secret: str, timestamp: str, body: bytes) -> str:
        sig_basestring = f"v0:{timestamp}:".encode() + body
        digest = hmac.new(secret.encode(), sig_basestring, hashlib.sha256).hexdigest()
        return f"v0={digest}"

    def test_slack_webhook_signature_valid(self):
        secret = "my-signing-secret"
        timestamp = "1700000000"
        body = b'{"type":"event_callback"}'
        signature = self._compute_signature(secret, timestamp, body)

        adapter = self._make_adapter_with_secret(secret)
        assert adapter.verify_webhook_signature(body, timestamp, signature) is True

    def test_slack_webhook_signature_invalid(self):
        secret = "my-signing-secret"
        timestamp = "1700000000"
        body = b'{"type":"event_callback"}'
        # Use wrong secret to compute signature
        wrong_signature = self._compute_signature("wrong-secret", timestamp, body)

        adapter = self._make_adapter_with_secret(secret)
        assert adapter.verify_webhook_signature(body, timestamp, wrong_signature) is False

    def test_slack_webhook_signature_no_config(self):
        from graphclaw.gateway.channels.slack.adapter import SlackAdapter

        adapter = SlackAdapter()
        assert adapter.verify_webhook_signature(b"body", "ts", "v0=sig") is False


# ---------------------------------------------------------------------------
# Sender tests
# ---------------------------------------------------------------------------


class TestSlackSender:
    @pytest.mark.asyncio
    async def test_slack_sender_send(self):
        from graphclaw.gateway.channels.slack.sender import SlackSender

        config = SlackConfig(bot_token="xoxb-test", signing_secret="secret")
        sender = SlackSender(config)

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await sender.send(channel="#general", text="Hello Slack")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "chat.postMessage" in call_kwargs[0][0]
        payload = call_kwargs[1]["json"]
        assert payload["channel"] == "#general"
        assert payload["text"] == "Hello Slack"
        headers = call_kwargs[1]["headers"]
        assert headers["Authorization"] == "Bearer xoxb-test"

    @pytest.mark.asyncio
    async def test_slack_sender_send_with_blocks(self):
        from graphclaw.gateway.channels.slack.sender import SlackSender

        config = SlackConfig(bot_token="xoxb-test", signing_secret="secret")
        sender = SlackSender(config)

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*bold*"}}]
        with patch("httpx.AsyncClient", return_value=mock_client):
            await sender.send(channel="C123", text="fallback", blocks=blocks)

        payload = mock_client.post.call_args[1]["json"]
        assert payload["blocks"] == blocks

    @pytest.mark.asyncio
    async def test_slack_sender_raises_on_api_error(self):
        from graphclaw.gateway.channels.slack.sender import SlackSender

        config = SlackConfig(bot_token="xoxb-test", signing_secret="secret")
        sender = SlackSender(config)

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="channel_not_found"):
                await sender.send(channel="#bad", text="test")


# ---------------------------------------------------------------------------
# Adapter tests
# ---------------------------------------------------------------------------


class TestSlackAdapter:
    def test_slack_adapter_channel_name(self):
        from graphclaw.gateway.channels.slack.adapter import SlackAdapter

        assert SlackAdapter().channel_name == "slack"

    @pytest.mark.asyncio
    async def test_start_sets_config(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-secret")

        from graphclaw.gateway.channels.slack.adapter import SlackAdapter

        broker = AsyncMock()
        adapter = SlackAdapter()
        await adapter.start(broker)
        assert adapter._config is not None
        assert adapter._sender is not None
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_start_skips_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

        from graphclaw.gateway.channels.slack.adapter import SlackAdapter

        broker = AsyncMock()
        adapter = SlackAdapter()
        await adapter.start(broker)
        assert adapter._config is None

    @pytest.mark.asyncio
    async def test_handle_webhook_publishes_message(self):
        from graphclaw.gateway.channels.slack.adapter import SlackAdapter

        broker = AsyncMock()
        adapter = SlackAdapter()
        adapter._config = SlackConfig(bot_token="xoxb-tok", signing_secret="sec")
        adapter._broker = broker

        msg = await adapter.handle_webhook(_SAMPLE_SLACK_PAYLOAD)
        assert msg is not None
        broker.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_webhook_url_verification_returns_none(self):
        from graphclaw.gateway.channels.slack.adapter import SlackAdapter

        adapter = SlackAdapter()
        result = await adapter.handle_webhook({"type": "url_verification", "challenge": "abc"})
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_webhook_bot_message_not_published(self):
        from graphclaw.gateway.channels.slack.adapter import SlackAdapter

        broker = AsyncMock()
        adapter = SlackAdapter()
        adapter._config = SlackConfig(bot_token="xoxb-tok", signing_secret="sec")
        adapter._broker = broker

        bot_payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "bot_id": "B123",
                "text": "bot message",
                "channel": "C123",
                "ts": "1700000000.000001",
            },
        }
        msg = await adapter.handle_webhook(bot_payload)
        assert msg is None
        broker.publish.assert_not_called()
