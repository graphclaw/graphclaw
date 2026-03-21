"""graphclaw.gateway.attachment_handler — Channel attachment download and storage.

Description
-----------
Downloads non-text attachments from WhatsApp and Telegram (images, audio,
documents, video) and stores them in S3/MinIO under a structured key path.

Flow:
    1. Receive raw attachment dict from channel normalizer
       (``extract_whatsapp_attachments`` or ``extract_telegram_attachments``).
    2. Download the binary content from the channel's media API.
    3. Upload to ``StorageClient`` under ``attachments/{channel}/{date}/{msg_id}/{filename}``.
    4. Return a list of storage keys so the caller can attach them to an ``InboundMessage``.

Design Patterns
---------------
- Strategy: ``_download_whatsapp`` and ``_download_telegram`` are separate
  private methods selected by channel name — same public interface, different
  download protocols.
- Graceful degradation: If storage or httpx is unavailable, methods return
  empty lists and log warnings.

Public API
----------
- AttachmentHandler: Main handler class.
  - ``process(channel, attachment_dict, config)`` — Download + store one attachment.
  - ``process_all(channel, attachments, config)`` — Process a list of attachments.

Dependencies
------------
- graphclaw.infra.storage: StorageClient ABC.
- httpx: Async HTTP client for media downloads.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# S3 prefix for all attachment objects
_ATTACHMENTS_PREFIX = "attachments"


class AttachmentHandler:
    """Downloads channel attachments and stores them in S3/MinIO."""

    def __init__(self, storage_client: Any | None = None) -> None:
        """
        Args:
            storage_client: A ``StorageClient`` instance (S3StorageClient or stub).
                If ``None``, the handler operates in no-op mode.
        """
        self._storage = storage_client

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def process_all(
        self,
        channel: str,
        attachments: list[dict[str, Any]],
        channel_config: Any | None = None,
        bucket: str = "graphclaw",
    ) -> list[str]:
        """Download and store all attachments for a message.

        Args:
            channel: Channel name (``"whatsapp"`` or ``"telegram"``).
            attachments: List of raw attachment dicts from channel normalizer.
            channel_config: Channel config object (``WhatsAppConfig`` or
                ``TelegramConfig``) with API credentials.
            bucket: S3/MinIO bucket name.

        Returns:
            List of storage keys for successfully stored attachments.
        """
        keys: list[str] = []
        for attachment in attachments:
            key = await self.process(channel, attachment, channel_config, bucket)
            if key:
                keys.append(key)
        return keys

    async def process(
        self,
        channel: str,
        attachment: dict[str, Any],
        channel_config: Any | None = None,
        bucket: str = "graphclaw",
    ) -> str | None:
        """Download and store a single attachment.

        Args:
            channel: Channel name.
            attachment: Raw attachment dict from channel normalizer.
            channel_config: Channel config with API credentials.
            bucket: S3/MinIO bucket name.

        Returns:
            Storage key string on success, ``None`` on failure.
        """
        if self._storage is None:
            logger.warning("AttachmentHandler: storage not configured, skipping attachment")
            return None

        try:
            import httpx  # noqa: PLC0415,F401
        except ImportError:
            logger.warning("AttachmentHandler: httpx not installed, cannot download attachments")
            return None

        try:
            if channel == "whatsapp":
                data, content_type = await self._download_whatsapp(attachment, channel_config)
            elif channel == "telegram":
                data, content_type = await self._download_telegram(attachment, channel_config)
            else:
                logger.warning("AttachmentHandler: unsupported channel %r for attachments", channel)
                return None

            if data is None:
                return None

            key = self._build_storage_key(channel, attachment, content_type)
            await self._storage.write(bucket, key, data)
            logger.info("AttachmentHandler: stored attachment at %s/%s", bucket, key)
            return key

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "AttachmentHandler: failed to process attachment %s: %s",
                attachment.get("msg_id", "?"),
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Channel-specific downloaders
    # ------------------------------------------------------------------

    async def _download_whatsapp(
        self,
        attachment: dict[str, Any],
        config: Any | None,
    ) -> tuple[bytes | None, str]:
        """Download a WhatsApp media object via the Graph API.

        Returns ``(bytes, content_type)`` on success or ``(None, "")`` on failure.
        """
        import httpx  # noqa: PLC0415,F401

        media_id = attachment.get("media_id", "")
        if not media_id:
            return None, ""

        if config is None:
            logger.warning("AttachmentHandler: WhatsApp config missing, cannot download")
            return None, ""

        headers = {
            "Authorization": f"Bearer {config.access_token}",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Get download URL for the media ID
            meta_url = f"https://graph.facebook.com/{config.api_version}/{media_id}"
            meta_resp = await client.get(meta_url, headers=headers)
            if not meta_resp.is_success:
                logger.warning(
                    "AttachmentHandler: WhatsApp media lookup failed: HTTP %s",
                    meta_resp.status_code,
                )
                return None, ""

            meta = meta_resp.json()
            download_url = meta.get("url", "")
            content_type = meta.get(
                "mime_type", attachment.get("mime_type", "application/octet-stream")
            )

            if not download_url:
                return None, ""

            # Step 2: Download the actual binary content
            dl_resp = await client.get(download_url, headers=headers)
            if not dl_resp.is_success:
                logger.warning(
                    "AttachmentHandler: WhatsApp media download failed: HTTP %s",
                    dl_resp.status_code,
                )
                return None, ""

            return dl_resp.content, content_type

    async def _download_telegram(
        self,
        attachment: dict[str, Any],
        config: Any | None,
    ) -> tuple[bytes | None, str]:
        """Download a Telegram file via the Bot API getFile method.

        Returns ``(bytes, content_type)`` on success or ``(None, "")`` on failure.
        """
        import httpx  # noqa: PLC0415,F401

        file_id = attachment.get("file_id", "")
        if not file_id:
            return None, ""

        if config is None:
            logger.warning("AttachmentHandler: Telegram config missing, cannot download")
            return None, ""

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Get file path from getFile
            get_file_url = f"{config.api_base_url}/getFile"
            resp = await client.get(get_file_url, params={"file_id": file_id})
            if not resp.is_success:
                logger.warning(
                    "AttachmentHandler: Telegram getFile failed: HTTP %s", resp.status_code
                )
                return None, ""

            data = resp.json()
            if not data.get("ok"):
                return None, ""

            file_path = data.get("result", {}).get("file_path", "")
            if not file_path:
                return None, ""

            # Step 2: Download from file CDN
            bot_token = config.bot_token
            cdn_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            dl_resp = await client.get(cdn_url)
            if not dl_resp.is_success:
                logger.warning(
                    "AttachmentHandler: Telegram file download failed: HTTP %s",
                    dl_resp.status_code,
                )
                return None, ""

            content_type = attachment.get("mime_type", "") or _infer_mime(file_path)
            return dl_resp.content, content_type

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_storage_key(
        channel: str,
        attachment: dict[str, Any],
        content_type: str,
    ) -> str:
        """Build a deterministic S3 key for an attachment.

        Format: ``attachments/{channel}/{YYYY-MM-DD}/{msg_id}/{unique}_{filename}``
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        msg_id = attachment.get("msg_id", "unknown").replace("/", "_")
        filename = attachment.get("filename", "") or _filename_from_type(
            attachment.get("type", "file"), content_type
        )
        unique = uuid.uuid4().hex[:8]
        return f"{_ATTACHMENTS_PREFIX}/{channel}/{today}/{msg_id}/{unique}_{filename}"


def _infer_mime(file_path: str) -> str:
    """Guess MIME type from file extension."""
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"


def _filename_from_type(media_type: str, content_type: str) -> str:
    """Generate a fallback filename from media type + content type."""
    ext = mimetypes.guess_extension(content_type) or ""
    return f"{media_type}{ext}"
