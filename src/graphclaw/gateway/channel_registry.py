"""graphclaw.gateway.channel_registry — Channel discovery and routing.

Description
-----------
Provides ``ChannelRegistry`` for managing channel adapters and ``build_registry``
for discovering and loading enabled channels via importlib.

Design Patterns
---------------
- Registry: Central registry of channel adapters indexed by channel_name.
- Plugin Discovery: ``build_registry`` uses importlib to load channel modules
  by convention (each channel subfolder exports an ``Adapter`` class).

Public API
----------
- ChannelRegistry: Manages channel adapters — register, lookup, start/stop all, route outbound.
- build_registry: Discover and load enabled channels into a registry.

Dependencies
------------
- importlib: Dynamic module loading for channel discovery.
- graphclaw.gateway.channel_base: ChannelAdapter ABC.
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker.
"""

from __future__ import annotations

import importlib
import logging

from graphclaw.gateway.channel_base import ChannelAdapter
from graphclaw.gateway.schemas import OutboundMessage
from graphclaw.infra.broker import MessageBroker

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """Registry of active channel adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        self._adapters[adapter.channel_name] = adapter
        logger.info("Registered channel adapter: %s", adapter.channel_name)

    def get(self, channel_name: str) -> ChannelAdapter | None:
        return self._adapters.get(channel_name)

    @property
    def all_adapters(self) -> list[ChannelAdapter]:
        return list(self._adapters.values())

    async def start_all(self, broker: MessageBroker) -> None:
        for adapter in self._adapters.values():
            logger.info("Starting channel: %s", adapter.channel_name)
            await adapter.start(broker)

    async def stop_all(self) -> None:
        for adapter in self._adapters.values():
            logger.info("Stopping channel: %s", adapter.channel_name)
            await adapter.stop()

    async def route_outbound(self, message: OutboundMessage) -> bool:
        adapter = self._adapters.get(message.channel)
        if adapter is None:
            logger.warning(
                "No adapter for channel %r, dropping message %s",
                message.channel,
                message.message_id,
            )
            return False
        await adapter.send(message)
        return True


def build_registry(enabled_channels: list[str] | None = None) -> ChannelRegistry:
    """Build a ChannelRegistry from a list of enabled channel names.

    Each channel name corresponds to a subpackage under
    ``graphclaw.gateway.channels.{name}`` that exports an ``Adapter`` class.

    If ``enabled_channels`` is ``None``, the list is read from the
    ``GATEWAY_ENABLED_CHANNELS`` environment variable (comma-separated).
    Defaults to ``["email"]`` when the env var is also absent.
    """
    import os  # noqa: PLC0415

    if enabled_channels is None:
        raw = os.environ.get("GATEWAY_ENABLED_CHANNELS", "email")
        enabled_channels = [ch.strip() for ch in raw.split(",") if ch.strip()]

    registry = ChannelRegistry()
    for name in enabled_channels:
        try:
            module = importlib.import_module(f"graphclaw.gateway.channels.{name}")
            adapter_cls = getattr(module, "Adapter")
            adapter = adapter_cls()
            registry.register(adapter)
        except (ImportError, AttributeError) as exc:
            logger.error("Failed to load channel %r: %s", name, exc)
    return registry
