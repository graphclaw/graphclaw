"""tests.test_gateway.test_telegram — Unit tests for Telegram channel adapter.

Tests the normalizer, config, sender, and adapter independently.
No real Telegram API calls are made.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from graphclaw.gateway.channels.telegram.config import TelegramConfig
from graphclaw.gateway.channels.telegram.normalizer import (
    extract_telegram_attachments,
    normalize_telegram,
)
from graphclaw.gateway.schemas import InboundMessage


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestTelegramConfig:
    def test_from_env_with_token(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
        config = TelegramConfig.from_env()
        assert config is not None
        assert config.bot_token == "123456:ABC-DEF"
        assert config.use_webhook is False
        assert config.poll_timeout == 30

    def test_from_env_webhook_mode(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_USE_WEBHOOK", "true")
        config = TelegramConfig.from_env()
        assert config is not None
        assert config.use_webhook is True

    def test_from_env_missing_token_returns_none(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        assert TelegramConfig.from_env() is None

    def test_api_base_url(self):
        config = TelegramConfig(bot_token="123:ABC")
        assert config.api_base_url == "https://api.telegram.org/bot123:ABC"


# ---------------------------------------------------------------------------
# Normalizer tests
# ---------------------------------------------------------------------------


_SAMPLE_UPDATE = {
    "update_id": 123456789,
    "message": {
        "message_id": 42,
        "from": {
            "id": 987654321,
            "first_name": "Alice",
            "last_name": "Smith",
            "username": "alicesmith",
        },
        "chat": {"id": 987654321, "type": "private"},
        "date": 1700000000,
        "text": "Hello from Telegram",
    },
}


class TestNormalizeTelegram:
    def test_extracts_text_message(self):
        msgs = normalize_telegram(_SAMPLE_UPDATE)
        assert len(msgs) == 1
        msg = msgs[0]
        assert isinstance(msg, InboundMessage)
        assert msg.channel == "telegram"
        assert msg.sender == "987654321"
        assert msg.body == "Hello from Telegram"

    def test_message_id_uses_update_id(self):
        msgs = normalize_telegram(_SAMPLE_UPDATE)
        assert msgs[0].message_id == "tg-123456789"

    def test_received_at_from_date(self):
        msgs = normalize_telegram(_SAMPLE_UPDATE)
        assert msgs[0].received_at == datetime.fromtimestamp(1700000000, tz=timezone.utc)

    def test_session_id_set(self):
        msgs = normalize_telegram(_SAMPLE_UPDATE)
        assert msgs[0].session_id.startswith("SES-")

    def test_raw_headers_populated(self):
        msgs = normalize_telegram(_SAMPLE_UPDATE)
        h = msgs[0].raw_headers
        assert "Alice Smith" in h.get("tg_sender_name", "")
        assert h.get("tg_update_id") == "123456789"
        assert h.get("tg_chat_id") == "987654321"

    def test_skips_non_text_update(self):
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "from": {"id": 1, "first_name": "Bob"},
                "chat": {"id": 1, "type": "private"},
                "date": 1700000000,
                "photo": [{"file_id": "abc", "file_size": 100}],
                # no "text" key
            },
        }
        assert normalize_telegram(update) == []

    def test_no_message_or_edited_message(self):
        assert normalize_telegram({"update_id": 1}) == []

    def test_edited_message_supported(self):
        update = {
            "update_id": 2,
            "edited_message": {
                "message_id": 5,
                "from": {"id": 1, "first_name": "Bob"},
                "chat": {"id": 1, "type": "private"},
                "date": 1700000000,
                "text": "Edited text",
            },
        }
        msgs = normalize_telegram(update)
        assert len(msgs) == 1
        assert msgs[0].body == "Edited text"

    def test_malformed_update_returns_empty(self):
        assert normalize_telegram({"update_id": None, "message": None}) == []

    def test_sender_display_username_only(self):
        update = {
            "update_id": 3,
            "message": {
                "message_id": 3,
                "from": {"id": 99, "username": "johndoe"},
                "chat": {"id": 99, "type": "private"},
                "date": 1700000000,
                "text": "hi",
            },
        }
        msgs = normalize_telegram(update)
        assert "@johndoe" in msgs[0].subject


class TestExtractTelegramAttachments:
    def test_extracts_photo(self):
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "from": {"id": 1, "first_name": "A"},
                "chat": {"id": 1, "type": "private"},
                "date": 1700000000,
                "photo": [
                    {"file_id": "small_id", "file_size": 100},
                    {"file_id": "large_id", "file_size": 5000},
                ],
            },
        }
        atts = extract_telegram_attachments(update)
        assert len(atts) == 1
        assert atts[0]["type"] == "photo"
        assert atts[0]["file_id"] == "large_id"  # largest

    def test_extracts_document(self):
        update = {
            "update_id": 1,
            "message": {
                "from": {"id": 1},
                "document": {
                    "file_id": "doc_id",
                    "mime_type": "application/pdf",
                    "file_name": "report.pdf",
                },
            },
        }
        atts = extract_telegram_attachments(update)
        assert len(atts) == 1
        assert atts[0]["type"] == "document"
        assert atts[0]["filename"] == "report.pdf"

    def test_skips_text_messages(self):
        update = {
            "update_id": 1,
            "message": {
                "from": {"id": 1},
                "text": "hello",
            },
        }
        assert extract_telegram_attachments(update) == []

    def test_empty_update(self):
        assert extract_telegram_attachments({}) == []


# ---------------------------------------------------------------------------
# Adapter tests
# ---------------------------------------------------------------------------


class TestTelegramChannelAdapter:
    @pytest.fixture
    def _env_vars(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TESTTOKEN")
        monkeypatch.delenv("TELEGRAM_USE_WEBHOOK", raising=False)

    async def test_start_sets_config(self, _env_vars):
        from graphclaw.gateway.channels.telegram.adapter import TelegramChannelAdapter

        broker = AsyncMock()
        adapter = TelegramChannelAdapter()

        # Patch _poll_loop to prevent it from actually running
        import asyncio
        adapter._poll_task = asyncio.create_task(asyncio.sleep(0))

        await adapter.start(broker)
        assert adapter._config is not None
        assert adapter._sender is not None
        await adapter.stop()

    async def test_start_skips_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        from graphclaw.gateway.channels.telegram.adapter import TelegramChannelAdapter

        broker = AsyncMock()
        adapter = TelegramChannelAdapter()
        await adapter.start(broker)
        assert adapter._config is None

    def test_channel_name(self):
        from graphclaw.gateway.channels.telegram.adapter import TelegramChannelAdapter

        assert TelegramChannelAdapter().channel_name == "telegram"

    async def test_handle_update_publishes_message(self, _env_vars):
        from graphclaw.gateway.channels.telegram.adapter import TelegramChannelAdapter

        broker = AsyncMock()
        adapter = TelegramChannelAdapter()
        adapter._config = TelegramConfig(bot_token="tok")
        adapter._broker = broker

        count = await adapter.handle_update(_SAMPLE_UPDATE)
        assert count == 1
        broker.publish.assert_called_once()

    async def test_handle_update_no_broker(self):
        from graphclaw.gateway.channels.telegram.adapter import TelegramChannelAdapter

        adapter = TelegramChannelAdapter()
        # _broker is None
        count = await adapter.handle_update(_SAMPLE_UPDATE)
        assert count == 0

    def test_verify_secret_token_no_secret(self, _env_vars):
        from graphclaw.gateway.channels.telegram.adapter import TelegramChannelAdapter

        adapter = TelegramChannelAdapter()
        adapter._config = TelegramConfig(bot_token="tok", webhook_secret="")
        assert adapter.verify_secret_token("anything") is True

    def test_verify_secret_token_match(self, _env_vars):
        from graphclaw.gateway.channels.telegram.adapter import TelegramChannelAdapter

        adapter = TelegramChannelAdapter()
        adapter._config = TelegramConfig(bot_token="tok", webhook_secret="mysecret")
        assert adapter.verify_secret_token("mysecret") is True
        assert adapter.verify_secret_token("wrong") is False

    def test_verify_secret_no_config(self):
        from graphclaw.gateway.channels.telegram.adapter import TelegramChannelAdapter

        adapter = TelegramChannelAdapter()
        assert adapter.verify_secret_token("anything") is False
