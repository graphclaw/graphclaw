# Adding a Gateway Channel

GraphClaw's channel gateway is fully pluggable. Any messaging platform can be added by implementing the `ChannelAdapter` ABC.

## The ChannelAdapter ABC

```python
# src/graphclaw/gateway/channel_base.py
from abc import ABC, abstractmethod
from typing import Any

class ChannelAdapter(ABC):
    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Unique identifier for this channel (e.g., 'email', 'whatsapp')."""
        ...

    @abstractmethod
    async def start_polling(self) -> None:
        """Begin receiving messages. Runs until stop_polling() is called."""
        ...

    @abstractmethod
    async def stop_polling(self) -> None:
        """Graceful shutdown."""
        ...

    @abstractmethod
    async def send_message(
        self, to: str, body: str, attachments: list[dict[str, Any]] | None = None
    ) -> None:
        """Send a message via this channel."""
        ...

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> "InboundMessage":
        """Convert channel-specific payload to a normalized InboundMessage."""
        ...
```

## The InboundMessage Model

All channels normalize their payloads to `InboundMessage`:

```python
# src/graphclaw/gateway/models.py
class InboundMessage(BaseModel):
    channel: str               # "email" | "whatsapp" | "telegram" | ...
    sender_id: str             # channel-specific sender address/ID
    sender_name: str | None
    subject: str | None        # email subject, message thread title, etc.
    body: str                  # plain text content
    attachments: list[dict]    # [{filename, content_type, size, s3_key}]
    received_at: datetime
    raw_headers: dict          # channel-specific metadata
    message_id: str            # channel-native message ID (for dedup)
```

## Step-by-Step: Add a New Channel

### 1. Create the directory

```
src/graphclaw/gateway/channels/
└── myplatform/
    ├── __init__.py
    └── adapter.py
```

### 2. Implement the ABC

```python
# src/graphclaw/gateway/channels/myplatform/adapter.py
"""Channel adapter for MyPlatform."""
# graphclaw - Apache 2.0 license

from __future__ import annotations

import asyncio
import logging
from typing import Any

from graphclaw.gateway.channel_base import ChannelAdapter
from graphclaw.gateway.models import InboundMessage
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MyPlatformAdapter(ChannelAdapter):
    """ChannelAdapter for MyPlatform webhooks."""

    def __init__(self, api_token: str, webhook_secret: str) -> None:
        self._token = api_token
        self._secret = webhook_secret
        self._running = False

    @property
    def channel_id(self) -> str:
        return "myplatform"

    async def start_polling(self) -> None:
        """Register webhook or start polling loop."""
        self._running = True
        logger.info("MyPlatform adapter started")
        # For webhook-based channels: register webhook URL with platform API
        # For polling: run a loop here
        while self._running:
            await asyncio.sleep(5)  # Replace with real polling logic

    async def stop_polling(self) -> None:
        self._running = False
        logger.info("MyPlatform adapter stopped")

    async def send_message(
        self, to: str, body: str, attachments: list[dict[str, Any]] | None = None
    ) -> None:
        # Call MyPlatform's send API
        logger.info("Sending MyPlatform message to %s", to)

    def normalize(self, raw: dict[str, Any]) -> InboundMessage:
        """Translate MyPlatform webhook payload to InboundMessage."""
        return InboundMessage(
            channel=self.channel_id,
            sender_id=raw["from"]["id"],
            sender_name=raw["from"].get("name"),
            subject=None,
            body=raw["message"]["text"],
            attachments=[],
            received_at=datetime.fromtimestamp(raw["timestamp"], tz=timezone.utc),
            raw_headers={k: v for k, v in raw.items() if k not in ("message",)},
            message_id=raw["message"]["id"],
        )
```

### 3. Export from `__init__.py`

```python
# src/graphclaw/gateway/channels/myplatform/__init__.py
from graphclaw.gateway.channels.myplatform.adapter import MyPlatformAdapter

__all__ = ["MyPlatformAdapter"]
```

### 4. Register in the channel registry

The `ChannelRegistry` auto-discovers channels by scanning the `channels/` folder. As long as the adapter class is importable and implements `ChannelAdapter`, it will be picked up automatically.

If you need explicit registration, add to `src/graphclaw/gateway/channel_registry.py`:

```python
from graphclaw.gateway.channels.myplatform import MyPlatformAdapter
registry.register(MyPlatformAdapter(api_token=..., webhook_secret=...))
```

### 5. Add configuration

Add env vars to `docker/.env.example`:

```
MYPLATFORM_API_TOKEN=
MYPLATFORM_WEBHOOK_SECRET=
```

### 6. Add tests

```python
# tests/test_gateway/test_myplatform.py
import pytest
from graphclaw.gateway.channels.myplatform.adapter import MyPlatformAdapter


@pytest.fixture
def adapter():
    return MyPlatformAdapter(api_token="test-token", webhook_secret="test-secret")


def test_channel_id(adapter):
    assert adapter.channel_id == "myplatform"


def test_normalize_basic_message(adapter):
    raw = {
        "from": {"id": "user123", "name": "Alice"},
        "message": {"id": "msg-1", "text": "Hello world"},
        "timestamp": 1700000000,
    }
    msg = adapter.normalize(raw)
    assert msg.channel == "myplatform"
    assert msg.sender_id == "user123"
    assert msg.body == "Hello world"
    assert msg.message_id == "msg-1"
```

## Security Considerations

For webhook-based channels:
- Always verify HMAC signatures before processing payloads
- Use HTTPS endpoints only
- Validate content-type headers
- Reject payloads larger than a reasonable limit (e.g., 10 MB)

Example HMAC verification:

```python
import hashlib
import hmac

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

## Existing Channels

| Channel | Class | Phase | Protocol |
|---------|-------|-------|----------|
| Email | `EmailAdapter` | Phase 1 (done) | IMAP polling + SMTP send |
| WhatsApp | _(planned)_ | Phase 2 | Webhook + HMAC |
| Telegram | _(planned)_ | Phase 2 | Webhook + bot token |
| Slack | _(planned)_ | Phase 5 | OAuth 2.0 + Events API |
| Teams | _(planned)_ | Phase 5 | OAuth 2.0 + Activity Feed |
