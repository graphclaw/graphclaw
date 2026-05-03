"""scripts/backfill_checkin_fields.py — Best-effort backfill of CheckinNode fields (FR-GRAPH-004).

Reads existing CheckinNodes from the graph, attempts to extract channel/thread_id/
recipient_id/direction from the ``outbound_message`` / ``inbound_response`` fields or
from the intelligence log lines, and writes the fields back.

Missing fields that cannot be inferred are left as NULL and the node is tagged
``legacy=true``.

Usage:
    python scripts/backfill_checkin_fields.py [--dry-run] [--limit 1000]

Environment variables:
    GRAPHCLAW_DB_URL — psycopg3 connection string to the AGE database.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Pattern to detect Telegram thread IDs in outbound_message
_TG_RE = re.compile(r"telegram[:/\s]+(\d+)", re.IGNORECASE)
# Pattern for email thread IDs
_EMAIL_RE = re.compile(r"<([^>]+@[^>]+)>")


def _infer_channel_from_message(message: str | None) -> str | None:
    if not message:
        return None
    m = message.lower()
    if "telegram" in m:
        return "telegram"
    if "whatsapp" in m:
        return "whatsapp"
    if "slack" in m:
        return "slack"
    if "email" in m or "@" in m:
        return "email"
    return None


def _infer_thread_id(message: str | None, channel: str | None) -> str | None:
    if not message:
        return None
    if channel == "telegram":
        m = _TG_RE.search(message)
        return m.group(1) if m else None
    if channel == "email":
        m = _EMAIL_RE.search(message)
        return m.group(1) if m else None
    return None


async def main(dry_run: bool = False, limit: int = 1000) -> None:
    from graphclaw.db.age.pool import create_pool  # noqa: PLC0415
    from graphclaw.db.age.repository import AgeGraphStore  # noqa: PLC0415
    from graphclaw.cross_tenant.acl import system_caller_context  # noqa: PLC0415

    db_url = os.environ.get(
        "GRAPHCLAW_DB_URL", "postgresql://postgres:password@localhost:5432/graphclaw"
    )
    pool = await create_pool(db_url)
    store = AgeGraphStore(pool)
    ctx = system_caller_context("admin_principal")

    nodes = await store.list_nodes("CheckinNode", caller_context=ctx)
    total = len(nodes)
    logger.info("Found %d CheckinNode(s) to inspect", total)

    backfilled = 0
    tagged_legacy = 0

    for node in nodes[:limit]:
        node_id = node.get("id")
        if not node_id:
            continue

        # Skip already-backfilled nodes.
        if node.get("channel") is not None:
            continue

        outbound = node.get("outbound_message")
        inbound = node.get("inbound_response")

        channel = _infer_channel_from_message(outbound) or _infer_channel_from_message(inbound)
        thread_id = _infer_thread_id(outbound, channel) or _infer_thread_id(inbound, channel)
        recipient_id = node.get("target_resource")
        direction = "out" if outbound else None

        if channel:
            updates = {
                "channel": channel,
                "thread_id": thread_id,
                "recipient_id": recipient_id,
                "direction": direction,
            }
            if dry_run:
                logger.info("[DRY RUN] Would update %s: %s", node_id, updates)
            else:
                await store.update_node(node_id, updates, caller_context=ctx)
            backfilled += 1
        else:
            # Cannot infer — tag as legacy.
            updates = {"legacy": True, "recipient_id": recipient_id}
            if dry_run:
                logger.info("[DRY RUN] Would tag %s as legacy", node_id)
            else:
                await store.update_node(node_id, updates, caller_context=ctx)
            tagged_legacy += 1

    logger.info(
        "Done. Backfilled: %d / Tagged legacy: %d / Total inspected: %d",
        backfilled,
        tagged_legacy,
        min(limit, total),
    )
    await pool.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill CheckinNode fields")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, limit=args.limit))
