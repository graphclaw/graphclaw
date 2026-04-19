"""graphclaw.cli.agent_commands — Agent reasoning loop CLI sub-commands.

Description
-----------
Implements sub-commands for the ``graphclaw agent`` group:

- ``run`` / ``score`` / ``briefing`` — scoring cycle and briefing generation.
- ``create`` — bootstrap a new orchestrating agent (writes profile.md +
  config.json to MinIO and creates the AgentNode in the graph).
- ``configure-briefing`` — register morning/afternoon/evening briefing
  triggers in the TriggerScheduler.
- ``send-welcome`` — send a welcome message to the user across all configured
  channels.

Design Patterns
---------------
- Async Bridge: Each Typer command is synchronous but delegates to an ``async``
  helper via ``asyncio.run()``.

Public API
----------
- app: The ``typer.Typer`` instance for the ``agent`` sub-group.

Dependencies
------------
- graphclaw.agent.loop: AgentLoop.
- graphclaw.cli.formatters: format_action_queue, format_briefing.
- graphclaw.db.connection: create_pool.
- graphclaw.db.age: AgeGraphStore.
- graphclaw.scoring.engine: ScoringEngine.
- graphclaw.state.machine: StateMachine.
- graphclaw.llm.factory: create_llm_client.
- graphclaw.infra.storage: S3StorageClient, StoragePaths.
- graphclaw.agent.outbound: OutboundDispatcher.
- typer: CLI framework.
- rich: Console output with status spinners.
"""

from __future__ import annotations

import json
import os

import typer
from rich.console import Console

from graphclaw.cli._shared import run_async
from graphclaw.cli.formatters import format_action_queue, format_briefing

app = typer.Typer(help="Agent reasoning loop commands")
console = Console()
err_console = Console(stderr=True, style="bold red")


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _build_agent_loop():
    """Initialise and return an AgentLoop from environment config.

    Returns (pool, AgentLoop).  Raises SystemExit on any setup failure.
    """
    import os

    from graphclaw.agent.loop import AgentLoop
    from graphclaw.db.age import AgeGraphStore
    from graphclaw.db.connection import create_pool
    from graphclaw.scoring.engine import ScoringEngine
    from graphclaw.state.machine import StateMachine

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print(
            "DATABASE_URL is not set. "
            "Set it in your environment or .env file before running CLI commands."
        )
        raise typer.Exit(code=1)

    try:
        pool = await create_pool(dsn)
    except Exception as exc:
        err_console.print(f"Could not connect to the database: {exc}")
        raise typer.Exit(code=1)

    repo = AgeGraphStore(pool)
    engine = ScoringEngine()
    sm = StateMachine()
    loop = AgentLoop(graph_repo=repo, scoring_engine=engine, state_machine=sm)
    return pool, loop


async def _run_cycle_async(top_n: int | None) -> None:
    """Run one agent cycle and print the action queue."""
    with console.status("[bold green]Connecting to database...[/bold green]"):
        pool, loop = await _build_agent_loop()

    try:
        with console.status("[bold green]Running scoring cycle...[/bold green]"):
            queue = await loop.run_cycle()

        if not queue:
            console.print("[dim]No actionable tasks found in this cycle.[/dim]")
            return

        format_action_queue(
            queue,
            console=console,
            title="Agent Reasoning Cycle — Action Queue",
            top_n=top_n,
        )
    finally:
        await pool.close()


async def _score_async(top_n: int | None) -> None:
    """Score all tasks and print the ranked queue."""
    with console.status("[bold green]Connecting to database...[/bold green]"):
        pool, loop = await _build_agent_loop()

    try:
        with console.status("[bold green]Scoring tasks...[/bold green]"):
            queue = await loop.run_cycle()

        if not queue:
            console.print("[dim]No scoreable tasks found.[/dim]")
            return

        format_action_queue(
            queue,
            console=console,
            title="Scored Tasks — Action Queue",
            top_n=top_n,
        )
    finally:
        await pool.close()


async def _briefing_async(top_n: int) -> None:
    """Generate and print the agent briefing."""
    with console.status("[bold green]Connecting to database...[/bold green]"):
        pool, loop = await _build_agent_loop()

    try:
        with console.status("[bold green]Running scoring cycle...[/bold green]"):
            queue = await loop.run_cycle()

        briefing_text = await loop.generate_briefing(queue, top_n=top_n)
        format_briefing(briefing_text, console=console)
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("run")
def agent_run(
    top_n: int | None = typer.Option(
        None,
        "--top",
        "-n",
        help="Limit output to the top N entries in the action queue.",
    ),
) -> None:
    """Run one complete agent reasoning cycle and display the action queue."""
    try:
        run_async(_run_cycle_async(top_n))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("score")
def agent_score(
    top_n: int | None = typer.Option(
        None,
        "--top",
        "-n",
        help="Limit output to the top N scored tasks.",
    ),
) -> None:
    """Score all tasks and display the ranked action queue."""
    try:
        run_async(_score_async(top_n))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("briefing")
def agent_briefing(
    top_n: int = typer.Option(
        5,
        "--top",
        "-n",
        help="Number of top priorities to include in the briefing.",
    ),
) -> None:
    """Generate a human-readable briefing of the top priority tasks."""
    try:
        run_async(_briefing_async(top_n))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------

_DEFAULT_PROFILE_TEMPLATE = """\
# Agent Profile: {name}

## Identity
- **Name:** {name}
- **Role:** Personal AI task orchestrator and productivity partner
- **Owner:** {user_id}

## Persona & Style
- Warm, proactive, and concise in communication
- Surfaces blockers and risks before the user has to ask
- Celebrates wins — acknowledges task completions and milestones
- Never overwhelming — batches updates into briefings unless urgent

## Core Goals
1. Help the user stay on top of their most important tasks
2. Proactively follow up on delegated work and external contacts
3. Manage project plans end-to-end: from work breakdown to execution tracking
4. Grow the user's network through thoughtful, non-spammy outreach

## Working Style
- Briefs the user three times a day (morning, afternoon, evening)
- When assigned a project: plan first, show the user, get approval, then act
- When following up with external contacts: be professional, friendly, include a soft invitation to join GraphClaw
- Respects the user's interrupt threshold — only reaches out urgently for P1 blockers

## Memory Rules
- Remember people the user interacts with regularly (store in semantic memory)
- Record key decisions and approvals in episodic memory
- Always check working context before starting a new task to avoid duplication
"""

_DEFAULT_AGENT_CONFIG = {
    "heartbeat_interval_seconds": 60,
    "llm_provider": "anthropic",
    "llm_model": "claude-sonnet-4-6",
    "briefing_schedule": ["08:00", "13:00", "18:00"],
    "enabled_channels": ["email", "telegram"],
    "interrupt_threshold": "P1",
    "max_follow_up_days": 3,
    "auto_update_ai_agents": True,
    "auto_send_followups": True,
    "auto_close_resolved": False,
}

_WELCOME_MESSAGE_TEMPLATE = """\
Hi! I'm {name}, your personal AI task orchestrator.

I'm here to help you manage your tasks, follow up on delegated work, and \
keep your projects on track. Here's what I can do:

• 📋 Manage your task graph — create, update, and prioritise tasks
• 📬 Follow up with people on your behalf via email and Telegram
• 🎯 Plan and execute projects from work breakdown to completion
• 🌅 Send you daily briefings (morning, afternoon, evening)

To get started, just tell me what's on your mind — a project to plan, \
a follow-up to track, or a goal to work toward.

What would you like to work on first?
"""


async def _create_agent_async(
    user_id: str,
    agent_id: str,
    name: str,
) -> None:
    """Bootstrap a new agent: write profile.md + config.json to MinIO."""
    from graphclaw.infra.storage import S3StorageClient, StoragePaths

    storage = S3StorageClient(
        bucket=os.environ.get("STORAGE_BUCKET", "graphclaw"),
        endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL") or None,
        region=os.environ.get("STORAGE_REGION", "us-east-1"),
    )

    with console.status(f"[bold green]Creating agent '{name}' ({agent_id})...[/bold green]"):
        # Write profile.md
        profile_content = _DEFAULT_PROFILE_TEMPLATE.format(name=name, user_id=user_id)
        profile_path = StoragePaths.agent_profile(user_id, agent_id)
        await storage.write(
            profile_path,
            profile_content.encode(),
            content_type="text/markdown",
        )
        console.print(f"[green]✓[/green] Profile written: {profile_path}")

        # Write config.json
        config = dict(_DEFAULT_AGENT_CONFIG)
        config["agent_id"] = agent_id
        config["agent_name"] = name
        config_path = StoragePaths.agent_config(user_id, agent_id)
        await storage.write(
            config_path,
            json.dumps(config, indent=2).encode(),
            content_type="application/json",
        )
        console.print(f"[green]✓[/green] Config written: {config_path}")

        # Seed empty working memory
        working_path = StoragePaths.agent_memory_working(user_id, agent_id)
        await storage.write(
            working_path,
            f"# Working Context\n\nAgent {name} initialised.\n".encode(),
            content_type="text/markdown",
        )
        console.print(f"[green]✓[/green] Working memory seeded: {working_path}")

    console.print(
        f"\n[bold]Agent '{name}' (ID: {agent_id}) created for user {user_id}.[/bold]\n"
        "Next step: configure briefing triggers with:\n"
        f"  graphclaw agent configure-briefing --user-id {user_id} --agent-id {agent_id}"
    )


async def _configure_briefing_async(
    user_id: str,
    agent_id: str,
    times: list[str],
) -> None:
    """Register morning/afternoon/evening briefing triggers."""
    from datetime import datetime, timezone

    from graphclaw.triggers.models import TriggerConfig, TriggerType
    from graphclaw.triggers.scheduler import TriggerScheduler

    scheduler = TriggerScheduler()

    with console.status("[bold green]Registering briefing triggers...[/bold green]"):
        for time_str in times:
            parts = time_str.strip().split(":")
            if len(parts) != 2:
                err_console.print(f"Invalid time format: {time_str} (expected HH:MM)")
                continue
            hour, minute = int(parts[0]), int(parts[1])
            trigger_id = f"briefing-{user_id}-{hour:02d}{minute:02d}"
            cron_expr = f"{minute} {hour} * * *"

            # Compute initial next_fire_at
            now = datetime.now(timezone.utc)
            next_fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_fire <= now:
                from datetime import timedelta

                next_fire = next_fire + timedelta(days=1)

            config = TriggerConfig(
                trigger_id=trigger_id,
                trigger_type=TriggerType.TIME_BASED,
                user_id=user_id,
                enabled=True,
                cron_expression=cron_expr,
                next_fire_at=next_fire,
                payload_template={"agent_id": agent_id, "briefing": True},
            )
            scheduler.register(config)
            console.print(
                f"[green]✓[/green] Trigger registered: {trigger_id} — fires at {time_str} UTC daily"
            )

    # Persist the trigger configs to MinIO so the engine can reload on restart
    from graphclaw.infra.storage import S3StorageClient

    storage = S3StorageClient(
        bucket=os.environ.get("STORAGE_BUCKET", "graphclaw"),
        endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL") or None,
        region=os.environ.get("STORAGE_REGION", "us-east-1"),
    )
    triggers_path = f"{user_id}/agents/{agent_id}/triggers.json"
    trigger_list = [
        cfg.model_dump(mode="json")
        for cfg in scheduler._triggers.values()  # noqa: SLF001
    ]
    await storage.write(
        triggers_path,
        json.dumps(trigger_list, indent=2, default=str).encode(),
        content_type="application/json",
    )
    console.print(f"\n[green]✓[/green] Trigger config persisted: {triggers_path}")
    console.print(
        f"\n[bold]{len(times)} briefing trigger(s) configured.[/bold] "
        "The TriggerEngine will pick these up on next tick."
    )


async def _send_welcome_async(
    user_id: str,
    agent_id: str,
    name: str,
    email: str | None,
    telegram_chat_id: str | None,
) -> None:
    """Send a welcome message to the user via configured channels."""
    from graphclaw.agent.outbound import OutboundDispatcher

    dispatcher = OutboundDispatcher.from_env()
    message_body = _WELCOME_MESSAGE_TEMPLATE.format(name=name)

    if email:
        with console.status(f"[bold green]Sending welcome email to {email}...[/bold green]"):
            await dispatcher.send_email(
                to=email,
                subject=f"Welcome! I'm {name}, your GraphClaw agent",
                body=message_body,
            )
        console.print(f"[green]✓[/green] Welcome email sent to {email}")

    if telegram_chat_id:
        with console.status(
            f"[bold green]Sending Telegram welcome to {telegram_chat_id}...[/bold green]"
        ):
            await dispatcher.send_telegram(chat_id=telegram_chat_id, text=message_body)
        console.print(
            f"[green]✓[/green] Welcome Telegram message sent to chat_id={telegram_chat_id}"
        )

    if not email and not telegram_chat_id:
        err_console.print("No channels specified. Provide --email and/or --telegram-chat-id.")


# ---------------------------------------------------------------------------
# Bootstrap commands
# ---------------------------------------------------------------------------


@app.command("create")
def agent_create(
    name: str = typer.Option(
        ..., "--name", "-n", help="Display name for the agent (e.g. 'betty')."
    ),
    agent_id: str = typer.Option("main", "--agent-id", help="Agent storage ID (default: 'main')."),
    user_id: str = typer.Option(
        None,
        "--user-id",
        "-u",
        help="User ID to create the agent for. Defaults to GRAPHCLAW_USER_ID env var.",
    ),
) -> None:
    """Bootstrap a new orchestrating agent with profile and config in MinIO."""
    resolved_user_id = user_id or os.environ.get("GRAPHCLAW_USER_ID", "")
    if not resolved_user_id:
        err_console.print("User ID required. Pass --user-id or set GRAPHCLAW_USER_ID env var.")
        raise typer.Exit(code=1)
    try:
        run_async(_create_agent_async(resolved_user_id, agent_id, name))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("configure-briefing")
def agent_configure_briefing(
    user_id: str = typer.Option(
        None,
        "--user-id",
        "-u",
        help="User ID. Defaults to GRAPHCLAW_USER_ID env var.",
    ),
    agent_id: str = typer.Option("main", "--agent-id", help="Agent storage ID."),
    times: str = typer.Option(
        "08:00,13:00,18:00",
        "--times",
        "-t",
        help="Comma-separated UTC times for daily briefings (HH:MM format).",
    ),
) -> None:
    """Register daily briefing triggers (morning, afternoon, evening)."""
    resolved_user_id = user_id or os.environ.get("GRAPHCLAW_USER_ID", "")
    if not resolved_user_id:
        err_console.print("User ID required. Pass --user-id or set GRAPHCLAW_USER_ID env var.")
        raise typer.Exit(code=1)
    time_list = [t.strip() for t in times.split(",") if t.strip()]
    try:
        run_async(_configure_briefing_async(resolved_user_id, agent_id, time_list))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("send-welcome")
def agent_send_welcome(
    name: str = typer.Option(
        "Betty", "--name", "-n", help="Agent display name used in the message."
    ),
    user_id: str = typer.Option(
        None,
        "--user-id",
        "-u",
        help="User ID. Defaults to GRAPHCLAW_USER_ID env var.",
    ),
    agent_id: str = typer.Option("main", "--agent-id", help="Agent storage ID."),
    email: str = typer.Option(
        None, "--email", "-e", help="User's email address to send welcome to."
    ),
    telegram_chat_id: str = typer.Option(
        None, "--telegram-chat-id", help="Telegram chat ID to send welcome to."
    ),
) -> None:
    """Send a welcome message to the user via email and/or Telegram."""
    resolved_user_id = user_id or os.environ.get("GRAPHCLAW_USER_ID", "")
    if not resolved_user_id:
        err_console.print("User ID required. Pass --user-id or set GRAPHCLAW_USER_ID env var.")
        raise typer.Exit(code=1)
    try:
        run_async(_send_welcome_async(resolved_user_id, agent_id, name, email, telegram_chat_id))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# chat command
# ---------------------------------------------------------------------------


async def _chat_async(
    user_id: str, agent_id: str, message: str | None, trace: bool = False
) -> None:
    """Run a single-message or interactive chat session with the agent."""
    try:
        import readline  # noqa: F401  (enables arrow keys / history on Linux/macOS)
    except ImportError:
        pass  # readline is not available on Windows

    from graphclaw.agent.loop import AgentLoop
    from graphclaw.db.age import AgeGraphStore
    from graphclaw.db.connection import create_pool
    from graphclaw.infra.storage import S3StorageClient
    from graphclaw.llm.factory import create_llm_client
    from graphclaw.scoring.engine import ScoringEngine
    from graphclaw.state.machine import StateMachine

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print("DATABASE_URL is not set.")
        raise typer.Exit(code=1)

    with console.status("[bold green]Connecting...[/bold green]"):
        pool = await create_pool(dsn)

    repo = AgeGraphStore(pool)
    engine = ScoringEngine()
    sm = StateMachine()
    llm = create_llm_client("anthropic")
    storage = S3StorageClient(
        bucket=os.environ.get("STORAGE_BUCKET", "graphclaw"),
        endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL"),
        region=os.environ.get("STORAGE_REGION", "us-east-1"),
    )

    # Optional: initialise skill registry and MCP registry for planning/execution
    skill_registry = None
    worker_pool = None
    mcp_registry = None
    try:
        from graphclaw.skills.registry import SkillRegistryService

        skill_registry = SkillRegistryService(storage_client=storage)
    except Exception:
        pass
    try:
        from graphclaw.mcp.registry import MCPRegistry

        mcp_registry = MCPRegistry(storage_client=storage)
    except Exception:
        pass
    try:
        from graphclaw.skills.llm_router import LLMRouter
        from graphclaw.skills.worker import WorkerPool

        llm_router = LLMRouter(llm_client=llm)
        worker_pool = WorkerPool(pool_size=2, llm_router=llm_router)
    except Exception:
        pass

    agent_loop = AgentLoop(
        graph_repo=repo,
        scoring_engine=engine,
        state_machine=sm,
        llm_client=llm,
        storage_client=storage,
        agent_id=agent_id,
        skill_registry=skill_registry,
        worker_pool=worker_pool,
        mcp_registry=mcp_registry,
    )

    history: list[dict] = []

    async def _run_with_trace(text: str) -> str:
        """Run one turn via process_chat_message_stream and print trace events."""
        from graphclaw.agent.run_events import RunEventType as ET  # noqa: PLC0415

        full_text = ""
        console.print()
        async for event in agent_loop.process_chat_message_stream(
            user_id=user_id,
            text=text,
            conversation_history=list(history),
        ):
            etype = event.event_type
            payload = event.payload
            if etype == ET.RUN_STARTED:
                console.print(f"  [dim]▶ run started  ({event.run_id[:8]}…)[/dim]")
            elif etype == ET.ASSISTANT_DELTA:
                delta = getattr(payload, "delta", "")
                console.print(delta, end="", highlight=False)
                full_text += delta
            elif etype == ET.ASSISTANT_FINAL:
                console.print()  # newline after streamed text
            elif etype == ET.TOOL_STARTED:
                name = getattr(payload, "tool_name", "?")
                args = getattr(payload, "args_summary", "")
                console.print(f"  [yellow]⚙ calling {name}[/yellow]  [dim]{args}[/dim]")
            elif etype == ET.TOOL_COMPLETED:
                name = getattr(payload, "tool_name", "?")
                ms = getattr(payload, "latency_ms", 0)
                summary = getattr(payload, "result_summary", "")[:80]
                console.print(f"  [green]✓ {name}[/green] [dim]({ms}ms) → {summary}[/dim]")
            elif etype == ET.TOOL_FAILED:
                name = getattr(payload, "tool_name", "?")
                err = getattr(payload, "error_message", "")
                console.print(f"  [red]✗ {name} failed:[/red] {err}")
            elif etype == ET.RUN_COMPLETED:
                in_t = getattr(payload, "input_tokens", 0)
                out_t = getattr(payload, "output_tokens", 0)
                ms = getattr(payload, "duration_ms", 0)
                console.print(f"  [dim]✔ run completed  in={in_t} out={out_t} {ms}ms[/dim]")
            elif etype == ET.RUN_FAILED:
                err = getattr(payload, "error_message", "?")
                console.print(f"  [red]run failed:[/red] {err}")
        return full_text

    try:
        if message:
            # Single-shot mode
            if trace:
                reply = await _run_with_trace(message)
            else:
                with console.status("[bold cyan]Betty is thinking...[/bold cyan]"):
                    reply = await agent_loop.process_chat_message(
                        user_id=user_id,
                        text=message,
                        conversation_history=history,
                    )
            console.print(f"\n[bold cyan]Betty:[/bold cyan] {reply}\n")
        else:
            # Interactive REPL mode
            console.print(
                "\n[bold green]GraphClaw Chat[/bold green] — type [bold]/quit[/bold] or "
                "[bold]/exit[/bold] to leave, [bold]/clear[/bold] to reset history.\n"
            )
            while True:
                try:
                    user_input = console.input("[bold]You:[/bold] ").strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Bye![/dim]")
                    break

                if not user_input:
                    continue
                if user_input.lower() in ("/quit", "/exit", "quit", "exit"):
                    console.print("[dim]Bye![/dim]")
                    break
                if user_input.lower() == "/clear":
                    history.clear()
                    console.print("[dim]Conversation history cleared.[/dim]")
                    continue

                if trace:
                    reply = await _run_with_trace(user_input)
                else:
                    with console.status("[bold cyan]Betty is thinking...[/bold cyan]"):
                        reply = await agent_loop.process_chat_message(
                            user_id=user_id,
                            text=user_input,
                            conversation_history=history,
                        )

                console.print(f"\n[bold cyan]Betty:[/bold cyan] {reply}\n")
                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": reply})
    finally:
        await pool.close()


@app.command("chat")
def agent_chat(
    message: str = typer.Argument(
        None,
        help="Single message to send. Omit to start an interactive session.",
    ),
    user_id: str = typer.Option(
        None,
        "--user-id",
        "-u",
        help="User ID. Defaults to GRAPHCLAW_USER_ID env var.",
    ),
    agent_id: str = typer.Option("main", "--agent-id", help="Agent storage ID (default: main)."),
    trace: bool = typer.Option(
        False,
        "--trace",
        "-t",
        help="Stream live run-trace events (tool calls, deltas, timing) to the terminal.",
    ),
) -> None:
    """Chat with the Betty agent. Omit MESSAGE for an interactive REPL session."""
    resolved_user_id = user_id or os.environ.get("GRAPHCLAW_USER_ID", "")
    if not resolved_user_id:
        err_console.print("User ID required. Pass --user-id or set GRAPHCLAW_USER_ID env var.")
        raise typer.Exit(code=1)
    try:
        run_async(_chat_async(resolved_user_id, agent_id, message, trace=trace))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)
