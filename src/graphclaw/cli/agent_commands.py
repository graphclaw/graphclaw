"""graphclaw.cli.agent_commands — Agent reasoning loop CLI sub-commands.

Description
-----------
Implements the three ``graphclaw agent`` sub-commands: ``run``, ``score``, and
``briefing``.  Each command initialises an ``AgentLoop`` from environment config,
runs one scoring cycle, and displays the results.  ``run`` and ``score`` display
the action queue table; ``briefing`` displays the human-readable priority summary.

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
- typer: CLI framework.
- rich: Console output with status spinners.
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

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
        asyncio.run(_run_cycle_async(top_n))
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
        asyncio.run(_score_async(top_n))
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
        asyncio.run(_briefing_async(top_n))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)
