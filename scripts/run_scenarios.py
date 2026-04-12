"""scripts/run_scenarios.py — Execute the 4 GraphClaw test scenarios via the AgentLoop.

Runs the 4 test scenarios by calling process_chat_message() directly (no HTTP/JWT needed).
Outputs agent responses to console and dispatches outbound messages via OutboundDispatcher.

Usage:
    Set env vars first, then:
    python scripts/run_scenarios.py [--scenario 1|2|3|4|all]

Environment variables (same as setup_test_user.py):
    DATABASE_URL, STORAGE_BUCKET, STORAGE_ENDPOINT_URL,
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, ANTHROPIC_API_KEY,
    GATEWAY_SMTP_USER, GATEWAY_SMTP_PASS, TELEGRAM_BOT_TOKEN,
    GRAPHCLAW_USER_ID (output from setup_test_user.py)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import selectors
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

# Silence noisy sub-loggers
for _noisy in ("botocore", "boto3", "urllib3", "httpcore", "httpx", "aiosmtplib"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ── Configuration ──────────────────────────────────────────────────────────────

USER_ID = os.environ.get("GRAPHCLAW_USER_ID", "USER-abhishek-0c3bfe")
AGENT_ID = os.environ.get("GRAPHCLAW_AGENT_ID", "main")

# External contact for Scenario 2
SONI_EMAIL = "nikhilgupta1611@gmail.com"
SONI_NAME = "Soni"

# Divider for readability
_DIV = "─" * 70


# ── Bootstrap: build AgentLoop ────────────────────────────────────────────────

async def build_agent_loop(pool):
    """Construct a fully wired AgentLoop with real LLM + storage."""
    from graphclaw.agent.loop import AgentLoop
    from graphclaw.db.factory import create_graph_store
    from graphclaw.infra.storage import S3StorageClient
    from graphclaw.llm.factory import create_llm_client
    from graphclaw.scoring.engine import ScoringEngine
    from graphclaw.state.machine import StateMachine

    store = create_graph_store("age", pool=pool)
    scoring = ScoringEngine()
    sm = StateMachine()
    llm = create_llm_client("anthropic")
    storage = S3StorageClient(
        bucket=os.environ.get("STORAGE_BUCKET", "graphclaw"),
        endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL", "http://localhost:9000"),
        region=os.environ.get("STORAGE_REGION", "us-east-1"),
    )
    return AgentLoop(
        graph_repo=store,
        scoring_engine=scoring,
        state_machine=sm,
        llm_client=llm,
        storage_client=storage,
        agent_id=AGENT_ID,
    )


def build_dispatcher():
    """Build OutboundDispatcher from env vars."""
    from graphclaw.agent.outbound import OutboundDispatcher
    return OutboundDispatcher.from_env()


# ── Scenario runners ──────────────────────────────────────────────────────────

async def run_scenario_1(agent_loop, dispatcher):
    """Scenario 1: Onboarding — verifies channel setup, welcome, briefing schedule."""
    print(f"\n{'═'*70}")
    print("SCENARIO 1: Onboarding — channel setup, welcome, briefing schedule")
    print(f"{'═'*70}\n")

    conversation = []

    messages = [
        "Hi Betty! I've just set up my account. I'd like to configure my communication channels.",
        "My email is abhishekgupta86@gmail.com and I also use Telegram.",
        "Great! Now please set up my daily briefings — I want them at 8am, 1pm, and 6pm.",
        "Can you give me a quick briefing on what you can help me with?",
    ]

    for msg in messages:
        print(f"[User] {msg}")
        reply = await agent_loop.process_chat_message(
            user_id=USER_ID,
            text=msg,
            conversation_history=conversation,
        )
        print(f"[Betty] {reply}")
        print()
        conversation.append({"role": "user", "content": msg})
        conversation.append({"role": "assistant", "content": reply})

    # Send final briefing via email
    print("[Sending welcome briefing email to user...]")
    try:
        await dispatcher.send_email(
            to="abhishekgupta86@gmail.com",
            subject="Your GraphClaw briefing — all set!",
            body=f"Hi Abhishek,\n\nYou're all set up with GraphClaw!\n\nBetty is configured and ready. "
                 f"Briefings are scheduled for 08:00, 13:00, and 18:00 UTC daily.\n\n"
                 f"Your conversation summary:\n\n{conversation[-1].get('content', '')}\n\n— Betty",
        )
        print("[✓] Welcome email dispatched.\n")
    except Exception as exc:
        print(f"[✗] Email dispatch failed: {exc}\n")


async def run_scenario_2(agent_loop, dispatcher):
    """Scenario 2: Follow-up task with external contact (Soni)."""
    print(f"\n{'═'*70}")
    print("SCENARIO 2: Follow-up task — track Soni's assessment readiness")
    print(f"{'═'*70}\n")

    conversation = []

    messages = [
        f"Betty, I need you to follow up with Soni ({SONI_EMAIL}) about her assessment. "
        f"She was supposed to let me know when she's ready but hasn't replied yet.",

        "Create a task for this follow-up. Make it a DEL (Delegated) task type. "
        "The goal is to check if Soni is ready for the assessment within 3 days.",

        "Now please draft and send an email to Soni checking in on her assessment readiness. "
        "Also mention she can try GraphClaw to manage her workflow — soft invite, keep it natural.",
    ]

    for msg in messages:
        print(f"[User] {msg}")
        reply = await agent_loop.process_chat_message(
            user_id=USER_ID,
            text=msg,
            conversation_history=conversation,
        )
        print(f"[Betty] {reply}")
        print()
        conversation.append({"role": "user", "content": msg})
        conversation.append({"role": "assistant", "content": reply})

    # Send the follow-up email to Soni
    print(f"[Sending follow-up email to {SONI_NAME} at {SONI_EMAIL}...]")
    try:
        await dispatcher.send_email(
            to=SONI_EMAIL,
            subject="Quick check-in — your assessment",
            body=f"Hi {SONI_NAME},\n\n"
                 f"Hope you're doing well! Just wanted to check in — I know you were working toward "
                 f"getting ready for the assessment and wanted to see how things are progressing.\n\n"
                 f"Whenever you're ready, just drop me a note and we'll schedule it.\n\n"
                 f"Also, I've been using GraphClaw (graphclaw.ai) to keep my projects and follow-ups "
                 f"organised — thought you might find it handy too if you're juggling a lot of things!\n\n"
                 f"Best,\nAbhishek\n\n(Sent via GraphClaw AI Agent — Betty)",
        )
        print(f"[✓] Follow-up email sent to {SONI_EMAIL}.\n")
    except Exception as exc:
        print(f"[✗] Email to Soni failed: {exc}\n")


async def run_scenario_3(agent_loop, dispatcher):
    """Scenario 3: Birthday party project — WBS, approval, task graph creation."""
    print(f"\n{'═'*70}")
    print("SCENARIO 3: Birthday party project — WBS, approval, task graph")
    print(f"{'═'*70}\n")

    conversation = []

    messages = [
        "Betty, during our briefing I wanted to bring up a project — I need to plan my son's "
        "birthday party! It's in 3 weeks.",

        "Ok great! Please do a full work breakdown of the birthday party project. "
        "Think about all phases: theme, venue, invitations, catering, entertainment, decorations, "
        "photographer. Show me the plan before creating any tasks.",

        "This looks great! I approve the plan. Please go ahead and create the tasks in my "
        "task graph. Mark them as DELEGATED type since you'll be tracking them.",

        "Now assign a goal to this project — call it 'Birthday Party for Arjun' with a target "
        "3 weeks from today.",
    ]

    for msg in messages:
        print(f"[User] {msg}")
        reply = await agent_loop.process_chat_message(
            user_id=USER_ID,
            text=msg,
            conversation_history=conversation,
        )
        print(f"[Betty] {reply}")
        print()
        conversation.append({"role": "user", "content": msg})
        conversation.append({"role": "assistant", "content": reply})

    # Send plan summary via email
    print("[Sending birthday party plan email to user...]")
    try:
        final_reply = conversation[-1].get("content", "") if conversation else ""
        await dispatcher.send_email(
            to="abhishekgupta86@gmail.com",
            subject="Birthday party plan — tasks created in GraphClaw",
            body=f"Hi Abhishek,\n\n"
                 f"I've created all the birthday party tasks in your task graph!\n\n"
                 f"Summary from our chat:\n\n{final_reply[:1000]}\n\n"
                 f"You can review all tasks in your GraphClaw dashboard.\n\n— Betty",
        )
        print("[✓] Birthday party plan email dispatched.\n")
    except Exception as exc:
        print(f"[✗] Email dispatch failed: {exc}\n")


async def run_scenario_4(agent_loop, dispatcher):
    """Scenario 4: Podcast leads goal — research, profile evaluation, outreach."""
    print(f"\n{'═'*70}")
    print("SCENARIO 4: Podcast leads — find interview candidates from LinkedIn network")
    print(f"{'═'*70}\n")

    conversation = []

    messages = [
        "Betty, I have a new goal for you. I'm running a growing tech podcast and I need to "
        "find interesting interview guests from my LinkedIn network and beyond.",

        "Please create a goal called 'Podcast Interview Pipeline' — the objective is to find "
        "5 compelling interview guests per month, reach out to them, and manage the scheduling. "
        "Set this as a high-priority goal with a 30-day timeline.",

        "Break this down into tasks: "
        "1) Research and identify 15 candidate profiles from my network, "
        "2) Score and shortlist the top 5 based on relevance and engagement potential, "
        "3) Draft personalised interview invitation emails for the top 5, "
        "4) Send the invitations and track responses, "
        "5) Follow up with non-responders after 5 days.",

        "What's your plan for executing on this? How will you track everything?",
    ]

    for msg in messages:
        print(f"[User] {msg}")
        reply = await agent_loop.process_chat_message(
            user_id=USER_ID,
            text=msg,
            conversation_history=conversation,
        )
        print(f"[Betty] {reply}")
        print()
        conversation.append({"role": "user", "content": msg})
        conversation.append({"role": "assistant", "content": reply})

    # Send podcast leads summary
    print("[Sending podcast pipeline summary email...]")
    try:
        final_reply = conversation[-1].get("content", "") if conversation else ""
        await dispatcher.send_email(
            to="abhishekgupta86@gmail.com",
            subject="Podcast Interview Pipeline — goals & tasks set up",
            body=f"Hi Abhishek,\n\n"
                 f"Your Podcast Interview Pipeline goal is live in GraphClaw!\n\n"
                 f"Betty's execution plan:\n\n{final_reply[:1500]}\n\n"
                 f"All tasks are tracked in your dashboard.\n\n— Betty",
        )
        print("[✓] Podcast pipeline email dispatched.\n")
    except Exception as exc:
        print(f"[✗] Email dispatch failed: {exc}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(scenarios: list[int]) -> None:
    database_url = os.environ.get("DATABASE_URL", "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw")

    print(f"\n{'═'*70}")
    print(f"GraphClaw Scenario Runner — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"User ID: {USER_ID} | Agent: {AGENT_ID}")
    print(f"Scenarios: {scenarios}")
    print(f"{'═'*70}")

    from graphclaw.db.age.connection import create_pool

    pool = await create_pool(database_url)
    try:
        agent_loop = await build_agent_loop(pool)
        dispatcher = build_dispatcher()

        runners = {
            1: run_scenario_1,
            2: run_scenario_2,
            3: run_scenario_3,
            4: run_scenario_4,
        }

        for s in scenarios:
            runner = runners.get(s)
            if runner is None:
                print(f"[WARN] Unknown scenario {s}, skipping.")
                continue
            await runner(agent_loop, dispatcher)

    finally:
        await pool.close()

    print(f"\n{'═'*70}")
    print("All scenarios complete.")
    print(f"{'═'*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run GraphClaw test scenarios")
    parser.add_argument(
        "--scenario",
        type=str,
        default="all",
        help="Scenario to run: 1, 2, 3, 4, or 'all'",
    )
    args = parser.parse_args()

    if args.scenario == "all":
        scenarios_to_run = [1, 2, 3, 4]
    else:
        scenarios_to_run = [int(x.strip()) for x in args.scenario.split(",")]

    asyncio.run(
        main(scenarios_to_run),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )
