"""scripts/setup_test_user.py — Provision the test user and configure betty for scenario testing.

Run this script with the venv active and docker compose up:

    cd C:\\Users\\abhis\\Projects\\graphclaw
    python scripts/setup_test_user.py

Environment variables required (from docker/.env):
    DATABASE_URL=postgresql://graphclaw:graphclaw@localhost:5432/graphclaw
    STORAGE_BUCKET=graphclaw
    STORAGE_ENDPOINT_URL=http://localhost:9000
    AWS_ACCESS_KEY_ID=graphclaw
    AWS_SECRET_ACCESS_KEY=graphclaw (or your MINIO_PASSWORD)
    ANTHROPIC_API_KEY=<your key>
    GATEWAY_SMTP_USER=graphclaw26@gmail.com
    GATEWAY_SMTP_PASS=<app password>
    TELEGRAM_BOT_TOKEN=<bot token>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEST_USER_EMAIL = "abhishekgupta86@gmail.com"
TEST_USER_NAME = "Abhishek"
AGENT_NAME = "betty"
AGENT_ID = "main"
BRIEFING_TIMES = ["08:00", "13:00", "18:00"]

# MinIO / S3 dev credentials
os.environ.setdefault("STORAGE_BUCKET", "graphclaw")
os.environ.setdefault("STORAGE_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("STORAGE_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "graphclaw")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", os.environ.get("MINIO_PASSWORD", "graphclaw"))


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------


async def provision_user(pool) -> str:
    """Create or fetch the test UserNode. Returns user_id."""
    from graphclaw.db.factory import create_graph_store
    from graphclaw.models.nodes import OrganizationNode, UserNode, WorkspaceNode

    store = create_graph_store("age", pool=pool)

    # Check if user already exists
    try:
        existing = await store.list_nodes("UserNode")
        for node in existing:
            if node.get("email") == TEST_USER_EMAIL:
                user_id = node["id"]
                logger.info("Test user already exists: %s", user_id)
                return user_id
    except Exception as exc:
        logger.warning("Could not search existing users: %s", exc)

    # IDs must match the regex patterns in models/base.py
    short_id = uuid.uuid4().hex[:6]
    user_id = f"USER-abhishek-{short_id}"
    org_id = f"ORG-personal-{short_id}"
    ws_id = f"WS-main-{short_id}"
    now = datetime.now(timezone.utc)

    # 1. Create OrganizationNode (required by WorkspaceNode.org_id)
    org_node = OrganizationNode(
        id=org_id,
        name=f"{TEST_USER_NAME}'s Personal Org",
        owner_id=user_id,
        created_at=now,
        updated_at=now,
    )
    await store.create_node(org_node)
    logger.info("Created OrganizationNode: %s", org_id)

    # 2. Create UserNode — `name` is the field, not `display_name`
    user_node = UserNode(
        id=user_id,
        name=TEST_USER_NAME,
        email=TEST_USER_EMAIL,
        role="OWNER",
        created_at=now,
        updated_at=now,
    )
    await store.create_node(user_node)
    logger.info("Created UserNode: %s (%s)", user_id, TEST_USER_EMAIL)

    # 3. Create WorkspaceNode
    ws_node = WorkspaceNode(
        id=ws_id,
        org_id=org_id,
        name=f"{TEST_USER_NAME}'s Workspace",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    await store.create_node(ws_node)
    await store.create_edge(user_id, ws_id, "OWNS", {})
    logger.info("Created WorkspaceNode: %s", ws_id)

    return user_id


# ---------------------------------------------------------------------------
# MinIO / Storage setup
# ---------------------------------------------------------------------------


async def write_agent_to_storage(user_id: str) -> None:
    """Write betty's profile.md, config.json, and seed memory to MinIO."""
    from graphclaw.infra.storage import S3StorageClient, StoragePaths

    storage = S3StorageClient(
        bucket=os.environ["STORAGE_BUCKET"],
        endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL"),
        region=os.environ.get("STORAGE_REGION", "us-east-1"),
    )

    # profile.md
    profile = f"""# Agent Profile: {AGENT_NAME.title()}

## Identity
- **Name:** {AGENT_NAME.title()}
- **Role:** Personal AI task orchestrator and productivity partner
- **Owner:** {user_id}

## Persona & Style
- Warm, proactive, and concise in communication
- Surfaces blockers and risks before the user has to ask
- Celebrates wins and task completions
- Batches updates into briefings unless urgent

## Core Goals
1. Help {TEST_USER_NAME} stay on top of their most important tasks
2. Proactively follow up on delegated work and external contacts
3. Manage project plans end-to-end: work breakdown → execution → completion
4. Grow {TEST_USER_NAME}'s network through thoughtful, non-spammy outreach

## Working Style
- Briefs {TEST_USER_NAME} three times a day (morning, afternoon, evening)
- When assigned a project: plan first, show the user, get approval, then act
- When following up with external contacts: professional, friendly, include soft GraphClaw invitation
- Respects interrupt threshold — only reaches out urgently for P1 blockers

## Memory Rules
- Remember people {TEST_USER_NAME} interacts with (store in semantic memory)
- Record key decisions in episodic memory
- Check working context before starting new tasks to avoid duplication
"""

    profile_path = StoragePaths.agent_profile(user_id, AGENT_ID)
    await storage.write(profile_path, profile.encode(), content_type="text/markdown")
    logger.info("Written: %s", profile_path)

    # config.json
    config = {
        "agent_id": AGENT_ID,
        "agent_name": AGENT_NAME,
        "heartbeat_interval_seconds": 60,
        "llm_provider": "anthropic",
        "llm_model": "claude-sonnet-4-6",
        "briefing_schedule": BRIEFING_TIMES,
        "enabled_channels": ["email", "telegram"],
        "interrupt_threshold": "P1",
        "max_follow_up_days": 3,
        "auto_update_ai_agents": True,
        "auto_send_followups": True,
        "auto_close_resolved": False,
        "owner_email": TEST_USER_EMAIL,
    }
    config_path = StoragePaths.agent_config(user_id, AGENT_ID)
    await storage.write(
        config_path,
        json.dumps(config, indent=2).encode(),
        content_type="application/json",
    )
    logger.info("Written: %s", config_path)

    # Working memory seed
    working_path = StoragePaths.agent_memory_working(user_id, AGENT_ID)
    working_seed = f"""# Working Context

Agent {AGENT_NAME.title()} initialised for {TEST_USER_NAME} ({TEST_USER_EMAIL}).

## Current Session
- Session started: {datetime.now(timezone.utc).isoformat()}
- Status: Awaiting first interaction

## Pending Actions
- Send welcome message to user
- Await first task assignment or briefing request
"""
    await storage.write(working_path, working_seed.encode(), content_type="text/markdown")
    logger.info("Written: %s", working_path)

    # Semantic memory: user profile
    user_topic_path = StoragePaths.agent_memory_semantic_topic(user_id, AGENT_ID, "owner-profile")
    user_semantic = f"""# Owner Profile

- **Name:** {TEST_USER_NAME}
- **Email:** {TEST_USER_EMAIL}
- **User ID:** {user_id}
- **Timezone:** Asia/Kolkata (IST, UTC+5:30)
- **Communication style:** Direct, brief
- **Key projects:** (to be learned from conversation)

## Known Contacts
- Soni (nikhilgupta1611@gmail.com) — External contact, assessment follow-up pending
"""
    await storage.write(user_topic_path, user_semantic.encode(), content_type="text/markdown")
    logger.info("Written: %s", user_topic_path)

    # Triggers persisted to MinIO
    triggers = []
    for time_str in BRIEFING_TIMES:
        hour, minute = map(int, time_str.split(":"))
        trigger_id = f"briefing-{user_id}-{hour:02d}{minute:02d}"
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        next_fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_fire <= now:
            next_fire = next_fire + timedelta(days=1)

        triggers.append(
            {
                "trigger_id": trigger_id,
                "trigger_type": "TIME_BASED",
                "user_id": user_id,
                "enabled": True,
                "cron_expression": f"{minute} {hour} * * *",
                "next_fire_at": next_fire.isoformat(),
                "last_fired_at": None,
                "payload_template": {"agent_id": AGENT_ID, "briefing": True},
            }
        )

    triggers_path = f"{user_id}/agents/{AGENT_ID}/triggers.json"
    await storage.write(
        triggers_path,
        json.dumps(triggers, indent=2, default=str).encode(),
        content_type="application/json",
    )
    logger.info("Written: %s (%d triggers)", triggers_path, len(triggers))


# ---------------------------------------------------------------------------
# Skill seeding
# ---------------------------------------------------------------------------


async def seed_skills(user_id: str, storage) -> None:
    """Install the work-breakdown-agent skill from the built-in local source."""
    from graphclaw.skills.registry import SkillRegistryService
    from graphclaw.skills.registry_models import SkillSource, SkillSourceType

    registry = SkillRegistryService(storage_client=storage)

    # Add the local built-in source (idempotent — replace if already present)
    local_source = SkillSource(
        source_type=SkillSourceType.LOCAL,
        uri="local://definitions",
        name="GraphClaw Built-in Skills",
    )
    try:
        listings = await registry.add_source(user_id, local_source)
        logger.info("Local skill source registered (%d listings)", len(listings))
    except Exception as exc:
        logger.warning("Could not add local source: %s", exc)
        return

    # Install the work-breakdown-agent if not already installed
    installed = await registry.list_installed(user_id)
    installed_names = {sk.name for sk in installed}
    skill_name = "work-breakdown-agent"
    if skill_name in installed_names:
        logger.info("Skill already installed: %s", skill_name)
        return

    try:
        sk = await registry.install(user_id, skill_name, "local://definitions")
        logger.info("Installed skill: %s (id=%s)", sk.name, sk.skill_id)
    except Exception as exc:
        logger.warning("Could not install skill %s: %s", skill_name, exc)


# ---------------------------------------------------------------------------
# MCP server seeding
# ---------------------------------------------------------------------------


async def seed_mcp_servers(user_id: str, storage) -> None:
    """Register a dev GitHub MCP server config for the test user (idempotent).

    Config is written as JSON to MinIO at:
      {user_id}/mcp/servers/MCP-github-dev-001.json
    """
    from graphclaw.mcp.registry import MCPRegistry
    from graphclaw.models.enums import MCPTransport, TrustTier
    from graphclaw.models.nodes import MCPServerNode

    registry = MCPRegistry(storage_client=storage)
    server_id = "MCP-github-dev-001"

    # Idempotent — skip if already written
    existing = await registry.get(user_id, server_id)
    if existing is not None:
        logger.info("MCP server already registered: %s", server_id)
        return

    node = MCPServerNode(
        id=server_id,
        name="GitHub (dev)",
        transport=MCPTransport.HTTP,
        endpoint_url="http://localhost:3100",  # local dev stub — not required to be live
        trust_tier=TrustTier.GATED,
        scope=["repos:read", "issues:read", "issues:write", "pulls:read"],
        enabled=True,
    )
    try:
        await registry.register(user_id, node)
        logger.info("Registered MCP server: %s (trust=%s)", server_id, node.trust_tier.value)
    except Exception as exc:
        logger.warning("Could not register MCP server: %s", exc)


# ---------------------------------------------------------------------------
# Send welcome message
# ---------------------------------------------------------------------------


async def send_welcome(user_id: str) -> None:
    """Send welcome email to the test user. Telegram welcome requires chat_id."""
    from graphclaw.agent.outbound import OutboundDispatcher

    dispatcher = OutboundDispatcher.from_env()
    subject = f"Welcome! I'm {AGENT_NAME.title()}, your GraphClaw agent"
    body = f"""Hi {TEST_USER_NAME}!

I'm {AGENT_NAME.title()}, your personal AI task orchestrator powered by GraphClaw.

Here's what I can do for you:

• Manage your task graph — create, update, and prioritise tasks
• Follow up with people on your behalf via email
• Plan and execute projects from work breakdown to completion
• Send you daily briefings (morning 8am, afternoon 1pm, evening 6pm UTC)

To get started, just reply to any of my emails or mention me in a message. \
Tell me what's on your mind — a project to plan, a follow-up to track, or a goal to work toward.

What would you like to work on first?

— {AGENT_NAME.title()}
(Your GraphClaw AI Agent)
"""

    try:
        await dispatcher.send_email(to=TEST_USER_EMAIL, subject=subject, body=body)
        logger.info("Welcome email sent to %s", TEST_USER_EMAIL)
    except Exception as exc:
        logger.error("Failed to send welcome email: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql://graphclaw:graphclaw@localhost:5432/graphclaw"
    )

    logger.info("=== GraphClaw Test User Setup ===")
    logger.info("User: %s (%s)", TEST_USER_NAME, TEST_USER_EMAIL)
    logger.info("Agent: %s (ID: %s)", AGENT_NAME, AGENT_ID)

    # 1. Provision user in graph DB
    from graphclaw.db.age.connection import create_pool

    pool = await create_pool(database_url)
    try:
        user_id = await provision_user(pool)
    finally:
        await pool.close()

    # 2. Write agent files to MinIO
    await write_agent_to_storage(user_id)

    # 3. Seed built-in skill + dev MCP server
    from graphclaw.infra.storage import S3StorageClient

    storage = S3StorageClient(
        bucket=os.environ["STORAGE_BUCKET"],
        endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL"),
        region=os.environ.get("STORAGE_REGION", "us-east-1"),
    )
    await seed_skills(user_id, storage)
    await seed_mcp_servers(user_id, storage)

    # 4. Send welcome email
    await send_welcome(user_id)

    # 5. Print summary
    print("\n" + "=" * 60)
    print("TEST SETUP COMPLETE")
    print("=" * 60)
    print(f"User ID:    {user_id}")
    print(f"Email:      {TEST_USER_EMAIL}")
    print(f"Agent:      {AGENT_NAME.title()} (agent_id={AGENT_ID})")
    print(f"Briefings:  {', '.join(BRIEFING_TIMES)} UTC daily")
    print()
    print("Next steps:")
    print(f"  export GRAPHCLAW_USER_ID={user_id}")
    print("  graphclaw agent briefing")
    print("  graphclaw agent run")
    print()
    print("MinIO console: http://localhost:9001 (user: graphclaw)")
    print("Gateway docs:  http://localhost:8000/docs")
    print("=" * 60)


if __name__ == "__main__":
    import selectors

    asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
