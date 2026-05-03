"""scripts/migrate_chat_history.py — One-shot chat history migration (FR-STORE-001).

Migrates ``{user_id}/chat/history.json`` → ``{user_id}/conversations/{user_id}/cockpit/main.jsonl``
and archives the original at ``{user_id}/conversations/.legacy/chat-history.json.archived``.

Usage:
    python scripts/migrate_chat_history.py [--user-id USR-xxx] [--dry-run]

If --user-id is omitted, all users found by listing MinIO objects are migrated.

Environment variables:
    MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# Add project src to path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _build_storage() -> Any:
    """Build an S3StorageClient from environment variables."""
    from graphclaw.infra.storage import S3StorageClient  # noqa: PLC0415

    return S3StorageClient(
        bucket=os.environ.get("MINIO_BUCKET", "graphclaw"),
        endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        principal_name="admin_principal",
    )


def _to_jsonl_entries(history_entries: list[dict], user_id: str) -> list[dict]:
    """Convert legacy history.json entries to new conversation JSONL schema."""
    result = []
    for i, entry in enumerate(history_entries):
        # Legacy format: { human: str, agent: str, timestamp: str }
        ts = entry.get("timestamp") or datetime.now(timezone.utc).isoformat()
        # Human message
        if entry.get("human"):
            result.append(
                {
                    "message_id": f"legacy-{i}-h",
                    "ts": ts,
                    "direction": "in",
                    "channel": "cockpit",
                    "thread_id": "main",
                    "sender_id": user_id,
                    "content": entry["human"],
                    "task_refs": [],
                    "checkin_id": None,
                }
            )
        # Agent response
        if entry.get("agent"):
            result.append(
                {
                    "message_id": f"legacy-{i}-a",
                    "ts": ts,
                    "direction": "out",
                    "channel": "cockpit",
                    "thread_id": "main",
                    "sender_id": "AGENT",
                    "content": entry["agent"],
                    "task_refs": [],
                    "checkin_id": None,
                }
            )
    return result


async def migrate_user(storage: Any, user_id: str, dry_run: bool = False) -> bool:
    """Migrate one user's chat history. Returns True if migrated."""
    from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

    legacy_path = StoragePaths.chat_history(user_id)
    try:
        raw = await storage.read(legacy_path)
    except FileNotFoundError:
        logger.info("No chat history for %s — skipping", user_id)
        return False

    entries = json.loads(raw.decode("utf-8"))
    if not isinstance(entries, list):
        logger.warning("Unexpected format in %s — skipping", legacy_path)
        return False

    jsonl_entries = _to_jsonl_entries(entries, user_id)
    jsonl_bytes = b"\n".join(json.dumps(e).encode() for e in jsonl_entries)

    # Target path: conversations/{user_id}/cockpit/main.jsonl
    thread_path = StoragePaths.conversation_thread(user_id, user_id, "cockpit", "main")
    archive_path = StoragePaths.conversation_legacy_archive(user_id)

    if dry_run:
        logger.info("[DRY RUN] Would write %d entries to %s", len(jsonl_entries), thread_path)
        logger.info("[DRY RUN] Would archive %s → %s", legacy_path, archive_path)
        return True

    await storage.write(thread_path, jsonl_bytes, content_type="application/x-ndjson")
    logger.info("Wrote %d entries to %s", len(jsonl_entries), thread_path)

    # Archive original (NOT delete — Wave 0 no-delete principle).
    await storage.write(archive_path, raw, content_type="application/json")
    logger.info("Archived original to %s", archive_path)

    return True


async def main(user_ids: list[str] | None = None, dry_run: bool = False) -> None:
    storage = await _build_storage()

    if user_ids:
        targets = user_ids
    else:
        # Discover all users by listing objects.
        all_objects = await storage.list_objects("")
        # user prefixes are like USER-xxx/ or USR-xxx/
        prefixes: set[str] = set()
        for obj in all_objects:
            if "/" in obj:
                prefix = obj.split("/")[0]
                if prefix.startswith(("USER-", "USR-")):
                    prefixes.add(prefix)
        targets = sorted(prefixes)
        logger.info("Found %d user prefixes", len(targets))

    migrated = 0
    for uid in targets:
        ok = await migrate_user(storage, uid, dry_run=dry_run)
        if ok:
            migrated += 1

    logger.info("Done. Migrated %d / %d users.", migrated, len(targets))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate chat history to conversation layout")
    parser.add_argument("--user-id", action="append", help="Specific user ID(s) to migrate")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    asyncio.run(main(user_ids=args.user_id, dry_run=args.dry_run))
